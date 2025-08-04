
# epub-shrink

A command‑line optimiser that cleans and compresses EPUB files, by dropping unreferenced files and compressing images until a target size in MB is met or image is at 15% quality.

## Quick start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
chmod +x epub-shrink.py
./epub-shrink.py book.epub --targetsize 100 --verbose
```

* Runs lossless clean‑up first, then optional lossy passes until the target size is reached.
* Generates `book-lossless.epub` or book-90c.epub etc alongside the original.
