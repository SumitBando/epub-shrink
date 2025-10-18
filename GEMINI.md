# Requirements
The `epub-shrink.py` script is a command-line tool for reducing the file size of EPUB files. It achieves this by:

- **Removing unreferenced assets:** It analyzes the EPUB's content and removes any files (like images, stylesheets, etc.) that are not referenced in the EPUB's structure.
- **Compressing images:** It uses external tools `jpegoptim` and `oxipng` to compress JPEG and PNG images. It supports both lossless and lossy compression.
- **Targeted size reduction:** It can iteratively reduce image quality to reach a specific target file size in megabytes.
- **File purging:** It allows users to specify additional file patterns to be removed from the EPUB.

## Features
- Input an EPUB file.
- Specify an output file path.
- Set image compression quality (0-100).
- Define a target size in MB for the output file.
- Provide glob patterns for files to be purged.
- Verbose mode for detailed output.

## Dependencies
- Python 3
- `jpegoptim` command-line tool
- `oxipng` command-line tool
- Python libraries from `requirements.txt`

Use global variables when appropriate.
Do not copy global variables to locals unnecessarily.

Always verify that your proposed change compiles.

# Testing
To smoke test, run the script epub-shrink.py with a test file:
```bash
source venv/bin/activate && python epub-shrink.py Songs.epub
```
Running on Songs.epub, following files should be dropped:
- ../promo.css
- ../xpromo.xhtml
- ../xpromo.css
- Literata-Regular.ttf
- 1724456391785279984_img44.jpg

The font file Bookerly.ttf should not be dropped, as it is referened via the style file 0.css.

Do not attempt to delete the test file.