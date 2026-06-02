import pytest
from lxml import etree as ET
import pathlib
from epub_shrink import EpubContext, remove_unreferenced, remove_asset

def test_remove_unreferenced_handles_missing_file(tmp_path):
    ns = {
        'opf': 'http://www.idpf.org/2007/opf',
    }

    # Setup mock OPF XML structure
    root = ET.Element('{' + ns['opf'] + '}package', attrib={'version': '2.0'})
    
    # Manifest
    manifest_node = ET.SubElement(root, '{' + ns['opf'] + '}manifest')
    
    # An item that is in the manifest but does not exist on disk
    missing_href = "storytel_metadata.json"
    missing_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={
        'id': 'storytel-meta',
        'href': missing_href,
        'media-type': 'application/json'
    })
    
    # An item that is in the manifest and exists on disk (essential/to keep)
    keep_href = "nav.xhtml"
    keep_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={
        'id': 'nav',
        'href': keep_href,
        'media-type': 'application/xhtml+xml',
        'properties': 'nav'
    })

    # Spine containing the kept item
    spine = ET.SubElement(root, '{' + ns['opf'] + '}spine')
    ET.SubElement(spine, '{' + ns['opf'] + '}itemref', attrib={'idref': 'nav'})

    # Write mock files to disk
    content_dir = tmp_path / "OEBPS"
    content_dir.mkdir(parents=True, exist_ok=True)
    
    # nav.xhtml actually exists
    (content_dir / keep_href).write_text("<html><body>Nav</body></html>", encoding="utf-8")
    
    # storytel_metadata.json does NOT exist on disk

    # Manifest dictionary
    manifest = {
        missing_href: missing_item,
        keep_href: keep_item
    }

    tree = ET.ElementTree(root)
    ctx = EpubContext(
        input_file=tmp_path / "dummy.epub",
        extract_dir=tmp_path,
        verbose=True
    )

    # Call remove_unreferenced
    remove_unreferenced(ctx, manifest, tree, ns, tmp_path, content_dir=content_dir, show_summary=True)

    # Verify that storytel-meta item is removed from XML tree manifest
    items = tree.findall('.//opf:item', ns)
    assert len(items) == 1
    assert items[0].get('id') == 'nav'

    # Verify that it was removed from the in-memory manifest dict
    assert missing_href not in manifest
    assert keep_href in manifest


def test_remove_asset_handles_missing_file_gracefully(tmp_path):
    ns = {
        'opf': 'http://www.idpf.org/2007/opf',
    }

    # Setup mock OPF XML structure
    root = ET.Element('{' + ns['opf'] + '}package', attrib={'version': '2.0'})
    
    # Manifest
    manifest_node = ET.SubElement(root, '{' + ns['opf'] + '}manifest')
    
    # A missing file in the manifest
    missing_href = "storytel_metadata.json"
    missing_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={
        'id': 'storytel-meta',
        'href': missing_href,
        'media-type': 'application/json'
    })

    # Spine containing the item
    spine = ET.SubElement(root, '{' + ns['opf'] + '}spine')
    itemref = ET.SubElement(spine, '{' + ns['opf'] + '}itemref', attrib={'idref': 'storytel-meta'})

    content_dir = tmp_path / "OEBPS"
    content_dir.mkdir(parents=True, exist_ok=True)

    manifest_dict = {
        missing_href: missing_item
    }

    tree = ET.ElementTree(root)

    # Call remove_asset. It should not raise FileNotFoundError.
    remove_asset(tree, content_dir, missing_href, manifest_dict=manifest_dict)

    # Verify removed from XML manifest and spine
    assert len(tree.findall('.//opf:item', ns)) == 0
    assert len(spine) == 0
    assert missing_href not in manifest_dict
