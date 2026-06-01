import pytest
import tempfile
import pathlib
from bs4 import BeautifulSoup
from lxml import etree as ET
from epub_shrink import (
    modernize_ncx_and_tours,
    modernize_cover_image_id,
    ensure_epub3_navigation,
    modernize_html_and_css_files,
    inject_ul_disc_css,
    modernize_opf_metadata,
    standardize_manifest_media_types,
    ensure_nonlinear_reachable,
    modernize_assets
)

NS = {
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/'
}

def test_modernize_ncx_and_tours():
    opf_xml = """<package xmlns="http://www.idpf.org/2007/opf">
        <tours/>
    </package>"""
    root = ET.fromstring(opf_xml)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # fix_ncx won't find ncx, returns None
        ncx_path = modernize_ncx_and_tours(pathlib.Path(tmpdir), root, NS)
        assert ncx_path is None
        assert root.find('opf:tours', NS) is None


def test_modernize_cover_image_id():
    # Cover image ID modernized from 'cover-img' to 'cover'
    opf_xml = """<package xmlns="http://www.idpf.org/2007/opf">
        <metadata>
            <meta name="cover" content="cover-img"/>
        </metadata>
    </package>"""
    root = ET.fromstring(opf_xml)
    
    manifest_item = ET.Element('{http://www.idpf.org/2007/opf}item')
    manifest_item.set('id', 'cover-img')
    manifest_item.set('href', 'images/cover.jpg')
    manifest = {'images/cover.jpg': manifest_item}
    
    cover_item = modernize_cover_image_id(root, manifest, NS)
    assert cover_item is not None
    assert cover_item.get('id') == 'cover'
    assert 'cover-image' in cover_item.get('properties').split()


def test_ensure_epub3_navigation():
    # If no nav exists but ncx exists, generate_nav_from_ncx is skipped if ncx_path is None
    # Let's test with a fake ncx_path that exists or is mock-like
    opf_xml = """<package xmlns="http://www.idpf.org/2007/opf"><manifest/></package>"""
    root = ET.fromstring(opf_xml)
    manifest = {}
    
    with tempfile.TemporaryDirectory() as tmpdir:
        opf_dir = pathlib.Path(tmpdir)
        ncx_path = opf_dir / 'toc.ncx'
        ncx_path.write_text("""<?xml version="1.0" encoding="utf-8"?>
        <ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
            <navMap>
                <navPoint id="p1" playOrder="1">
                    <navLabel><text>Chapter 1</text></navLabel>
                    <content src="chapter1.xhtml"/>
                </navPoint>
            </navMap>
        </ncx>""", encoding='utf-8')
        
        ensure_epub3_navigation(root, manifest, NS, opf_dir, ncx_path)
        
        assert 'nav.xhtml' in manifest
        assert manifest['nav.xhtml'].get('id') == 'nav'
        assert manifest['nav.xhtml'].get('properties') == 'nav'
        assert (opf_dir / 'nav.xhtml').exists()


def test_modernize_html_and_css_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        opf_dir = pathlib.Path(tmpdir)
        html_file = opf_dir / 'chapter1.xhtml'
        html_file.write_text("""<html><body>
            <ul type="disc"><li>Item</li></ul>
            <p id="invalid id">Text</p>
        </body></html>""", encoding='utf-8')
        
        manifest_item = ET.Element('{http://www.idpf.org/2007/opf}item')
        manifest_item.set('id', 'chapter1')
        manifest_item.set('media-type', 'application/xhtml+xml')
        manifest = {'chapter1.xhtml': manifest_item}
        
        ul_disc_needed = modernize_html_and_css_files(opf_dir, manifest, None)
        
        assert ul_disc_needed is True
        
        soup = BeautifulSoup(html_file.read_bytes(), 'lxml-xml')
        ul = soup.find('ul')
        assert ul.get('class') == '_ul_disc'
        assert not ul.has_attr('type')
        
        p = soup.find('p')
        assert p.get('id') == 'invalid_id'


