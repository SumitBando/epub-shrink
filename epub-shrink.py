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
import datetime
import uuid
import html
from collections import defaultdict
from lxml import etree as ET
from fnmatch import fnmatch
from PIL import Image
from bs4 import BeautifulSoup
import tinycss2

# Define deprecated items and their substitutions for EPUB modernization
DEPRECATED_ITEMS = {
    'tags': {
        'center': ('div', {'style': 'text-align: center;'}),
        'font': ('span', {}),
        'strike': ('span', {'style': 'text-decoration: line-through;'}),
        's': ('span', {'style': 'text-decoration: line-through;'}),
        'u': ('span', {'style': 'text-decoration: underline;'}),
        'big': ('span', {'style': 'font-size: larger;'}),
        'tt': ('code', {}),
        'acronym': ('abbr', {}),
    },
    'attributes': {
        'align': 'style', 
        'bgcolor': 'style',
        'color': 'style',
        'face': 'style',
        'size': 'style',
        'width': 'style',
        'height': 'style',
        'cellpadding': 'style',
        'cellspacing': 'style',
        'border': 'style',
        'valign': 'style',
        'rules': 'style',
    }
}

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
    tree = ET.parse(str(opf_path))
    ns = {
        "opf": "http://www.idpf.org/2007/opf",
        "dc": "http://purl.org/dc/elements/1.1/",
        "dcterms": "http://purl.org/dc/terms/",
        "xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "ncx": "http://www.daisy.org/z3986/2005/ncx/"
    }
    manifest = {item.attrib["href"]: item
                for item in tree.findall(".//opf:item", ns)}
    
    return opf_path, tree, manifest, ns


def fix_ncx(extract_dir):
    """Add missing IDs to navPoint elements in NCX files."""
    ncx_file = None
    for root, dirs, files in os.walk(extract_dir):
        for file in files:
            if file.endswith('.ncx'):
                ncx_path = pathlib.Path(root) / file
                ncx_file = ncx_path
                try:
                    with open(ncx_path, 'r', encoding='utf-8') as f:
                        soup = BeautifulSoup(f, 'lxml-xml')
                    modified = False
                    for i, navpoint in enumerate(soup.find_all('navPoint')):
                        if not navpoint.has_attr('id'):
                            navpoint['id'] = f"navpoint-{i+1}"
                            modified = True
                    if modified:
                        with open(ncx_path, 'w', encoding='utf-8') as f:
                            f.write(str(soup))
                except Exception as e:
                    print(f"Warning: Could not fix NCX {file}: {e}")
    return ncx_file


def generate_nav_from_ncx(ncx_path, nav_path):
    """Generate an EPUB 3 nav document from an EPUB 2 NCX file."""
    try:
        with open(ncx_path, 'r', encoding='utf-8') as f:
            ncx_soup = BeautifulSoup(f, 'lxml-xml')
        
        doc_title = ncx_soup.find('docTitle')
        title_text = doc_title.find('text').text.strip() if doc_title and doc_title.find('text') else "Table of Contents"
        title_text_escaped = html.escape(title_text)
        
        def process_nav_points(container):
            points = container.find_all('navPoint', recursive=False)
            if not points:
                return ""
            
            res = "<ol>\n"
            for pt in points:
                nav_label = pt.find('navLabel')
                label = nav_label.find('text').text if nav_label and nav_label.find('text') else "Unnamed"
                content_tag = pt.find('content')
                src = content_tag['src'] if content_tag and content_tag.has_attr('src') else "#"
                res += f'    <li><a href="{html.escape(src)}">{html.escape(label)}</a>'
                
                # Handle nested points
                nested = process_nav_points(pt)
                if nested:
                    res += "\n" + nested
                
                res += "</li>\n"
            res += "</ol>\n"
            return res

        nav_map = ncx_soup.find('navMap')
        ol_content = process_nav_points(nav_map) if nav_map else "<ol><li><a href=\"index.xhtml\">Contents</a></li></ol>"
        
        nav_content = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <title>{title_text_escaped}</title>
