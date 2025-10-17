#!/usr/bin/env python3
"""epub-shrink: shrink EPUB files by cleaning unused assets and compressing images.

Usage:
    epub-shrink INPUT.epub [options]

Options:
    -o, --output FILE          Output file (default: INPUT stem + '-min.epub')
    -q, --quality N            Initial image quality (0‑100, default 100 = lossless)
    -t, --targetsize MB        Target size in MB (try lossy passes 95→25 until reached)
    -i, --purge PATTERN       Extra glob(s) to delete (can repeat)
    -v, --verbose              Print disposition of each processed file
"""

import argparse
import pathlib
import shutil
import sys
import tempfile
import zipfile
import subprocess
import os
from collections import defaultdict
from xml.etree import ElementTree as ET
from fnmatch import fnmatch
from PIL import Image
from bs4 import BeautifulSoup
import tinycss2

TMP_ROOT = pathlib.Path(tempfile.gettempdir())

GLOBAL_EXTRACT_DIR = None
GLOBAL_OPF_PATH = None
GLOBAL_TREE = None
GLOBAL_MANIFEST = None
GLOBAL_NS = None
GLOBAL_KEEP_FILES = None
GLOBAL_INPUT_FILE = None
GLOBAL_VERBOSE = False


def verify_compressors_availability():
    """Check if required image compressors are available."""
    if not shutil.which("jpegoptim"):
        print("Please install missing jpeg compressor jpegoptim for JPEG optimization")
        sys.exit(1)
    
    if not shutil.which("oxipng"):
        print("Please install missing PNG compressor oxipng for PNG optimization")
        sys.exit(1)


