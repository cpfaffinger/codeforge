import io
import zipfile

from app.engines.render import label_sheet
from app.engines.qr import make_qr
from tests.conftest import needs_gs


def test_batch_json_qr(client):
    body = {"items": [{"type": "qr", "data": "one"}, {"type": "qr", "data": "two", "error": "H"}, {"type": "qr", "data": ""}], "output": "json", "format": "png"}
    r = client.post("/api/v1/batch", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == 3 and j["succeeded"] == 2 and j["failed"] == 1
    assert j["items"][0]["data_uri"].startswith("data:image/png;base64,") and j["items"][2]["error"]


def test_batch_zip_svg(client):
    body = {"items": [{"type": "qr", "data": "a"}, {"type": "qr", "data": "b"}], "output": "zip", "format": "svg"}
    r = client.post("/api/v1/batch", json=body)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert names == ["code-0001.svg", "code-0002.svg"]
        assert zf.read("code-0001.svg").startswith(b"<?xml")


def test_batch_pdf_sheet(client):
    items = [{"type": "qr", "data": f"item-{i}", "caption": f"Item {i}"} for i in range(30)]
    body = {"items": items, "output": "pdf", "sheet": {"page": "A4", "columns": 4, "rows": 6, "captions": True, "title": "test sheet"}}
    r = client.post("/api/v1/batch", json=body)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-" and r.content.count(b"/Type /Page\n") >= 2 or b"/Count 2" in r.content


def test_batch_limits(client):
    r = client.post("/api/v1/batch", json={"items": [], "output": "json"})
    assert r.status_code == 422
    r = client.post("/api/v1/batch", json={"items": [{"type": "qr", "data": ""}], "output": "pdf"})
    assert r.status_code == 400


def test_label_sheet_pages():
    png = make_qr("x", scale=2).content
    pdf = label_sheet([(png, "cap")] * 5, columns=2, rows=2)
    assert pdf[:5] == b"%PDF-"


@needs_gs
def test_batch_mixed_barcode(client):
    body = {"items": [{"type": "barcode", "bcid": "code128", "text": "A1", "options": {"includetext": True}}, {"type": "qr", "data": "q"}], "output": "json"}
    j = client.post("/api/v1/batch", json=body).json()
    assert j["succeeded"] == 2
