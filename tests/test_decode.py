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
    assert res.symbols and res.symbols[0]["data"] == "inverted-test" and res.symbols[0]["found_by"]
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
    assert j["width"] > 0 and j["elapsed_ms"] >= 0 and j["passes"] >= 1 and j["symbols"][0]["engine"] in ("zxingcpp", "zbar")


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


def test_zbar_ladder_alone_finds_inverted_code(monkeypatch):
    """The zbar fallback ladder must still work on its own (servers without zxing-cpp)."""
    from app.engines import decoder

    if not decoder.zbar_available():
        import pytest

        pytest.skip("libzbar not installed")
    monkeypatch.setattr(decoder, "zxing_available", lambda: False)
    png = make_qr("ladder-test", scale=6).content
    inverted = io.BytesIO(); ImageOps.invert(Image.open(io.BytesIO(png)).convert("L")).save(inverted, "PNG")
    res = decoder.decode_image_full(inverted.getvalue())
    assert res.symbols and res.symbols[0]["engine"] == "zbar" and "inverted" in res.symbols[0]["found_by"]


def test_zxingcpp_reads_2d_symbologies(client):
    """zxing-cpp decodes DataMatrix, Aztec, PDF417 and Micro QR rendered by BWIPP (needs ghostscript)."""
    import pytest

    from app.engines.decoder import zxing_available
    from tests.conftest import gs_available

    if not (zxing_available() and gs_available()):
        pytest.skip("zxing-cpp or ghostscript missing")
    from app.engines.barcode import make_barcode

    for bcid, text, expect_type in (("datamatrix", "DM-roundtrip", "DATAMATRIX"), ("azteccode", "Aztec roundtrip", "AZTEC"), ("pdf417", "PDF417 roundtrip", "PDF417"), ("microqrcode", "1234", "MICROQR")):
        png = make_barcode(bcid, text, {"scale": 4}).content
        r = client.post("/api/v1/decode", content=png, headers={"Content-Type": "image/png"})
        j = r.json()
        assert r.status_code == 200 and j["count"] >= 1, (bcid, j)
        assert j["symbols"][0]["data"] == text and j["symbols"][0]["type"] == expect_type and j["symbols"][0]["engine"] == "zxingcpp", (bcid, j["symbols"][0])


def test_heic_photo_is_decoded(client):
    import pytest

    from app.engines.decoder import HEIF_SUPPORTED

    if not HEIF_SUPPORTED:
        pytest.skip("pillow-heif missing")
    import pillow_heif

    png = make_qr("heic-test", scale=8).content
    img = Image.open(io.BytesIO(png)).convert("RGB")
    buf = io.BytesIO()
    try:
        pillow_heif.from_pillow(img).save(buf, quality=90)
    except Exception as exc:  # encoder not built into this wheel
        pytest.skip(f"HEIF encoder unavailable: {exc}")
    r = client.post("/api/v1/decode", files={"file": ("photo.heic", buf.getvalue(), "image/heic")})
    assert r.status_code == 200 and r.json()["count"] == 1 and r.json()["symbols"][0]["data"] == "heic-test"


def test_decode_stats(client):
    s = client.get("/stats").json()
    assert "decoder" in s and "engines" in s["decoder"] and s["decoder"]["images"] >= 1


def test_parse_formats_groups_types_and_errors():
    import pytest

    from app.engines.decoder import LINEAR_TYPES, QR_TYPES, parse_formats
    from app.engines.render import RenderError

    assert parse_formats(None) is None and parse_formats("") is None and parse_formats("all") is None and parse_formats("qr,all") is None
    assert parse_formats("qr") == QR_TYPES
    assert parse_formats("1D") == LINEAR_TYPES and parse_formats("barcodes") == LINEAR_TYPES
    assert parse_formats("ean-13, Code128,itf") == {"EAN13", "CODE128", "I25"}
    assert parse_formats("qr,ean13") == QR_TYPES | {"EAN13"}
    with pytest.raises(RenderError, match="unknown format 'foo'"):
        parse_formats("qr,foo")


