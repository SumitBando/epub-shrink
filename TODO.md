[TODO] Cleanup: Remove unused `fonttools` dependency from pyproject.toml (declared but never imported)

# Fix RSC-005: Nested `<a>` tags (`<a>` elements must not appear inside `<a>` elements). (Include a test case).

# Fix validate image files
[TODO] ERROR: Invalid image: cannot identify image file <_io.BytesIO object at 0x000001A55E134360>    [EPUB/images/Federica_Bocco_Headshot.jpg]

Do we have to check is_valid_xml_id() before sanitize_xml_id()

[TODO] After processing still has:
ERROR: Unexpected unknown property "font-weigth"    [OEBPS/pdlmsr.css:242]


[TODO] Refactor: Split epub_shrink.py into modules — extract.py, purge.py, images.py, reference.py, modernize/ (cover.py, navigation.py, html.py, metadata.py, manifest.py)


- Check https://github.com/karpathy/reader3/blob/master/reader3.py

- BUG when purging an item like         "SS_recommendpage*", remove from nav

- remove Z-library from file name

- remove non-linear items from the spine if there are truly no references to them from any linear items, navigation, or the table of contents

- Explanation https://www.perplexity.ai/search/explain-the-powershell-script-DfzSO_cRQbam2gU8d6Xuew

# Completed tasks
- [x] Optimize: Ensure compressed image files are strictly smaller than their original counterparts, reverting to the original file if compression results in a larger file size.
- [x] Optimize: Tune adaptive image quality estimation using size-weighted average image quality and implement a step-up quality refinement pass (interpolating/searching back up after overshooting) to hit the target size as closely as possible.
- [x] Refactor: Split modernize_assets() god-function (~315 lines, 8 sub-steps) into separate callable functions
- [x] Refactor: Split handle_deprecated() god-function — separate deprecated tags, deprecated attrs, invalid data-attrs, `<a name>` → `<a id>`, URI scheme validation, and `<meta>`/`<epub:trigger>` cleanup into individual functions
- [x] Optimize: Improve dynamic image quality reduction algorithm inside epub_shrink.py using a Secant Method (linear interpolation) to mathematically estimate the quality q required to hit the target MB, with safety clamps (strict quality decreases, step caps) and robust fallbacks.
- [x] Fix Invalid ID attributes: Automatically sanitize and correct all invalid XML/HTML ID attributes inside EPUB assets (stripping spaces/nbsp, replacing invalid chars with underscores, prepending 'id_' to digit-starts) and dynamically re-link all internal local and cross-chapter referencing links (including NCX toc and NAV maps) to maintain 100% link integrity.
- [x] Refactor: Fix purge_unwanted_files() not updating the in-memory manifest dict (main() has to refresh it separately). Updated purge_unwanted_files() and remove_asset() to directly mutate the in-memory manifest dictionary, eliminating the redundant XML re-query and rebuild in main() entirely.
- [x] Refactor: pngquant is checked at startup but not used. Integrated pngquant into the lossy PNG compression pipeline to achieve high-quality quantization (with alpha transparency preserved) and significantly smaller file sizes, falling back to Pillow's basic adaptive palette conversion if pngquant is not available.
- [x] Refactor: Consolidate remove_from_spine(), remove_from_manifest(), and remove_file() into a single operation (they each independently search the manifest). Unified the three standalone functions into a single consolidated `remove_asset` helper, performing a single manifest lookup to resolve ID, update the spine XML, update the manifest XML, and remove the file from disk cleanly.
- [x] Refactor: Replace 7 GLOBAL_* variables with an EpubContext dataclass threaded through the pipeline. Replaced the 8 GLOBAL_* variables (both used and unused dead code) with an EpubContext dataclass threaded cleanly through all pipeline functions (unzip, load_opf, analyze_file, purge_unwanted_files, remove_unreferenced, analyze_images, compress_images, analyze_image_quality, and prune_unreferenced_assets).
- [x] Fix Calibre warning: "The cover image has an id != \"cover\". Renaming to work around bug in Nook Color". epub-shrink.py now automatically renames the cover image ID to "cover" during metadata modernization, resolving Nook Color compatibility issues and avoiding Calibre warnings. It handles any potential ID collisions with other manifest items and updates the spine references and legacy cover metadata accordingly.
- [x] Fix Calibre/Epubcheck validation error: "WARNING: The file OEBPS/page-template.xpgt has a MIME type that does not match its extension". epub-shrink.py now automatically standardizes the media-type of manifest items in the OPF package document to match standard MIME types based on their file extension (e.g. .xpgt -> application/adobe-page-template+xml, .css -> text/css, .html/.xhtml -> application/xhtml+xml, images, fonts, and .ncx).
- [x] Fix Calibre/Epubcheck validation error: "The meta cover tag has content before name" [OEBPS/theworld.opf:12]. epub-shrink.py now automatically intercepts `<meta name="cover">` and corrects the attribute order (name before content) during package modernization.
- [x] Fix Google Play Books missing cover issue. When modernizing EPUB files, automatically ensure that the legacy EPUB 2 cover metadata tag (`<meta name="cover" content="[cover_id]" />`) is added to the package document if a cover-image manifest item exists. This guarantees that Google Play Books can successfully extract and display the cover thumbnail.
- [x] Fix HTM-025: Non-registered URI scheme type found in href (e.g., `kindle:embed:` in `<li class="toc-front" id="cover" value="1"><a href="kindle:embed:0002?mime=image/jpg">`). Unregistered schemes are converted to `<span>` tags during asset modernization to maintain styling while removing validation errors.

