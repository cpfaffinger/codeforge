"""Browser tests for the playground and the live scanner (Playwright + Chromium).

They are skipped when Playwright is not installed (the Docker test stage) and run locally with

    pip install pytest-playwright && python -m playwright install chromium
    pytest tests/e2e

The camera is simulated: ``getUserMedia`` is replaced by a canvas stream that shows a QR code
rendered by the server, moving slightly like a hand-held phone.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

pytest.importorskip("playwright")

FAKE_CAMERA = """
(() => {
  const codes = __CODES__;
  const imgs = [];
  window.__cam = { index: 0, ready: false };
  Promise.all(codes.map((u) => new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => res(null); i.src = u; }))).then((loaded) => {
    loaded.forEach((i) => imgs.push(i));
    const cv = document.createElement("canvas"); cv.width = 1280; cv.height = 720; const ctx = cv.getContext("2d");
    let t = 0;
    function draw() {
      t++; ctx.fillStyle = "#b8b0a8"; ctx.fillRect(0, 0, cv.width, cv.height);
      const img = imgs[window.__cam.index]; if (!img) return;
      ctx.save(); ctx.translate(640 + Math.sin(t / 20) * 30, 360 + Math.cos(t / 25) * 20); ctx.rotate(0.08 * Math.sin(t / 40));
      ctx.drawImage(img, -130, -130, 260, 260); ctx.restore();
    }
    draw(); setInterval(draw, 66);
    navigator.mediaDevices.getUserMedia = async () => cv.captureStream(15);
    navigator.mediaDevices.enumerateDevices = async () => [{ kind: "videoinput", deviceId: "fake", label: "fake back camera" }];
    window.__cam.ready = true;
  });
})();
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    url = os.environ.get("CODEFORGE_E2E_URL")
    if url:
        yield url.rstrip("/")
        return
    port = _free_port()
    env = dict(os.environ, CODEFORGE_RATE_LIMIT="0", CODEFORGE_GS_WORKERS="1")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"], env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if urllib.request.urlopen(url + "/healthz", timeout=1).status == 200:
                break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("dev server did not start")
    try:
        yield url
    finally:
        proc.kill()


@pytest.fixture
def scanner(page, base_url):
    """/scan with the fake camera showing two different QR codes (switch with window.__cam.index)."""
    codes = [base_url + "/api/v1/qr?data=first-code-1&scale=6&border=2", base_url + "/api/v1/qr?data=https://example.com/second-2&scale=6&border=2"]
    page.add_init_script(FAKE_CAMERA.replace("__CODES__", repr(codes).replace("'", '"')))
    page.goto(base_url + "/scan")
    page.wait_for_function("window.__cam && window.__cam.ready")
    page.wait_for_function("document.getElementById('status').textContent.startsWith('engine:')")
    page.evaluate("localStorage.removeItem('codeforge.scans.v1')")
    return page


def test_scanner_detects_and_stops_after_scan(scanner):
    page = scanner
    page.uncheck("#keepon")
    page.click("#start")
    page.wait_for_selector("#result.show", timeout=10000)
    assert page.text_content("#result-text") == "first-code-1"
    assert "zxing-wasm" in page.text_content("#result-meta")  # headless Chromium has no BarcodeDetector, the wasm engine is used
    page.wait_for_function("document.getElementById('video').hidden", timeout=3000)  # camera stopped after the scan
    assert page.locator("#scans .scan").count() == 1


def test_scanner_keep_on_no_duplicates_second_code(scanner):
    page = scanner
    page.check("#keepon")
    page.click("#start")
    page.wait_for_selector("#result.show", timeout=10000)
    page.wait_for_timeout(1500)  # the same code stays in front of the camera
    assert page.locator("#scans .scan").count() == 1 and "seen" not in page.text_content("#scans")
    assert not page.evaluate("document.getElementById('video').hidden")
    page.evaluate("window.__cam.index = 1")
    page.wait_for_function("document.getElementById('result-text').textContent.includes('second-2')", timeout=10000)
    assert page.locator("#scans .scan").count() == 2
    page.evaluate("window.__cam.index = 0")  # back to the first one: counted again because another code was in between
    page.wait_for_function("document.getElementById('scans').textContent.includes('seen 2x')", timeout=10000)
    page.click("#stop")


def test_barcode_options_panel_and_validation(page, base_url):
    page.goto(base_url + "/?tab=barcode&bcid=d3aqr")
    page.wait_for_function("document.querySelector('#opt-required-list [data-opt=category]')")
    assert page.input_value("#opt-required-list [data-opt=category]") == "2842"  # from the example
    assert "HTTPS://ID.GS1.ORG" in page.input_value("#form-barcode [name=text]")
    assert "category=2842" in page.input_value("#url-barcode")
    # HIBC: lowercase letters violate the charset rule, the UI says so before asking the server
    page.goto(base_url + "/?tab=barcode&bcid=hibcdatamatrixrectangular")
    page.wait_for_function("document.querySelector('#form-barcode [name=text]').value !== ''")
    page.fill("#form-barcode [name=text]", "abc lower")
    page.wait_for_function("document.getElementById('text-check').classList.contains('err')")
    assert "must contain only" in page.text_content("#text-check")
    assert page.locator("#render-anyway").count() == 1


