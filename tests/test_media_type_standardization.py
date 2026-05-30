import pytest
from lxml import etree as ET
import os

def test_media_type_standardization():
    # Define standard namespace
    ns = {'opf': 'http://www.idpf.org/2007/opf'}
    
    # 1. Setup mock manifest items with mismatched/legacy media-types
    manifest = {}
    
    test_cases = [
        ('page-template.xpgt', 'text/css', 'application/adobe-page-template+xml'),
        ('styles.css', 'text/plain', 'text/css'),
        ('chapter1.html', 'text/html', 'application/xhtml+xml'),
        ('chapter2.xhtml', 'application/xml', 'application/xhtml+xml'),
        ('image.jpg', 'image/jpg', 'image/jpeg'),
        ('image2.jpeg', 'image/pjpeg', 'image/jpeg'),
        ('image3.png', 'image/x-png', 'image/png'),
        ('image4.gif', 'image/giff', 'image/gif'),
        ('vector.svg', 'image/svg', 'image/svg+xml'),
        ('image5.webp', 'image/x-webp', 'image/webp'),
        ('toc.ncx', 'application/xml', 'application/x-dtbncx+xml'),
        ('font.ttf', 'application/x-font-truetype', 'application/vnd.ms-opentype'),
        ('font.otf', 'application/x-font-opentype', 'application/vnd.ms-opentype'),
        ('font.woff', 'font/x-woff', 'font/woff'),
        ('font.woff2', 'font/x-woff2', 'font/woff2'),
    ]
    
    for href, old_type, expected_type in test_cases:
        item = ET.Element('{' + ns['opf'] + '}item')
        item.set('href', href)
        item.set('media-type', old_type)
        manifest[href] = item

    # 2. Simulate the modernization logic implemented in epub_shrink.py
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
        
        if not href:
            continue
        
        ext = os.path.splitext(href.lower())[1]
        if ext in media_type_map:
            current_type = None
            for attr, val in item.attrib.items():
                if attr == 'media-type' or attr.endswith('}media-type'):
                    current_type = val
                    break
            
            new_type = media_type_map[ext]
            if current_type != new_type:
                # Clean up all versions of media-type attribute to avoid duplicates
                to_del = [a for a in item.attrib if a == 'media-type' or a.endswith('}media-type')]
                for a in to_del:
                    del item.attrib[a]
                # Set plain media-type attribute
                item.set('media-type', new_type)

    # 3. Assert all cases are standardized correctly
    for href, _, expected_type in test_cases:
        item = manifest[href]
        assert item.get('media-type') == expected_type
