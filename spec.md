# epub-shrink: Specification & Architecture Review

## 1. Project Identity

**Name:** epub_shrink
**Purpose:** Review, repair, and reduce the size of EPUB files.  
**Status:** Highly modular and modernized pipeline (v0.2.0, ~1700 LOC).  

---

## 2. Execution Pipeline

The script runs as a linear pipeline in `main()` using a threaded context object:

```
1. Verify external tools
2. Unzip EPUB to /tmp
3. Load OPF manifest and initialize EpubContext
4. Purge unwanted files (automatically mutates manifest)
5. Modernize assets (CSS, XHTML tags/attributes, cover, navigation documents)
6. Prune unreferenced assets (using BFS reference scan)
7. Write updated OPF
8. Analyze images
9. Iterative compress + rebuild loop (Secant Method dynamic quality adjustment)
10. Cleanup temp dirs
```

Each phase is described in detail below.

---

## 3. Feature Inventory by Phase

### Phase 1 — Extract & Load

| Feature | Location | Notes |
|---|---|---|
| Extract EPUB zip to temp dir | `unzip()` | Uses PID for uniqueness |
| Locate OPF via `META-INF/container.xml` | `load_opf()` | Falls back to `rglob("*.opf")` |
| Parse OPF into lxml tree + manifest dict | `load_opf()` | Manifest dictionary mapping href to XML elements |
| Thread pipeline with EpubContext | `EpubContext` | Encapsulates all state, eliminating global variables |

### Phase 2 — Purge Unwanted Files

| Feature | Location | Notes |
|---|---|---|
| Remove junk by glob pattern | `purge_unwanted_files()` | `.DS_Store`, `*cross-sale*`, `*xpromo*`, etc. |
| User-supplied purge patterns (`-i`) | `parse_args()` | Appended to defaults |
| Consolidated asset removal | `remove_asset()` | Single helper that resolving manifest lookups, spine XML, manifest XML, and disk deletion |

### Phase 3 — Modernize Assets

This phase is highly modular. The `modernize_assets()` orchestrator coordinates **8 separate sub-steps** split into modular, testable helper functions:

| # | Feature | Error Code | Location |
|---|---|---|---|
| 1 | Fix NCX missing navPoint IDs | — | `fix_ncx()` |
| 1 | Remove obsolete `<tours>` element | RSC-005 | `modernize_ncx_and_tours()` |
| 1.5 | Rename cover image ID to `"cover"` | Calibre/Nook | `modernize_cover_image_id()` |
| 1.5 | Add `properties="cover-image"` | EPUB 3 | `modernize_cover_image_id()` |
| 2 | Generate `nav.xhtml` from NCX | EPUB 3 upgrade | `ensure_epub3_navigation()` |
| 3 | Ensure XHTML namespace on `<html>` | RSC-005 | `modernize_html_and_css_files()` |
| 3 | Detect SVG and add `properties="svg"` | — | `modernize_html_and_css_files()` |
| 3 | Convert deprecated tags (`<center>`, `<font>`, etc.) | — | `convert_deprecated_tags()` |
| 3 | Convert deprecated attributes (`align`, `bgcolor`, etc.) | — | `convert_deprecated_attrs()` |
| 3 | Remove invalid custom `data-` attributes | — | `remove_invalid_data_attrs()` |
| 3 | Convert `<a name="...">` to `<a id="...">` | RSC-012 | `convert_a_name_to_id()` |
| 3 | Convert non-registered URI schemes to `<span>` | HTM-025 | `validate_uri_schemes()` |
| 3 | Fix aria attributes referring to missing IDs | — | `modernize_html_and_css_files()` |
| 3 | Convert `<ul type="disc">` to CSS class | — | `modernize_html_and_css_files()` |
| 3 | Remove obsolete `<meta http-equiv>` and `<meta charset>` | — | `cleanup_meta_and_triggers()` |
| 3 | Remove `<epub:trigger>` elements | — | `cleanup_meta_and_triggers()` |
| 4 | Inject `._ul_disc` CSS rule | — | `inject_ul_disc_css()` |
| 5 | Strip namespaced OPF attributes from metadata | — | `modernize_opf_metadata()` |
| 5 | Remove empty `<dc:*>` elements | — | `modernize_opf_metadata()` |
| 5 | Convert `value` attr to `content` on `<meta>` | RSC-005 | `modernize_opf_metadata()` |
| 5 | Fix `<meta name="cover">` attribute order | Calibre | `modernize_opf_metadata()` |
| 5 | Remove `<meta>` tags missing required attrs | RSC-005 | `modernize_opf_metadata()` |
| 5 | Set `dcterms:modified` to current time | EPUB 3 | `modernize_opf_metadata()` |
| 5 | Add legacy `<meta name="cover">` for Google Play | GPB compat | `modernize_opf_metadata()` |
| 6 | Standardize manifest media-types by extension | Calibre/Epubcheck | `standardize_manifest_media_types()` |
| 7 | Set EPUB version to `3.0` | — | `modernize_assets()` |
| 8 | Ensure non-linear spine items are reachable | OPF-096 | `ensure_nonlinear_reachable()` |

### Phase 4 — Prune Unreferenced Assets

