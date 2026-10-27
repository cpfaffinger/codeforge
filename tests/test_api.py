from app.ratelimit import RateLimiter, parse_limit


def test_health_and_version(client):
    h = client.get("/healthz").json()
    assert h["status"] in ("ok", "degraded") and h["symbologies"] > 50 and "version" in h
    assert client.get("/version").json()["name"] == "codeforge"


def test_openapi_and_docs(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for p in ("/qr", "/", "/api/v1/qr", "/api/v1/barcode", "/api/v1/symbologies", "/api/v1/decode", "/api/v1/batch", "/healthz"):
        assert p in paths, p
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_symbologies(client):
    items = client.get("/api/v1/symbologies").json()
    ids = {i["id"] for i in items}
    assert {"code128", "ean13", "qrcode", "datamatrix", "pdf417", "auspost"} <= ids
    ean = client.get("/api/v1/symbologies/ean13").json()
    assert ean["description"] == "EAN-13" and ean["example"] == "2112345678900"
    assert client.get("/api/v1/symbologies/nope").status_code == 404
    filtered = client.get("/api/v1/symbologies", params={"q": "auspost"}).json()
    assert any(i["id"] == "auspost" for i in filtered)


def test_formats(client):
    f = client.get("/api/v1/formats").json()
    assert "svg" in f["qr"] and "svg" not in f["barcode"] and "pdf" in f["barcode"]


def test_static_and_favicon(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/favicon.ico").status_code == 200


def test_parse_limit():
    assert parse_limit("120/minute") == (120, 60)
    assert parse_limit("10 per 5 minutes") == (10, 300)
    assert parse_limit("2000/hour") == (2000, 3600)
    assert parse_limit("0") is None and parse_limit("") is None


def test_rate_limiter():
    rl = RateLimiter(3, 60)
    assert [rl.check("a")[0] for _ in range(4)] == [True, True, True, False]
    assert rl.check("b")[0] is True


def test_rate_limit_middleware():
    import os

    from fastapi.testclient import TestClient

    os.environ["CODEFORGE_RATE_LIMIT"] = "2/minute"
    try:
        from app.config import Settings
        from app.main import create_app
        import app.main as m

        m.settings = Settings()
        app = create_app()
        with TestClient(app) as c:
            assert c.get("/api/v1/formats").status_code == 200
            assert c.get("/api/v1/formats").status_code == 200
            r = c.get("/api/v1/formats")
            assert r.status_code == 429 and "Retry-After" in r.headers
            assert c.get("/healthz").status_code == 200  # exempt
    finally:
        os.environ["CODEFORGE_RATE_LIMIT"] = "0"


def test_showcase_and_scan_pages(client):
    r = client.get("/showcase")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "/api/v1/symbologies" in r.text
    r = client.get("/scan")
    assert r.status_code == 200 and "BarcodeDetector" in r.text and "/static/vendor/zxing-library.min.js" in r.text
    assert client.get("/static/vendor/zxing-library.min.js").status_code == 200
    r = client.get("/decode")
    assert r.status_code == 200 and 'id="files"' in r.text and "multiple" in r.text and "/static/codes.js?v=" in r.text
    assert 'capture="environment"' not in r.text.split('id="files"')[1].split(">")[0]  # the picker must offer the gallery, not only the camera
    assert "photo-box" not in client.get("/scan").text and 'href="/decode"' in client.get("/scan").text
    assert "function wasmRead" in client.get("/static/codes.js").text
    # playground links from the showcase/scanner prefill the barcode form
    assert "p.get(\"bcid\")" in client.get("/static/app.js").text


def test_symbology_rules_and_categories(client):
    ean = client.get("/api/v1/symbologies/ean13").json()
    assert any("12 or 13 digits" in r for r in ean["rules"]), ean["rules"]
    assert ean["charset"] == "digits" and ean["min_length"] == 12 and ean["max_length"] == 13
    assert ean["category"].startswith("Point of sale") and ean["wiki"].endswith("/wiki/EAN-13")
    aus = client.get("/api/v1/symbologies/auspost").json()
    assert any("DPID must be 8 digits" in r for r in aus["rules"])
    items = client.get("/api/v1/symbologies").json()
    assert all(i["category"] for i in items)
    assert sum(1 for i in items if i["rules"]) > 80


def test_symbologies_etag_and_gzip(client):
    r = client.get("/api/v1/symbologies", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("etag") and r.headers.get("content-encoding") == "gzip"
    assert client.get("/api/v1/symbologies", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    item = r.json()[0]
    assert "options" in item and "pattern" in item and "required_options" in item


def test_pages_are_version_stamped_and_pwa_files_exist(client):
    from app import __version__

    html = client.get("/").text
    assert "/static/app.js?v=" + __version__ in html and 'rel="manifest"' in html
    assert "/static/vendor/zxing-reader.iife.js?v=" + __version__ in client.get("/scan").text
    assert "/static/codes.js?v=" + __version__ in client.get("/decode").text
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["start_url"].startswith("/scan")
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and __version__ in sw.text and "javascript" in sw.headers["content-type"]
    for icon in ("icon-192.png", "icon-512.png", "icon-192-maskable.png"):
        assert client.get("/static/icons/" + icon).status_code == 200
    assert client.get("/static/vendor/zxing_reader.wasm").status_code == 200


def test_option_docs_from_wiki(client):
    ean = client.get("/api/v1/symbologies/ean13").json()
    opts = {o["name"]: o for o in ean["options"]}
    assert "guard" in opts["guardwhitespace"]["description"].lower()
    aztec = {o["name"]: o for o in client.get("/api/v1/symbologies/azteccode").json()["options"]}
    assert aztec["layers"]["description"] and aztec["readerinit"]["description"]
