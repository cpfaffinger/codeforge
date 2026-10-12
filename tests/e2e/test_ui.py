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
    png = urllib.request.urlopen(base_url + "/api/v1/qr?data=https://example.com/e2e-decode&scale=5").read()
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
