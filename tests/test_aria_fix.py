import pytest
from bs4 import BeautifulSoup

def fix_aria(soup):
    modified = False
    for attr in ['aria-labelledby', 'aria-describedby']:
        for tag in soup.find_all(attrs={attr: True}):
            target_ids = tag[attr].split()
            if not target_ids:
                del tag[attr]
                modified = True
                continue

            valid_ids = [tid for tid in target_ids if soup.find(id=tid)]
            if len(valid_ids) != len(target_ids):
                if not valid_ids:
                    del tag[attr]
                else:
                    tag[attr] = " ".join(valid_ids)
                modified = True
    return modified

def test_aria_fix_missing_id():
    html = '<div aria-labelledby="missing"></div>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert fix_aria(soup) is True
    assert 'aria-labelledby' not in soup.div.attrs

def test_aria_fix_empty_id():
    html = '<div aria-labelledby=""></div>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert fix_aria(soup) is True
    assert 'aria-labelledby' not in soup.div.attrs

def test_aria_fix_mixed_ids():
    html = '<root><div id="valid"></div><div aria-labelledby="valid missing"></div></root>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert fix_aria(soup) is True
    assert soup.find(attrs={'aria-labelledby': True})['aria-labelledby'] == 'valid'

def test_aria_fix_valid_id():
    html = '<root><div id="valid"></div><div aria-labelledby="valid"></div></root>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert fix_aria(soup) is False
    assert soup.find(attrs={'aria-labelledby': True})['aria-labelledby'] == 'valid'

def test_aria_describedby():
    html = '<div aria-describedby="missing"></div>'
    soup = BeautifulSoup(html, 'lxml-xml')
    assert fix_aria(soup) is True
    assert 'aria-describedby' not in soup.div.attrs
