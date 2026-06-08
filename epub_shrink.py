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
from urllib.parse import unquote, urlparse
from collections import defaultdict
from dataclasses import dataclass
from lxml import etree as ET
from fnmatch import fnmatch
from PIL import Image
from bs4 import BeautifulSoup, Doctype
import tinycss2
from tqdm import tqdm

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


@dataclass
class EpubContext:
    """Context object threaded through the EPUB shrinking pipeline."""
    input_file: pathlib.Path
    extract_dir: pathlib.Path
    verbose: bool = False
    max_estimated_quality: int = 0
    weighted_avg_quality: float = None



def verify_compressors_availability():
    """Check if required image compressors are available."""
    if not shutil.which("jpegoptim"):
        print("Please install missing jpeg compressor jpegoptim for JPEG optimization")
        sys.exit(1)
    
    if not shutil.which("oxipng"):
        print("Please install missing PNG compressor oxipng for PNG optimization")
        sys.exit(1)

    if not shutil.which("pngquant"):
        print("Please install missing PNG compressor pngquant for lossy PNG optimization")
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


def unzip(ctx: EpubContext):
    """Extract the EPUB to a new temporary directory."""
    print(f"Extracting {ctx.input_file} to temporary directory {ctx.extract_dir}")
    if ctx.extract_dir.exists():
        shutil.rmtree(ctx.extract_dir)
    ctx.extract_dir.mkdir()
    zipfile.ZipFile(ctx.input_file).extractall(ctx.extract_dir)


def load_opf(ctx: EpubContext):
    """Find and load the 'Open Package Format' file using container.xml or fallback to direct search. 
    
    According to the EPUB spec, META-INF/container.xml points to the OPF file.
    If container.xml isn't found or doesn't contain a valid reference,
    fall back to searching for .opf files directly.
    """
    container_path = ctx.extract_dir / "META-INF" / "container.xml"
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
                        opf_path = ctx.extract_dir / opf_path_str
                        if opf_path.exists():
                            break
        except Exception as e:
            print(f"Warning: Error parsing container.xml: {e}")
            opf_path = None
    
    # Fallback: If container.xml parsing fails, search for .opf files directly
    if not opf_path or not opf_path.exists():
        try:
            opf_path = next(ctx.extract_dir.rglob("*.opf"))
            print(f"Using fallback OPF file: {opf_path.relative_to(ctx.extract_dir)}")
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
        title_text = ""
        if doc_title:
            text_el = doc_title.find('text')
            if text_el:
                title_text = text_el.get_text().strip()
        
        if not title_text:
            title_text = "Table of Contents"
            
        title_text_escaped = html.escape(title_text)
        
        def process_nav_points(container):
            points = container.find_all('navPoint', recursive=False)
            if not points:
                return ""
            
            res = "<ol>\n"
            for pt in points:
                nav_label = pt.find('navLabel')
                label = ""
                if nav_label:
                    text_el = nav_label.find('text')
                    if text_el:
                        label = text_el.get_text().strip()
                
                if not label:
                    label = "Unnamed"
                
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
<!DOCTYPE html>
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


def is_valid_xml_name_char(c):
    o = ord(c)
    return (
        o == 0x2D or  # -
        o == 0x2E or  # .
        (0x30 <= o <= 0x39) or  # 0-9
        o == 0x5F or  # _
        (0x61 <= o <= 0x7A) or  # a-z
        (0x41 <= o <= 0x5A) or  # A-Z
        o == 0xB7 or
        (0xC0 <= o <= 0xD6) or
        (0xD8 <= o <= 0xF6) or
        (0xF8 <= o <= 0x2FF) or
        (0x300 <= o <= 0x37D) or
        (0x37F <= o <= 0x1FFF) or
        (0x200C <= o <= 0x200D) or
        (0x203F <= o <= 0x2040) or
        (0x2070 <= o <= 0x218F) or
        (0x2C00 <= o <= 0x2FEF) or
        (0x3001 <= o <= 0xD7FF) or
        (0xF900 <= o <= 0xFDCF) or
        (0xFDF0 <= o <= 0xFFFD) or
        (0x10000 <= o <= 0xEFFFF)
    )


def is_valid_xml_id(id_val):
    """Check if an ID attribute value is a valid XML NCName."""
    if not id_val:
        return False
    # Strip any leading/trailing whitespace or non-breaking spaces
    stripped = id_val.strip(" \t\n\r\xa0\u200b\u200c\u200d")
    if not stripped or len(stripped) != len(id_val):
        return False
    
    # Check first character
    first = stripped[0]
    first_ord = ord(first)
    is_letter_or_underscore = (
        (0x61 <= first_ord <= 0x7A) or  # a-z
        (0x41 <= first_ord <= 0x5A) or  # A-Z
        first_ord == 0x5F               # _
    )
    if not is_letter_or_underscore:
        return False
        
    # Check remaining characters
    for c in stripped[1:]:
        if not is_valid_xml_name_char(c):
            return False
    return True


def sanitize_xml_id(id_val):
    """Sanitize an invalid ID attribute to be a valid XML NCName."""
    if not id_val:
        return ""
    # Strip leading/trailing whitespaces and non-breaking spaces
    stripped = id_val.strip(" \t\n\r\xa0\u200b\u200c\u200d")
    if not stripped:
        return ""
        
    # Replace any invalid XML name characters with underscores
    chars = []
    for c in stripped:
        if is_valid_xml_name_char(c):
            chars.append(c)
        else:
            chars.append("_")
    sanitized = "".join(chars)
    
    if sanitized:
        first = sanitized[0]
        first_ord = ord(first)
        is_letter_or_underscore = (
            (0x61 <= first_ord <= 0x7A) or  # a-z
            (0x41 <= first_ord <= 0x5A) or  # A-Z
            first_ord == 0x5F               # _
        )
        if not is_letter_or_underscore:
            sanitized = "id_" + sanitized
            
    return sanitized


def is_invalid_custom_data_attribute(attr_name):
    # Check if it starts with "data-" case-insensitively
    if not attr_name.lower().startswith('data-'):
        return False
    
    # Must have at least one character after the hyphen
    if len(attr_name) <= 5:
        return True
    
    # Must not contain ASCII uppercase letters
    if any('A' <= c <= 'Z' for c in attr_name):
        return True
    
    # Must not contain colon (as colons are for namespaces, and custom data attributes must be in no namespace)
    if ':' in attr_name:
        return True
        
    # Every character must be a valid XML Name character (excluding ASCII uppercase and colon, which are checked above)
    for c in attr_name:
        if not is_valid_xml_name_char(c):
            return True
            
    return False