def test_inject_ul_disc_css():
    with tempfile.TemporaryDirectory() as tmpdir:
        opf_dir = pathlib.Path(tmpdir)
        css_file = opf_dir / 'style.css'
        css_file.write_text('body { color: black; }', encoding='utf-8')
        
        manifest_item = ET.Element('{http://www.idpf.org/2007/opf}item')
        manifest_item.set('id', 'style')
        manifest_item.set('media-type', 'text/css')
        manifest = {'style.css': manifest_item}
        
        inject_ul_disc_css(opf_dir, manifest)
        
        content = css_file.read_text(encoding='utf-8')
        assert '._ul_disc { list-style-type: disc; }' in content


def test_modernize_opf_metadata():
    opf_xml = """<package xmlns="http://www.idpf.org/2007/opf">
        <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
            <dc:title>Test Title</dc:title>
            <dc:description>   </dc:description> <!-- Empty description, should be removed -->
            <meta name="cover" content="cover-img"/>
            <meta property="dcterms:modified">2020-01-01T00:00:00Z</meta>
        </metadata>
    </package>"""
    root = ET.fromstring(opf_xml)
    manifest = {}
    
    cover_item = ET.Element('{http://www.idpf.org/2007/opf}item')
    cover_item.set('id', 'cover')
    cover_item.set('properties', 'cover-image')
    
    modernize_opf_metadata(root, manifest, NS, pathlib.Path('test.opf'), cover_item)
    
    metadata = root.find('opf:metadata', NS)
    # Check that empty description is removed
    assert metadata.find('dc:description', NS) is None
    # Check that title is preserved
    assert metadata.find('dc:title', NS) is not None
    # Check that dcterms:modified is updated (there should be only one modified tag)
    mods = metadata.findall('.//opf:meta[@property="dcterms:modified"]', NS)
    assert len(mods) == 1
    assert mods[0].text is not None


def test_standardize_manifest_media_types():
    manifest_item = ET.Element('{http://www.idpf.org/2007/opf}item')
    manifest_item.set('id', 'font')
    manifest_item.set('href', 'font.ttf')
    manifest_item.set('media-type', 'application/x-font-truetype')
    manifest = {'font.ttf': manifest_item}
    
    standardize_manifest_media_types(manifest)
    assert manifest_item.get('media-type') == 'application/vnd.ms-opentype'


def test_ensure_nonlinear_reachable():
    opf_xml = """<package xmlns="http://www.idpf.org/2007/opf">
        <spine>
            <itemref idref="non-linear-doc" linear="no"/>
        </spine>
    </package>"""
    root = ET.fromstring(opf_xml)
    
    doc_item = ET.Element('{http://www.idpf.org/2007/opf}item')
    doc_item.set('id', 'non-linear-doc')
    doc_item.set('href', 'chapter-non-linear.xhtml')
    
    nav_item = ET.Element('{http://www.idpf.org/2007/opf}item')
    nav_item.set('id', 'nav')
    nav_item.set('href', 'nav.xhtml')
    nav_item.set('properties', 'nav')
    
    manifest = {
        'chapter-non-linear.xhtml': doc_item,
        'nav.xhtml': nav_item
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        opf_dir = pathlib.Path(tmpdir)
        nav_file = opf_dir / 'nav.xhtml'
        nav_file.write_text("""<html><body><nav id="toc"><h1>TOC</h1></nav></body></html>""", encoding='utf-8')
        
        ensure_nonlinear_reachable(root, manifest, NS, opf_dir)
        
        soup = BeautifulSoup(nav_file.read_bytes(), 'lxml-xml')
        hidden_div = soup.find(id="hidden-reachability-links")
        assert hidden_div is not None
        assert hidden_div.a['href'] == 'chapter-non-linear.xhtml'