| Feature | Location | Notes |
|---|---|---|
| Build "keep list" from spine, guide, cover, essentials | `remove_unreferenced()` | Dedicated keep list builder |
| Iterative BFS scan of XHTML `href`/`src` and CSS `url()` | `remove_unreferenced()` | Uses tinycss2 for CSS parsing |
| Delete files not in keep list from disk and XML tree | `remove_unreferenced()` | Cleans OPF and disk |

### Phase 5 — Image Compression

| Feature | Location | Notes |
|---|---|---|
| Discover JPEG/PNG/WebP by extension | `analyze_images()` | Scans assets for compression candidates |
| Estimate JPEG quality from quantization tables | `analyze_image_quality()` | Rough heuristic for initial pass |
| Lossless JPEG optimization | `compress_images()` | Shells out to `jpegoptim` |
| Lossless PNG optimization | `compress_images()` | Shells out to `oxipng` |
| Lossy JPEG/WebP via Pillow | `compress_images()` | `Image.save(quality=N)` |
| Lossy PNG via pngquant / palette conversion | `compress_images()` | Uses `pngquant` for high-quality quantization (transparency-preserved), falling back to Pillow's basic adaptive palette conversion |
| Iterative dynamic quality reduction loop to target size | `main()` / `estimate_next_quality()` | Employs Secant Method (linear interpolation) to mathematically estimate the required quality `q` to hit the target MB |

### Phase 6 — Rebuild

| Feature | Location | Notes |
|---|---|---|
| Rebuild EPUB zip with `mimetype` first + uncompressed | `rebuild_epub()` | Correct per EPUB spec |

---

## 4. Catalog of Ad-Hoc Fixes

All ad-hoc fixes have been fully integrated and modularly scoped:

| Fix | Trigger | Location | Scoped? |
|---|---|---|---|
| Cover image ID → `"cover"` | Calibre warning, Nook Color | `modernize_cover_image_id()` | ✅ Yes (collision safe) |
| `<meta name="cover">` attr order | Calibre error | `modernize_opf_metadata()` | ✅ Yes |
| Legacy cover meta for GPB | Google Play Books | `modernize_opf_metadata()` | ✅ Yes |
| Media-type standardization | Calibre/Epubcheck | `standardize_manifest_media_types()` | ✅ Yes (table-driven) |
| Unregistered URI schemes | HTM-025 | `validate_uri_schemes()` | ✅ Yes |
| Invalid custom data attributes | Epubcheck | `remove_invalid_data_attrs()` | ✅ Yes |
| `<a name>` → `<a id>` | RSC-012 | `convert_a_name_to_id()` | ✅ Yes |
| Remove `<tours>` | RSC-005 | `modernize_ncx_and_tours()` | ✅ Yes |
| Remove invalid `<meta>` | RSC-005 | `modernize_opf_metadata()` | ✅ Yes |
| XHTML namespace | RSC-005 | `modernize_html_and_css_files()` | ✅ Yes |
| SVG property declaration | — | `modernize_html_and_css_files()` | ✅ Yes |
| Aria dangling references | — | `modernize_html_and_css_files()` | ✅ Yes |
| Non-linear reachability | OPF-096 | `ensure_nonlinear_reachable()` | ✅ Yes |
| NCX navPoint IDs | — | `fix_ncx()` | ✅ Yes |
| `<epub:trigger>` removal | — | `cleanup_meta_and_triggers()` | ✅ Yes |
| Obsolete `<meta http-equiv>` | — | `cleanup_meta_and_triggers()` | ✅ Yes |

---

## 5. Architectural Assessment

### 5.1 What Works Well

- **The pipeline structure is sound.** The linear flow (extract → clean → modernize → prune → compress → rebuild) is the correct conceptual model.
- **Reference analysis is solid.** The BFS scan with tinycss2 CSS parsing handles complex real-world EPUBs well.
- **Dynamic Image quality reduction loop.** Secant Method interpolation accurately converges to the target size limit.
- **Modular Refactoring.** Replaced all monolithic functions with highly scoped, single-responsibility helper functions.
- **EpubContext State Threading.** Completely eliminated the usage of global variables.
- **Consolidated Asset Manipulation.** Reduced redundant manifest lookups by introducing a single unified asset removal pipeline.

---

## 6. External Dependencies

| Dependency | Purpose | Required? |
|---|---|---|
| `lxml` | OPF/XML parsing | Yes |
| `beautifulsoup4` | XHTML parsing/modification | Yes |
| `tinycss2` | CSS parsing for reference scanning | Yes |
| `Pillow` | Image analysis and lossy compression | Yes |
| `tqdm` | Progress bars | Yes (UX) |
| `fonttools` | Declared in pyproject.toml | No (unused dependency) |
| `jpegoptim` (external) | Lossless JPEG optimization | Yes |
| `oxipng` (external) | Lossless PNG optimization | Yes |
| `pngquant` (external) | Lossy PNG optimization | Yes (fully integrated) |

---

## 7. CLI Interface

```
epub-shrink INPUT.epub [options]

  -o, --output FILE       Output file (default: INPUT-lossless.epub or INPUT-qN.epub)
  -q, --quality N         Initial image quality 0-100 (default: 100 = lossless)
  -t, --targetsize MB     Target size in MB (iterates quality down to reach it)
  -i, --purge PATTERN     Extra glob patterns to delete (repeatable)
  -v, --verbose           Detailed per-file logging
```