def human(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f"{n:.1f} {unit}"
        n /= 1024


def parse_args():
    p = argparse.ArgumentParser(description="Lossless (and optionally lossy) EPUB optimiser")
    p.add_argument("epub", type=pathlib.Path, help="Input .epub file", nargs="?")
    p.add_argument("-o", "--output", type=pathlib.Path,
                   help="Output file (default: input stem + '-min.epub')")
    p.add_argument("-q", "--quality", type=int, default=100,
                   help="Initial lossy quality (0‑100, default 100=lossless)")
    p.add_argument("-t", "--targetsize", type=int,
                   help="Target size in MB (after lossless step)")
    p.add_argument("-i", "--purge", action="append",
                   help="Extra glob pattern(s) to delete (can repeat)")
    p.add_argument("-v", "--verbose", action="store_true")
    
    args = p.parse_args()
    if args.epub is None:
        p.print_help()
        sys.exit(1)
    
    return args


def unzip() -> pathlib.Path:
    global GLOBAL_INPUT_FILE, GLOBAL_EXTRACT_DIR
    """Extract the EPUB to a new temporary directory."""
    GLOBAL_EXTRACT_DIR = TMP_ROOT / f"epub-shrink-{os.getpid()}"
    if GLOBAL_EXTRACT_DIR.exists():
        shutil.rmtree(GLOBAL_EXTRACT_DIR)
    GLOBAL_EXTRACT_DIR.mkdir()
    zipfile.ZipFile(GLOBAL_INPUT_FILE).extractall(GLOBAL_EXTRACT_DIR)
    return GLOBAL_EXTRACT_DIR


def load_opf():
    """Find and load the 'Open Package Format' file using container.xml or fallback to direct search. 
    
    According to the EPUB spec, META-INF/container.xml points to the OPF file.
    If container.xml isn't found or doesn't contain a valid reference,
    fall back to searching for .opf files directly.
    """
    global GLOBAL_EXTRACT_DIR
    container_path = GLOBAL_EXTRACT_DIR / "META-INF" / "container.xml"
    opf_path = None
    
    # First try to find the OPF file from container.xml
    if container_path.exists():
        try:
            container_tree = ET.parse(container_path)
            # Define namespace for container.xml
            container_ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
            # Look for rootfile with media-type="application/oebps-package+xml"
            rootfiles = container_tree.findall(".//c:rootfile", container_ns)
            
            for rootfile in rootfiles:
                if rootfile.get("media-type") == "application/oebps-package+xml":
                    opf_path_str = rootfile.get("full-path")
                    if opf_path_str:
                        opf_path = GLOBAL_EXTRACT_DIR / opf_path_str
                        if opf_path.exists():
                            break
        except Exception as e:
            print(f"Warning: Error parsing container.xml: {e}")
            opf_path = None
    
    # Fallback: If container.xml parsing fails, search for .opf files directly
    if not opf_path or not opf_path.exists():
        try:
            opf_path = next(GLOBAL_EXTRACT_DIR.rglob("*.opf"))
            print(f"Using fallback OPF file: {opf_path.relative_to(GLOBAL_EXTRACT_DIR)}")
        except StopIteration:
            raise FileNotFoundError("No .opf file found in the EPUB")
    
    # Parse the OPF file to get manifest
    tree = ET.parse(opf_path)
    ns = {"opf": "http://www.idpf.org/2007/opf"}
    manifest = {item.attrib["href"]: item
                for item in tree.findall(".//opf:item", ns)}
    
    return opf_path, tree, manifest, ns



def remove_unreferenced(manifest, tree, ns, root, content_dir=None):
    global GLOBAL_VERBOSE
    spine_refs = {item.attrib["idref"] for item in tree.findall(".//opf:itemref", ns)}
    keep_hrefs = {i.attrib["href"] for i in manifest.values()
                  if i.attrib["id"] in spine_refs}
    files_to_keep = set(keep_hrefs)  # Start with all files from the spine
    
    # Check for guide entries in the OPF file and add them to files_to_keep
    for reference in tree.findall(".//opf:guide/opf:reference", ns):
        href = reference.get("href")
        if href:
            # Check if file isn't already in the keep set
            if href not in files_to_keep:
                files_to_keep.add(href)
                if GLOBAL_VERBOSE:
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
                    if GLOBAL_VERBOSE:
                        print(f"Preserving cover image from metadata: {cover_href}")
    
    # Also check for cover in properties
    for item in tree.findall(".//opf:item[@properties]", ns):
        properties = item.get("properties", "").split()
        if "cover-image" in properties:
            cover_href = item.get("href")
            if cover_href:
                files_to_keep.add(cover_href)
                if GLOBAL_VERBOSE:
                    print(f"Preserving cover image from properties: {cover_href}")
    

    # Essential file types that should never be removed
    essential_patterns = [
        "*.ncx",                      # Navigation Control file for XML
        "nav.xhtml",                   # Common navigation file
        "*[Cc]ontents*",                 # Table of contents
        "*logo*",                        # Logo images
        "META-INF/*",                    # Package metadata
    ]
    # First pass - identify essential files
    for href, node in list(manifest.items()):
        if any(fnmatch(href, pat) for pat in essential_patterns):
            files_to_keep.add(href)
            if GLOBAL_VERBOSE:
                print(f"Preserving essential file: {href}")
    
    # Second pass - find all referenced files from XHTML files
    all_xhtml_files = [root / href for href in keep_hrefs]
    
    # Track all references we've found
    referenced_files = set()
    
    # Extensions to look for in content
    font_extensions = ('.ttf', '.otf', '.woff', '.woff2')

    # Scan all XHTML files for references
    for file in all_xhtml_files:
        if file.exists():
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    soup = BeautifulSoup(f, 'lxml')
                    file_dir = file.parent

                    for attribute in ['href', 'src']:
                        for tag in soup.find_all(attrs={attribute: True}):
                            ref = tag[attribute]
                            if ref:
                                referenced_files.add(ref)
                                referenced_files.add(os.path.basename(ref))
                                if not ref.startswith('/') and not ref.startswith('http'):
                                    rel_path = os.path.normpath(str(file_dir / ref))
                                    rel_path_to_root = os.path.relpath(rel_path, str(root))
                                    referenced_files.add(rel_path_to_root)
            except Exception as e:
                if GLOBAL_VERBOSE:
                    print(f"Error processing {file}: {e}")

    # Process all CSS files to find font references
    css_files = []
    
    # Only collect CSS files that are referenced in HTML
    for href in list(manifest.keys()):
        if href.lower().endswith('.css'):
            # Only include CSS files that are referenced in HTML
            if href in referenced_files or os.path.basename(href) in referenced_files:
                css_path = root / href
                if css_path.exists():
                    css_files.append(css_path)
                    if GLOBAL_VERBOSE:
                        print(f"Keeping referenced CSS file: {href}")
            else:
                print(f"Dropping unreferenced CSS file: {href}")
    
    # Find font files and imports referenced in CSS
    font_urls = set()
    for css_file in css_files:
        try:
            content = css_file.read_text(encoding='utf-8', errors='ignore')
            rules = tinycss2.parse_stylesheet(content, skip_comments=True, skip_whitespace=True)
            
            for rule in rules:
                if rule.type == 'at-rule' and rule.at_keyword == 'import':
                    for token in rule.prelude:
                        if token.type == 'string' or token.type == 'url':
                            url = token.value
                            referenced_files.add(url)
                            referenced_files.add(os.path.basename(url))
                            if not url.startswith('/') and not url.startswith('http'):
                                rel_path = str(css_file.parent / url)
                                rel_path_to_root = os.path.relpath(rel_path, str(root))
                                referenced_files.add(rel_path_to_root)
                                if GLOBAL_VERBOSE:
                                    print(f"Found @import reference: {rel_path_to_root} in {css_file}")
                elif rule.type == 'qualified-rule':
                    for token in rule.content:
                        if token.type == 'url':
                            url = token.value
                            referenced_files.add(url)
                            referenced_files.add(os.path.basename(url))
                            if any(url.lower().endswith(ext) for ext in font_extensions):
                                font_urls.add(url)
                                font_urls.add(os.path.basename(url))

                            if not url.startswith('/') and not url.startswith('http'):
                                rel_path = str(css_file.parent / url)
                                rel_path_to_root = os.path.relpath(rel_path, str(root))
                                referenced_files.add(rel_path_to_root)
                                if GLOBAL_VERBOSE and any(url.lower().endswith(ext) for ext in font_extensions):
                                    print(f"Found font reference: {rel_path_to_root} in {css_file}")

        except Exception as e:
            if GLOBAL_VERBOSE:
                print(f"Error scanning CSS file {css_file}: {e}")
    
    # Now check if any file in the manifest is referenced
    for href in list(manifest.keys()):
        filename = os.path.basename(href)
        
        # Check for direct reference
        if href in referenced_files or filename in referenced_files:
            files_to_keep.add(href)
            if GLOBAL_VERBOSE:
                print(f"Found reference to: {href}")
        
        # Special handling for fonts - check against font URLs
        if any(href.lower().endswith(ext) for ext in font_extensions):
            if href in font_urls or filename in font_urls:
                files_to_keep.add(href)
                if GLOBAL_VERBOSE:
                    print(f"Found font reference: {href}")
    
    # Now remove files that are not in files_to_keep
    for href, node in list(manifest.items()):
        if href not in files_to_keep:
            file_path = root / href
            if file_path.exists():
                file_path.unlink()
                if GLOBAL_VERBOSE:
                    print(f"Successfully removed file from disk: {href}")
            else:
                if GLOBAL_VERBOSE:
                    print(f"File to remove did not exist on disk: {href}")
            # Use standard library approach for removing nodes
            parent = tree.getroot()
            for child in list(parent):
                if child == node:
                    parent.remove(child)
                    break


def purge_unwanted_files(purge_patterns, extract_dir, content_dir, tree, manifest):
    global GLOBAL_VERBOSE
    if GLOBAL_VERBOSE:
        print("Purging unwanted files...")
    DEFAULT_PURGES = [
        "*.DS_Store",
        "*.epubcheck*",
        "*cross-sale*",
        "*cross-sell*",
        "*xpromo*",
        "promo.css",
        "next-reads",
        "newsletter",
        "com.apple.ibooks.display-options.xml",
    ]
    all_patterns = DEFAULT_PURGES + (purge_patterns or [])
    for href in list(manifest.keys()):
        if any(fnmatch(href, pat) for pat in all_patterns):
            remove_from_spine(tree, href)
            remove_from_manifest(tree, href)
            remove_file(extract_dir, content_dir, href)
            print(f"Removed file: {href} from spine, manifest, and disk")

def remove_from_spine(tree, href):
    global GLOBAL_VERBOSE
    try:
        # Find the manifest item with the matching href
        manifest = tree.find("{http://www.idpf.org/2007/opf}manifest")
        item = None
        
        # Search all items for the matching href
        for manifest_item in manifest.findall("*"):
            if manifest_item.get("href") == href:
                item = manifest_item
                break
                
        if item is None:
            print(f"Warning: Could not find manifest item with href '{href}'")
            return
            
        # Get the item's id
        item_id = item.get("id")
        
        # Find and remove the corresponding spine itemref
        spine = tree.find("{http://www.idpf.org/2007/opf}spine")
        for itemref in list(spine):
            if itemref.get("idref") == item_id:
                spine.remove(itemref)
                if GLOBAL_VERBOSE:
                    print(f"Removed {href} from spine")
                break
    except Exception as e:
        print(f"Warning: Could not remove {href} from spine: {e}")

def remove_from_manifest(tree, href):
    try:
        parent = tree.getroot()
        # from root, get manifest, then iterate through manifest items to find the one to remove
        manifest = parent.find("{http://www.idpf.org/2007/opf}manifest")
        for item in list(manifest):
            if item.get("href") == href:
                manifest.remove(item)
                break
    except Exception as e:
        print(f"Warning: Could not remove {href} from manifest: {e}")

def remove_file(extract_dir,content_dir, href):
    """Remove a file from the content directory."""
    # Use content_dir if available, otherwise fall back to extract_dir
    # if content_dir:
    #     (content_dir / href).unlink(missing_ok=True)
    # else:
    #     (extract_dir / href).unlink(missing_ok=True)

    try:
        file_path = content_dir / href
        if file_path.exists():
            file_path.unlink()
    except Exception as e:
        print(f"Warning: Could not remove {href}: {e}")


def css_referenced_fonts(root):
    css_files = list(root.rglob("*.css"))
    font_refs = set()
    font_basenames = set()
    for css in css_files:
        content = css.read_text(errors="ignore")
        rules = tinycss2.parse_stylesheet(content, skip_comments=True, skip_whitespace=True)
        for rule in rules:
            if rule.type == 'qualified-rule':
                for token in rule.content:
                    if token.type == 'url':
                        href = token.value
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

def remove_unreferenced_fonts(root, manifest, content_dir=None):
    global GLOBAL_VERBOSE
    referenced = css_referenced_fonts(root)
    if GLOBAL_VERBOSE:
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
    
    if GLOBAL_VERBOSE:
        if preserved:
            print("Preserved fonts:", *preserved, sep="\n  ")
        if removed:
            print("Fonts not referenced by CSS:", *removed, sep="\n  ")
    
    return removed


def compress_image(path: pathlib.Path, quality: int):
    global GLOBAL_VERBOSE
    before = path.stat().st_size
    try:
        img = Image.open(path)
        fmt = img.format
        if quality == 100:
            if fmt == "JPEG":
                cmd = ["jpegoptim", "--strip-all", str(path)]
                # print(f"Running jpegoptim: {' '.join(cmd)}")
                subprocess.run(cmd, stdout=subprocess.DEVNULL)
            elif fmt == "PNG":
                oxipng_args = ["oxipng", "-o", "4", "--strip", "safe"]
                # if not GLOBAL_VERBOSE:
                # oxipng_args.append("-q")
                oxipng_args.append(str(path))
                # print(f"Running oxipng: {' '.join(oxipng_args)}")
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
        if GLOBAL_VERBOSE:
            print("Image compress error:", path, e)
    return before, path.stat().st_size


def compress_images(root, quality):
    global GLOBAL_VERBOSE
    # Find all image paths
    jpg_paths = [*root.rglob("*.jpg"), *root.rglob("*.jpeg")]
    png_paths = list(root.rglob("*.png"))
    webp_paths = list(root.rglob("*.webp"))
    
    # Print summary of found images
    print(f"Found {len(jpg_paths)} JPEG files, {len(png_paths)} PNG files, and {len(webp_paths)} WebP files")
    
    savings = []
    
    # Process PNG files by directory to optimize oxipng performance
    if png_paths and quality == 100:
        png_dirs = defaultdict(list)
        for png_path in png_paths:
            png_dirs[png_path.parent].append(png_path)
        
        for directory, files in png_dirs.items():
            if GLOBAL_VERBOSE:
                print(f"\nProcessing {len(files)} PNG files in {directory.relative_to(root)} using oxipng with quality: {quality}...")
            
            # Record sizes and analysis data before compression
            before_data = {}
            for f in files:
                if GLOBAL_VERBOSE:
                    image_info = analyze_image_quality(f)
                    before_data[f] = {
                        'size': f.stat().st_size,
                        'analysis': image_info
                    }
                else:
                    before_data[f] = {'size': f.stat().st_size}
            
            # Run the optimization
            oxipng_args = ["oxipng", "-q", "-o", "4", "--strip", "safe"]
            
            oxipng_args.extend([str(f) for f in files])
            subprocess.run(oxipng_args, stdout=subprocess.DEVNULL)
            
            # Compare before and after
            for f in files:
                before = before_data[f]['size']
                after = f.stat().st_size
                if GLOBAL_VERBOSE:
                    relative_path = f.relative_to(root)
                    reduction_pct = (before - after) / before * 100 if before > 0 else 0
                    image_info = before_data[f]['analysis']
                    if 'error' not in image_info:
                        dims = f"{image_info['dimensions'][0]}x{image_info['dimensions'][1]}"
                        mode = image_info['mode']
                        color_type = "Unknown"
                        if image_info['png_info'] and 'color_type' in image_info['png_info']:
                            color_type = image_info['png_info']['color_type']
                        
                        print(f"PNG: {relative_path} | Dims: {dims} | Mode: {mode} | Type: {color_type} | {human(before)} → {human(after)} ({reduction_pct:.1f}% saved)")
                
                savings.append((before, after))
    
    # Process JPEG files by directory to optimize jpegoptim performance
    if jpg_paths and quality == 100:        
        jpg_dirs = defaultdict(list)
        for jpg_path in jpg_paths:
            jpg_dirs[jpg_path.parent].append(jpg_path)
        
        for directory, files in jpg_dirs.items():
            if GLOBAL_VERBOSE:
                print(f"\nProcessing {len(files)} JPEG files in {directory.relative_to(root)} using jpegoptim with quality: {quality}...")
            
            # Record sizes and analysis data before compression
            before_data = {}
            for f in files:
                if GLOBAL_VERBOSE:
                    image_info = analyze_image_quality(f)
                    before_data[f] = {
                        'size': f.stat().st_size,
                        'analysis': image_info
                    }
                else:
                    before_data[f] = {'size': f.stat().st_size}
            
            # Run the optimization
            jpegoptim_args = ["jpegoptim", "--strip-all"]
            if not GLOBAL_VERBOSE:
                jpegoptim_args.append("-q")
            
            jpegoptim_args.extend([str(f) for f in files])
            subprocess.run(jpegoptim_args, stdout=subprocess.DEVNULL)
            
            # Compare before and after
            for f in files:
                before = before_data[f]['size']
                after = f.stat().st_size
                if GLOBAL_VERBOSE:
                    relative_path = f.relative_to(root)
                    reduction_pct = (before - after) / before * 100 if before > 0 else 0
                    image_info = before_data[f]['analysis']
                    if 'error' not in image_info:
                        dims = f"{image_info['dimensions'][0]}x{image_info['dimensions'][1]}"
                        mode = image_info['mode']
                        est_quality = f"{image_info['estimated_quality'] or 'Unknown'}"
                        
                        print(f"{relative_path} | Dims: {dims} | Mode: {mode} | Est.Quality: {est_quality} | {human(before)} → {human(after)} ({reduction_pct:.1f}% saved)")
                
                savings.append((before, after))
    
    # Handle WebP files and other quality settings for JPEG/PNG
    img_paths = webp_paths + [p for p in png_paths if quality != 100] + [p for p in jpg_paths if quality != 100]
    
    for p in img_paths:
        # Store analysis data before compression
        before_size = p.stat().st_size
        image_info = None
        
        if GLOBAL_VERBOSE:
            image_info = analyze_image_quality(p)
        
        # Compress the image
        b, a = compress_image(p, quality)
        
        if GLOBAL_VERBOSE:
            relative_path = p.relative_to(root)
            reduction_pct = (b - a) / b * 100 if b > 0 else 0
            
            if image_info and 'error' not in image_info:
                fmt = image_info['format']
                dims = f"{image_info['dimensions'][0]}x{image_info['dimensions'][1]}"
                mode = image_info['mode']
                
                output = f"{fmt}: {relative_path} | Dims: {dims} | Mode: {mode}"
                
                if fmt == "JPEG":
                    est_quality = f"{image_info['estimated_quality'] or 'Unknown'}"
                    output += f" | Est.Quality: {est_quality}"
                elif fmt == "PNG" and image_info['png_info']:
                    color_type = image_info['png_info'].get('color_type', 'Unknown')
                    output += f" | Type: {color_type}"
                
                output += f" | Quality: {quality} | {human(b)} → {human(a)} ({reduction_pct:.1f}% saved)"
                print(output)
            else:
                print(f"File: {relative_path} | {human(b)} → {human(a)} ({reduction_pct:.1f}% saved)")
        
        savings.append((b, a))
    
    return savings


def analyze_image_quality(path: pathlib.Path):
    """Analyze the quality of an image file.
    
    Args:
        path: Path to the image file
        
    Returns:
        A tuple of (image format, estimated quality, color mode, dimensions)
    """
    global GLOBAL_VERBOSE
    try:
        img = Image.open(path)
        fmt = img.format
        mode = img.mode
        dimensions = img.size
        
        estimated_quality = None
        
        # For JPEG, try to estimate quality
        if fmt == "JPEG":
            # Method to estimate JPEG quality based on quantization tables
            try:
                # Check if we can access quantization tables
                if hasattr(img, 'quantization'):
                    qtables = img.quantization
                    if qtables:
                        # Simple algorithm for quality estimation
                        # Higher values in qtables = lower quality
                        if len(qtables) > 0:
                            # Sample the first quantization table
                            sample = list(qtables.values())[0]
                            if isinstance(sample, list) and len(sample) > 0:
                                # Estimate quality inversely proportional to quantization values
                                # This is a rough approximation
                                avg_qtable = sum(sample) / len(sample)
                                if avg_qtable < 1:
                                    estimated_quality = 100
                                else:
                                    # Rough formula, inversely proportional to average quantization value
                                    estimated_quality = min(100, max(1, int(100 - (avg_qtable / 2.5))))
            except Exception as e:
                if GLOBAL_VERBOSE:
                    print(f"Error estimating JPEG quality: {e}")
                    
        # For PNG, check color type and bit depth
        png_info = None
        if fmt == "PNG":
            color_type = "unknown"
            bit_depth = "unknown"
            try:
                # Try to get more detailed PNG info
                if hasattr(img, 'text') and 'Software' in img.text:
                    software = img.text['Software']
                else:
                    software = "unknown"
                    
                if mode == "P":
                    color_type = "palette"
                    if hasattr(img, 'palette'):
                        palette_size = len(img.palette.palette) // 3
                        color_type = f"palette ({palette_size} colors)"
                elif mode == "L":
                    color_type = "grayscale"
                elif mode == "LA":
                    color_type = "grayscale+alpha"
                elif mode == "RGB":
                    color_type = "RGB"
                elif mode == "RGBA":
                    color_type = "RGB+alpha"
                    
                # Get bit depth if available
                if hasattr(img, 'bits'):
                    bit_depth = img.bits
                
                png_info = {
                    "color_type": color_type,
                    "bit_depth": bit_depth,
                    "software": software
                }
            except Exception as e:
                if GLOBAL_VERBOSE:
                    print(f"Error getting PNG info: {e}")
                
        # Calculate file size
        file_size = path.stat().st_size
                
        return {
            "format": fmt,
            "mode": mode,
            "dimensions": dimensions,
            "file_size": file_size,
            "estimated_quality": estimated_quality,
            "png_info": png_info if fmt == "PNG" else None
        }
        
    except Exception as e:
        if GLOBAL_VERBOSE:
            print(f"Error analyzing image {path}: {e}")
        return {
            "format": "unknown",
            "error": str(e)
        }


def rebuild_epub(root: pathlib.Path, out_path: pathlib.Path):
    with zipfile.ZipFile(out_path, "w") as z:
        mimetype_path = root / "mimetype"
        if mimetype_path.exists():
            # this file must be the first and uncompressed
            z.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)

        for file in sorted(root.rglob("*")):
            if file.is_file() and file.name != "mimetype":
                z.write(file, file.relative_to(root), compress_type=zipfile.ZIP_DEFLATED)

