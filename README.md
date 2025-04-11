
# epub-shrink

A command‑line optimiser that cleans and compresses EPUB files.

## Quick start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
chmod +x epub-shrink.py
./epub-shrink.py book.epub --targetsize 500 --verbose
```

* Runs lossless clean‑up first, then optional lossy passes until the target size is reached.
* Generates `book-min.epub` alongside the original.
