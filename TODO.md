- when compressing to a target size, instead of compressing images by 5% more every time, use the initial size against the target size to guess required compression. E.g. if the original is more than 2x the target, start at 80% quality; if output turns out to be still more than 2x, then try next at 60% quality. However, if the previous output was within 100% excess, try reducing by 10% steps. If the previous failed was within 50%, try reducing quality by another 5%. E.g. a sequence may be q80, q60, q50, q40, q35. 

- since we are looking at the image statistics and know image estimated quality, it does not make sense to start the compression loop at higher than the existing estimated quality

- merge rebind.py

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
- [x] research and improve oxipng invocation, e.g. one suggestion was to change args:
  oxipng_args = [
      "oxipng", 
      "-o", "max", 
      "--strip", "all", 
      "--alpha", 
      "--threads", "4"
  ]
- [x] font files referenced from style should not be purged (also handles quoted url() and background images)
- [x] when showing image count summary, also display size per type, e.g. change
  Found 4 JPEG files, 0 PNG files, and 0 WebP files
  to
  Found 4 JPEG / 20MB, 1 PNG / 145KB and 0 WebP files


- [x] when iteratively compressing images in a file, dont show file statistics every iteration, show it only on first inspection,
  e.g. avoid showing multiple times: Found 225 JPEG files, 43 PNG files, and 0 WebP files