def cleanup_meta_and_triggers(soup):
    """Remove obsolete meta tags and epub:trigger elements."""
    modified = False
    for meta in soup.find_all('meta'):
        http_equiv = meta.get('http-equiv', '').lower()
        if http_equiv in ['content-style-type', 'content-type']:
            meta.decompose()
            modified = True
        elif meta.has_attr('charset'):
            meta.decompose()
            modified = True
    
    for trigger in soup.find_all(['epub:trigger', 'trigger']):
        trigger.decompose()
        modified = True
    return modified


def convert_deprecated_tags(soup):
    """Convert deprecated HTML tags (e.g. center, font) to modern CSS equivalents."""
    modified = False
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
    return modified


def convert_deprecated_attrs(soup):
    """Convert deprecated HTML attributes to inline CSS styles."""
    modified = False
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


def remove_invalid_data_attrs(soup):
    """Remove invalid custom data attributes (e.g. data-* attributes with invalid characters)."""
    modified = False
    for tag in soup.find_all(True):
        attrs_to_remove = []
        for attr in list(tag.attrs):
            if is_invalid_custom_data_attribute(attr):
                attrs_to_remove.append(attr)
                modified = True
        for attr in attrs_to_remove:
            del tag[attr]
    return modified


def convert_a_name_to_id(soup):
    """Convert <a name="..."> to <a id="..."> for EPUB 3 (RSC-012)."""
    modified = False
    for a in soup.find_all('a', attrs={'name': True}):
        if not a.has_attr('id'):
            a['id'] = a['name']
        del a['name']
        modified = True
    return modified


def validate_uri_schemes(soup):
    """Convert links with non-registered or unapproved URI schemes to span tags (HTM-025)."""
    modified = False
    APPROVED_SCHEMES = {'http', 'https', 'mailto', 'tel', 'data', 'urn', 'ftp', 'geo', 'sms'}
    for a in soup.find_all('a', attrs={'href': True}):
        href = a['href']
        try:
            parsed = urlparse(href)
            scheme = parsed.scheme
            if scheme:
                if scheme.lower() not in APPROVED_SCHEMES:
                    a.name = 'span'
                    del a['href']
                    modified = True
        except Exception:
            pass
    return modified


def handle_deprecated(soup):
    """Convert deprecated HTML tags and attributes to modern CSS equivalents."""
    modified = False
    if cleanup_meta_and_triggers(soup):
        modified = True
    if convert_deprecated_tags(soup):
        modified = True
    if convert_deprecated_attrs(soup):
        modified = True
    if remove_invalid_data_attrs(soup):
        modified = True
    if convert_a_name_to_id(soup):
        modified = True
    if validate_uri_schemes(soup):
        modified = True
    return modified


def modernize_ncx_and_tours(extract_dir, opf_root, ns):
    """Fix NCX if it exists and remove obsolete <tours> element."""
    ncx_path = fix_ncx(extract_dir)
    for tours in opf_root.findall('opf:tours', ns):
        opf_root.remove(tours)
        
    if ncx_path and ncx_path.exists():
        # Sync NCX identifier (dtb:uid) with OPF unique identifier
        unique_id_attr = opf_root.attrib.get('unique-identifier')
        opf_identifier = None
        if unique_id_attr:
            dc_identifier = opf_root.find(f'.//{{http://purl.org/dc/elements/1.1/}}identifier[@id="{unique_id_attr}"]')
            if dc_identifier is not None and dc_identifier.text:
                opf_identifier = dc_identifier.text.strip()
        
        if not opf_identifier:
            dc_identifier = opf_root.find('.//{http://purl.org/dc/elements/1.1/}identifier')
            if dc_identifier is not None and dc_identifier.text:
                opf_identifier = dc_identifier.text.strip()
                
        if opf_identifier:
            try:
                with open(ncx_path, 'r', encoding='utf-8') as f:
                    ncx_soup = BeautifulSoup(f, 'lxml-xml')
                
                dtb_uid_meta = None
                for meta in ncx_soup.find_all('meta'):
                    if meta.get('name') == 'dtb:uid':
                        dtb_uid_meta = meta
                        break
                
                if dtb_uid_meta:
                    if dtb_uid_meta.get('content') != opf_identifier:
                        print(f"Syncing NCX identifier ('{dtb_uid_meta.get('content')}') to match OPF identifier ('{opf_identifier}')")
                        dtb_uid_meta['content'] = opf_identifier
                        with open(ncx_path, 'w', encoding='utf-8') as f:
                            f.write(str(ncx_soup))
                else:
                    head = ncx_soup.find('head')
                    if head:
                        # Create missing dtb:uid meta tag
                        dtb_uid_meta = ncx_soup.new_tag('meta', attrs={'name': 'dtb:uid', 'content': opf_identifier})
                        head.append(dtb_uid_meta)
                        print(f"Added missing NCX identifier ('{opf_identifier}')")
                        with open(ncx_path, 'w', encoding='utf-8') as f:
                            f.write(str(ncx_soup))
            except Exception as e:
                print(f"Warning: Could not sync NCX identifier: {e}")
                
    return ncx_path


def modernize_cover_image_id(opf_root, manifest, ns):
    """Modernize cover image ID to satisfy Calibre & Nook Color compatibility."""
    cover_item = None
    # Search cover image via EPUB 3 properties
    cover_item = next((item for item in manifest.values() if 'cover-image' in (item.get('properties') or '').split()), None)
    
    # If not found, search cover image via EPUB 2 legacy cover metadata
    if cover_item is None:
        metadata = opf_root.find('opf:metadata', ns)
        if metadata is not None:
            for meta in metadata.findall('.//opf:meta[@name="cover"]', ns):
                c_id = meta.get('content')
                if c_id:
                    cover_item = next((item for item in manifest.values() if item.get('id') == c_id), None)
                    if cover_item is not None:
                        break

    if cover_item is not None:
        old_cover_id = cover_item.get('id')
        if old_cover_id != 'cover':
            print(f"Renaming cover image ID from '{old_cover_id}' to 'cover' to work around Nook Color bug and satisfy Calibre requirements.")
            
            # If another item already has ID "cover", we must rename it to avoid collision
            other_cover_item = next((item for item in manifest.values() if item.get('id') == 'cover' and item != cover_item), None)
            if other_cover_item is not None:
                new_id = "cover-page"
                existing_ids = {item.get('id') for item in manifest.values()}
                while new_id in existing_ids:
                    new_id = f"cover-page-{uuid.uuid4().hex[:4]}"
                print(f"Renaming existing manifest item ID 'cover' (associated with {other_cover_item.get('href')}) to '{new_id}' to avoid collision.")
                other_cover_item.set('id', new_id)
                
                # Update references in <spine> to the renamed item
                spine = opf_root.find('opf:spine', ns)
                if spine is not None:
                    for itemref in spine.findall('opf:itemref', ns):
                        if itemref.get('idref') == 'cover':
                            itemref.set('idref', new_id)
            
            # Rename cover image's ID to 'cover'
            cover_item.set('id', 'cover')
            
            # Ensure it has cover-image property in EPUB 3 style
            props = cover_item.get('properties') or ''
            prop_list = props.split()
            if 'cover-image' not in prop_list:
                prop_list.append('cover-image')
                cover_item.set('properties', ' '.join(prop_list))
                print(f"Added properties='cover-image' to cover item {cover_item.get('href')}")
    return cover_item


