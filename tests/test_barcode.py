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


def test_result_cache_roundtrip():
    from app.engines import barcode as b
    from app.engines.render import Rendered

    key = ("t", "x", (), 2, 2, 1, 1, "N", False, "png")
    assert b._cache_get(key) is None
    b._cache_put(key, Rendered(b"png", "image/png", "png", 1, 1))
    assert b._cache_get(key).content == b"png"
    assert b.cache_stats()["entries"] >= 1


@needs_gs
def test_gs_worker_output_decodes_and_scales():
    """Worker output must be a complete, decodable symbol (treepoem's own bbox pass crops the quiet zone,
    so it is not used as a reference) and must scale linearly."""
    from app.engines import gsworker
    from tests.conftest import zbar_available

    pool = gsworker.GsPool(size=1)
    two = pool.render("code128", "Count01234567!", {"includetext": True, "backgroundcolor": "FFFFFF"}, 2)
    four = pool.render("code128", "Count01234567!", {"includetext": True, "backgroundcolor": "FFFFFF"}, 4)
    assert abs(four.width - 2 * two.width) <= 4 and abs(four.height - 2 * two.height) <= 4, (two.size, four.size)
    assert two.width > 250 and two.height > 100
    # white quiet zone on the left and right edge (backgroundcolor painted, nothing cropped)
    assert two.getpixel((0, two.height // 3)) == (255, 255, 255) and two.getpixel((two.width - 1, two.height // 3)) == (255, 255, 255)
    if zbar_available():
        from pyzbar.pyzbar import decode

        assert [s.data.decode() for s in decode(four)] == ["Count01234567!"]
        ean = pool.render("ean13", "2112345678900", {"includetext": True, "guardwhitespace": True, "backgroundcolor": "FFFFFF"}, 3)
        assert [s.data.decode() for s in decode(ean)] == ["2112345678900"]
        dm = pool.render("datamatrix", "Hello codeforge", {"backgroundcolor": "FFFFFF"}, 4)
        assert dm.width > 30
    import pytest as _pytest

    with _pytest.raises(gsworker.GsJobError):
        pool.render("ean13", "abc", {"backgroundcolor": "FFFFFF"}, 2)
    # worker survives an error
    assert pool.render("code39", "OK", {"backgroundcolor": "FFFFFF"}, 2).width > 10


@needs_gs
def test_gs_worker_is_fast():
    import time

    from app.engines import gsworker

    pool = gsworker.GsPool(size=1)
    pool.render("code128", "warm", {"includetext": True, "backgroundcolor": "FFFFFF"}, 2)
    t0 = time.perf_counter()
    for i in range(5):
        pool.render("code128", f"item{i}", {"includetext": True, "backgroundcolor": "FFFFFF"}, 3)
    per = (time.perf_counter() - t0) / 5
    assert per < 0.5, f"{per:.3f}s per barcode"