def analyze_epub(purge_patterns):
    pass

def process_epub(quality, out_path, purge_patterns):
    """Process an EPUB file with the given quality setting."""

    global GLOBAL_KEEP_FILES, GLOBAL_INPUT_FILE, GLOBAL_VERBOSE

    extract_dir = unzip()
    opf_path, tree, manifest, ns = load_opf()
    content_dir = opf_path.parent

    purge_unwanted_files(purge_patterns, extract_dir, content_dir, tree, manifest)
    if GLOBAL_VERBOSE:
        print("Performing reference analysis...")
    remove_unreferenced(manifest, tree, ns, extract_dir, content_dir)
    remove_unreferenced_fonts(extract_dir, manifest, content_dir)

    GLOBAL_KEEP_FILES = set(manifest.keys())
    if GLOBAL_VERBOSE:
        print(f"Found {len(GLOBAL_KEEP_FILES)} files to preserve after reference analysis.")
        
    # If out_path is not provided, generate based on quality
    if out_path is None:
        if quality == 100:
            out_path = GLOBAL_INPUT_FILE.with_stem(GLOBAL_INPUT_FILE.stem + "-lossless")
        else:
            out_path = GLOBAL_INPUT_FILE.with_stem(f"{GLOBAL_INPUT_FILE.stem}-q{quality}")
    
    # Load and process OPF file
    opf_path, tree, manifest, ns = load_opf()
        
    # Use the pre-computed list to skip reference analysis
    if GLOBAL_VERBOSE:
        print(f"Using pre-computed list of {len(GLOBAL_KEEP_FILES)} files to preserve.")
    
    # Remove files not in the keep_files list
    to_remove = []
    for href, node in list(manifest.items()):
        if href not in GLOBAL_KEEP_FILES:
            to_remove.append(href)
            file_path = extract_dir / href
            if file_path.exists():
                file_path.unlink()
            # Use standard library approach for removing nodes
            parent = tree.getroot()
            for child in list(parent):
                if child == node:
                    parent.remove(child)
                    break
    
    if GLOBAL_VERBOSE and to_remove:
        print(f"Removed {len(to_remove)} files not in the keep list.")
    
    # Save the cleaned tree to opf file
    tree.write(opf_path, encoding="utf-8", xml_declaration=True)
    
    # Compress images with the specified quality
    compress_images(extract_dir, quality)
    
    # Rebuild the EPUB
    rebuild_epub(extract_dir, out_path)
    final_size = out_path.stat().st_size
    
    return extract_dir, final_size, out_path


