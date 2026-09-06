import io

from PIL import Image

from app.engines.qr import make_qr
from app.engines.render import RenderError


def test_qr_png_defaults():
    r = make_qr("hello")
    assert r.mime == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(r.content))
    # version 1 = 21 modules, +2*4 border, 10 px each
    assert img.size == (290, 290)
    assert r.width == 290


def test_qr_svg_colours():
    # segno normalises colours (#ff0000 -> "red", #00ff00 -> "#0f0"), so use values it cannot shorten
    r = make_qr("hello", fmt="svg", fg="#123456", bg="#fedcba", scale=4, border=1)
    assert r.mime == "image/svg+xml"
    svg = r.content.decode().lower()
    assert svg.startswith("<?xml") and "#123456" in svg and "#fedcba" in svg


def test_qr_jpg_pdf_webp():
    assert make_qr("x", fmt="jpg").mime == "image/jpeg"
    assert make_qr("x", fmt="pdf").content[:5] == b"%PDF-"
    assert make_qr("x", fmt="webp").mime == "image/webp"


def test_qr_version_and_error():
    r = make_qr("hello", version=10, error="H", scale=1, border=0)
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (57, 57)  # version 10 = 57 modules


def test_qr_error_level_aliases():
    for alias in ("LOW", "l", "L", "HIGH", "quartile"):
        make_qr("x", error=alias)


def test_qr_validation():
    for kwargs in ({"data": ""}, {"data": "x", "version": 41}, {"data": "x", "error": "Z"}, {"data": "x", "fg": "nope!"}, {"data": "x", "fmt": "tiff"}, {"data": "x", "scale": 0}):
        try:
            make_qr(**kwargs)
        except RenderError:
            continue
        raise AssertionError(f"expected RenderError for {kwargs}")


def test_qr_api_get(client):
    r = client.get("/api/v1/qr", params={"data": "https://example.com", "format": "svg"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    r = client.get("/api/v1/qr", params={"data": "x", "output": "base64"})
    body = r.json()
    assert body["mime"] == "image/png" and body["data_uri"].startswith("data:image/png;base64,")
    r = client.get("/api/v1/qr", params={"data": "x", "error": "Z"})
    assert r.status_code == 400 and "error correction" in r.json()["detail"]


def test_qr_api_post_and_info(client):
    r = client.post("/api/v1/qr", json={"data": "hello", "format": "png", "scale": 2})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    info = client.get("/api/v1/qr/info", params={"data": "hello"}).json()
    assert info["version"] == 1 and info["modules"] == 21