</head>
<body>
    <nav epub:type="toc" id="toc">
        <h1>{title_text_escaped}</h1>
        {ol_content}
    </nav>
</body>
</html>"""
        
        with open(nav_path, 'w', encoding='utf-8') as f:
            f.write(nav_content)
        print(f"Generated nav.xhtml from {ncx_path.name}")
    except Exception as e:
        print(f"Warning: Could not generate nav from NCX: {e}")


def handle_deprecated(soup):
    """Convert deprecated HTML tags and attributes to modern CSS equivalents."""
    modified = False
    
    # Handle obsolete meta tags
    for meta in soup.find_all('meta'):
        http_equiv = meta.get('http-equiv', '').lower()
        if http_equiv in ['content-style-type', 'content-type']:
            meta.decompose()
            modified = True
        elif meta.has_attr('charset'):
            meta.decompose()
            modified = True
    
    # Handle Tags
    for tag_name, (new_name, extra_attrs) in DEPRECATED_ITEMS['tags'].items():
        for tag in soup.find_all(tag_name):
            tag.name = new_name
            for attr, val in extra_attrs.items():
                if tag.has_attr(attr):
                    existing = tag[attr]
                    if val not in existing:
                        tag[attr] = f"{existing}; {val}" if ';' in val else val
                else:
                    tag[attr] = val
            modified = True

    # Handle Attributes
    for tag in soup.find_all(True):
        attrs_to_remove = []
        for attr in list(tag.attrs):
            if attr in DEPRECATED_ITEMS['attributes']:
                val = tag[attr]
                style = ""
                if attr == 'align':
                    style = f"float: {val};" if tag.name in ['img', 'table'] else f"text-align: {val};"
                elif attr == 'bgcolor':
                    style = f"background-color: {val};"
                elif attr == 'color':
                    style = f"color: {val};"
                elif attr == 'width':
                    style = f"width: {val}px;" if val.isdigit() else f"width: {val};"
                elif attr == 'height':
                    style = f"height: {val}px;" if val.isdigit() else f"height: {val};"
                elif attr == 'border' and tag.name == 'table':
                    style = f"border: {val}px solid;"
                elif attr == 'cellspacing' and tag.name == 'table':
                    style = f"border-spacing: {val}px;"
                    if val == '0': style += " border-collapse: collapse;"
                elif attr == 'rules' and tag.name == 'table':
                    style = "border-collapse: collapse;"
                elif attr == 'valign':
                    style = f"vertical-align: {val};"

                if style:
                    if tag.has_attr('style'):
                        existing = tag['style'].strip('; ')
                        tag['style'] = f"{existing}; {style}" if existing else style
                    else:
                        tag['style'] = style
                
                attrs_to_remove.append(attr)
                modified = True
        
        for attr in attrs_to_remove:
            del tag[attr]

    return modified


def modernize_assets(extract_dir, tree, manifest, ns, opf_path):
    """Apply various modernizations to EPUB assets and OPF metadata."""
    opf_root = tree.getroot()
    opf_dir = opf_path.parent
    
    # 1. Fix NCX if it exists
    ncx_path = fix_ncx(extract_dir)
    
    # 2. Ensure EPUB 3 navigation document
    nav_item = next((item for item in manifest.values() if 'nav' in (item.get('properties') or '').split()), None)
    if nav_item is None and ncx_path:
        nav_href = 'nav.xhtml'
        nav_abs_path = opf_dir / nav_href
        generate_nav_from_ncx(ncx_path, nav_abs_path)
        
        # Add to OPF manifest
        manifest_node = opf_root.find('opf:manifest', ns)
        new_item = ET.SubElement(manifest_node, '{http://www.idpf.org/2007/opf}item')
        new_id = "nav"
        # Ensure ID is unique
        existing_ids = {item.get('id') for item in manifest.values()}
        while new_id in existing_ids:
            new_id = f"nav-{uuid.uuid4().hex[:4]}"
        new_item.set('id', new_id)
        new_item.set('href', nav_href)
        new_item.set('media-type', 'application/xhtml+xml')
        new_item.set('properties', 'nav')
        manifest[nav_href] = new_item
        print(f"Added generated nav.xhtml to manifest as {new_id}")

    # 3. Analyze and fix HTML/CSS files
    ul_disc_needed = False
    for href, item in list(manifest.items()):
        if item.get('media-type') == 'application/xhtml+xml':
            html_path = opf_dir / href
            if not html_path.exists(): continue
            
            modified = False
            try:
                content = html_path.read_bytes()
                soup = BeautifulSoup(content, 'lxml-xml')
                
                # Check for SVG
                if soup.find('svg'):
                    props = item.get('properties', '')
                    if 'svg' not in (props or '').split():
                        item.set('properties', ((props or '') + ' svg').strip())
                        modified = True

                # Handle deprecated tags/attributes
                if handle_deprecated(soup): modified = True

                # Fix aria attributes referring to missing IDs
                for attr in ['aria-labelledby', 'aria-describedby']:
                    for tag in soup.find_all(attrs={attr: True}):
                        target_ids = tag[attr].split()
                        valid_ids = [tid for tid in target_ids if soup.find(id=tid)]
                        if len(valid_ids) != len(target_ids):
                            if not valid_ids:
                                del tag[attr]
                            else:
                                tag[attr] = " ".join(valid_ids)
                            modified = True
                
                # Special handling for ul type="disc"
                for ul in soup.find_all('ul'):
                    if ul.has_attr('type'):
                        if ul.has_attr('class'):
                            del ul['type']
                        elif ul['type'] == 'disc':
                            ul['class'] = '_ul_disc'
                            del ul['type']
                            ul_disc_needed = True
                        modified = True
                
                if modified:
                    html_path.write_text(str(soup), encoding='utf-8')
            except Exception as e:
                print(f"Warning: Error modernizing {href}: {e}")

    # 4. Inject CSS for ul_disc if needed
    if ul_disc_needed:
        css_href = next((h for h, m in manifest.items() if m.get('media-type') == 'text/css'), None)
        if css_href:
            css_path = opf_dir / css_href
            try:
                with open(css_path, 'a', encoding='utf-8') as f:
                    f.write('\n._ul_disc { list-style-type: disc; }\n')
            except Exception as e:
                print(f"Warning: Error updating CSS {css_href}: {e}")

    # 5. Modernize Metadata
    metadata = opf_root.find('opf:metadata', ns)
    if metadata is not None:
        for child in metadata:
            # Remove any attribute that starts with {http://www.idpf.org/2007/opf}
            attrs_to_del = [a for a in child.attrib if a.startswith('{http://www.idpf.org/2007/opf}')]
            for a in attrs_to_del:
                del child.attrib[a]
        
        # Single dcterms:modified
        for old_mod in metadata.findall('.//opf:meta[@property="dcterms:modified"]', ns):
            metadata.remove(old_mod)
        
        now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        mod = ET.SubElement(metadata, '{http://www.idpf.org/2007/opf}meta')
        mod.set('property', 'dcterms:modified')
        mod.text = now

    # 6. Standardize Font Media Types
    font_map = {
        '.ttf': 'application/vnd.ms-opentype',
        '.otf': 'application/vnd.ms-opentype',
        '.woff': 'font/woff',
        '.woff2': 'font/woff2'
    }
    for item in manifest.values():
        href = None
        # Robustly get href
        for attr, val in item.attrib.items():
            if attr == 'href' or attr.endswith('}href'):
                href = val
                break
        
        if not href: continue
        
        ext = os.path.splitext(href.lower())[1]
        if ext in font_map:
            # Robustly get current media-type
            current_type = None
            for attr, val in item.attrib.items():
                if attr == 'media-type' or attr.endswith('}media-type'):
                    current_type = val
                    break
            
            new_type = font_map[ext]
            if current_type != new_type:
                print(f"Modernizing font media-type for {href}: {current_type} -> {new_type}")
                # Clean up all versions of media-type attribute to avoid duplicates
                to_del = [a for a in item.attrib if a == 'media-type' or a.endswith('}media-type')]
                for a in to_del: del item.attrib[a]
                # Set plain media-type attribute
                item.set('media-type', new_type)

    # 7. Set EPUB version to 3.0
    opf_root.set('version', '3.0')



def extract_refs(tokens, is_import=False):
    refs = []
    for token in tokens:
        if token.type == 'url':
            refs.append(token.value)
        elif token.type == 'function' and token.lower_name == 'url':
            for arg in token.arguments:
                if arg.type == 'string':
                    refs.append(arg.value)
                    break
        elif is_import and token.type == 'string':
            refs.append(token.value)
        
        if hasattr(token, 'content') and token.content:
            refs.extend(extract_refs(token.content))
        if hasattr(token, 'arguments') and token.arguments:
            refs.extend(extract_refs(token.arguments))
        if hasattr(token, 'value') and isinstance(token.value, list):
            refs.extend(extract_refs(token.value))
    return refs


def remove_unreferenced(manifest, tree, ns, root, content_dir=None, show_summary=True):
    global GLOBAL_VERBOSE
    
    # 1. Initialize files_to_keep with essential references
    spine_refs = {item.attrib["idref"] for item in tree.findall(".//opf:itemref", ns)}
    files_to_keep = {i.attrib["href"] for i in manifest.values() if i.attrib.get("id") in spine_refs}

    for reference in tree.findall(".//opf:guide/opf:reference", ns):
        href = reference.get("href")
        if href:
            files_to_keep.add(href)

    cover_id = None
    for meta in tree.findall(".//opf:meta[@name='cover']", ns):
        cover_id = meta.get("content")
        if cover_id:
            break
    if cover_id:
        for item in tree.findall(".//opf:item", ns):
            if item.get("id") == cover_id:
                cover_href = item.get("href")
                if cover_href:
                    files_to_keep.add(cover_href)

    for item in tree.findall(".//opf:item[@properties]", ns):
        if "cover-image" in item.get("properties", "").split():
            cover_href = item.get("href")
            if cover_href:
                files_to_keep.add(cover_href)

    essential_patterns = ["*.ncx", "nav.xhtml", "*[Cc]ontents*", "*logo*", "META-INF/*"]
    for href in manifest:
        if any(fnmatch(href, pat) for pat in essential_patterns):
            files_to_keep.add(href)

    # 2. Iteratively find all references by scanning files
    # Start scanning with all XHTML files, not just the spine
    files_to_scan = [href for href, item in manifest.items() if item.attrib.get("media-type") == "application/xhtml+xml"]
    
    processed_scans = set()
    scan_count = 0

    while files_to_scan:
        href = files_to_scan.pop(0)
        if href in processed_scans:
            continue
        processed_scans.add(href)
        files_to_keep.add(href)
        
        scan_count += 1
        if scan_count % 50 == 0:
            print(f"Scanned {scan_count} files...")

        file_path = content_dir / href
        if not file_path.exists():
            continue

        file_dir = file_path.parent
        
        try:
            # Use binary read and detect encoding if possible, but for performance,
            # we'll stick to a fast read and specific parsing.
            if href.lower().endswith(('.xhtml', '.html')):
                content = file_path.read_bytes()
                soup = BeautifulSoup(content, 'lxml-xml')
                
                # Combined search for speed
                for tag in soup.find_all(True, attrs={'href': True}):
                    ref = tag.get('href')
                    if ref and not ref.startswith(('http', 'data', '#', 'mailto:')):
                        ref = ref.split('#')[0]
                        abs_path = os.path.normpath(os.path.join(file_dir, ref))
                        content_relative_path = os.path.relpath(abs_path, content_dir)
                        if content_relative_path in manifest and content_relative_path not in processed_scans:
                            files_to_scan.append(content_relative_path)

                for tag in soup.find_all(True, attrs={'src': True}):
                    ref = tag.get('src')
                    if ref and not ref.startswith(('http', 'data', '#')):
                        ref = ref.split('#')[0]
                        abs_path = os.path.normpath(os.path.join(file_dir, ref))
                        content_relative_path = os.path.relpath(abs_path, content_dir)
                        if content_relative_path in manifest and content_relative_path not in processed_scans:
                            files_to_scan.append(content_relative_path)
                
                # Scan style attributes
                for tag in soup.find_all(True, attrs={'style': True}):
                    try:
                        declarations = tinycss2.parse_declaration_list(tag['style'], skip_comments=True, skip_whitespace=True)
                        for ref in extract_refs(declarations):
                            if ref and not ref.startswith(('http', 'data', '#')):
                                ref = ref.split('#')[0]
                                abs_path = os.path.normpath(os.path.join(file_dir, ref))
                                content_relative_path = os.path.relpath(abs_path, content_dir)
                                if content_relative_path in manifest and content_relative_path not in processed_scans:
                                    files_to_scan.append(content_relative_path)
                    except Exception:
                        pass
            
            # Scan CSS for @import, url(), and @font-face
            elif href.lower().endswith('.css'):
                content = file_path.read_text(encoding='utf-8', errors='ignore')
                try:
                    rules = tinycss2.parse_stylesheet(content, skip_comments=True, skip_whitespace=True)
                    for rule in rules:
                        is_import = (rule.type == 'at-rule' and rule.at_keyword == 'import')
                        all_refs = []
                        if hasattr(rule, 'prelude') and rule.prelude:
                            all_refs.extend(extract_refs(rule.prelude, is_import=is_import))
                        if hasattr(rule, 'content') and rule.content:
                            all_refs.extend(extract_refs(rule.content))
                        
                        for ref in all_refs:
                            if ref and not ref.startswith(('http', 'data', '#')):
                                ref = ref.split('#')[0]
                                abs_path = os.path.normpath(os.path.join(file_dir, ref))
                                content_relative_path = os.path.relpath(abs_path, content_dir)
                                if content_relative_path in manifest and content_relative_path not in processed_scans:
                                    files_to_scan.append(content_relative_path)
                except Exception as e:
                    if GLOBAL_VERBOSE:
                        print(f"Error parsing CSS {href}: {e}")

        except Exception as e:
            if GLOBAL_VERBOSE:
                print(f"Error scanning file {href}: {e}")

    # 3. Remove files that are not in our final keep list
    # Pre-calculate parent map for efficient node removal
    parent_map = {c: p for p in tree.iter() for c in p}
    
    for href, node in list(manifest.items()):
        if href not in files_to_keep:
            file_path = content_dir / href
            if not file_path.exists():
                if GLOBAL_VERBOSE:
                    print(f"File to remove not found on disk: {href}")
                continue
            size = file_path.stat().st_size
            file_path.unlink()
            
            # Remove from XML manifest
            parent = parent_map.get(node)
            if parent is not None:
                parent.remove(node)

            if show_summary:
                print(f"Dropping unreferenced file: {href} ({human(size)})")


def purge_unwanted_files(purge_patterns, extract_dir, content_dir, tree, manifest, show_summary=True):
    global GLOBAL_VERBOSE
    if GLOBAL_VERBOSE and show_summary:
        print("Purging unwanted files...")
    DEFAULT_PURGES = [
        "*.DS_Store",
        "*.epubcheck*",
        "SS_recommendpage*",
        "_signup_*",
        "*cross-sale*",
        "*cross-sell*",
        "*xpromo*",
        "promo.css",
        "next-reads",
        "newsletter",
        "com.apple.ibooks.display-options.xml",
    ]
    all_patterns = DEFAULT_PURGES + (purge_patterns or [])
    for relative_filename in list(manifest.keys()):
        filename = os.path.basename(relative_filename)
        if any(fnmatch(filename, pat) for pat in all_patterns):
            remove_from_spine(tree, relative_filename)
            remove_from_manifest(tree, relative_filename)
            remove_file(content_dir, relative_filename)
            if show_summary:
                print(f"Purged unwanted file: {relative_filename} from spine, manifest, and disk")

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

def remove_file(content_dir, href):
    """Remove a file from the content directory."""
    # Use content_dir if available, otherwise fall back to extract_dir
    # if content_dir:
    #     (content_dir / href).unlink(missing_ok=True)
    # else:
    #     (extract_dir / href).unlink(missing_ok=True)

    file_path = content_dir / href
    if not file_path.exists():
        error_message = f"File not found, could not remove: {href}"
        print(error_message)
        raise FileNotFoundError(error_message)
    
    try:
        file_path.unlink()
    except Exception as e:
        print(f"Warning: Could not remove {href}: {e}")
        raise


def analyze_images(root, show_summary=True):
    """Find all image paths relative to root and optionally print a summary."""
    jpg_paths = [p.relative_to(root) for p in [*root.rglob("*.jpg"), *root.rglob("*.jpeg")]]
    png_paths = [p.relative_to(root) for p in root.rglob("*.png")]
    webp_paths = [p.relative_to(root) for p in root.rglob("*.webp")]
    
    # Store quality and size per type
    types = [
        ("JPEG", jpg_paths),
        ("PNG", png_paths),
        ("WebP", webp_paths)
    ]
    
    max_estimated_quality = 0
    type_summaries = []
    
    for name, paths in types:
        count = len(paths)
        if count == 0:
            type_summaries.append(f"0 {name} files")
            continue
            
        size = 0
        type_max_q = 0
        for p in paths:
            full_path = root / p
            size += full_path.stat().st_size
            info = analyze_image_quality(full_path)
            q = info.get("estimated_quality")
            if q:
                type_max_q = max(type_max_q, q)
                max_estimated_quality = max(max_estimated_quality, q)
        
        summary = f"{count} {name} / {human(size)}"
        if type_max_q > 0:
            summary += f" (max q: {type_max_q})"
        type_summaries.append(summary)

    if show_summary:
        summary_line = f"Found {type_summaries[0]}, {type_summaries[1]} and {type_summaries[2]}"
        print(summary_line)
    
    return jpg_paths, png_paths, webp_paths, max_estimated_quality


def compress_images(root, quality, jpg_paths, png_paths, webp_paths):
    global GLOBAL_VERBOSE
    
    savings = []
    
    # Process PNG files by directory to optimize oxipng performance
    if png_paths and quality == 100:
        png_dirs = defaultdict(list)
        for rel_path in png_paths:
            p = root / rel_path
            png_dirs[p.parent].append(p)
        
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
            oxipng_args = ["oxipng", "-o", "max", "--strip", "all", "--alpha", "--threads", "4"]
            if not GLOBAL_VERBOSE:
                oxipng_args.append("-q")
            
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
        for rel_path in jpg_paths:
            p = root / rel_path
            jpg_dirs[p.parent].append(p)
        
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
    img_rel_paths = webp_paths + [p for p in png_paths if quality != 100] + [p for p in jpg_paths if quality != 100]
    
    for rel_path in img_rel_paths:
        p = root / rel_path
        # Store analysis data before compression
        before = p.stat().st_size
        image_info = None
        
        if GLOBAL_VERBOSE:
            image_info = analyze_image_quality(p)
        
        # Compress the image
        try:
            img = Image.open(p)
            fmt = img.format
            if quality == 100:
                # Lossless fallback for formats other than batch-processed JPEG/PNG (e.g. WebP)
                img.save(p, format=fmt, optimize=True)
            else:
                if fmt == "JPEG":
                    img.save(p, format="JPEG", quality=quality,
                             optimize=True, progressive=True)
                elif fmt == "PNG":
                    # For lossy PNG, convert to palette-based image
                    img = img.convert("P", palette=Image.ADAPTIVE)
                    img.save(p, format="PNG", optimize=True)
                else:
                    img.save(p, format=fmt, quality=quality, optimize=True)
        except Exception as e:
            if GLOBAL_VERBOSE:
                print("Image compress error:", p, e)
        
        after = p.stat().st_size
        
        if GLOBAL_VERBOSE:
            relative_path = p.relative_to(root)
            reduction_pct = (before - after) / before * 100 if before > 0 else 0
            
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
                
                output += f" | Quality: {quality} | {human(before)} → {human(after)} ({reduction_pct:.1f}% saved)"
                print(output)
            else:
                print(f"File: {relative_path} | {human(before)} → {human(after)} ({reduction_pct:.1f}% saved)")
        
        savings.append((before, after))
    
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

def analyze_file():
    """Extract EPUB and load metadata."""
    extract_dir = unzip()
    opf_path, tree, manifest, ns = load_opf()
    return extract_dir, opf_path, tree, manifest, ns


def prune_unreferenced_assets(tree, manifest, ns, extract_dir, opf_path, show_summary=True):
    """Remove unreferenced assets and write the updated OPF."""
    content_dir = opf_path.parent
    remove_unreferenced(manifest, tree, ns, extract_dir, content_dir, show_summary=show_summary)
    tree.write(opf_path, encoding="utf-8", xml_declaration=True)


def main():
    global GLOBAL_INPUT_FILE, GLOBAL_VERBOSE
    verify_compressors_availability()
    args = parse_args()
    GLOBAL_INPUT_FILE = args.epub
    GLOBAL_VERBOSE = args.verbose
    original_size = GLOBAL_INPUT_FILE.stat().st_size
    print("Original size:", human(original_size))

    # 1. Analyze and Prepare
    extract_dir, opf_path, tree, manifest, ns = analyze_file()
    content_dir = opf_path.parent

    # 2. Purge unwanted patterns
    purge_unwanted_files(args.purge, extract_dir, content_dir, tree, manifest, show_summary=True)
    
    # Refresh manifest after purge
    manifest = {item.attrib["href"]: item for item in tree.findall(".//opf:item", ns)}

    # 3. Modernize assets (convert deprecated tags, generate nav.xhtml, etc.)
    modernize_assets(extract_dir, tree, manifest, ns, opf_path)

    # 4. Prune unreferenced assets and update OPF
    if GLOBAL_VERBOSE:
        print("Performing reference analysis...")
    prune_unreferenced_assets(tree, manifest, ns, extract_dir, opf_path, show_summary=True)

    # 5. Image Analysis (Discovery and Summary)
    jpg_paths, png_paths, webp_paths, max_estimated_quality = analyze_images(extract_dir, show_summary=True)

    # 6. Iterative Compression and Rebuild
    q = args.quality
    final_size = 0
    current_out = None

    while True:
        # Create a fresh build directory from the cleaned extract_dir
        build_dir = TMP_ROOT / f"epub-build-{os.getpid()}-{q}"
        if build_dir.exists():
            shutil.rmtree(build_dir)
        shutil.copytree(extract_dir, build_dir)

        # Determine output path for this iteration if not explicitly provided
        if args.output:
            current_out = args.output
        else:
            suffix = "-lossless" if q == 100 else f"-q{q}"
            current_out = GLOBAL_INPUT_FILE.with_stem(f"{GLOBAL_INPUT_FILE.stem}{suffix}")

        compress_images(build_dir, q, jpg_paths, png_paths, webp_paths)
        rebuild_epub(build_dir, current_out)
        
        final_size = current_out.stat().st_size
        print(f"Quality {q}: {human(final_size)}")

        # Clean up build directory
        shutil.rmtree(build_dir)

        # Stop if: target reached, no target set, or quality floor reached
        target_met = not args.targetsize or (final_size / (1024 * 1024) <= args.targetsize)
        MIN_QUALITY = 15
        if target_met or q <= MIN_QUALITY:
            break
        
        QUALITY_STEP = 5
        if q == 100: # just completed the lossless step, now switch to lossy with a smart initial quality setting
            if max_estimated_quality > 0:
                q = max_estimated_quality - 1
            else:
                q -= QUALITY_STEP
        else:
            q -= QUALITY_STEP

    print(f"\nFinal size: {human(final_size)} (saved {(original_size - final_size) / original_size:.1%}) of original {human(original_size)}")
    print(f"Output file: {current_out}")
    
    if extract_dir.exists():
        shutil.rmtree(extract_dir)


if __name__ == "__main__":
    main()