def ensure_epub3_navigation(opf_root, manifest, ns, opf_dir, ncx_path):
    """Ensure there is an EPUB 3 navigation document, generating it from NCX if missing."""
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


def modernize_html_and_css_files(opf_dir, manifest, ncx_path):
    """Analyze and modernize HTML and CSS assets inside the EPUB."""
    ul_disc_needed = False
    html_items = [(h, i) for h, i in manifest.items() if i.get('media-type') == 'application/xhtml+xml']
    
    # Store global ID mappings: id_mappings[file_href][old_id] = new_id
    id_mappings = defaultdict(dict)
    
    if not html_items:
        return ul_disc_needed

    pbar = tqdm(html_items, unit="file", desc="Modernizing assets", leave=True)
    for href, item in pbar:
        html_path = opf_dir / unquote(href)
        if not html_path.exists(): continue
        
        pbar.set_postfix(file=href[-30:], refresh=False)
        modified = False
        try:
            content = html_path.read_bytes()
            soup = BeautifulSoup(content, 'lxml-xml')

            # Ensure HTML5 DOCTYPE (HTM-004)
            doctypes = [c for c in soup.contents if isinstance(c, Doctype)]
            if doctypes:
                if str(doctypes[0]).strip() != 'html':
                    doctypes[0].replace_with(Doctype('html'))
                    modified = True
            else:
                soup.insert(0, Doctype('html'))
                modified = True

            # Pass 1: Sanitize IDs and update local links/ARIA attributes within the same file
            local_id_map = {}
            for tag in soup.find_all(True, id=True):
                old_id = tag['id']
                if not is_valid_xml_id(old_id):
                    new_id = sanitize_xml_id(old_id)
                    if new_id and new_id != old_id:
                        temp_id = new_id
                        existing_ids = {t.get('id') for t in soup.find_all(True, id=True) if t != tag}
                        while temp_id in existing_ids:
                            temp_id = f"{new_id}_{uuid.uuid4().hex[:4]}"
                        new_id = temp_id

                        tag['id'] = new_id
                        local_id_map[old_id] = new_id
                        id_mappings[href][old_id] = new_id
                        modified = True
                    elif not new_id:
                        del tag['id']
                        modified = True

            if local_id_map:
                for a in soup.find_all(True, href=True):
                    a_href = a['href']
                    if a_href.startswith('#'):
                        local_anchor = a_href[1:]
                        if local_anchor in local_id_map:
                            a['href'] = '#' + local_id_map[local_anchor]
                            modified = True

                for attr in ['aria-labelledby', 'aria-describedby']:
                    for tag in soup.find_all(attrs={attr: True}):
                        target_ids = tag[attr].split()
                        new_target_ids = [local_id_map.get(tid, tid) for tid in target_ids]
                        if new_target_ids != target_ids:
                            tag[attr] = " ".join(new_target_ids)
                            modified = True

            # Ensure XHTML namespace (RSC-005)
            html_tag = soup.find('html')
            if html_tag and not html_tag.has_attr('xmlns'):
                html_tag['xmlns'] = "http://www.w3.org/1999/xhtml"
                modified = True
            
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
                    if not target_ids:
                        del tag[attr]
                        modified = True
                        continue

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
            pbar.write(f"Warning: Error modernizing {href}: {e}")
    pbar.close()

    # Pass 2: Update cross-file references across all HTML files
    if id_mappings:
        pbar_p2 = tqdm(html_items, unit="file", desc="Updating cross-file links", leave=True)
        for href, item in pbar_p2:
            html_path = opf_dir / unquote(href)
            if not html_path.exists(): continue
            
            pbar_p2.set_postfix(file=href[-30:], refresh=False)
            modified = False
            try:
                content = html_path.read_bytes()
                soup = BeautifulSoup(content, 'lxml-xml')
                
                current_dir = os.path.dirname(href)
                
                for a in soup.find_all(True, href=True):
                    a_href = a['href']
                    if not a_href.startswith('#') and '#' in a_href:
                        file_part, anchor_part = a_href.split('#', 1)
                        target_rel_path = unquote(file_part)
                        target_abs_path = os.path.normpath(os.path.join(current_dir, target_rel_path)) if current_dir else os.path.normpath(target_rel_path)
                        
                        if target_abs_path in id_mappings and anchor_part in id_mappings[target_abs_path]:
                            new_anchor = id_mappings[target_abs_path][anchor_part]
                            a['href'] = f"{file_part}#{new_anchor}"
                            modified = True
                            
                if modified:
                    html_path.write_text(str(soup), encoding='utf-8')
            except Exception as e:
                pbar_p2.write(f"Warning: Error updating references in {href}: {e}")
        pbar_p2.close()

        # Update NCX references
        if ncx_path:
            try:
                ncx_tree = ET.parse(str(ncx_path))
                ncx_root = ncx_tree.getroot()
                ncx_modified = False
                
                for content_tag in ncx_root.findall(".//{http://www.daisy.org/z3986/2005/ncx/}content"):
                    src = content_tag.get('src')
                    if src and '#' in src:
                        file_part, anchor_part = src.split('#', 1)
                        target_rel_path = unquote(file_part)
                        target_abs_path = os.path.normpath(target_rel_path)
                        if target_abs_path in id_mappings and anchor_part in id_mappings[target_abs_path]:
                            new_anchor = id_mappings[target_abs_path][anchor_part]
                            content_tag.set('src', f"{file_part}#{new_anchor}")
                            ncx_modified = True
                
                if ncx_modified:
                    ncx_tree.write(str(ncx_path), encoding="utf-8", xml_declaration=True)
                    print("Updated invalid ID references in NCX document")
            except Exception as e:
                print(f"Warning: Error updating NCX link references: {e}")
    return ul_disc_needed


def inject_ul_disc_css(opf_dir, manifest):
    """Inject CSS rule for _ul_disc class if needed."""
    css_href = next((h for h, m in manifest.items() if m.get('media-type') == 'text/css'), None)
    if css_href:
        css_path = opf_dir / css_href
        try:
            with open(css_path, 'a', encoding='utf-8') as f:
                f.write('\n._ul_disc { list-style-type: disc; }\n')
        except Exception as e:
            print(f"Warning: Error updating CSS {css_href}: {e}")


