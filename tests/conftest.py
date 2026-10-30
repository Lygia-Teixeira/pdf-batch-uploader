from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pdf_batch_uploader.core.models import Tenant
from pdf_batch_uploader.core.store import TenantStore
from pdf_batch_uploader.demo import minimal_pdf
from pdf_batch_uploader.paths import DataPaths

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "examples" / "tenants.demo.json"


@pytest.fixture
def demo_db() -> dict:
    data = json.loads(DEMO_DB.read_text(encoding="utf-8"))
    data["kind"] = "database"
    return data


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    return DataPaths(tmp_path / "data").ensure()


@pytest.fixture
def store(paths, demo_db) -> TenantStore:
    s = TenantStore(paths.tenants_file, paths.backups_dir)
    s.save(copy.deepcopy(demo_db), backup=False)
    return s.load()


@pytest.fixture
def alfa(store) -> Tenant:
    return store.tenant("alfa")


@pytest.fixture
def make_pdf(tmp_path):
    folder = tmp_path / "pdfs"
    folder.mkdir()

    def _make(name: str, label: str | None = None) -> Path:
        p = folder / name
        p.write_bytes(minimal_pdf(label or name))
        return p

    _make.folder = folder
    return _make