def test_decode_upload_shows_result_and_overlay(page, base_url):
    page.goto(base_url + "/?tab=decode")
    page.wait_for_selector("#dropzone")
    png = page.request.get(base_url + "/api/v1/qr?data=https://example.com/e2e-decode&scale=5").body()  # through the browser (works behind Cloudflare too)
    page.set_input_files("#form-decode [name=file]", {"name": "qr.png", "mimeType": "image/png", "buffer": png})
    page.wait_for_function("document.getElementById('decode-results').textContent.includes('found')", timeout=15000)
    results = page.text_content("#decode-results")
    assert "1 code found" in results and "https://example.com/e2e-decode" in results
    assert page.locator("#preview-decode canvas.overlay").count() == 1
    assert page.evaluate("document.getElementById('decode-progress').hidden")


def test_showcase_groups_and_filter(page, base_url):
    page.goto(base_url + "/showcase")
    page.wait_for_function("document.querySelectorAll('.card').length > 100")
    assert page.locator("h2.cat").count() >= 8
    page.fill("#filter", "d3aqr")
    page.wait_for_function("document.getElementById('count').textContent.startsWith('1 of')")
    assert page.locator(".card:not([hidden]) .req").count() == 1


def _blank_png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (320, 240), (200, 190, 180)).save(buf, "PNG")
    return buf.getvalue()


def _decode_page(page, base_url):
    page.goto(base_url + "/decode")
    page.evaluate("() => { localStorage.removeItem('codeforge.decodes.v1'); localStorage.removeItem('codeforge.decode.formats'); }")
    page.reload()
    page.wait_for_function("window.CF && CF.isWasmReady()", timeout=15000)
    return page


def _wait_jobs_done(page, n):
    page.wait_for_function(f"document.querySelectorAll('.job.found, .job.none, .job.failed').length >= {n}", timeout=30000)


def test_decode_page_batch_browser_first_and_history(page, base_url):
    """Several files at once: the QR code is read in the browser (never uploaded), the others go to the server;
    'Choose images' offers the gallery (no capture attribute), results land in the decode history, not in the scan history."""
    _decode_page(page, base_url)
    assert page.get_attribute("#files", "capture") is None and page.get_attribute("#files", "multiple") is not None
    assert page.get_attribute("#camera", "capture") == "environment"
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    qr = page.request.get(base_url + "/api/v1/qr?data=LPA:1$example.com$TEST-ACTIVATION&scale=4").body()
    page.set_input_files("#files", [
        {"name": "esim.png", "mimeType": "image/png", "buffer": qr},
        {"name": "blank.png", "mimeType": "image/png", "buffer": _blank_png()},
        {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"no image"},
    ])
    _wait_jobs_done(page, 3)
    assert page.locator(".job.found").count() == 1 and page.locator(".job.none").count() == 1 and page.locator(".job.failed").count() == 1
    found = page.locator(".job.found")
    assert "not uploaded" in found.locator(".state").text_content() and "browser" in found.locator(".timing").text_content()
    assert found.locator(".code .raw").text_content() == "LPA:1$example.com$TEST-ACTIVATION"
    assert "no code found" in page.locator(".job.none .state").text_content() and "upload" in page.locator(".job.none .timing").text_content()
    assert "not a readable image" in page.locator(".job.failed .state").text_content()
    assert len(posts) == 2  # blank.png and notes.txt; the QR code never left the browser
    assert "3 files" in page.text_content("#summary-text") and "1 with codes" in page.text_content("#summary-text")
    assert page.locator("#history .scan").count() == 1 and "esim.png" in page.text_content("#history")
    page.reload()
    page.wait_for_selector("#history .scan")
    assert page.locator("#history .scan").count() == 1  # persisted
    assert "LPA:1" not in (page.evaluate("localStorage.getItem('codeforge.scans.v1')") or "")  # separate from the camera history


def test_decode_page_server_fallback_with_timing(page, base_url):
    _decode_page(page, base_url)
    page.evaluate("() => { window.ZXingWASM.readBarcodes = async () => { throw new Error('unsupported image'); }; }")
    png = page.request.get(base_url + "/api/v1/qr?data=server-fallback&scale=4").body()
    page.set_input_files("#files", {"name": "a.png", "mimeType": "image/png", "buffer": png})
    _wait_jobs_done(page, 1)
    assert "found by the server" in page.text_content(".job .state")
    timing = page.text_content(".job .timing")
    assert "upload" in timing and "server" in timing and "total" in timing


def test_decode_page_formats_filter(page, base_url):
    """1D barcodes only: a QR code is ignored in the browser and on the server, which says why."""
    _decode_page(page, base_url)
    page.select_option("#formats", "1d")
    png = page.request.get(base_url + "/api/v1/qr?data=only-qr&scale=4").body()
    page.set_input_files("#files", {"name": "qr.png", "mimeType": "image/png", "buffer": png})
    _wait_jobs_done(page, 1)
    assert page.locator(".job.none").count() == 1
    assert "formats filter excludes" in page.text_content(".job .state")
    page.reload()
    assert page.input_value("#formats") == "1d"  # remembered


def test_scanner_formats_select(scanner):
    page = scanner
    page.select_option("#formats", "1d")
    page.wait_for_function("document.getElementById('status').textContent.includes('1D barcodes')")
    page.click("#start")
    page.wait_for_timeout(2500)  # a QR code in front of the camera is not a 1D barcode
    assert not page.evaluate("document.getElementById('result').classList.contains('show')")
    page.select_option("#formats", "qr")
    page.wait_for_selector("#result.show", timeout=10000)
    assert page.text_content("#result-text") == "first-code-1"