def modernize_opf_metadata(opf_root, manifest, ns, opf_path, cover_item):
    """Modernize OPF metadata elements to conform to EPUB 3 specifications."""
    metadata = opf_root.find('opf:metadata', ns)
    if metadata is not None:
        VALID_DC_ELEMENTS = {
            'contributor', 'coverage', 'creator', 'date', 'description', 'format',
            'identifier', 'language', 'publisher', 'relation', 'rights',
            'source', 'subject', 'title', 'type'
        }
        for child in list(metadata):
            if not isinstance(child.tag, str):
                if child.tag is ET.Comment:
                    continue
                print(f"Notice: skipping non-element metadata node in {opf_path.name}:{child.sourceline} ({type(child.tag).__name__}): {repr(child.text)}")
                continue

            # Remove any attribute that starts with {http://www.idpf.org/2007/opf}
            attrs_to_del = [a for a in child.attrib if a.startswith('{http://www.idpf.org/2007/opf}')]
            for a in attrs_to_del:
                del child.attrib[a]
            
            # Remove empty dc metadata elements (allowed in EPUB 2 but not EPUB 3)
            if child.tag.startswith('{' + ns['dc'] + '}') and not (child.text and child.text.strip()) and not list(child):
                metadata.remove(child)
                continue

            # Remove invalid dc metadata elements (e.g. <dc:meta>) (RSC-005)
            if child.tag.startswith('{' + ns['dc'] + '}'):
                local_name = child.tag.split('}', 1)[1]
                if local_name not in VALID_DC_ELEMENTS:
                    print(f"Warning: Removing invalid Dublin Core element <{child.tag}> from metadata in {opf_path.name}")
                    metadata.remove(child)
                    continue

            # Handle <meta> tags (RSC-005)
            if child.tag == '{' + ns['opf'] + '}meta':
                # Convert 'value' attribute to 'content'
                if 'value' in child.attrib:
                    val = child.attrib.pop('value')
                    if 'content' not in child.attrib:
                        child.set('content', val)
                
                # Fix Calibre error "The meta cover tag has content before name"
                if child.attrib.get('name') == 'cover' and 'content' in child.attrib:
                    content_val = 'cover' if cover_item is not None else child.attrib.get('content')
                    child.attrib.clear()
                    child.attrib['name'] = 'cover'
                    child.attrib['content'] = content_val
                
                # Check for required attributes (name, property, or refines)
                if not any(attr in child.attrib for attr in ['name', 'property', 'refines']):
                    print(f"Warning: Removing invalid <meta> tag missing required attributes in {opf_path.name}: {ET.tostring(child, encoding='unicode').strip()}")
                    metadata.remove(child)
                    continue
        
        # Single dcterms:modified
        for old_mod in metadata.findall('.//opf:meta[@property="dcterms:modified"]', ns):
            metadata.remove(old_mod)
        
        now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        mod = ET.SubElement(metadata, '{http://www.idpf.org/2007/opf}meta')
        mod.set('property', 'dcterms:modified')
        mod.text = now
        
        # Ensure legacy cover meta tag exists if there is a cover-image (Google Play Books compatibility)
        cover_item = next((item for item in manifest.values() if 'cover-image' in (item.get('properties') or '').split()), None)
        if cover_item is not None:
            cover_id = cover_item.get('id')
            if cover_id:
                has_legacy_cover = any(meta.get('name') == 'cover' and meta.get('content') == cover_id 
                                       for meta in metadata.findall('.//opf:meta', ns))
                if not has_legacy_cover:
                    print(f"Adding legacy cover meta tag for {cover_id} (Google Play Books compatibility)")
                    c_meta = ET.SubElement(metadata, '{http://www.idpf.org/2007/opf}meta')
                    c_meta.set('name', 'cover')
                    c_meta.set('content', cover_id)


def standardize_manifest_media_types(manifest):
    """Standardize media-types of manifest items in OPF document based on file extensions."""
    media_type_map = {
        '.ttf': 'application/vnd.ms-opentype',
        '.otf': 'application/vnd.ms-opentype',
        '.woff': 'font/woff',
        '.woff2': 'font/woff2',
        '.xpgt': 'application/adobe-page-template+xml',
        '.css': 'text/css',
        '.xhtml': 'application/xhtml+xml',
        '.html': 'application/xhtml+xml',
        '.htm': 'application/xhtml+xml',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.svg': 'image/svg+xml',
        '.webp': 'image/webp',
        '.ncx': 'application/x-dtbncx+xml'
    }
    for item in manifest.values():
        href = None
        for attr, val in item.attrib.items():
            if attr == 'href' or attr.endswith('}href'):
                href = val
                break
        
        if not href: continue
        
        ext = os.path.splitext(href.lower())[1]
        if ext in media_type_map:
            current_type = None
            for attr, val in item.attrib.items():
                if attr == 'media-type' or attr.endswith('}media-type'):
                    current_type = val
                    break
            
            new_type = media_type_map[ext]
            if current_type != new_type:
                print(f"Modernizing media-type for {href}: {current_type} -> {new_type}")
                to_del = [a for a in item.attrib if a == 'media-type' or a.endswith('}media-type')]
                for a in to_del: del item.attrib[a]
                item.set('media-type', new_type)


def ensure_nonlinear_reachable(opf_root, manifest, ns, opf_dir):
    """Ensure non-linear content is reachable, adding links to nav.xhtml (OPF-096)."""
    non_linear_items = []
    spine = opf_root.find('opf:spine', ns)
    if spine is not None:
        for itemref in spine.findall('opf:itemref[@linear="no"]', ns):
            idref = itemref.get('idref')
            for href, m_item in manifest.items():
                if m_item.get('id') == idref:
                    non_linear_items.append((href, itemref))
                    break

    if non_linear_items:
        nav_item = next((item for item in manifest.values() if 'nav' in (item.get('properties') or '').split()), None)
        if nav_item is not None:
            nav_href = nav_item.get('href')
            nav_path = opf_dir / unquote(nav_href)
            if nav_path.exists():
                try:
                    import posixpath
                    content = nav_path.read_bytes()
                    soup = BeautifulSoup(content, 'lxml-xml')
                    body = soup.find('body')
                    if body:
                        hidden_div = soup.new_tag('div', style="display:none; visibility:hidden;", id="hidden-reachability-links")
                        for href, _ in non_linear_items:
                            rel_href = posixpath.relpath(href, posixpath.dirname(nav_href))
                            a = soup.new_tag('a', href=rel_href)
                            a.string = f"Reachability link for {href}"
                            hidden_div.append(a)
                        body.append(hidden_div)
                        nav_path.write_text(str(soup), encoding='utf-8')
                        print(f"Added {len(non_linear_items)} reachability links to {nav_href}")
                except Exception as e:
                    print(f"Warning: Could not add reachability links to nav: {e}")
        else:
            for _, itemref in non_linear_items:
                if 'linear' in itemref.attrib:
                    del itemref.attrib['linear']
            print(f"Marked {len(non_linear_items)} non-linear items as linear because nav.xhtml is missing")


