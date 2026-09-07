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
