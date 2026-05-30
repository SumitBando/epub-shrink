import pytest
from lxml import etree as ET
import pathlib
import uuid

def test_cover_image_id_renaming(tmp_path):
    from epub_shrink import modernize_assets

    ns = {
        'opf': 'http://www.idpf.org/2007/opf',
        'dc': 'http://purl.org/dc/elements/1.1/',
    }

    # 1. Setup mock OPF XML
    # Package root
    root = ET.Element('{' + ns['opf'] + '}package', attrib={'version': '2.0'})
    
    # Metadata
    metadata = ET.SubElement(root, '{' + ns['opf'] + '}metadata')
    meta_cover = ET.SubElement(metadata, '{' + ns['opf'] + '}meta', attrib={'name': 'cover', 'content': 'my-old-cover-id'})
    
    # Manifest
    manifest_node = ET.SubElement(root, '{' + ns['opf'] + '}manifest')
    
    # Cover image item (id != 'cover')
    cover_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={'id': 'my-old-cover-id', 'href': 'images/cover.jpg', 'media-type': 'image/jpeg'})
    
    # Another item with id 'cover' to trigger collision handling (e.g., cover HTML page)
    other_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={'id': 'cover', 'href': 'cover.xhtml', 'media-type': 'application/xhtml+xml'})
    
    # Spine referencing 'cover' (the page)
    spine = ET.SubElement(root, '{' + ns['opf'] + '}spine')
    itemref = ET.SubElement(spine, '{' + ns['opf'] + '}itemref', attrib={'idref': 'cover'})

    # Manifest dictionary mapping href to elements
    manifest = {
        'images/cover.jpg': cover_item,
        'cover.xhtml': other_item
    }

    tree = ET.ElementTree(root)
    opf_path = tmp_path / "content.opf"

    # Call modernize_assets
    modernize_assets(tmp_path, tree, manifest, ns, opf_path)

    # 2. Assert Cover ID was renamed to 'cover'
    assert cover_item.get('id') == 'cover'
    
    # 3. Assert collision item was renamed (e.g. to 'cover-page')
    assert other_item.get('id') != 'cover'
    assert other_item.get('id').startswith('cover-page')
    
    # 4. Assert spine reference was updated to the new ID of other_item
    assert itemref.get('idref') == other_item.get('id')

    # 5. Assert properties="cover-image" was added to cover item
    assert 'cover-image' in (cover_item.get('properties') or '').split()

    # 6. Assert metadata meta tag content was updated to 'cover' and correct attribute order is preserved
    assert meta_cover.get('content') == 'cover'
    assert list(meta_cover.attrib.keys()) == ['name', 'content']


def test_cover_image_id_renaming_epub3(tmp_path):
    from epub_shrink import modernize_assets

    ns = {
        'opf': 'http://www.idpf.org/2007/opf',
        'dc': 'http://purl.org/dc/elements/1.1/',
    }

    # Setup mock OPF XML
    root = ET.Element('{' + ns['opf'] + '}package', attrib={'version': '3.0'})
    
    # Metadata (no legacy cover tag initially)
    metadata = ET.SubElement(root, '{' + ns['opf'] + '}metadata')
    
    # Manifest
    manifest_node = ET.SubElement(root, '{' + ns['opf'] + '}manifest')
    
    # Cover image item (id != 'cover', with cover-image property)
    cover_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={'id': 'img-cover-id', 'href': 'images/cover.jpg', 'media-type': 'image/jpeg', 'properties': 'cover-image'})

    # Manifest dictionary
    manifest = {
        'images/cover.jpg': cover_item,
    }

    tree = ET.ElementTree(root)
    opf_path = tmp_path / "content.opf"

    # Call modernize_assets
    modernize_assets(tmp_path, tree, manifest, ns, opf_path)

    # Assert cover ID was renamed to 'cover'
    assert cover_item.get('id') == 'cover'

    # Assert legacy cover meta tag was added for Google Play Books compatibility and has content="cover"
    meta_tags = metadata.findall('.//opf:meta[@name="cover"]', ns)
    assert len(meta_tags) == 1
    assert meta_tags[0].get('content') == 'cover'