def modernize_assets(extract_dir, tree, manifest, ns, opf_path):
    """Apply various modernizations to EPUB assets and OPF metadata."""
    opf_root = tree.getroot()
    opf_dir = opf_path.parent
    
    # 1. Fix NCX if it exists and remove obsolete <tours> element
    ncx_path = modernize_ncx_and_tours(extract_dir, opf_root, ns)

    # 1.5 Cover Image ID Modernization
    cover_item = modernize_cover_image_id(opf_root, manifest, ns)
    
    # 2. Ensure EPUB 3 navigation document
    ensure_epub3_navigation(opf_root, manifest, ns, opf_dir, ncx_path)

    # 3. Analyze and fix HTML/CSS files
    ul_disc_needed = modernize_html_and_css_files(opf_dir, manifest, ncx_path)

    # 4. Inject CSS for ul_disc if needed
    if ul_disc_needed:
        inject_ul_disc_css(opf_dir, manifest)

    # 5. Modernize Metadata
    modernize_opf_metadata(opf_root, manifest, ns, opf_path, cover_item)

    # 6. Manifest Item Media Type Standardization
    standardize_manifest_media_types(manifest)

    # 7. Set EPUB version to 3.0
    opf_root.set('version', '3.0')

    # 8. Ensure Non-Linear Content is Reachable (OPF-096)
    ensure_nonlinear_reachable(opf_root, manifest, ns, opf_dir)



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


def remove_unreferenced(ctx: EpubContext, manifest, tree, ns, root, content_dir=None, show_summary=True):
    # Map unquoted path to original manifest key
    unquoted_manifest = {}
    for href in manifest:
        norm_href = unquote(href).replace('\\', '/')
        unquoted_manifest[norm_href] = href

    # Helper to resolve a raw href (from OPF metadata, HTML, CSS, etc.) to the matching original manifest href
    def resolve_to_manifest(raw_href, base_dir=None):
        if not raw_href:
            return None
        # Split hash fragment if any
        raw_href = raw_href.split('#')[0]
        # Unquote and normalize slashes
        unquoted_ref = unquote(raw_href).replace('\\', '/')
        
        # If there's a base_dir, make it relative to content_dir
        if base_dir is not None:
            abs_path = os.path.normpath(os.path.join(base_dir, unquoted_ref))
            rel_path = os.path.relpath(abs_path, content_dir).replace('\\', '/')
        else:
            rel_path = unquoted_ref
            
        return unquoted_manifest.get(rel_path)

    # 1. Initialize files_to_keep with essential references
    spine_refs = {item.attrib["idref"] for item in tree.findall(".//opf:itemref", ns)}
    files_to_keep = {i.attrib["href"] for i in manifest.values() if i.attrib.get("id") in spine_refs}

    for reference in tree.findall(".//opf:guide/opf:reference", ns):
        href = reference.get("href")
        resolved = resolve_to_manifest(href, content_dir)
        if resolved:
            files_to_keep.add(resolved)

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
        unquoted_href = unquote(href).replace('\\', '/')
        if any(fnmatch(href, pat) or fnmatch(unquoted_href, pat) for pat in essential_patterns):
            files_to_keep.add(href)

    # 2. Iteratively find all references by scanning files
    # Start scanning with all XHTML files, not just the spine
    files_to_scan = []
    seen_queued = set()
    for href, item in manifest.items():
        if item.attrib.get("media-type") == "application/xhtml+xml":
            if href not in seen_queued:
                files_to_scan.append(href)
                seen_queued.add(href)
    
    processed_scans = set()
    
    pbar = tqdm(total=len(files_to_scan), unit="file", desc="Scanning references", disable=not show_summary, leave=True)
    while files_to_scan:
        href = files_to_scan.pop(0)
        if href in processed_scans:
            pbar.total -= 1
            continue
        processed_scans.add(href)
        files_to_keep.add(href)
        
        pbar.set_postfix(file=href[-40:], refresh=False)

        unquoted_href = unquote(href).replace('\\', '/')
        file_path = content_dir / unquoted_href
        if not file_path.exists():
            pbar.update(1)
            continue

        file_dir = file_path.parent
        
        try:
            # Use binary read and detect encoding if possible, but for performance,
            # we'll stick to a fast read and specific parsing.
            is_html = href.lower().endswith(('.xhtml', '.html', '.htm'))
            if not is_html:
                manifest_item = manifest.get(href)
                if manifest_item is not None and manifest_item.get('media-type') == 'application/xhtml+xml':
                    is_html = True
            
            is_css = href.lower().endswith('.css')
            if not is_css:
                manifest_item = manifest.get(href)
                if manifest_item is not None and manifest_item.get('media-type') == 'text/css':
                    is_css = True

            if is_html:
                content = file_path.read_bytes()
                soup = BeautifulSoup(content, 'lxml-xml')
                
                # Scan all tag attributes for references
                for tag in soup.find_all(True):
                    for attr, val in tag.attrs.items():
                        lower_attr = attr.lower()
                        if lower_attr in ('href', 'src', 'poster') or lower_attr.endswith(':href') or lower_attr.endswith(':src') or lower_attr.endswith('}href') or lower_attr.endswith('}src'):
                            resolved = resolve_to_manifest(val, file_dir)
                            if resolved and resolved not in seen_queued:
                                files_to_scan.append(resolved)
                                seen_queued.add(resolved)
                                pbar.total += 1
                
                # Scan style attributes
                for tag in soup.find_all(True, attrs={'style': True}):
                    try:
                        declarations = tinycss2.parse_declaration_list(tag['style'], skip_comments=True, skip_whitespace=True)
                        for ref in extract_refs(declarations):
                            resolved = resolve_to_manifest(ref, file_dir)
                            if resolved and resolved not in seen_queued:
                                files_to_scan.append(resolved)
                                seen_queued.add(resolved)
                                pbar.total += 1
                    except Exception:
                        pass

                # Scan style tags in HTML/XHTML
                for tag in soup.find_all('style'):
                    style_content = tag.string
                    if style_content:
                        try:
                            rules = tinycss2.parse_stylesheet(style_content, skip_comments=True, skip_whitespace=True)
                            for rule in rules:
                                is_import = (rule.type == 'at-rule' and rule.at_keyword == 'import')
                                all_refs = []
                                if hasattr(rule, 'prelude') and rule.prelude:
                                    all_refs.extend(extract_refs(rule.prelude, is_import=is_import))
                                if hasattr(rule, 'content') and rule.content:
                                    all_refs.extend(extract_refs(rule.content))
                                
                                for ref in all_refs:
                                    resolved = resolve_to_manifest(ref, file_dir)
                                    if resolved and resolved not in seen_queued:
                                        files_to_scan.append(resolved)
                                        seen_queued.add(resolved)
                                        pbar.total += 1
                        except Exception as e:
                            if ctx.verbose:
                                pbar.write(f"Error parsing style tag in {href}: {e}")
            
            # Scan CSS for @import, url(), and @font-face
            elif is_css:
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
                            resolved = resolve_to_manifest(ref, file_dir)
                            if resolved and resolved not in seen_queued:
                                files_to_scan.append(resolved)
                                seen_queued.add(resolved)
                                pbar.total += 1
                except Exception as e:
                    if ctx.verbose:
                        pbar.write(f"Error parsing CSS {href}: {e}")

        except Exception as e:
            if ctx.verbose:
                pbar.write(f"Error scanning file {href}: {e}")
        
        pbar.update(1)
    
    pbar.close()

    # 3. Remove files that are not in our final keep list
    # Pre-calculate parent map for efficient node removal
    parent_map = {c: p for p in tree.iter() for c in p}
    
    for href, node in list(manifest.items()):
        if href not in files_to_keep:
            file_path = content_dir / unquote(href)
            size = 0
            if file_path.exists():
                size = file_path.stat().st_size
                file_path.unlink()
            else:
                if ctx.verbose:
                    print(f"File to remove not found on disk: {href} (removing from manifest)")
            
            # Remove from XML manifest
            parent = parent_map.get(node)
            if parent is not None:
                parent.remove(node)

            # Keep in-memory manifest dict in sync
            if href in manifest:
                del manifest[href]

            if show_summary:
                if size > 0:
                    print(f"Dropping unreferenced file: {href} ({human(size)})")
                else:
                    print(f"Dropping unreferenced missing file reference: {href}")


