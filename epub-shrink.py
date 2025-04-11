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
    "Generic Cross Sales.xhtml",
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
    tmp = TMP_ROOT / f"epub-shrink-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    with zipfile.ZipFile(epub_path) as z:
        z.extractall(tmp)
    return tmp


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
    
    # Essential file types that should never be removed
    essential_patterns = [
        "*toc.ncx",                      # Navigation Control file for XML
        "Text/nav.xhtml",                # Common navigation file
        # "*nav.*",                        # Navigation Document (EPUB3)
        # "*.css",                       # All stylesheets
        # "Styles/*stylesheet*.css",     # Common stylesheet paths
        # "*[Cc]over*",                  # Cover images/files (now handled via metadata)
        "*[Cc]ontents*",                 # Table of contents
        "*logo*",                        # Logo images
        "META-INF/*",                    # Package metadata
    ]
    
    # Build a list of files to keep
    files_to_keep = set(keep_hrefs)  # Start with all files from the spine
    
    # Find cover image from metadata
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
    removed = []
    for href, node in list(manifest.items()):
        if href not in files_to_keep:
            removed.append(href)
            file_path = root / href
            if file_path.exists():
                file_path.unlink()
            parent = node.getparent() if hasattr(node, 'getparent') else tree.getroot()
            if node in parent:
                parent.remove(node)
    
    if verbose and removed:
        print("Unreferenced files:", *removed, sep="\n  ")
    return removed


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
                subprocess.run(["jpegoptim", "--strip-all", str(path)],
                               stdout=subprocess.DEVNULL)
            elif fmt == "PNG" and shutil.which("oxipng"):
                oxipng_args = ["oxipng", "-o", "4", "--strip", "safe"]
                # if not verbose:
                # oxipng_args.append("-q")
                oxipng_args.append(str(path))
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
        
        # Group PNG files by directory
        png_dirs = defaultdict(list)
        for png_path in png_paths:
            png_dirs[png_path.parent].append(png_path)
        
        # Process each directory of PNGs at once
        for directory, files in png_dirs.items():
            if verbose:
                print(f"Optimizing {len(files)} PNG files in {directory.relative_to(root)}")
            
            # Record sizes before compression
            before_sizes = {f: f.stat().st_size for f in files}
            
            # Run oxipng on all PNG files in the directory at once
            oxipng_args = ["oxipng", "-o", "4", "--strip", "safe"]
            if not verbose:
                oxipng_args.append("-q")
            
            # Add all PNG files in this directory to the command
            oxipng_args.extend([str(f) for f in files])
            
            subprocess.run(oxipng_args, stdout=subprocess.DEVNULL)
            
            # Record sizes after compression
            for f in files:
                before = before_sizes[f]
                after = f.stat().st_size
                if verbose:
                    relative_path = f.relative_to(root)
                    reduction_pct = (before - after) / before * 100 if before > 0 else 0
                    print(f"{relative_path}: {human(before)} → {human(after)} ({reduction_pct:.1f}% reduction)")
                savings.append((before, after))
    
    # Process remaining image types individually
    img_paths = jpg_paths + webp_paths
    if quality < 100:
        # Also process PNG files individually if we're using a lossy quality setting
        img_paths += png_paths
    
    for p in img_paths:
        if p in png_paths and quality == 100:
            # Skip PNGs we've already processed
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


def main():
    args = parse_args()
    out = args.output or args.epub.with_stem(args.epub.stem + "-min")
    original = args.epub.stat().st_size
    print("Original:", human(original))

    tmp = explode(args.epub)
    opf_path, tree, manifest, ns = load_opf(tmp)

    remove_unreferenced(manifest, tree, ns, tmp, args.verbose)
    delete_ignored(DEFAULT_IGNORE + (args.ignore or []),
                   tmp, tree, manifest, args.verbose)
    remove_unreferenced_fonts(tmp, manifest, args.verbose)

    tree.write(opf_path, encoding="utf-8", xml_declaration=True)

    compress_images(tmp, args.quality, args.verbose)
    rebuild_epub(tmp, out)
    final = out.stat().st_size

    q = args.quality
    while args.targetsize and final / 1024 > args.targetsize and q > 25:
        q = max(q - 5, 25)
        # if args.verbose:
        print(f"Target not met, current size {final}, retrying lossy quality={q}")
        compress_images(tmp, q, args.verbose)
        rebuild_epub(tmp, out)
        final = out.stat().st_size

    print(f"Final:   {human(final)}  (saved {(original - final) / original:.1%})")
    shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
