import os

import pytest

os.environ.setdefault("CODEFORGE_RATE_LIMIT", "0")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def gs_available() -> bool:
    import shutil

    return shutil.which("gs") is not None


def zbar_available() -> bool:
    try:
        import pyzbar.pyzbar  # noqa: F401

        return True
    except Exception:
        return False


needs_gs = pytest.mark.skipif(not gs_available(), reason="ghostscript not installed")
needs_zbar = pytest.mark.skipif(not zbar_available(), reason="libzbar not installed")