def purge_unwanted_files(ctx: EpubContext, purge_patterns, extract_dir, content_dir, tree, manifest, show_summary=True):
    if ctx.verbose and show_summary:
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
            remove_asset(tree, content_dir, relative_filename, manifest_dict=manifest)
            if show_summary:
                print(f"Purged unwanted file: {relative_filename} from spine, manifest, and disk")


def remove_asset(tree, content_dir, href, manifest_dict=None):
    """Remove a file from the spine, manifest, and disk in a single operation."""
    try:
        # Find the manifest and spine elements in the OPF
        manifest = tree.find("{http://www.idpf.org/2007/opf}manifest")
        spine = tree.find("{http://www.idpf.org/2007/opf}spine")
        
        item = None
        if manifest is not None:
            # Search all items for the matching href
            for manifest_item in manifest.findall("*"):
                if manifest_item.get("href") == href:
                    item = manifest_item
                    break
        
        if item is None:
            print(f"Warning: Could not find manifest item with href '{href}'")
            return
            
        # Get the item's id and remove from spine
        item_id = item.get("id")
        if item_id and spine is not None:
            for itemref in list(spine):
                if itemref.get("idref") == item_id:
                    spine.remove(itemref)
                    break
                    
        # Remove from XML manifest
        if manifest is not None:
            manifest.remove(item)
            
        if manifest_dict is not None and href in manifest_dict:
            del manifest_dict[href]
            
        # Remove from disk
        file_path = content_dir / unquote(href)
        if file_path.exists():
            file_path.unlink()
        else:
            print(f"Warning: File {href} not found on disk, but removed from manifest/spine.")
        
    except Exception as e:
        print(f"Warning: Could not remove {href}: {e}")
        raise


def analyze_images(ctx: EpubContext, root, show_summary=True):
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
    
    type_summaries = []
    
    for name, paths in types:
        count = len(paths)
        if count == 0:
            type_summaries.append(f"0 {name} files")
            continue
            
        size = sum((root / p).stat().st_size for p in paths)
        summary = f"{count} {name} / {human(size)}"
        type_summaries.append(summary)

    if show_summary:
        summary_line = f"Found {type_summaries[0]}, {type_summaries[1]} and {type_summaries[2]}"
        print(summary_line)
    
    return jpg_paths, png_paths, webp_paths


