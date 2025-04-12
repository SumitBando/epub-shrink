#!/usr/bin/env python3
"""epub-shrink: shrink EPUB files by cleaning unused assets and compressing images.

Usage:
    epub-shrink INPUT.epub [options]

Options:
    -o, --output FILE          Output file (default: INPUT stem + '-min.epub')
    -q, --quality N            Initial image quality (0‑100, default 100 = lossless)
    -t, --targetsize KB        Target size in KB (try lossy passes 95→25 until reached)
    -i, --ignore PATTERN       Extra glob(s) to delete (can repeat)
    -v, --verbose              Print disposition of each processed file
"""

import argparse
import pathlib
import shutil
import sys
import tempfile
import re
import zipfile
import subprocess
import os
from collections import defaultdict
from xml.etree import ElementTree as ET
from fnmatch import fnmatch
from PIL import Image

DEFAULT_IGNORE = [
    "generic-cross-sale",
    "promo.css",
    "xpromo",
    "*.DS_Store",
    "*.epubcheck*",  # EPUBCheck files
]

TMP_ROOT = pathlib.Path(tempfile.gettempdir())


def human(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f"{n:.1f} {unit}"
        n /= 1024


def parse_args():
    p = argparse.ArgumentParser(description="Lossless (and optionally lossy) EPUB optimiser")
    p.add_argument("epub", type=pathlib.Path, help="Input .epub file")
    p.add_argument("-o", "--output", type=pathlib.Path,
                   help="Output file (default: input stem + '-min.epub')")
    p.add_argument("-q", "--quality", type=int, default=100,
                   help="Initial lossy quality (0‑100, default 100=lossless)")
    p.add_argument("-t", "--targetsize", type=int,
                   help="Target size in KB (after lossless step)")
    p.add_argument("-i", "--ignore", action="append",
                   help="Extra glob pattern(s) to delete (can repeat)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def explode(epub_path: pathlib.Path) -> pathlib.Path:
    extract_dir = TMP_ROOT / f"epub-shrink-{os.getpid()}"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir()
    with zipfile.ZipFile(epub_path) as z:
        z.extractall(extract_dir)
    return extract_dir


def load_opf(root: pathlib.Path):
    opf_path = next(root.rglob("*.opf"))
    tree = ET.parse(opf_path)
    ns = {"opf": "http://www.idpf.org/2007/opf"}
    manifest = {item.attrib["href"]: item
                for item in tree.findall(".//opf:item", ns)}
    return opf_path, tree, manifest, ns


def remove_unreferenced(manifest, tree, ns, root, verbose=False):
    spine_refs = {item.attrib["idref"] for item in tree.findall(".//opf:itemref", ns)}
    keep_hrefs = {i.attrib["href"] for i in manifest.values()
                  if i.attrib["id"] in spine_refs}

    # Build a list of files to keep
    files_to_keep = set(keep_hrefs)  # Start with all files from the spine
    
    # Check for guide entries in the OPF file and add them to files_to_keep
    for reference in tree.findall(".//opf:guide/opf:reference", ns):
        href = reference.get("href")
        if href:
            # Check if file isn't already in the keep set
            if href not in files_to_keep:
                files_to_keep.add(href)
                if verbose:
                    print(f"Adding file from guide: {href} (type: {reference.get('type', 'unknown')})")
    
    # First check meta tags with name="cover"
    cover_id = None
    for meta in tree.findall(".//opf:meta[@name='cover']", ns):
        cover_id = meta.get("content")
        if cover_id:
            break
    
    # If cover ID found, find the corresponding item in manifest
    if cover_id:
        for item in tree.findall(".//opf:item", ns):
            if item.get("id") == cover_id:
                cover_href = item.get("href")
                if cover_href:
                    files_to_keep.add(cover_href)
                    if verbose:
                        print(f"Preserving cover image from metadata: {cover_href}")
    
    # Also check for cover in properties
    for item in tree.findall(".//opf:item[@properties]", ns):
        properties = item.get("properties", "").split()
        if "cover-image" in properties:
            cover_href = item.get("href")
            if cover_href:
                files_to_keep.add(cover_href)
                if verbose:
                    print(f"Preserving cover image from properties: {cover_href}")
    

    # Essential file types that should never be removed
    essential_patterns = [
        "*toc.ncx",                      # Navigation Control file for XML
        "Text/nav.xhtml",                # Common navigation file
        "*[Cc]ontents*",                 # Table of contents
        "*logo*",                        # Logo images
        "META-INF/*",                    # Package metadata
    ]
    # First pass - identify essential files
    for href, node in list(manifest.items()):
        if any(fnmatch(href, pat) for pat in essential_patterns):
            files_to_keep.add(href)
            if verbose:
                print(f"Preserving essential file: {href}")
    
    # Second pass - find all referenced files from XHTML files
    all_xhtml_files = [root / href for href in keep_hrefs]
    
    # Track all references we've found
    referenced_files = set()
    
    # Extensions to look for in content
    # image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp')
    # css_extensions = ('.css',)
    font_extensions = ('.ttf', '.otf', '.woff', '.woff2')
    
    # Regular expressions to find references in HTML
    href_re = re.compile(r'href=["\']([^"\']+)["\']')
    src_re = re.compile(r'src=["\']([^"\']+)["\']')
    url_re = re.compile(r'url\([\'"]?([^)\'"\s]+)')
    
    # Scan all XHTML files for references
    for file in all_xhtml_files:
        if file.exists():
            try:
                content = file.read_text(encoding='utf-8', errors='ignore')
                file_dir = file.parent
                
                # Find all href attributes (CSS files, links)
                for match in href_re.finditer(content):
                    href = match.group(1)
                    referenced_files.add(href)
                    # Handle relative paths by checking basename
                    referenced_files.add(os.path.basename(href))
                    
                    # Handle relative paths - resolve against the current file's directory
                    if not href.startswith('/') and not href.startswith('http'):
                        # Try to get the absolute path relative to current file
                        rel_path = os.path.normpath(str(file_dir / href))
                        rel_path_to_root = os.path.relpath(rel_path, str(root))
                        referenced_files.add(rel_path_to_root)
                        if verbose and href.lower().endswith('.css'):
                            print(f"Found stylesheet reference: {rel_path_to_root} in {file}")
                
                # Find all src attributes (images)
                for match in src_re.finditer(content):
                    src = match.group(1)
                    referenced_files.add(src)
                    # Handle relative paths by checking basename
                    referenced_files.add(os.path.basename(src))
                
                # Find all url() references (CSS, images)
                for match in url_re.finditer(content):
                    url = match.group(1)
                    referenced_files.add(url)
                    # Handle relative paths by checking basename
                    referenced_files.add(os.path.basename(url))
                
            except Exception as e:
                if verbose:
                    print(f"Error scanning {file}: {e}")
    
    # Process all CSS files to find font references
    css_files = []
    
    # First collect all CSS files - both from manifest and referenced in HTML
    for href in manifest:
        if href.lower().endswith('.css'):
            css_path = root / href
            if css_path.exists():
                css_files.append(css_path)
    
    # Find font files referenced in CSS
    font_urls = set()
    for css_file in css_files:
        try:
            content = css_file.read_text(encoding='utf-8', errors='ignore')
            
            # Find all @font-face declarations
            for match in url_re.finditer(content):
                url = match.group(1)
                font_urls.add(url)
                # Also add the basename for relative references
                font_urls.add(os.path.basename(url))
                
                # Add to our referenced files
                referenced_files.add(url)
                referenced_files.add(os.path.basename(url))
                
                # Handle relative paths from the CSS file location
                if not url.startswith('/') and not url.startswith('http'):
                    # Get absolute path relative to CSS file
                    rel_path = str(css_file.parent / url)
                    rel_path_to_root = os.path.relpath(rel_path, str(root))
                    referenced_files.add(rel_path_to_root)
                    if verbose and any(url.lower().endswith(ext) for ext in font_extensions):
                        print(f"Found font reference: {rel_path_to_root} in {css_file}")
        except Exception as e:
            if verbose:
                print(f"Error scanning CSS file {css_file}: {e}")
    
    # Now check if any file in the manifest is referenced
    for href in list(manifest.keys()):
        filename = os.path.basename(href)
        
        # Check for direct reference
        if href in referenced_files or filename in referenced_files:
            files_to_keep.add(href)
            if verbose:
                print(f"Found reference to: {href}")
        
        # Special handling for fonts - check against font URLs
        if any(href.lower().endswith(ext) for ext in font_extensions):
            if href in font_urls or filename in font_urls:
                files_to_keep.add(href)
                if verbose:
                    print(f"Found font reference: {href}")
                    
        # Special handling for CSS files - always preserve stylesheets
        if href.lower().endswith('.css'):
            if "stylesheet" in href.lower() or "style" in href.lower():
                files_to_keep.add(href)
                if verbose:
                    print(f"Preserving stylesheet: {href}")
    
    # Now remove files that are not in files_to_keep
    for href, node in list(manifest.items()):
        if href not in files_to_keep:
            if verbose:
                print(f"Removing unreferenced file: {href}")
            file_path = root / href
            if file_path.exists():
                file_path.unlink()
            parent = node.getparent() if hasattr(node, 'getparent') else tree.getroot()
            if node in parent:
                parent.remove(node)


def delete_ignored(patterns, root, tree, manifest, verbose=False):
    removed = []
    for href in list(manifest.keys()):
        if any(fnmatch(href, pat) for pat in patterns):
            removed.append(href)
            (root / href).unlink(missing_ok=True)
            manifest[href].getparent().remove(manifest[href]) if hasattr(manifest[href], 'getparent') else tree.getroot().remove(manifest[href])
    if verbose and removed:
        print("Ignored‑pattern files:", *removed, sep="\n  ")
    return removed


def css_referenced_fonts(root):
    css_files = list(root.rglob("*.css"))
    font_refs = set()
    font_basenames = set()
    url_re = re.compile(r"url\(['\"]?([^)'\"\s]+)")
    for css in css_files:
        for m in url_re.finditer(css.read_text(errors="ignore")):
            href = m.group(1)
            if href.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
                # Store both the resolved path and the basename
                font_refs.add((css.parent / href).resolve())
                font_basenames.add(os.path.basename(href))
    
    # Also find all actual font files in the EPUB
    all_fonts = set()
    for ext in (".ttf", ".otf", ".woff", ".woff2"):
        for font_path in root.rglob(f"*{ext}"):
            all_fonts.add(font_path.resolve())
            
    # If we have basenames of fonts referenced in CSS but couldn't resolve them,
    # try to find matching files by basename
    for font_basename in font_basenames:
        for font_path in all_fonts:
            if font_path.name.lower() == font_basename.lower():
                font_refs.add(font_path)
    
    return font_refs


def remove_unreferenced_fonts(root, manifest, verbose=False):
    referenced = css_referenced_fonts(root)
    if verbose:
        print("Referenced fonts:", *[str(f.relative_to(root)) for f in referenced], sep="\n  ")
    
    removed = []
    preserved = []
    for href in list(manifest.keys()):
        if href.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
            font_path = (root / href).resolve()
            font_basename = os.path.basename(href)
            
            # Check if this font is referenced by its path or basename
            is_referenced = False
            
            # Check by full path
            if font_path in referenced:
                is_referenced = True
            
            # Check if any referenced font has the same basename
            if not is_referenced:
                for ref in referenced:
                    if ref.name.lower() == font_basename.lower():
                        is_referenced = True
                        break
            
            if not is_referenced:
                removed.append(href)
                (root / href).unlink(missing_ok=True)
                del manifest[href]
            else:
                preserved.append(href)
    
    if verbose:
        if preserved:
            print("Preserved fonts:", *preserved, sep="\n  ")
        if removed:
            print("Fonts not referenced by CSS:", *removed, sep="\n  ")
    
    return removed


def compress_image(path: pathlib.Path, quality: int, verbose=False):
    before = path.stat().st_size
    try:
        img = Image.open(path)
        fmt = img.format
        if quality == 100:
            if fmt == "JPEG" and shutil.which("jpegoptim"):
                cmd = ["jpegoptim", "--strip-all", str(path)]
                print(f"Running: {' '.join(cmd)}")
                subprocess.run(cmd, stdout=subprocess.DEVNULL)
            elif fmt == "PNG" and shutil.which("oxipng"):
                oxipng_args = ["oxipng", "-o", "4", "--strip", "safe"]
                # if not verbose:
                # oxipng_args.append("-q")
                oxipng_args.append(str(path))
                print(f"Running: {' '.join(oxipng_args)}")
                subprocess.run(oxipng_args, stdout=subprocess.DEVNULL)
            else:
                img.save(path, format=fmt, optimize=True)
        else:
            if fmt == "JPEG":
                img.save(path, format="JPEG", quality=quality,
                         optimize=True, progressive=True)
            elif fmt == "PNG":
                img = img.convert("P", palette=Image.ADAPTIVE)
                img.save(path, format="PNG", optimize=True)
    except Exception as e:
        if verbose:
            print("Image compress error:", path, e)
    return before, path.stat().st_size


def compress_images(root, quality, verbose=False):
    # Find all image paths
    jpg_paths = [*root.rglob("*.jpg"), *root.rglob("*.jpeg")]
    png_paths = list(root.rglob("*.png"))
    webp_paths = list(root.rglob("*.webp"))
    
    savings = []
    
    # Process PNG files by directory to optimize oxipng performance
    if png_paths and shutil.which("oxipng") and quality == 100:
        if verbose:
            print("Processing PNG files by directory using oxipng...")
        
        png_dirs = defaultdict(list)
        for png_path in png_paths:
            png_dirs[png_path.parent].append(png_path)
        
        for directory, files in png_dirs.items():
            if verbose:
                print(f"Optimizing {len(files)} PNG files in {directory.relative_to(root)}")
            
            # Record sizes before compression
            before_sizes = {f: f.stat().st_size for f in files}
            
            oxipng_args = ["oxipng", "-o", "4", "--strip", "safe"]
            if not verbose:
                oxipng_args.append("-q")
            
            oxipng_args.extend([str(f) for f in files])
            print(oxipng_args)
            subprocess.run(oxipng_args, stdout=subprocess.DEVNULL)
            
            for f in files:
                before = before_sizes[f]
                after = f.stat().st_size
                if verbose:
                    relative_path = f.relative_to(root)
                    reduction_pct = (before - after) / before * 100 if before > 0 else 0
                    print(f"{relative_path}: {human(before)} → {human(after)} ({reduction_pct:.1f}% reduction)")
                savings.append((before, after))
    
    # Process JPEG files by directory to optimize jpegoptim performance
    if jpg_paths and shutil.which("jpegoptim") and quality == 100:
        if verbose:
            print("Processing JPEG files by directory using jpegoptim...")
        
        jpg_dirs = defaultdict(list)
        for jpg_path in jpg_paths:
            jpg_dirs[jpg_path.parent].append(jpg_path)
        
        for directory, files in jpg_dirs.items():
            if verbose:
                print(f"Optimizing {len(files)} JPEG files in {directory.relative_to(root)}")
            
            before_sizes = {f: f.stat().st_size for f in files}
            
            jpegoptim_args = ["jpegoptim", "--strip-all"]
            if not verbose:
                jpegoptim_args.append("-q")
            
            # print(jpegoptim_args)
            jpegoptim_args.extend([str(f) for f in files])
            print(jpegoptim_args)
            subprocess.run(jpegoptim_args, stdout=subprocess.DEVNULL)
            
            for f in files:
                before = before_sizes[f]
                after = f.stat().st_size
                if verbose:
                    relative_path = f.relative_to(root)
                    reduction_pct = (before - after) / before * 100 if before > 0 else 0
                    print(f"{relative_path}: {human(before)} → {human(after)} ({reduction_pct:.1f}% reduction)")
                savings.append((before, after))
    
    img_paths = webp_paths
    if quality < 100:
        img_paths += png_paths + jpg_paths
    else:
        img_paths += [p for p in webp_paths]
        
        if not shutil.which("oxipng"):
            img_paths += png_paths
            
        if not shutil.which("jpegoptim"):
            img_paths += jpg_paths
    
    for p in img_paths:
        if ((p in png_paths and shutil.which("oxipng") and quality == 100) or 
            (p in jpg_paths and shutil.which("jpegoptim") and quality == 100)):
            continue
            
        b, a = compress_image(p, quality, verbose)
        if verbose:
            relative_path = p.relative_to(root)
            reduction_pct = (b - a) / b * 100 if b > 0 else 0
            print(f"{relative_path}: {human(b)} → {human(a)} ({reduction_pct:.1f}% reduction)")
        savings.append((b, a))
    
    return savings


def rebuild_epub(root: pathlib.Path, out_path: pathlib.Path):
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for file in root.rglob("*"):
            if file.is_file():
                z.write(file, file.relative_to(root))


def process_epub(epub_path, extract_dir, quality, out_path, ignore_patterns, verbose=False, keep_files=None):
    """Process an EPUB file with the given quality setting.
    
    Args:
        epub_path: Path to the original EPUB file
        extract_dir: Directory to extract to (or None to create a new one)
        quality: Image quality (0-100)
        out_path: Output path for the compressed EPUB (if None, will be generated based on quality)
        ignore_patterns: List of patterns to ignore
        verbose: Whether to print verbose output
        keep_files: Optional pre-computed list of files to keep (skip reference analysis if provided)
        
    Returns:
        Tuple of (extract_dir, final_size, keep_files, out_path)
    """
    # Extract the EPUB if extract_dir is not provided
    if extract_dir is None:
        extract_dir = explode(epub_path)
    
    # If out_path is not provided, generate based on quality
    if out_path is None:
        if quality == 100:
            out_path = epub_path.with_stem(epub_path.stem + "-lossless")
        else:
            out_path = epub_path.with_stem(f"{epub_path.stem}-q{quality}")
    
    # Load and process OPF file
    opf_path, tree, manifest, ns = load_opf(extract_dir)
    
    # If we don't have pre-computed keep_files, perform reference analysis
    if keep_files is None:
        if verbose:
            print("Performing reference analysis...")
        # Apply cleanup steps to determine which files to keep
        remove_unreferenced(manifest, tree, ns, extract_dir, verbose)
        delete_ignored(DEFAULT_IGNORE + (ignore_patterns or []),
                      extract_dir, tree, manifest, verbose)
        remove_unreferenced_fonts(extract_dir, manifest, verbose)
        
        # Store the list of files that survived reference analysis
        keep_files = set(manifest.keys())
        if verbose:
            print(f"Found {len(keep_files)} files to preserve after reference analysis.")
    else:
        # Use the pre-computed list to skip reference analysis
        if verbose:
            print(f"Using pre-computed list of {len(keep_files)} files to preserve.")
        
        # Remove files not in the keep_files list
        to_remove = []
        for href, node in list(manifest.items()):
            if href not in keep_files:
                to_remove.append(href)
                file_path = extract_dir / href
                if file_path.exists():
                    file_path.unlink()
                parent = node.getparent() if hasattr(node, 'getparent') else tree.getroot()
                if node in parent:
                    parent.remove(node)
        
        if verbose and to_remove:
            print(f"Removed {len(to_remove)} files not in the keep list.")
    
    # Save the cleaned tree to opf file
    tree.write(opf_path, encoding="utf-8", xml_declaration=True)
    
    # Compress images with the specified quality
    compress_images(extract_dir, quality, verbose)
    
    # Rebuild the EPUB
    rebuild_epub(extract_dir, out_path)
    final_size = out_path.stat().st_size
    
    return extract_dir, final_size, keep_files, out_path


def main():
    args = parse_args()
    original = args.epub.stat().st_size
    print("Original:", human(original))

    # Initial extraction and processing
    extract_dir = None
    keep_files = None
    
    # First attempt with initial quality
    extract_dir, final, keep_files, out_path = process_epub(
        args.epub, 
        extract_dir=extract_dir,
        quality=args.quality, 
        out_path=args.output,  # Use user specified output if provided, otherwise None
        ignore_patterns=args.ignore, 
        verbose=args.verbose,
        keep_files=keep_files
    )
    
    # Store initial quality
    q = args.quality

    # Check if target size is specified and not met
    if args.targetsize and final / 1024 > args.targetsize and q > 25:
        print(f"Target {args.targetsize}KB not met, current size {human(final)}")
        
        # Try progressively lower qualities until target is met or quality floor is reached
        while args.targetsize and final / 1024 > args.targetsize and q > 25:
            # Calculate new quality level
            q = max(q - 5, 25)
            print(f"Retrying with lossy quality={q}")
            
            # Clean up previous temporary directory
            shutil.rmtree(extract_dir)
            
            # Process the EPUB with new quality setting, reusing the keep_files list
            extract_dir, final, _, out_path = process_epub(
                args.epub, 
                extract_dir=None,  # Start fresh to avoid quality degradation
                quality=q, 
                out_path=args.output,  # Use user specified output if provided, otherwise None
                ignore_patterns=args.ignore, 
                verbose=args.verbose,
                keep_files=keep_files  # Reuse the computed list of files to keep
            )
            print(f"Quality {q}: {human(final)}")
    
    print(f"Final:   {human(final)}  (saved {(original - final) / original:.1%})")
    print(f"Output file: {out_path}")
    shutil.rmtree(extract_dir)


if __name__ == "__main__":
    main()
