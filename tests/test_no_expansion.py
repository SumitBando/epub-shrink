import pytest
import tempfile
import pathlib
import shutil
from PIL import Image
from epub_shrink import compress_images, EpubContext

def test_no_expansion_jpeg():
    # Setup mock files
    with tempfile.TemporaryDirectory() as tmpdir:
        root = pathlib.Path(tmpdir)

        # Create a tiny 1x1 JPEG image
        jpg_path = root / "OEBPS" / "images" / "test.jpg"
        jpg_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (1, 1), color="red")
        img.save(jpg_path, format="JPEG", quality=10)

        # Get its original size
        original_size = jpg_path.stat().st_size

        # Setup context
        ctx = EpubContext(
            input_file=pathlib.Path("test.epub"),
            extract_dir=root,
            verbose=True
        )

        # Run compress_images with high quality, which would normally expand a highly-compressed 1x1 image
        jpg_paths = [pathlib.Path("OEBPS") / "images" / "test.jpg"]
        compress_images(ctx, root, 95, jpg_paths, [], [])

        # Check size after compression
        after_size = jpg_path.stat().st_size

        # Ensure it did not expand!
        assert after_size <= original_size

def test_no_expansion_png():
    # Setup mock files
    with tempfile.TemporaryDirectory() as tmpdir:
        root = pathlib.Path(tmpdir)

        # Create a tiny 1x1 PNG image
        png_path = root / "OEBPS" / "images" / "test.png"
        png_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGBA", (1, 1), color="blue")
        img.save(png_path, format="PNG")

        # Get its original size
        original_size = png_path.stat().st_size

        # Setup context
        ctx = EpubContext(
            input_file=pathlib.Path("test.epub"),
            extract_dir=root,
            verbose=True
        )

        # Run compress_images with quality=95
        png_paths = [pathlib.Path("OEBPS") / "images" / "test.png"]
        compress_images(ctx, root, 95, [], png_paths, [])

        # Check size after compression
        after_size = png_path.stat().st_size

        # Ensure it did not expand!
        assert after_size <= original_size
