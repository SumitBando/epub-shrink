import pytest
from bs4 import BeautifulSoup
from epub_shrink import (
    cleanup_meta_and_triggers,
    convert_deprecated_tags,
    convert_deprecated_attrs,
    remove_invalid_data_attrs,
    convert_a_name_to_id,
    validate_uri_schemes,
    handle_deprecated
)

def test_cleanup_meta_and_triggers():
    # Test meta tag removal
    html = '<html><head><meta http-equiv="content-style-type" content="text/css"/><meta charset="utf-8"/></head></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert cleanup_meta_and_triggers(soup) is True
    assert soup.find('meta') is None

    # Test no meta tags to cleanup
    html = '<html><head><meta name="viewport" content="width=device-width"/></head></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert cleanup_meta_and_triggers(soup) is False
    assert soup.find('meta') is not None

    # Test epub:trigger / trigger elements
    html = '<html><body><epub:trigger>Some action</epub:trigger><trigger>Action 2</trigger></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert cleanup_meta_and_triggers(soup) is True
    assert soup.find('epub:trigger') is None
    assert soup.find('trigger') is None


def test_convert_deprecated_tags():
    # Test converting <center> to <div style="text-align: center;">
    html = '<html><body><center>Centered Text</center></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert convert_deprecated_tags(soup) is True
    div = soup.find('div')
    assert div is not None
    assert div.name == 'div'
    assert div['style'] == 'text-align: center;'

    # Test no deprecated tags
    html = '<html><body><div>No deprecated tags here</div></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert convert_deprecated_tags(soup) is False


def test_convert_deprecated_attrs():
    # Test converting align attribute
    html = '<html><body><p align="center">Paragraph</p></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert convert_deprecated_attrs(soup) is True
    p = soup.find('p')
    assert p is not None
    assert p['style'] == 'text-align: center;'
    assert not p.has_attr('align')

    # Test table rules/cellspacing
    html = '<html><body><table cellspacing="0" rules="all"><tr><td>Cell</td></tr></table></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert convert_deprecated_attrs(soup) is True
    table = soup.find('table')
    assert table is not None
    assert 'border-spacing: 0px;' in table['style']
    assert 'border-collapse: collapse;' in table['style']
    assert not table.has_attr('cellspacing')
    assert not table.has_attr('rules')


def test_remove_invalid_data_attrs():
    # Test invalid custom data attribute removal (capital letters or colon)
    html = '<html><body><div data-invalidName="test" data-valid-name="ok" data-ns:name="val">Content</div></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert remove_invalid_data_attrs(soup) is True
    div = soup.find('div')
    assert div.has_attr('data-valid-name') is True
    assert div.has_attr('data-invalidName') is False
    assert div.has_attr('data-ns:name') is False


def test_convert_a_name_to_id():
    # Test converting <a name="..."> to <a id="...">
    html = '<html><body><a name="section1">Link Target</a></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert convert_a_name_to_id(soup) is True
    a = soup.find('a')
    assert a.has_attr('id') is True
    assert a['id'] == 'section1'
    assert a.has_attr('name') is False

    # Test when both name and id are present
    html = '<html><body><a name="section1" id="existing_id">Link Target</a></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert convert_a_name_to_id(soup) is True
    a = soup.find('a')
    assert a['id'] == 'existing_id'
    assert a.has_attr('name') is False


def test_validate_uri_schemes():
    # Test unapproved URI schemes transformed to span
    html = '<html><body><a href="kindle:embed:0001">Kindle Link</a></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert validate_uri_schemes(soup) is True
    span = soup.find('span')
    assert span is not None
    assert not span.has_attr('href')

    # Test approved URI scheme is untouched
    html = '<html><body><a href="https://google.com">Google</a></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert validate_uri_schemes(soup) is False
    a = soup.find('a')
    assert a is not None
    assert a['href'] == 'https://google.com'


def test_handle_deprecated_orchestration():
    # Test that handle_deprecated orchestrates all individual passes
    html = '<html><body><center><p align="center" data-invalidAttr="1"><a name="anchor">Target</a></p></center></body></html>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert handle_deprecated(soup) is True
    
    div = soup.find('div')
    assert div is not None  # center converted to div
    p = soup.find('p')
    assert 'text-align: center;' in p['style']
    assert not p.has_attr('data-invalidAttr')
    a = soup.find('a')
    assert a['id'] == 'anchor'
    assert not a.has_attr('name')
