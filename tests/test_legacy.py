"""Compatibility with pjanczyk/qr-code-generator and padiazg/barcode-generator (bwip-js)."""

import base64
import io

from PIL import Image

from tests.conftest import needs_gs


def test_legacy_qr_png(client):
    r = client.get("/qr", params={"data": "https://example.com", "format": "PNG"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_legacy_qr_svg_with_options(client):
    r = client.get("/qr", params={"data": "x", "format": "SVG", "ec": "HIGH", "version": "40", "fg": "#123456", "bg": "#ffffff", "box": "3", "border": "2"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "#123456" in r.text


def test_legacy_qr_defaults_match_original(client):
    # original: box 10, border 4, MEDIUM, PNG
    r = client.get("/qr", params={"data": "hello"})
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (290, 290)


def test_legacy_qr_errors(client):
    assert client.get("/qr").status_code == 422  # data missing
    r = client.get("/qr", params={"data": "x", "ec": "BOGUS"})
    assert r.status_code == 400 and r.text.startswith("QrCodeGenerator:")


def test_root_is_playground(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "codeforge" in r.text


def test_root_with_old_qr_form_params_is_playground(client):
    r = client.get("/", params={"data": "x", "imageFormat": "PNG", "errorCorrection": "MEDIUM"})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_legacy_barcode_missing_params(client):
    r = client.get("/", params={"bcid": "code128"})
    assert r.status_code == 404 and r.text == "BarcodeGenerator: Missing bcid or text parameter"
    r = client.get("/", params={"text": "123"})
    assert r.status_code == 404


def test_live_redirects(client):
    r = client.get("/live", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/")


@needs_gs
def test_legacy_barcode_png(client):
    r = client.get("/", params={"bcid": "code128", "text": "Count01234567!", "includetext": ""})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(r.content))
    assert img.width > 100 and img.height > 30


@needs_gs
def test_legacy_barcode_auspost_like_uptime_kuma(client):
    r = client.get("/?bcid=auspost&text=5956439111ABA9&custinfoenc=character&includetext")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


@needs_gs
def test_legacy_barcode_jpg_base64_rotate(client):
    r = client.get("/", params={"bcid": "ean13", "text": "2112345678900", "format": "jpg", "rotate": "R", "paddingwidth": "5", "paddingheight": "5", "scale": "3"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(r.content))
    assert img.height > img.width  # rotated
    r = client.get("/", params={"bcid": "ean13", "text": "2112345678900", "base64": "true"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert base64.b64decode(r.text)[:8] == b"\x89PNG\r\n\x1a\n"


@needs_gs
def test_legacy_barcode_bad_data(client):
    r = client.get("/", params={"bcid": "ean13", "text": "notanumber"})
    assert r.status_code == 400 and r.text.startswith("BarcodeGenerator:")
    r = client.get("/", params={"bcid": "doesnotexist", "text": "1"})
    assert r.status_code == 400 and "unknown symbology" in r.text
