# Changelog

## 1.3.0 - 2026-10-12

* **scanner fixed**: the ZXing video path of the vendored library never returned a result, so live scanning only worked in browsers with a working BarcodeDetector. The scanner now grabs frames itself (full frame, centre crop and inverted passes at ~11 fps) and decodes them with BarcodeDetector (verified by a self-test, Chrome on Windows/Linux lists formats it cannot detect), ZXing, or as a last resort the codeforge API
* scanner: a code is never added twice while it stays in front of the camera; the camera stops after a successful scan unless "keep the camera on after a scan" is ticked (remembered); engine selector, torch and zoom where the camera supports it, result banner, highlight of the detected code, clear error messages for permission/HTTPS problems
* **decoder**: pre-processing ladder for photos (downscaling to 1600/1000/640 px, illumination flattening, autocontrast, sharpening, inverted copy, 45 degree rotation) within a time budget (`CODEFORGE_DECODE_BUDGET`); finds inverted and low-contrast codes and is 3-4x faster on 12 MP photos (libdmtx only runs when zbar found nothing). Responses carry `width`, `height`, `elapsed_ms`, `passes`, per symbol `engine` and `found_by`, and `hints` when nothing was found
* decoder API accepts the raw image as request body (`curl --data-binary @photo.jpg -H 'Content-Type: image/jpeg'`) next to multipart and base64; readable errors for HEIC/HEIF, PDF, SVG, empty or oversized uploads (413); decoding runs in the threadpool instead of blocking the event loop; upload limit 16 MB
* playground decode tab: upload progress, elapsed time, result table with copy/open/generate, bounding boxes drawn on the image, hints when nothing is found, API examples
* **options per symbology**: `/api/v1/symbologies` lists every encoder and renderer option BWIPP accepts for a symbology (`options`: name, type, default, required, choices, description, group) extracted from the vendored barcode.ps; required options (`d3aqr` needs `category`) are flagged. The playground shows them as form fields (required first, encoder / text / appearance groups) and keeps the raw option string for anything else
* **data format validation before rendering**: `pattern` (regex) derived from BWIPP's "must contain only ..." rules plus the GS1 AI syntax; the playground checks text live, names the offending characters and the rule, and offers "render anyway"; encoder errors are shown next to the text field, a missing required option focuses its field
* showcase: `d3aqr` (needs `category=2842 hostratio=2`) and Swiss QR Code have working examples (BWIPP's own `--EXAM`/`--EXOP` headers are used when the bwip-js table has none); cards are grouped by category with filter chips and show the encoder error instead of a broken image
* playground: switching the symbology loads its example when the text was empty or the previous example; the example line has a "use it" link; `/?tab=barcode&bcid=x` without text opens the example
* `/api/v1/limits` (upload size, data length, scale, batch size, decode budget), `bwipp` version in `/healthz`
* default rate limit raised to 300/minute (the showcase alone is ~110 requests)

## 1.2.0 - 2026-09-07

* symbology picker: every supported symbology is selectable from a grouped, filterable list (no more typing ids from memory)
* data format rules per symbology in the UI and API: extracted from BWIPP's own validation messages (charset, lengths, check digits, field structure), plus category and a link to the BWIPP reference page
* `/api/v1/symbologies` gained `rules`, `charset`, `min_length`, `max_length`, `category`, `wiki`

## 1.1.0 - 2026-09-07

* `/showcase`: gallery of every supported symbology with a rendered example, filter, click-through to the prefilled playground
* `/scan`: live camera scanner (browser BarcodeDetector API, ZXing fallback served locally, photo upload decoded on the server), local scan history without duplicates and "generate with codeforge" links
* playground accepts `?tab=barcode&bcid=&text=&options=` to prefill the barcode form
* compose: host port configurable via `CODEFORGE_BIND_PORT`

## 1.0.0 - 2026-09-06

Initial release.

* QR codes (segno): PNG, SVG, JPEG, WEBP, GIF, BMP, PDF; error levels, versions, colours, Micro QR
* 100+ barcode symbologies through BWIPP (treepoem + Ghostscript), encoder options passed through, renderer options (scale, rotate, padding) compatible with bwip-js
* decoder for QR codes and 1D barcodes (zbar) plus DataMatrix (libdmtx)
* batch endpoint: JSON with base64, ZIP, or PDF label sheets (reportlab)
* legacy endpoints: `/qr` (pjanczyk/qr-code-generator) and `/?bcid=&text=` (padiazg/barcode-generator), `/live` redirect
* interactive playground, OpenAPI/Swagger at `/docs`, ReDoc at `/redoc`
* per-IP rate limiting, size limits, health endpoint, non-root container with healthcheck
* persistent Ghostscript worker pool with BWIPP preloaded (tens of milliseconds per barcode instead of ~1.5 s with one gs start per image) and an in-memory result cache; treepoem as fallback
