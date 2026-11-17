from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("FIXTURES_DIR", str(ROOT / "fixtures"))


@pytest.fixture(scope="module")
def data_dir(request):
    d = ROOT / "backend" / f"data_{request.module.__name__}"
    db = d / "app.db"
    if db.exists():
        db.unlink()
    os.environ["DATA_DIR"] = str(d)
    return d


@pytest.fixture(scope="module")
def client(data_dir):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def db_file(data_dir):
    return data_dir / "app.db"
