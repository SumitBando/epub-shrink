- remove non-linear items from the spine if there are truly no references to them from any linear items, navigation, or the table of contents

- The cover image has an id != "cover". Renaming to work around bug in Nook Color

Explanation https://www.perplexity.ai/search/explain-the-powershell-script-DfzSO_cRQbam2gU8d6Xuew


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
