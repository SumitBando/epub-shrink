import pytest
from lxml import etree as ET
from bs4 import BeautifulSoup
import pathlib
import uuid

def test_invalid_ids_sanitization_and_link_updates(tmp_path):
    from epub_shrink import modernize_assets, is_valid_xml_id, sanitize_xml_id
    
    # 1. Verify helper functions directly
    assert not is_valid_xml_id("")
    assert not is_valid_xml_id("0-start-with-digit")
    assert not is_valid_xml_id("-start-with-hyphen")
    assert not is_valid_xml_id("id-with-nbsp\xa0")
    assert not is_valid_xml_id("id-with-space ")
    assert is_valid_xml_id("_valid-id")
    assert is_valid_xml_id("valid-id_123")
    
    assert sanitize_xml_id("0-start-with-digit") == "id_0-start-with-digit"
    assert sanitize_xml_id("-start-with-hyphen") == "id_-start-with-hyphen"
    assert sanitize_xml_id("id-with-nbsp\xa0") == "id-with-nbsp"
    assert sanitize_xml_id("id-with-space ") == "id-with-space"
    assert sanitize_xml_id("id-with#special@char") == "id-with_special_char"

    ns = {
        'opf': 'http://www.idpf.org/2007/opf',
        'dc': 'http://purl.org/dc/elements/1.1/',
    }

    # 2. Setup mock OPF XML
    root = ET.Element('{' + ns['opf'] + '}package', attrib={'version': '3.0'})
    metadata = ET.SubElement(root, '{' + ns['opf'] + '}metadata')
    manifest_node = ET.SubElement(root, '{' + ns['opf'] + '}manifest')
    
    # Manifest items
    item_ch1 = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={'id': 'ch1', 'href': 'chapter1.xhtml', 'media-type': 'application/xhtml+xml'})
    item_ch2 = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={'id': 'ch2', 'href': 'chapter2.xhtml', 'media-type': 'application/xhtml+xml'})
    item_ncx = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={'id': 'ncx', 'href': 'toc.ncx', 'media-type': 'application/x-dtbncx+xml'})

    manifest = {
        'chapter1.xhtml': item_ch1,
        'chapter2.xhtml': item_ch2,
        'toc.ncx': item_ncx
    }
    tree = ET.ElementTree(root)
    opf_path = tmp_path / "content.opf"

    # 3. Setup mock XHTML files
    # Chapter 1 (contains invalid ID element and local link references)
    ch1_content = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head>
<body>
    <h1 id="0-invalid-id">Invalid ID Section</h1>
    <h2 id="_RWTOC-25    ">Whitespace ID Section</h2>
    <p aria-labelledby="0-invalid-id">Aria Reference</p>
    <a href="#0-invalid-id">Local Link</a>
    <a href="#_RWTOC-25    ">Local Whitespace Link</a>
</body>
</html>
"""
    ch1_path = tmp_path / "chapter1.xhtml"
    ch1_path.write_text(ch1_content, encoding='utf-8')

    # Chapter 2 (contains cross-file links pointing to Chapter 1's old invalid IDs)
    ch2_content = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 2</title></head>
<body>
    <a href="chapter1.xhtml#0-invalid-id">Cross-File Link 1</a>
    <a href="chapter1.xhtml#_RWTOC-25    ">Cross-File Link 2</a>
</body>
</html>
"""
    ch2_path = tmp_path / "chapter2.xhtml"
    ch2_path.write_text(ch2_content, encoding='utf-8')

    # 4. Setup mock NCX file pointing to Chapter 1's old invalid IDs
    ncx_content = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="np-1" playOrder="1">
      <navLabel><text>Invalid ID link</text></navLabel>
      <content src="chapter1.xhtml#0-invalid-id"/>
    </navPoint>
    <navPoint id="np-2" playOrder="2">
      <navLabel><text>Whitespace ID link</text></navLabel>
      <content src="chapter1.xhtml#_RWTOC-25    "/>
    </navPoint>
  </navMap>
</ncx>
"""
    ncx_path = tmp_path / "toc.ncx"
    ncx_path.write_text(ncx_content, encoding='utf-8')

    # Call modernize_assets
    modernize_assets(tmp_path, tree, manifest, ns, opf_path)

    # 5. Assertions for Chapter 1 (Sanitizations and Local Updates)
    ch1_soup = BeautifulSoup(ch1_path.read_bytes(), 'lxml-xml')
    h1 = ch1_soup.find('h1')
    h2 = ch1_soup.find('h2')
    p = ch1_soup.find('p')
    a_tags = ch1_soup.find_all('a')

    assert h1.get('id') == 'id_0-invalid-id'
    assert h2.get('id') == '_RWTOC-25'
    assert p.get('aria-labelledby') == 'id_0-invalid-id'
    assert a_tags[0].get('href') == '#id_0-invalid-id'
    assert a_tags[1].get('href') == '#_RWTOC-25'

    # 6. Assertions for Chapter 2 (Cross-File Updates)
    ch2_soup = BeautifulSoup(ch2_path.read_bytes(), 'lxml-xml')
    ch2_a_tags = ch2_soup.find_all('a')
    assert ch2_a_tags[0].get('href') == 'chapter1.xhtml#id_0-invalid-id'
    assert ch2_a_tags[1].get('href') == 'chapter1.xhtml#_RWTOC-25'

    # 7. Assertions for NCX Document (TOC Updates)
    ncx_root = ET.parse(str(ncx_path)).getroot()
    ncx_contents = ncx_root.findall('.//{http://www.daisy.org/z3986/2005/ncx/}content')
    assert ncx_contents[0].get('src') == 'chapter1.xhtml#id_0-invalid-id'
    assert ncx_contents[1].get('src') == 'chapter1.xhtml#_RWTOC-25'
