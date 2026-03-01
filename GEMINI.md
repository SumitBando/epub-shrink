# Requirements
The `epub-shrink.py` script is a command-line tool for reducing the file size and modernizing EPUB files.
It achieves this by:
- **Removing unreferenced assets:** It performs deep analysis of the EPUB structure (OPF, NCX, HTML, CSS) to identify and remove unused images, fonts, and stylesheets.
- **Modernizing content:** 
    - Upgrades EPUB version to 3.0.
    - Generates EPUB 3 navigation (`nav.xhtml`) from EPUB 2 NCX if missing.
    - Converts deprecated HTML tags (e.g., `<center>`, `<font>`) and attributes (e.g., `align`, `bgcolor`) to modern CSS.
    - Standardizes font media types and cleans up metadata (e.g., `dcterms:modified`).
- **Compressing images:** Supports JPEG, PNG, and WebP.
    - **Lossless:** Uses `jpegoptim` and `oxipng` for maximum lossless optimization.
    - **Lossy:** Uses `PIL` for quality reduction (JPEG/WebP) and palette-based reduction for PNGs.
- **Targeted size reduction:** Iteratively reduces image quality based on the file size ratio to reach a specific target MB, stopping at a quality floor (15).
- **File purging:** Automatically removes common junk (e.g., `.DS_Store`, `*cross-sale*`) and allows user-specified glob patterns.

## Features
- Input an EPUB file.
  Generates `book-lossless.epub` or `book-q90.epub` etc. alongside the original.
- Specify an output file path. If not specified, uses input directory with suffix.
- Set initial image compression quality (0-100), default 100 (lossless).
- Define a target size in MB (e.g., `-t 99`). The script will shrink until the target is met.
- Provide glob patterns for files to be purged (e.g., `-i "*.woff"`).
- Verbose mode (`-v`) for detailed logs of file analysis and compression savings.

## Dependencies
- Python 3
- `jpegoptim` for JPEG optimization
- `oxipng` for lossless PNG compression
- `pngquant` for lossy PNG optimization (checked but PIL currently preferred for palette conversion)
- Python libraries from `requirements.txt`: `lxml`, `Pillow`, `beautifulsoup4`, `tinycss2`, `tqdm`

Use global variables when appropriate.
Do not copy global variables to locals unnecessarily.

Always verify that your proposed change compiles.

- When working on a task, do not make assumptions. If the requirement is not clear, ask questions.
- When implementing a task, if there are multiple compelling options, ask.
- After task is completed in TODO.md, mark it and move to the top of list of # Completed tasks.
- Do not automatically start on pending next task in the list. Wait for me to accept the current task and check it in.

# Testing
To smoke test, run the script `epub-shrink.py` with a test file:
```bash
source venv/bin/activate && python epub-shrink.py tests/Songs.epub
```
Running on `tests/Songs.epub`, the following files should be dropped:
- `../promo.css`
- `../xpromo.xhtml`
- `../xpromo.css`
- `Literata-Regular.ttf`
- `1724456391785279984_img44.jpg`

Do not delete the test file.
