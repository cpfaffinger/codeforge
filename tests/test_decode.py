import base64

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