def compress_images(ctx: EpubContext, root, quality, jpg_paths, png_paths, webp_paths):
    
    savings = []
    
    # Define groups
    groups = [
        (jpg_paths, 'JPEG'),
        (png_paths, 'PNG'),
        (webp_paths, 'WebP')
    ]
    
    estimate_quality = ctx.weighted_avg_quality is None
    if estimate_quality:
        max_estimated_quality = 0
        weighted_q_sum = 0
        total_img_size = 0
    
    for paths, img_type in groups:
        if not paths:
            continue
            
        total_before = 0
        total_after = 0
        
        desc = f"Optimizing {img_type}s" if quality == 100 else f"Compressing {img_type}s (q={quality})"
        pbar = tqdm(paths, unit="img", desc=desc, leave=True)
        
        for rel_path in pbar:
            p = root / rel_path
            if not p.exists():
                continue
                
            pbar.set_postfix(file=rel_path.name[-30:], refresh=False)
            before = p.stat().st_size
            total_before += before
            
            image_info = None
            if estimate_quality or ctx.verbose:
                image_info = analyze_image_quality(ctx, p)
                
            if estimate_quality and image_info and 'error' not in image_info:
                q_val = image_info.get("estimated_quality")
                if q_val is not None:
                    max_estimated_quality = max(max_estimated_quality, q_val)
                else:
                    if img_type == "PNG":
                        q_val = 100
                    elif img_type == "WebP":
                        q_val = 95
                    else:
                        q_val = 90
                
                weighted_q_sum += q_val * before
                total_img_size += before
                
            # Create a temporary file to perform compression
            tmp_path = None
            try:
                suffix = p.suffix.lower()
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = pathlib.Path(tmp.name)
                
                # Copy original to temp file because CLI tools compress in-place
                shutil.copy2(p, tmp_path)
                
                if quality == 100:
                    if img_type == 'PNG':
                        oxipng_args = ["oxipng", "-o", "3", "--strip", "all", "--alpha", "--threads", "4", "-q", str(tmp_path)]
                        subprocess.run(oxipng_args, stdout=subprocess.DEVNULL)
                    elif img_type == 'JPEG':
                        jpegoptim_args = ["jpegoptim", "--strip-all", "-q", str(tmp_path)]
                        subprocess.run(jpegoptim_args, stdout=subprocess.DEVNULL)
                    else:
                        # Lossless fallback for WebP or others
                        with Image.open(p) as img:
                            img.save(tmp_path, format=img.format, optimize=True)
                else:
                    if img_type == "PNG":
                        if shutil.which("pngquant"):
                            pngquant_args = [
                                "pngquant",
                                "--force",
                                "--skip-if-larger",
                                "--ext", ".png",
                                "--quality", f"{max(0, quality-10)}-{quality}",
                                str(tmp_path)
                            ]
                            subprocess.run(pngquant_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            with Image.open(p) as img:
                                img = img.convert("P", palette=Image.ADAPTIVE)
                                img.save(tmp_path, format="PNG", optimize=True)
                    else:
                        with Image.open(p) as img:
                            fmt = img.format or img_type
                            if fmt == "JPEG":
                                img.save(tmp_path, format="JPEG", quality=quality, optimize=True, progressive=True)
                            else:
                                img.save(tmp_path, format=fmt, quality=quality, optimize=True)
                
                after_size = tmp_path.stat().st_size
                if after_size < before:
                    shutil.copy2(tmp_path, p)
            except Exception as e:
                if ctx.verbose:
                    pbar.write(f"Image compress error: {p} {e}")
            finally:
                if tmp_path and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
            
            after = p.stat().st_size
            total_after += after
            savings.append((before, after))
            
            if ctx.verbose:
                reduction_pct = (before - after) / before * 100 if before > 0 else 0
                if image_info and 'error' not in image_info:
                    fmt = image_info['format']
                    dims = f"{image_info['dimensions'][0]}x{image_info['dimensions'][1]}"
                    mode = image_info['mode']
                    output = f"{fmt}: {rel_path} | Dims: {dims} | Mode: {mode}"
                    if fmt == "JPEG":
                        est_quality = f"{image_info['estimated_quality'] or 'Unknown'}"
                        output += f" | Est.Quality: {est_quality}"
                    elif fmt == "PNG" and image_info['png_info']:
                        color_type = image_info['png_info'].get('color_type', 'Unknown')
                        output += f" | Type: {color_type}"
                    output += f" | Quality: {quality} | {human(before)} → {human(after)} ({reduction_pct:.1f}% saved)"
                    pbar.write(output)
                else:
                    pbar.write(f"File: {rel_path} | {human(before)} → {human(after)} ({reduction_pct:.1f}% saved)")
                    
        pbar.close()
        
        if total_before > 0:
            reduction_pct = (total_before - total_after) / total_before * 100
            action = "Optimized" if quality == 100 else "Compressed"
            print(f"{action} {img_type}s (q={quality}): {human(total_before)} → {human(total_after)} ({reduction_pct:.1f}% saved)")
            
    if estimate_quality:
        ctx.max_estimated_quality = max_estimated_quality
        ctx.weighted_avg_quality = (weighted_q_sum / total_img_size) if total_img_size > 0 else 100.0
            
    return savings


def analyze_image_quality(ctx: EpubContext, path: pathlib.Path):
    """Analyze the quality of an image file.
    
    Args:
        ctx: EpubContext object
        path: Path to the image file
        
    Returns:
        A tuple of (image format, estimated quality, color mode, dimensions)
    """
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
                if ctx.verbose:
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
                if ctx.verbose:
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
        if ctx.verbose:
            print(f"Error analyzing image {path}: {e}")
        return {
            "format": "unknown",
            "error": str(e)
        }


def rebuild_epub(root: pathlib.Path, out_path: pathlib.Path):
    all_files = sorted([f for f in root.rglob("*") if f.is_file()])
    with zipfile.ZipFile(out_path, "w") as z:
        mimetype_path = root / "mimetype"
        if mimetype_path.exists():
            # this file must be the first and uncompressed
            z.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)
            all_files = [f for f in all_files if f.name != "mimetype"]

        pbar = tqdm(all_files, unit="file", desc="Rebuilding EPUB", leave=True)
        for file in pbar:
            pbar.set_postfix(file=file.name[-30:], refresh=False)
            z.write(file, file.relative_to(root), compress_type=zipfile.ZIP_DEFLATED)
        pbar.close()

def analyze_file(ctx: EpubContext):
    """Extract EPUB and load metadata."""
    unzip(ctx)
    opf_path, tree, manifest, ns = load_opf(ctx)
    return opf_path, tree, manifest, ns


def prune_unreferenced_assets(ctx: EpubContext, tree, manifest, ns, opf_path, show_summary=True):
    """Remove unreferenced assets and write the updated OPF."""
    content_dir = opf_path.parent
    remove_unreferenced(ctx, manifest, tree, ns, ctx.extract_dir, content_dir, show_summary=show_summary)
    tree.write(opf_path, encoding="utf-8", xml_declaration=True)


def estimate_next_quality(
    history: list[tuple[int, int]],
    target_bytes: int,
    ratio: float,
    ctx: EpubContext,
    min_quality: int = 15
) -> int:
    """
    Estimate the next quality value based on history of runs using Secant Method (linear interpolation).
    Uses size-weighted average image quality and power-law estimation for the first lossy pass.
    Clamps the quality drop to ensure it strictly decreases by at least 2 points,
    drops by at most 25 points, and never goes below min_quality.
    """
    q_curr, size_curr = history[-1]
    
    if len(history) < 2:
        # First lossy pass: use the size-weighted average quality and required ratio
        # to mathematically estimate a starting quality.
        # S_target / S_lossless = 1.0 / ratio.
        weighted_avg = ctx.weighted_avg_quality if ctx.weighted_avg_quality is not None else 100.0
        ref_q = weighted_avg if (q_curr == 100 or weighted_avg < q_curr) else q_curr

        if ref_q > 90.0:
            # High-quality source images (e.g. q95-100) are extremely compressible near quality 100.
            # A tiny drop in quality (like dropping 5-10 points) yields a massive size reduction.
            # We use a linear drop model proportional to the required ratio.
            drop = 4.0 * (ratio - 1.0)
            q_est = ref_q - drop
            q_next = int(round(q_est))
        else:
            # For standard compressed images, we assume a power-law relationship:
            # S_q / S_lossless = (q / Q_avg) ^ beta
            # So q = Q_avg * (1.0 / ratio) ^ (1/beta). Using beta = 2.0 is robust.
            beta = 2.0
            q_est = ref_q * ((1.0 / ratio) ** (1.0 / beta))
            q_next = int(round(q_est))

        # Clamp to reasonable starting lossy qualities
        q_next = max(min_quality, min(95, q_next))
    else:
        # We have at least 2 data points: use the Secant Method
        q_prev, size_prev = history[-2]
        
        size_diff = size_curr - size_prev
        q_diff = q_curr - q_prev
        
        if size_diff != 0 and q_diff != 0:
            # Linear interpolation estimate
            slope = size_diff / q_diff
            q_est = q_curr - (size_curr - target_bytes) / slope
            q_next = int(round(q_est))
        else:
            # Fallback if division by zero or no size change
            if ratio > 2.0:
                q_next = q_curr - 15
            elif ratio > 1.5:
                q_next = q_curr - 10
            else:
                q_next = q_curr - 5
                
    # Clamping & safety bounds
    # 1. Ensure we strictly decrease quality (at least a drop of 2)
    q_next = min(q_curr - 2, q_next)
    
    # 2. Limit the maximum drop in a single step to 25 to avoid overshooting
    MAX_DROP = 25
    q_next = max(q_curr - MAX_DROP, q_next)
    
    # 3. Ensure we don't drop below the minimum quality floor
    q_next = max(min_quality, q_next)
    
    return q_next


