from lxml import etree as ET
from epub_shrink import EpubContext, remove_unreferenced

def test_reference_robustness(tmp_path):
    ns = {
        'opf': 'http://www.idpf.org/2007/opf',
    }

    # Setup mock OPF XML structure
    root = ET.Element('{' + ns['opf'] + '}package', attrib={'version': '3.0'})
    manifest_node = ET.SubElement(root, '{' + ns['opf'] + '}manifest')

    # Manifest items
    # 1. nav.htm (entry point - testing .htm extension)
    nav_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={
        'id': 'nav',
        'href': 'nav.htm',
        'media-type': 'application/xhtml+xml',
        'properties': 'nav'
    })

    # 2. page.custom (testing non-standard extension but matching media-type)
    page_custom_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={
        'id': 'page-custom',
        'href': 'page.custom',
        'media-type': 'application/xhtml+xml'
    })

    # 3. URL-encoded image (with space) in manifest: "images/my%20image.jpg"
    img_spaced_href = "images/my%20image.jpg"
    img_spaced_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={
        'id': 'img-spaced',
        'href': img_spaced_href,
        'media-type': 'image/jpeg'
    })

    # 4. SVG image referenced via xlink:href: "images/svg-image.jpg"
    svg_img_href = "images/svg-image.jpg"
    svg_img_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={
        'id': 'svg-img',
        'href': svg_img_href,
        'media-type': 'image/jpeg'
    })

    # 5. Image in a <style> tag: "images/bg.jpg"
    bg_img_href = "images/bg.jpg"
    bg_img_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={
        'id': 'bg-img',
        'href': bg_img_href,
        'media-type': 'image/jpeg'
    })

    # 6. Video poster image: "images/poster.jpg"
    poster_img_href = "images/poster.jpg"
    poster_img_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={
        'id': 'poster-img',
        'href': poster_img_href,
        'media-type': 'image/jpeg'
    })

    # 7. Unreferenced image that should be dropped
    unref_img_href = "images/unreferenced.jpg"
    unref_img_item = ET.SubElement(manifest_node, '{' + ns['opf'] + '}item', attrib={
        'id': 'unref-img',
        'href': unref_img_href,
        'media-type': 'image/jpeg'
    })

    # Spine
    spine = ET.SubElement(root, '{' + ns['opf'] + '}spine')
    ET.SubElement(spine, '{' + ns['opf'] + '}itemref', attrib={'idref': 'nav'})

    # Write files to disk
    content_dir = tmp_path / "OEBPS"
    content_dir.mkdir(parents=True, exist_ok=True)
    (content_dir / "images").mkdir(exist_ok=True)

    # nav.htm points to page.custom
    nav_html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <a href="page.custom">link</a>
  </body>
</html>
"""
    (content_dir / "nav.htm").write_text(nav_html, encoding="utf-8")

    # page.custom contains references to the other assets
    page_html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:xlink="http://www.w3.org/1999/xlink">
  <head>
    <style type="text/css">
      body {
        background-image: url('images/bg.jpg');
      }
    </style>
  </head>
  <body>
    <img src="images/my image.jpg" alt="spaced" />

    <svg width="100" height="100">
      <image xlink:href="images/svg-image.jpg" width="100" height="100" />
    </svg>

    <video poster="images/poster.jpg" width="320" height="240">
      <source src="movie.mp4" type="video/mp4" />
    </video>
  </body>
</html>
"""
    (content_dir / "page.custom").write_text(page_html, encoding="utf-8")

    # Write the assets on disk (their names on disk are unquoted)
    (content_dir / "images" / "my image.jpg").write_text("dummy", encoding="utf-8")
    (content_dir / "images" / "svg-image.jpg").write_text("dummy", encoding="utf-8")
    (content_dir / "images" / "bg.jpg").write_text("dummy", encoding="utf-8")
    (content_dir / "images" / "poster.jpg").write_text("dummy", encoding="utf-8")
    (content_dir / "images" / "unreferenced.jpg").write_text("dummy", encoding="utf-8")

    # In-memory manifest dictionary mapping href -> XML element
    manifest = {
        'nav.htm': nav_item,
        'page.custom': page_custom_item,
        img_spaced_href: img_spaced_item,
        svg_img_href: svg_img_item,
        bg_img_href: bg_img_item,
        poster_img_href: poster_img_item,
        unref_img_href: unref_img_item,
    }

    tree = ET.ElementTree(root)
    ctx = EpubContext(
        input_file=tmp_path / "dummy.epub",
        extract_dir=tmp_path,
        verbose=True
    )

    # Execute remove_unreferenced
    remove_unreferenced(ctx, manifest, tree, ns, tmp_path, content_dir=content_dir, show_summary=True)

    # Assertions
    # The referenced images must be kept
    assert 'nav.htm' in manifest
    assert 'page.custom' in manifest
    assert img_spaced_href in manifest
    assert svg_img_href in manifest
    assert bg_img_href in manifest
    assert poster_img_href in manifest

    # The unreferenced image must be dropped
    assert unref_img_href not in manifest

    # Check disk presence
    assert (content_dir / "images" / "my image.jpg").exists()
    assert (content_dir / "images" / "svg-image.jpg").exists()
    assert (content_dir / "images" / "bg.jpg").exists()
    assert (content_dir / "images" / "poster.jpg").exists()
    assert not (content_dir / "images" / "unreferenced.jpg").exists()
