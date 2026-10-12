import io

import pytest
from PIL import Image

import re

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


def test_resolve_bcid_case_insensitive():
    from app.engines.barcode import resolve_bcid

    assert resolve_bcid("RATIONALIZEDCODABAR") == "rationalizedCodabar"
    assert resolve_bcid("Code128") == "code128"
    with pytest.raises(RenderError):
        resolve_bcid("nope")


def test_format_option_collision():
    enc, render = normalize_options({"format": "full", "scale": "2"})
    assert enc == {"format": "full"} and render == {"scale": "2"}
    enc, render = normalize_options({"format": "jpg"})
    assert enc == {} and render == {"format": "jpg"}


@needs_gs
def test_aztec_format_full_and_codabar(client):
    r = client.get("/api/v1/barcode", params={"bcid": "azteccode", "text": "This is Aztec Code", "format": "full"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    r = client.get("/", params={"bcid": "azteccode", "text": "x", "format": "full"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    r = client.get("/api/v1/barcode", params={"bcid": "rationalizedCodabar", "text": "A0123456789B", "includetext": ""})
    assert r.status_code == 200


def test_symbology_options_from_bwipp():
    s = symbologies()
    d3 = s["d3aqr"]
    names = {o["name"]: o for o in d3["options"]}
    assert names["category"]["required"] and d3["required_options"] == ["category"]
    assert names["hostratio"]["choices"] == ["2", "2.5", "3"]
    assert names["eclevel"]["choices"] == ["L", "M", "Q", "H"]  # inherited from qrcode
    assert d3["example"].startswith("HTTPS://ID.GS1.ORG") and "category=2842" in d3["example_options"]
    c128 = {o["name"]: o for o in s["code128"]["options"]}
    assert c128["includetext"]["type"] == "bool" and c128["textsize"]["group"] == "text" and c128["parse"]["group"] == "encoder"
    assert all(o["description"] for o in s["ean13"]["options"] if o["group"] in ("text", "appearance"))
    assert s["mailmark"]["example_options"] == "type=29" and {o["name"] for o in s["mailmark"]["options"]} >= {"type"}
    assert s["swissqrcode"]["example"].startswith("SPC" + chr(10) + "0200")


def test_symbology_patterns():
    s = symbologies()
    assert s["ean13"]["pattern"] == "^[0-9]*$"
    hibc = s["hibcdatamatrixrectangular"]
    assert re.match(hibc["pattern"], hibc["example"]) and not re.match(hibc["pattern"], "abc")
    assert re.match(s["gs1-128"]["pattern"], "(01)09521234543213(3103)000123") and not re.match(s["gs1-128"]["pattern"], "0109521234543213")
    assert s["daft"]["pattern"] == "^[DAFT]*$"
    for item in s.values():
        if item["pattern"] and item["example"]:
            assert re.match(item["pattern"], item["example"].replace(chr(10), "")), (item["id"], item["pattern"], item["example"])
        if item["pattern"]:
            re.compile(item["pattern"])


@needs_gs
def test_every_showcase_example_renders():
    """The showcase renders every symbology with its example data and options; none may fail."""
    from app.engines.barcode import make_barcode

    failures = []
    for item in symbologies().values():
        if not item["example"]:
            failures.append((item["id"], "no example"))
            continue
        try:
            make_barcode(item["id"], item["example"], parse_option_string(item["example_options"]))
        except RenderError as exc:
            failures.append((item["id"], str(exc)))
    assert not failures, failures
