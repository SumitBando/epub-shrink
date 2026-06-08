import pytest
from lxml import etree as ET
from bs4 import BeautifulSoup, Doctype
import pathlib
import os

def test_dc_meta_removal(tmp_path):
    from epub_shrink import modernize_opf_metadata
    
    ns = {
        'opf': 'http://www.idpf.org/2007/opf',
        'dc': 'http://purl.org/dc/elements/1.1/',
        'dcterms': 'http://purl.org/dc/terms/',
        'xsi': 'http://www.w3.org/2001/XMLSchema-instance'
    }
    
    root = ET.Element('{' + ns['opf'] + '}package')
    metadata = ET.SubElement(root, '{' + ns['opf'] + '}metadata')
    
    # Valid DC elements
    title = ET.SubElement(metadata, '{' + ns['dc'] + '}title')
    title.text = "Test Title"
    date = ET.SubElement(metadata, '{' + ns['dc'] + '}date')
    date.text = "2026-06-08"
    
    # Invalid DC element (dc:meta)
    invalid_dc = ET.SubElement(metadata, '{' + ns['dc'] + '}meta')
    invalid_dc.text = "invalid"
    
    # Standard opf:meta
    opf_meta = ET.SubElement(metadata, '{' + ns['opf'] + '}meta')
    opf_meta.set('property', 'dcterms:modified')
    opf_meta.text = "2026-06-08T00:00:00Z"

    manifest = {}
    opf_path = tmp_path / "content.opf"
    
    modernize_opf_metadata(root, manifest, ns, opf_path, cover_item=None)
    
    # Check that dc:title and dc:date are preserved
    assert root.find('.//dc:title', ns) is not None
    assert root.find('.//dc:date', ns) is not None
    
    # Check that dc:meta has been removed
    assert root.find('.//dc:meta', ns) is None
    
    # Check that opf:meta is present (either updated or preserved)
    assert root.find('.//opf:meta', ns) is not None


def test_xhtml_doctype_modernization(tmp_path):
    from epub_shrink import modernize_html_and_css_files
    
    ns = {
        'opf': 'http://www.idpf.org/2007/opf'
    }
    
    # Setup mock file with XHTML 1.0 Strict DOCTYPE
    ch_content = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head>
<body><p>Hello World</p></body>
</html>"""
    
    ch_path = tmp_path / "chapter1.xhtml"
    ch_path.write_text(ch_content, encoding="utf-8")
    
    manifest_item = ET.Element('{' + ns['opf'] + '}item', attrib={
        'id': 'ch1',
        'href': 'chapter1.xhtml',
        'media-type': 'application/xhtml+xml'
    })
    
    manifest = {
        'chapter1.xhtml': manifest_item
    }
    
    modernize_html_and_css_files(tmp_path, manifest, ncx_path=None)
    
    # Read modified file and verify DOCTYPE was modernized to HTML5
    updated_content = ch_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in updated_content
    assert "-//W3C//DTD XHTML 1.0 Strict//EN" not in updated_content


def test_ncx_identifier_sync(tmp_path):
    from epub_shrink import modernize_ncx_and_tours
    
    ns = {
        'opf': 'http://www.idpf.org/2007/opf',
        'dc': 'http://purl.org/dc/elements/1.1/'
    }
    
    # Setup mock OPF package element with unique-identifier attribute
    opf_root = ET.Element('{' + ns['opf'] + '}package', attrib={'unique-identifier': 'bookid'})
    metadata = ET.SubElement(opf_root, '{' + ns['opf'] + '}metadata')
    
    dc_id = ET.SubElement(metadata, '{' + ns['dc'] + '}identifier', attrib={'id': 'bookid'})
    dc_id.text = "urn:uuid:test-sync-12345"
    
    # Setup mock toc.ncx with mismatched dtb:uid
    ncx_content = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
   <head>
      <meta name="dtb:uid" content="mismatched-bookid"/>
   </head>
   <docTitle><text>Test</text></docTitle>
   <navMap></navMap>
</ncx>"""
    
    ncx_path = tmp_path / "toc.ncx"
    ncx_path.write_text(ncx_content, encoding="utf-8")
    
    # Call modernization
    modernize_ncx_and_tours(tmp_path, opf_root, ns)
    
    # Verify NCX dtb:uid was updated to match the unique OPF identifier
    updated_ncx = ncx_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(updated_ncx, 'lxml-xml')
    meta = soup.find('meta', attrs={'name': 'dtb:uid'})
    assert meta is not None
    assert meta.get('content') == "urn:uuid:test-sync-12345"
