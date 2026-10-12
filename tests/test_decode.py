import base64
import io

from PIL import Image, ImageOps

from app.engines.qr import make_qr
from tests.conftest import needs_zbar


@needs_zbar
def test_decode_qr_roundtrip():
    from app.engines.decoder import decode_image

    png = make_qr("https://example.com/roundtrip", scale=6).content
    symbols = decode_image(png)
    assert symbols and symbols[0]["type"] == "QRCODE" and symbols[0]["data"] == "https://example.com/roundtrip"


@needs_zbar
def test_decode_api_upload_and_base64(client):
    png = make_qr("upload-test", scale=6).content
    r = client.post("/api/v1/decode", files={"file": ("qr.png", png, "image/png")})
    assert r.status_code == 200 and r.json()["symbols"][0]["data"] == "upload-test"
    r = client.post("/api/v1/decode/base64", json={"image": "data:image/png;base64," + base64.b64encode(png).decode()})
    assert r.status_code == 200 and r.json()["count"] == 1


@needs_zbar
def test_decode_invalid_upload(client):
    r = client.post("/api/v1/decode", files={"file": ("x.txt", b"not an image", "text/plain")})
    assert r.status_code == 400


@needs_zbar
def test_decode_inverted_and_low_contrast_photo():
    from app.engines.decoder import decode_image_full

    png = make_qr("inverted-test", scale=6).content
    img = Image.open(io.BytesIO(png)).convert("L")
    inverted = io.BytesIO(); ImageOps.invert(img).save(inverted, "PNG")
    res = decode_image_full(inverted.getvalue())
    assert res.symbols and res.symbols[0]["data"] == "inverted-test" and "inverted" in res.symbols[0]["found_by"]
    # a big "photo": small code on a large grey background with a brightness gradient
    photo = Image.new("L", (2400, 1800), 170)
    photo.paste(img.resize((240, 240)), (900, 700))
    buf = io.BytesIO(); photo.save(buf, "JPEG", quality=70)
    res = decode_image_full(buf.getvalue())
    assert res.symbols and res.symbols[0]["data"] == "inverted-test" and res.width == 2400 and res.elapsed_ms >= 0


@needs_zbar
def test_decode_raw_body_and_response_fields(client):
    png = make_qr("raw-body", scale=6).content
    r = client.post("/api/v1/decode", content=png, headers={"Content-Type": "image/png"})
    j = r.json()
    assert r.status_code == 200 and j["count"] == 1 and j["symbols"][0]["data"] == "raw-body"
    assert j["width"] > 0 and j["elapsed_ms"] >= 0 and j["passes"] >= 1 and j["symbols"][0]["engine"] == "zbar"


@needs_zbar
def test_decode_nothing_found_gives_hints(client):
    blank = io.BytesIO(); Image.new("L", (300, 300), 255).save(blank, "PNG")
    r = client.post("/api/v1/decode", files={"file": ("blank.png", blank.getvalue(), "image/png")})
    j = r.json()
    assert r.status_code == 200 and j["count"] == 0 and j["hints"] and j["passes"] > 1


def test_decode_errors_explain(client):
    heic = bytes([0, 0, 0, 24]) + b"ftypheic" + bytes(4) + b"mif1heic" + bytes(64)
    r = client.post("/api/v1/decode", files={"file": ("photo.heic", heic, "image/heic")})
    assert r.status_code == 400 and "HEIC" in r.json()["detail"]
    r = client.post("/api/v1/decode", files={"file": ("empty.png", b"", "image/png")})
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    r = client.post("/api/v1/decode", data={"other": "no file at all"})
    assert r.status_code == 400 and "named 'file'" in r.json()["detail"]
    r = client.post("/api/v1/decode/base64", json={"image": "data:image/png;base64,!!!"})
    assert r.status_code == 400


def test_limits_endpoint(client):
    j = client.get("/api/v1/limits").json()
    assert j["max_upload_bytes"] > 0 and j["decode_budget_seconds"] > 0
