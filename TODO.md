[TODO] WARNING: The file OEBPS/page-template.xpgt has a MIME type that does not match its extension    [OEBPS/theworld.opf]

[TODO] 
WARNING: Invalid id: _RWTOC-25    [OEBPS/theworld_ack.html:11]
WARNING: Invalid id: _RWTOC-32    [OEBPS/theworld_adc01.html:11]
WARNING: Invalid id: _RWTOC-31    [OEBPS/theworld_ata.html:11]
WARNING: Invalid id: _RWTOC-1    [OEBPS/theworld_ch01.html:11]
WARNING: Invalid id: _RWTOC-5    [OEBPS/theworld_ch03.html:11]
WARNING: Invalid id: _RWTOC-7    [OEBPS/theworld_ch04.html:11]
WARNING: Invalid id: _RWTOC-9    [OEBPS/theworld_ch05.html:11]
WARNING: Invalid id: _RWTOC-10    [OEBPS/theworld_ch05.html:12]
WARNING: Invalid id: _RWTOC-11    [OEBPS/theworld_ch06.html:11]
WARNING: Invalid id: _RWTOC-15    [OEBPS/theworld_ch08.html:11]
WARNING: Invalid id: _RWTOC-16    [OEBPS/theworld_ch08.html:12]
WARNING: Invalid id: _RWTOC-19    [OEBPS/theworld_ch10.html:11]
WARNING: Invalid id: _RWTOC-23    [OEBPS/theworld_ch12.html:11]
WARNING: Invalid id: 0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0000.html:9]
WARNING: Invalid id: 1T140-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0002_split_000.html:9]
WARNING: Invalid id: 1T140-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0002_split_001.html:9]
WARNING: Invalid id: 5N3C0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0006.html:9]
WARNING: Invalid id: 9H5K0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0010.html:9]
WARNING: Invalid id: 1BRPS0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0046.html:9]
WARNING: Invalid id: 22O7C0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0070.html:9]
WARNING: Invalid id: 2LQIK0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0090.html:9]
WARNING: Invalid id: 3APV00-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0112.html:9]
WARNING: Invalid id: 3VPBC0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0134.html:9]
WARNING: Invalid id: 41MCG0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0136.html:9]
WARNING: Invalid id: 4LN8A0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0157.html:9]
WARNING: Invalid id: 55U1S0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0174.html:9]
WARNING: Invalid id: 5N3C00-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0192.html:9]
WARNING: Invalid id: 5SQFC0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0198.html:9]
WARNING: Invalid id: 5TOVU0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0199.html:9]
WARNING: Invalid id: 5UNGG0-5ee58a0d1c3149459c0c569fcfce5b24    [text/part0200.html:9]


# Fix RSC-005: Nested `<a>` tags (`<a>` elements must not appear inside `<a>` elements). (Include a test case).

# Fix validate image files
[TODO] ERROR: Invalid image: cannot identify image file <_io.BytesIO object at 0x000001A55E134360>    [EPUB/images/Federica_Bocco_Headshot.jpg]


[TODO] After processing still has:
ERROR: Unexpected unknown property "font-weigth"    [OEBPS/pdlmsr.css:242]



- Check https://github.com/karpathy/reader3/blob/master/reader3.py

- BUG when purging an item like         "SS_recommendpage*", remove from nav

- remove Z-library from file name

- remove non-linear items from the spine if there are truly no references to them from any linear items, navigation, or the table of contents

- The cover image has an id != "cover". Renaming to work around bug in Nook Color

- Explanation https://www.perplexity.ai/search/explain-the-powershell-script-DfzSO_cRQbam2gU8d6Xuew


- The <guide> element was the primary suspect because it is a legacy feature from the older EPUB 2 standard that is deprecated in EPUB
  3.

  Here's the breakdown:


   1. Mixed Signals: The sabai-bad.epub file was an EPUB 3 file, but it included the <guide> section, which is an EPUB 2 feature. This
      sends conflicting information to the reading system (like Google Play Books).


   2. Conflicting Cover Definitions:
       * EPUB 3 (the modern way): Defines the cover image in the <manifest> section using an item with the properties="cover-image"
         attribute. The bad file had this correct entry.
       * EPUB 2 (the old way): Used the <guide> section to point to the cover file, among other things (like the table of contents,
         start page, etc.).


  By including the old <guide> section in a new EPUB 3 file, the book was essentially telling the reader, "Here are two different
  ways to find my important parts." This ambiguity can confuse reading systems, causing them to fail to render the cover correctly.


  Removing the <guide> section eliminated this conflict, forcing the reader to use the modern, unambiguous EPUB 3 method to identify
  the cover image.
- Check repo https://github.com/martinus/epuboptim

# Completed tasks
- [x] Fix Calibre/Epubcheck validation error: "The meta cover tag has content before name" [OEBPS/theworld.opf:12]. epub-shrink.py now automatically intercepts `<meta name="cover">` and corrects the attribute order (name before content) during package modernization.
- [x] Fix Google Play Books missing cover issue. When modernizing EPUB files, automatically ensure that the legacy EPUB 2 cover metadata tag (`<meta name="cover" content="[cover_id]" />`) is added to the package document if a cover-image manifest item exists. This guarantees that Google Play Books can successfully extract and display the cover thumbnail.
- [x] Fix HTM-025: Non-registered URI scheme type found in href (e.g., `kindle:embed:` in `<li class="toc-front" id="cover" value="1"><a href="kindle:embed:0002?mime=image/jpg">`). Unregistered schemes are converted to `<span>` tags during asset modernization to maintain styling while removing validation errors.