def main():
    global GLOBAL_INPUT_FILE, GLOBAL_VERBOSE
    verify_compressors_availability()
    args = parse_args()
    GLOBAL_INPUT_FILE = args.epub
    GLOBAL_VERBOSE = args.verbose
    original_size = GLOBAL_INPUT_FILE.stat().st_size
    print("Original size:", human(original_size))
    
    # First attempt with initial quality
    extract_dir, final, out_path = process_epub(
        quality=args.quality, 
        out_path=args.output,
        purge_patterns=args.purge
    )
    
    # Store initial quality
    q = args.quality

    # Check if target size is specified and not met
    if args.targetsize and final / (1024 * 1024) > args.targetsize and q > 15:
        print(f"Target {args.targetsize}MB not met, current size {human(final)}")
        
        # Try progressively lower qualities until target is met or quality floor is reached
        while args.targetsize and final / (1024 * 1024) > args.targetsize and q > 15:
            # Calculate new quality level
            q = max(q - 5, 15)
            print(f"Retrying with lossy quality={q}")
            
            # Clean up previous temporary directory
            shutil.rmtree(extract_dir)
            
            # Process the EPUB with new quality setting, reusing the keep_files list
            extract_dir, final, out_path = process_epub(
                quality=q, 
                out_path=args.output
            )
            print(f"Quality {q}: {human(final)}")
    
    print(f"Final:   {human(final)}  (saved {(original_size - final) / original_size:.1%})")
    print(f"Output file: {out_path}")
    shutil.rmtree(extract_dir)


if __name__ == "__main__":
    main()