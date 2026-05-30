import pytest
from bs4 import BeautifulSoup

def test_custom_data_attributes_cleanup():
    from epub_shrink import handle_deprecated, is_invalid_custom_data_attribute
    
    # Direct tests of the validation helper
    assert is_invalid_custom_data_attribute('data-AmznRemoved-M8') is True
    assert is_invalid_custom_data_attribute('data-') is True
    assert is_invalid_custom_data_attribute('data-foo:bar') is True
    assert is_invalid_custom_data_attribute('data-foo#bar') is True
    assert is_invalid_custom_data_attribute('data-valid') is False
    assert is_invalid_custom_data_attribute('data-valid-123.test') is False
    assert is_invalid_custom_data_attribute('class') is False

    # 1. Invalid custom data attribute with uppercase letters
    html = '<div data-AmznRemoved-M8="1" data-valid="yes"></div>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert handle_deprecated(soup) is True
    assert 'data-AmznRemoved-M8' not in soup.div.attrs
    assert soup.div.attrs.get('data-valid') == 'yes'

    # 2. Invalid custom data attribute with no characters after hyphen
    html = '<div data-="1" data-valid="yes"></div>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert handle_deprecated(soup) is True
    assert 'data-' not in soup.div.attrs
    assert soup.div.attrs.get('data-valid') == 'yes'

    # 3. Invalid custom data attribute with colon (using declared namespace so it parses)
    html = '<div xmlns:data-foo="http://example.com" data-foo:bar="1" data-valid="yes"></div>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert handle_deprecated(soup) is True
    assert 'data-foo:bar' not in soup.div.attrs
    assert soup.div.attrs.get('data-valid') == 'yes'

    # 4. Valid custom data attributes (all lowercase, no invalid characters, starts with data-)
    html = '<div data-valid="yes" data-another-123.valid="ok"></div>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert handle_deprecated(soup) is False
    assert soup.div.attrs.get('data-valid') == 'yes'
    assert soup.div.attrs.get('data-another-123.valid') == 'ok'
