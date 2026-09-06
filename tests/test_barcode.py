import io

import pytest
from PIL import Image

from app.engines.barcode import normalize_options, parse_option_string, symbologies
from app.engines.render import RenderError, color_to_bwipp
from tests.conftest import needs_gs


def test_symbology_table_has_descriptions():
    s = symbologies()
    assert len(s) >= 90
    assert s["code128"]["description"] == "Code 128"
    assert s["auspost"]["example_options"]


def test_normalize_options_split():
    enc, render = normalize_options({"includetext": "", "textsize": "12", "scale": "3", "rotate": "R", "guardwhitespace": True, "parse": "false", "ScaleX": 4})
    assert enc == {"includetext": True, "textsize": "12", "guardwhitespace": True}
    assert render == {"scale": "3", "rotate": "R", "scalex": 4}


def test_parse_option_string():
    assert parse_option_string("includetext textsize=12\nguardwhitespace") == {"includetext": True, "textsize": "12", "guardwhitespace": True}


def test_color_to_bwipp():
    assert color_to_bwipp("#ff0000", "FFFFFF") == "FF0000"
    assert color_to_bwipp("abc", "FFFFFF") == "AABBCC"
    assert color_to_bwipp(None, "FFFFFF") == "FFFFFF"
    assert color_to_bwipp("red", "FFFFFF") == "FF0000"


@needs_gs
def test_make_barcode_png_and_scale():
    from app.engines.barcode import make_barcode

    r1 = make_barcode("code128", "hello", {"includetext": True, "scale": 2})
    r2 = make_barcode("code128", "hello", {"includetext": True, "scale": 4})
    assert r1.mime == "image/png"
    i1, i2 = Image.open(io.BytesIO(r1.content)), Image.open(io.BytesIO(r2.content))
    assert i2.width > i1.width * 1.5


@needs_gs
def test_make_barcode_2d_and_formats():
    from app.engines.barcode import make_barcode

    assert make_barcode("datamatrix", "Hello", {}).mime == "image/png"
    assert make_barcode("pdf417", "Hello", {"columns": "2"}, fmt="jpg").mime == "image/jpeg"
    assert make_barcode("azteccode", "Hello", {}, fmt="pdf").content[:5] == b"%PDF-"
    with pytest.raises(RenderError):
        make_barcode("code128", "x", {}, fmt="svg")


@needs_gs
def test_make_barcode_invalid_data():
    from app.engines.barcode import make_barcode

    with pytest.raises(RenderError) as exc:
        make_barcode("ean13", "12", {})
    assert "BWIPP" in str(exc.value) or "ean13" in str(exc.value).lower()


@needs_gs
def test_barcode_api(client):
    r = client.get("/api/v1/barcode", params={"bcid": "ean13", "text": "2112345678900", "includetext": "", "scale": 3})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    r = client.post("/api/v1/barcode", json={"bcid": "qrcode", "text": "hi", "options": {"eclevel": "H"}}, params={"output": "base64"})
    assert r.status_code == 200 and r.json()["mime"] == "image/png"
    r = client.get("/api/v1/barcode", params={"bcid": "upca", "text": "abc"})
    assert r.status_code == 400
