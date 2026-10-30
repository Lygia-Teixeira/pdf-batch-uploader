"""Teste ponta a ponta contra o portal de demonstração local (Playwright + Chromium).

É ignorado automaticamente se o Playwright ou o Chromium não estiverem instalados.
Rodar só ele:  pytest -m e2e
"""

from __future__ import annotations

import copy
import functools
import http.server
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from pdf_batch_uploader.automation.playwright_uploader import PlaywrightUploader  # noqa: E402
from pdf_batch_uploader.core.history import HistoryLog  # noqa: E402
from pdf_batch_uploader.core.journal import BROWSER_DONE, PENDING, BatchJournal  # noqa: E402
from pdf_batch_uploader.core.models import Tenant  # noqa: E402
from pdf_batch_uploader.core.planner import build_plan  # noqa: E402
from pdf_batch_uploader.core.runner import BatchRunner  # noqa: E402

pytestmark = pytest.mark.e2e
DEMO_DIR = Path(__file__).resolve().parent.parent / "demo_portal"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def portal_url():
    handler = functools.partial(_QuietHandler, directory=str(DEMO_DIR))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture
def tenant_at(portal_url, demo_db):
    def _t(tid: str) -> Tenant:
        d = copy.deepcopy(demo_db["tenants"][tid])
        d["portal"]["base_url"] = portal_url
        return Tenant.from_dict(tid, d)
    return _t


@pytest.fixture
def uploader():
    up = PlaywrightUploader("auto", headless=True, login_timeout_s=10, action_timeout_s=10)
    yield up
    up.close()


def _operator_login(up: PlaywrightUploader, tenant: Tenant):
    try:
        up.open(tenant)
    except Exception as exc:  # Chromium ausente
        pytest.skip(f"Chromium do Playwright indisponível: {exc}")
    up.page.click("#login-btn")  # simula o login manual do operador


def _run(tenant, files, up, paths):
    plan = build_plan(tenant, files)
    assert plan.ready, [i.issues for i in plan.items]
    journal = BatchJournal.create(paths.batches_dir, tenant, "auto", plan.items)
    runner = BatchRunner(tenant, plan.items, up, journal, HistoryLog(paths.history_file), "auto")
    return runner.run(), journal


def test_auto_mode_switches_tenant_and_uploads(tenant_at, uploader, make_pdf, paths):
    alfa = tenant_at("alfa")
    files = [make_pdf("folha_ativos_2026-09.pdf"), make_pdf("folha_ativos_13o_2025.pdf")]
    _operator_login(uploader, alfa)
    assert uploader.page.inner_text("#active-tenant") == "EMPRESA BETA S.A."  # começa no tenant errado

    res, journal = _run(alfa, files, uploader, paths)

    assert res.done == [f.name for f in files], res.aborted_reason
    assert journal.counts()[BROWSER_DONE] == 2
    receipts = uploader.page.locator("#receipts li").all_inner_texts()
    assert receipts == [
        "EMPRESA ALFA LTDA | 2026-09 | MONTHLY/ACTIVE | folha_ativos_2026-09.pdf",
        "EMPRESA ALFA LTDA | 2025-12 | ANNUAL_BONUS/ACTIVE | folha_ativos_13o_2025.pdf",
    ]


def test_similar_tenant_name_is_not_confused(tenant_at, uploader, make_pdf, paths):
    filial = tenant_at("alfa-filial")
    _operator_login(uploader, filial)
    res, _ = _run(filial, [make_pdf("folha_mensal_2026-09.pdf")], uploader, paths)
    assert not res.aborted_reason
    assert uploader.page.inner_text("#active-tenant") == "EMPRESA ALFA LTDA FILIAL NORTE"


def test_tenant_switched_mid_batch_aborts(tenant_at, make_pdf, paths, uploader):
    alfa = tenant_at("alfa")

    class Sabotage(PlaywrightUploader):
        def upload(self, tenant, item):
            out = super().upload(tenant, item)
            # outra aba/colega troca o tenant entre os envios
            self.page.select_option("#tenant-picker", "beta")
            self.page.click("#tenant-activate")
            self.page.wait_for_timeout(400)
            return out

    up = Sabotage("auto", headless=True, login_timeout_s=10, action_timeout_s=10)
    try:
        _operator_login(up, alfa)
        files = [make_pdf("folha_ativos_2026-09.pdf"), make_pdf("folha_aposentados_2026-09.pdf")]
        res, journal = _run(alfa, files, up, paths)
        assert res.done == [files[0].name]
        assert "tenant" in res.aborted_reason
        assert journal.counts()[PENDING] == 1
        assert len(up.page.locator("#receipts li").all_inner_texts()) == 1
    finally:
        up.close()


def test_manual_mode_waits_for_operator_and_rejects_wrong_tenant(tenant_at, make_pdf, paths):
    alfa = tenant_at("alfa")
    up = PlaywrightUploader("manual", headless=True, login_timeout_s=2, action_timeout_s=5)
    try:
        _operator_login(up, alfa)
        # operador não troca o tenant (fica em BETA): nada pode ser enviado
        res, journal = _run(alfa, [make_pdf("folha_ativos_2026-09.pdf")], up, paths)
        assert res.done == [] and "tenant" in res.aborted_reason
        assert up.page.locator("#receipts li").count() == 0
    finally:
        up.close()
