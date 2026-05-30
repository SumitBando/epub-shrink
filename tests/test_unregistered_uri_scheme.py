import pytest
from bs4 import BeautifulSoup

def test_unregistered_uri_schemes():
    from epub_shrink import handle_deprecated
    
    # Test cases that should not be touched (valid links)
    valid_links = [
        '<a href="http://example.com">HTTP</a>',
        '<a href="https://example.com">HTTPS</a>',
        '<a href="mailto:user@example.com">Mail</a>',
        '<a href="tel:+12345678">Tel</a>',
        '<a href="data:image/png;base64,iVBORw0KGgoAAAANS">Data</a>',
        '<a href="urn:uuid:f62cba8d-1899-493f-bad3-f94d30ec7641">URN</a>',
        '<a href="#footnote-1">Fragment</a>',
        '<a href="chapter1.xhtml">Relative File</a>',
        '<a href="../images/cover.jpg">Relative Image</a>',
        '<a href="chapter2.xhtml#sec-2.1">Relative File with Fragment</a>',
    ]
    
    for html in valid_links:
        soup = BeautifulSoup(html, 'lxml-xml')
        assert handle_deprecated(soup) is False
        assert soup.a is not None
        assert soup.a.name == 'a'
        assert soup.a.has_attr('href') is True

    # Test cases that should be transformed to span (unregistered schemes)
    invalid_links = [
        ('<a href="kindle:embed:0002?mime=image/jpg" class="toc-front" id="cover"><span class="red">Cover</span></a>',
         '<span class="toc-front" id="cover"><span class="red">Cover</span></span>'),
        ('<a href="epub-embed:0002">Embed</a>',
         '<span>Embed</span>'),
        ('<a href="amzn:to-something" class="promo">Promo</a>',
         '<span class="promo">Promo</span>'),
        ('<a href="calibre:query" id="cal">Calibre</a>',
         '<span id="cal">Calibre</span>'),
    ]

    for html, expected in invalid_links:
        soup = BeautifulSoup(html, 'lxml-xml')
        assert handle_deprecated(soup) is True
        element = soup.find('span')
        assert element is not None
        assert element.name == 'span'
        assert element.has_attr('href') is False
        if 'class' in html:
            assert 'toc-front' in element['class'] or 'promo' in element['class']
        if 'id' in html:
            assert element['id'] in ['cover', 'cal']
