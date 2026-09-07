# codeforge

QR codes and 100+ barcode symbologies from one small, self-building Docker container:
generate, decode, batch, PDF label sheets, an interactive playground and a documented
JSON API (OpenAPI/Swagger). It is a drop-in replacement for two abandoned services that
many people still run:

* [pjanczyk/qr-code-generator](https://github.com/pjanczyk/qr-code-generator) (`GET /qr?data=...`)
* [padiazg/barcode-generator](https://github.com/padiazg/barcode-generator) / bwip-js (`GET /?bcid=...&text=...`)

Their URLs keep working unchanged, so you can point both old hostnames at one codeforge
container and retire the originals.


## Features

| | |
|---|---|
| **QR codes** | [segno](https://github.com/heuer/segno): versions 1-40 and Micro QR, error levels L/M/Q/H, colours, PNG, SVG, JPEG, WEBP, GIF, BMP, PDF |
| **Barcodes** | [BWIPP](https://github.com/bwipp/postscriptbarcode) via [treepoem](https://github.com/adamchainz/treepoem) + Ghostscript: EAN/UPC/ISBN, Code 128/39/93, GS1-128, GS1 DataBar, ITF-14, Codabar, MSI, Pharmacode, DataMatrix, PDF417, Aztec, MaxiCode, Australia Post, Royal Mail, USPS, Deutsche Post, Japan Post, Swiss QR, ... - the same engine as bwip-js, so every `bcid` and encoder option is identical |
| **Decoder** | zbar (QR, EAN, UPC, Code 128/39, ITF, Codabar ...) and libdmtx (DataMatrix) from uploaded or base64 images |
| **Batch** | many codes per request as JSON (base64), ZIP, or a PDF label sheet with configurable grid and captions |
| **Playground** | `/` - live preview, symbology search with examples, decoder with drag & drop / paste, batch builder, copy API and legacy URLs |
| **Showcase** | `/showcase` - every symbology rendered with its example; click a card to open it prefilled in the playground |
| **Scanner** | `/scan` - live camera scanning in the browser (BarcodeDetector API, ZXing fallback, photo upload decoded on the server); recent scans are kept in localStorage without duplicates, each with a one-click "generate with codeforge" link |
| **API docs** | Swagger UI at `/docs`, ReDoc at `/redoc`, schema at `/openapi.json` |
| **Ops** | one container, non-root, healthcheck, per-IP rate limit, size limits, `/healthz`, 12-factor config via env vars |

## Quick start

```bash
git clone https://github.com/cpfaffinger/codeforge.git
cd codeforge
docker compose up -d --build
open http://localhost:33002/        # playground
open http://localhost:33002/docs    # API
```

The compose file binds to `127.0.0.1:33002` (change with `CODEFORGE_BIND_PORT` in `.env`); put nginx/Caddy/Traefik in front for TLS.

## API

### QR codes

```
GET  /api/v1/qr?data=https://example.com&format=svg&error=H&scale=8&border=2&fg=%23222222&bg=%23ffffff
POST /api/v1/qr            {"data": "...", "format": "png", "error": "M", "version": null, "scale": 10, "border": 4, "fg": "#000000", "bg": "#ffffff", "micro": false}
GET  /api/v1/qr/info?data=...      -> {"version": 3, "error": "M", "modules": 29, ...}
```

Add `output=base64` to get JSON (`mime`, `width`, `height`, `base64`, `data_uri`) instead of the image,
`download=true` to get a `Content-Disposition: attachment`.

### Barcodes

```
GET  /api/v1/barcode?bcid=code128&text=Count01234567!&includetext&scale=3
GET  /api/v1/barcode?bcid=ean13&text=2112345678900&includetext&guardwhitespace&format=jpg
GET  /api/v1/barcode?bcid=datamatrix&text=Hello&rotate=R&paddingwidth=10&paddingheight=10
GET  /api/v1/barcode?bcid=auspost&text=5956439111ABA9&custinfoenc=character&includetext
POST /api/v1/barcode       {"bcid": "qrcode", "text": "hello", "options": {"eclevel": "H", "scale": 4}, "format": "png"}
GET  /api/v1/symbologies   [{"id": "ean13", "description": "EAN-13", "example": "2112345678900", "example_options": "includetext guardwhitespace"}, ...]
GET  /api/v1/symbologies/ean13
```

Known query parameters: `bcid`, `text`, `scale`, `scaleX`, `scaleY`, `rotate` (N/R/L/I), `paddingwidth`,
`paddingheight`, `format`, `output`, `download`. **Every other query parameter is passed to BWIPP as an
encoder option** - see the [BWIPP options reference](https://github.com/bwipp/postscriptbarcode/wiki/Options-Reference)
and the per-symbology pages (`includetext`, `textsize`, `textxalign`, `height`, `width`, `barcolor`,
`backgroundcolor`, `textcolor`, `eclevel`, `parse`, `parsefnc`, `guardwhitespace`, `custinfoenc`, ...).
Flags are given without a value (`includetext`) or as `includetext=true`.

### Decode

```
POST /api/v1/decode          multipart/form-data, field "file"
POST /api/v1/decode/base64   {"image": "data:image/png;base64,...."}
-> {"count": 1, "symbols": [{"type": "QRCODE", "data": "https://example.com", "rect": {...}, "polygon": [...]}]}
```

### Batch and PDF label sheets

```
POST /api/v1/batch
{
  "items": [
    {"type": "qr", "data": "https://example.com/1", "caption": "Item 1"},
    {"type": "barcode", "bcid": "code128", "text": "A0001", "options": {"includetext": true}}
  ],
  "output": "pdf",                       // json | zip | pdf
  "format": "png",                       // for json/zip
  "sheet": {"page": "A4", "columns": 3, "rows": 8, "margin_mm": 10, "gap_mm": 4, "captions": true}
}
```

`json` returns every item with base64 and per-item errors, `zip` one file per item, `pdf` a
label sheet (A4, LETTER, landscape variants).

### Legacy endpoints

| Legacy | codeforge |
|---|---|
| `GET /qr?data=&format=PNG\|SVG&ec=LOW\|MEDIUM\|QUARTILE\|HIGH&version=&fg=&bg=&box=&border=` | unchanged |
| `GET /?bcid=ean13&text=...&includetext&scale=2&rotate=N&paddingwidth=1&format=jpg&base64=true` | unchanged (all bwip-js options), invalid requests answer `400 text/plain "BarcodeGenerator: ..."` instead of 500 (configurable) |
| `GET /?data=...&imageFormat=...` (old QR form) | opens the playground with the values prefilled |
| `GET /live` (old bwip-js demo) | redirects to the playground |

### Meta

`GET /healthz` (status, version, ghostscript/zbar availability, symbology count), `GET /version`, `GET /api/v1/formats`.

Rate limit headers `X-RateLimit-Limit` / `X-RateLimit-Remaining` are sent on every response, `429` with
`Retry-After` when exceeded (`/healthz`, `/docs` and static files are exempt).

## Performance

BWIPP is PostScript, so every barcode is rendered by Ghostscript. Starting Ghostscript and
parsing the 800 kB library for each image (what treepoem and most wrappers do) costs about
1.5 s per barcode. codeforge keeps a small pool of Ghostscript processes alive with BWIPP
and the fonts already loaded and feeds them jobs over stdin, which takes tens of
milliseconds per image; identical requests are additionally served from an in-memory cache.
If a worker cannot be started the service transparently falls back to treepoem. QR codes are
rendered in pure Python and never touch Ghostscript.

## Configuration

All settings are environment variables with the prefix `CODEFORGE_` (or a `.env` file, see
[.env.example](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `CODEFORGE_WORKERS` | `2` | uvicorn worker processes |
| `CODEFORGE_RATE_LIMIT` | `120/minute` | per client IP; `0` disables |
| `CODEFORGE_TRUST_PROXY` | `true` | use `X-Forwarded-For` behind a reverse proxy |
| `CODEFORGE_MAX_DATA_LENGTH` | `4000` | maximum encoded text length |
| `CODEFORGE_MAX_SCALE` / `MAX_BORDER` | `30` / `50` | limits for scale, padding, border |
| `CODEFORGE_MAX_IMAGE_PIXELS` | `30000000` | generated/uploaded image size guard |
| `CODEFORGE_MAX_BATCH_ITEMS` | `200` | items per batch request |
| `CODEFORGE_MAX_UPLOAD_BYTES` | `10485760` | decoder upload limit |
| `CODEFORGE_GS_WORKERS` | `2` | persistent Ghostscript processes per uvicorn worker; `0` = one gs start per image (slow) |
| `CODEFORGE_GS_TIMEOUT` | `15` | seconds per Ghostscript job before the worker is restarted |
| `CODEFORGE_CACHE_MAX_BYTES` | `67108864` | in-memory cache for rendered barcodes per worker, `0` = off |
| `CODEFORGE_ENABLE_DECODER` / `ENABLE_BATCH` / `ENABLE_PLAYGROUND` | `true` | switch features off |
| `CODEFORGE_CORS_ORIGINS` | `*` | CORS allow-list, empty disables |
| `CODEFORGE_LEGACY_ERROR_STATUS` | `400` | status for invalid legacy barcode requests |
| `CODEFORGE_LOG_LEVEL` | `info` | |

## Deployment behind nginx (both legacy hostnames on one container)

[deploy/nginx-codeforge.conf](deploy/nginx-codeforge.conf) shows two `server` blocks
(`qr.example.com` and `barcode.example.com`) that proxy to `127.0.0.1:33002`. Both hostnames
serve the same content; keep them only for compatibility.

With a `docker-compose@.service` systemd template unit (projects under `/etc/docker/compose/<name>`):

```bash
git clone https://github.com/cpfaffinger/codeforge.git /etc/docker/compose/codeforge
systemctl enable --now docker-compose@codeforge
```

A nightly `docker compose build --pull` (for example with
[docker-housekeeping](https://github.com/cpfaffinger/docker-housekeeping)) keeps the base image
and packages current; codeforge has no external build dependencies beyond PyPI and Debian packages.

## Development

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload
pytest
```

Barcode rendering needs Ghostscript (`apt install ghostscript` / `brew install ghostscript`) and the
decoder needs `libzbar0`; tests that depend on them are skipped when they are missing. The full test
suite runs inside the image: `docker build --target test .`

## Project layout

```
app/main.py            FastAPI app, middleware, routers
app/config.py          settings (env vars)
app/api/legacy.py      /qr, /?bcid=, /live, playground
app/api/v1.py          /api/v1/* JSON API
app/api/health.py      /healthz, /version
app/engines/qr.py      segno
app/engines/barcode.py treepoem/BWIPP + Pillow renderer options
app/engines/decoder.py zbar / libdmtx
app/engines/render.py  formats, colours, PDF label sheets
app/static/            playground, showcase, scanner (plain HTML/CSS/JS), vendored ZXing
app/data/symbologies.json  descriptions and examples per symbology (from bwip-js, MIT)
tests/                 pytest
deploy/                nginx example
```

## License

MIT. Symbology descriptions are derived from bwip-js (MIT); BWIPP is MIT; Ghostscript is AGPL and
used as an external process only.