@needs_zbar
def test_decode_formats_filter_and_hint(client):
    png = make_qr("filter-test", scale=6).content
    j = client.post("/api/v1/decode?formats=qr", files={"file": ("qr.png", png, "image/png")}).json()
    assert j["count"] == 1
    j = client.post("/api/v1/decode?formats=1d", files={"file": ("qr.png", png, "image/png")}).json()
    assert j["count"] == 0 and "formats filter excludes" in j["hints"][0] and "QRCODE" in j["hints"][0]
    r = client.post("/api/v1/decode/base64?formats=ean13", json={"image": base64.b64encode(png).decode()})
    assert r.status_code == 200 and r.json()["count"] == 0
    r = client.post("/api/v1/decode?formats=nope", files={"file": ("qr.png", png, "image/png")})
    assert r.status_code == 400 and "unknown format" in r.json()["detail"]
    f = client.get("/api/v1/decode/formats").json()
    assert set(f["groups"]) == {"qr", "2d", "1d"} and "EAN13" in f["groups"]["1d"] and "DATAMATRIX" in f["types"]


def test_decode_formats_mixed_image(client):
    """A QR code and an EAN-13 side by side: the filter picks the requested one (needs ghostscript for the EAN)."""
    import pytest

    from tests.conftest import gs_available

    if not gs_available():
        pytest.skip("ghostscript missing")
    from app.engines.barcode import make_barcode

    qr = Image.open(io.BytesIO(make_qr("mixed-qr", scale=6).content)).convert("L")
    ean = Image.open(io.BytesIO(make_barcode("ean13", "5901234123457", {"scale": 3, "includetext": True}).content)).convert("L")
    sheet = Image.new("L", (qr.width + ean.width + 80, max(qr.height, ean.height) + 40), 255)
    sheet.paste(qr, (20, 20)); sheet.paste(ean, (qr.width + 60, 20))
    buf = io.BytesIO(); sheet.save(buf, "PNG"); png = buf.getvalue()
    j = client.post("/api/v1/decode?formats=1d", content=png, headers={"Content-Type": "image/png"}).json()
    assert [s["type"] for s in j["symbols"]] == ["EAN13"] and j["symbols"][0]["data"] == "5901234123457"
    j = client.post("/api/v1/decode?formats=qr", content=png, headers={"Content-Type": "image/png"}).json()
    assert [s["data"] for s in j["symbols"]] == ["mixed-qr"]
    j = client.post("/api/v1/decode", content=png, headers={"Content-Type": "image/png"}).json()
    assert {s["type"] for s in j["symbols"]} == {"QRCODE", "EAN13"}


@needs_zbar
def test_decode_batch(client):
    files = [("file", ("a.png", make_qr("batch-a", scale=5).content, "image/png")),
             ("file", ("b.txt", b"not an image", "text/plain")),
             ("other", ("c.png", make_qr("batch-c", scale=5).content, "image/png"))]
    r = client.post("/api/v1/decode/batch?formats=qr", files=files)
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["files"], j["decoded"], j["with_codes"], j["symbols"]) == (3, 2, 2, 2)
    assert [i["filename"] for i in j["items"]] == ["a.png", "b.txt", "c.png"]
    assert j["items"][0]["symbols"][0]["data"] == "batch-a" and j["items"][2]["symbols"][0]["data"] == "batch-c"
    assert j["items"][1]["ok"] is False and "not a readable image" in j["items"][1]["error"]


def test_decode_batch_errors(client, monkeypatch):
    from app.config import settings

    png = make_qr("x", scale=3).content
    assert client.post("/api/v1/decode/batch", content=png, headers={"Content-Type": "image/png"}).status_code == 400
    assert client.post("/api/v1/decode/batch", data={"x": "1"}, files={}).status_code == 400
    monkeypatch.setattr(settings, "decode_batch_max_files", 2)
    r = client.post("/api/v1/decode/batch", files=[("file", (f"{i}.png", png, "image/png")) for i in range(3)])
    assert r.status_code == 400 and "at most 2 files" in r.json()["detail"]
    assert client.get("/api/v1/limits").json()["decode_batch_max_files"] == 2
