# Changelog

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