def main():
    verify_compressors_availability()
    args = parse_args()
    
    extract_dir = TMP_ROOT / f"epub-shrink-{os.getpid()}"
    ctx = EpubContext(
        input_file=args.epub,
        extract_dir=extract_dir,
        verbose=args.verbose
    )
    original_size = ctx.input_file.stat().st_size
    print("Original size:", human(original_size))

    # 1. Analyze and Prepare
    opf_path, tree, manifest, ns = analyze_file(ctx)
    content_dir = opf_path.parent

    # 2. Purge unwanted patterns
    purge_unwanted_files(ctx, args.purge, ctx.extract_dir, content_dir, tree, manifest, show_summary=True)
    
    # 3. Modernize assets (convert deprecated tags, generate nav.xhtml, etc.)
    modernize_assets(ctx.extract_dir, tree, manifest, ns, opf_path)

    # 4. Prune unreferenced assets and update OPF
    if ctx.verbose:
        print("Performing reference analysis...")
    prune_unreferenced_assets(ctx, tree, manifest, ns, opf_path, show_summary=True)

    # 5. Image Analysis (Discovery and Summary)
    jpg_paths, png_paths, webp_paths = analyze_images(ctx, ctx.extract_dir, show_summary=True)

    # 6. Iterative Compression and Rebuild
    q = args.quality
    final_size = 0
    history = []

    best_meeting_q = None
    best_meeting_size = None
    best_meeting_path = None

    smallest_size_q = None
    smallest_size = None
    smallest_size_path = None

    lowest_failing_q = None
    lowest_failing_size = None

    tried_qualities = set()
    refinement_steps = 0
    MAX_REFINEMENT_STEPS = 4
    MIN_QUALITY = 15

    try:
        while True:
            # Avoid repeating the same quality
            if q in tried_qualities:
                break
            tried_qualities.add(q)

            # Create a fresh build directory from the cleaned extract_dir
            build_dir = TMP_ROOT / f"epub-build-{os.getpid()}-{q}"
            if build_dir.exists():
                shutil.rmtree(build_dir)
            shutil.copytree(ctx.extract_dir, build_dir)

            # Output directly to the original file directory with suffix
            suffix = "-lossless" if q == 100 else f"-q{q}"
            iter_out = ctx.input_file.with_stem(f"{ctx.input_file.stem}{suffix}")
            if iter_out.exists():
                iter_out.unlink()

            compress_images(ctx, build_dir, q, jpg_paths, png_paths, webp_paths)
            rebuild_epub(build_dir, iter_out)
            
            final_size = iter_out.stat().st_size
            print(f"Quality {q}: {human(final_size)}")

            # Record this run
            history.append((q, final_size))

            # Clean up build directory
            shutil.rmtree(build_dir)

            # Track smallest size for fallback
            if smallest_size is None or final_size < smallest_size:
                smallest_size = final_size
                smallest_size_q = q
                smallest_size_path = iter_out

            # Check if target is met
            target_met = not args.targetsize or (final_size / (1024 * 1024) <= args.targetsize)

            if target_met:
                if best_meeting_q is None or q > best_meeting_q:
                    best_meeting_q = q
                    best_meeting_size = final_size
                    best_meeting_path = iter_out
            else:
                if lowest_failing_q is None or q < lowest_failing_q:
                    lowest_failing_q = q
                    lowest_failing_size = final_size

            # If no target size or target met with lossless, we stop immediately
            if not args.targetsize or (q == 100 and target_met):
                break

            target_bytes = args.targetsize * 1024 * 1024

            if best_meeting_q is None:
                # We haven't met the target yet, we must decrease quality
                if q <= MIN_QUALITY:
                    # We reached the floor and still didn't meet the target
                    break
                
                # Calculate next quality to decrease
                ratio = final_size / target_bytes
                q = estimate_next_quality(
                    history=history,
                    target_bytes=target_bytes,
                    ratio=ratio,
                    ctx=ctx,
                    min_quality=MIN_QUALITY
                )
            else:
                # We HAVE met the target at least once!
                # Can we refine to get closer to the target by increasing quality?
                if lowest_failing_q is None:
                    # Try to go up towards 100
                    lowest_failing_q = 100

                gap = lowest_failing_q - best_meeting_q
                if gap <= 2 or refinement_steps >= MAX_REFINEMENT_STEPS:
                    break

                # Interpolate to find a better quality in [best_meeting_q + 1, lowest_failing_q - 1]
                # Use linear interpolation between best_meeting_q and lowest_failing_q
                size_diff = lowest_failing_size - best_meeting_size
                q_diff = lowest_failing_q - best_meeting_q
                
                if size_diff > 0 and q_diff > 0:
                    slope = size_diff / q_diff
                    q_est = best_meeting_q + (target_bytes - best_meeting_size) / slope
                    q_next = int(round(q_est))
                else:
                    q_next = (best_meeting_q + lowest_failing_q) // 2
                    
                # Clamp to guarantee progress and stay within the bounds
                q_next = max(best_meeting_q + 1, min(lowest_failing_q - 1, q_next))
                
                print(f"Target met with q={best_meeting_q} ({human(best_meeting_size)}). Refining quality to get closer to target...")
                q = q_next
                refinement_steps += 1

        # Determine final output and copy the best file
        if best_meeting_q is not None:
            final_q = best_meeting_q
            final_path = best_meeting_path
            final_size = best_meeting_size
        else:
            final_q = smallest_size_q
            final_path = smallest_size_path
            final_size = smallest_size

        if args.output:
            current_out = args.output
        else:
            suffix = "-lossless" if final_q == 100 else f"-q{final_q}"
            current_out = ctx.input_file.with_stem(f"{ctx.input_file.stem}{suffix}")

        if final_path and final_path.exists() and final_path != current_out:
            shutil.copy2(final_path, current_out)

        print(f"\nFinal size: {human(final_size)} (saved {(original_size - final_size) / original_size:.1%}) of original {human(original_size)}")
        print(f"Output file: {current_out}")

    finally:
        if ctx.extract_dir.exists():
            shutil.rmtree(ctx.extract_dir)


if __name__ == "__main__":
    main()
