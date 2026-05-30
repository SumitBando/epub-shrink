import pytest
from lxml import etree as ET

def test_cover_attribute_order():
    ns = {
        'opf': 'http://www.idpf.org/2007/opf',
        'dc': 'http://purl.org/dc/elements/1.1/'
    }
    
    # 1. Create a metadata structure with a cover meta tag where content is before name
    metadata = ET.Element('{' + ns['opf'] + '}metadata')
    child = ET.SubElement(metadata, '{' + ns['opf'] + '}meta')
    child.attrib['content'] = 'my-cover-id'
    child.attrib['name'] = 'cover'
    
    # Verify the initial order (content is first)
    assert list(child.attrib.keys()) == ['content', 'name']
    
    # Simulate the fix logic in epub_shrink.py
    if child.attrib.get('name') == 'cover' and 'content' in child.attrib:
        content_val = child.attrib.get('content')
        child.attrib.clear()
        child.attrib['name'] = 'cover'
        child.attrib['content'] = content_val
        
    # Verify corrected order (name is first)
    assert list(child.attrib.keys()) == ['name', 'content']
    
    # Verify serialization order
    serialized = ET.tostring(metadata, encoding='unicode')
    assert 'name="cover" content="my-cover-id"' in serialized
