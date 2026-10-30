"""Simulação, execução com uploader falso, duplicidade por hash e recuperação."""

from __future__ import annotations

import json

import pytest

from pdf_batch_uploader.core.history import EVENT_BROWSER_DONE, HistoryLog
from pdf_batch_uploader.core.journal import BROWSER_DONE, FAILED, PENDING, STARTED, BatchJournal, prepare_recovery
from pdf_batch_uploader.core.planner import SentIndex, build_plan, format_plan
from pdf_batch_uploader.core.runner import BatchNotReady, BatchRunner
from pdf_batch_uploader.core.tenant_guard import TenantMismatch, assert_tenant


class FakeUploader:
    """Simula o portal: guarda o tenant ativo e os envios recebidos."""

    def __init__(self, active: str, fail_on: str | None = None, switch_after: int | None = None, on_upload=None):
        self.active = active
        self.sent: list[str] = []
        self.fail_on = fail_on
        self.switch_after = switch_after
        self.on_upload = on_upload

    def prepare(self, tenant):
        pass

    def verify_tenant(self, tenant):
        return assert_tenant([self.active], tenant.portal_name)

    def upload(self, tenant, item):
        if item.name == self.fail_on:
            raise RuntimeError("timeout simulado")
        self.sent.append(item.name)
        if self.on_upload:
            self.on_upload(self, item)
        if self.switch_after is not None and len(self.sent) == self.switch_after:
            self.active = "EMPRESA BETA S.A."  # alguém trocou o tenant no meio do lote
        return f"Recebido: {item.name}"


@pytest.fixture
def three_ready(make_pdf):
    return [
        make_pdf("folha_ativos_2026-09.pdf"),
        make_pdf("folha_aposentados_2026-09.pdf"),
        make_pdf("ferias_2026-07.pdf"),
    ]


def run_batch(alfa, plan, paths, uploader, **kw):
    hist = HistoryLog(paths.history_file)
    journal = BatchJournal.create(paths.batches_dir, alfa, "auto", plan.items)
    runner = BatchRunner(alfa, plan.items, uploader, journal, hist, "auto", **kw)
    return runner, runner.run(), journal, hist


# -- simulação ---------------------------------------------------------------

def test_simulation_lists_everything_and_flags_problems(alfa, make_pdf):
    make_pdf("folha_ativos_2026-09.pdf")
    make_pdf("ferias_mensal_2026-08.pdf")
    make_pdf("sem_competencia_ativos.pdf")
    (make_pdf.folder / "falso.pdf").write_bytes(b"isto nao e um pdf")
    files = sorted(make_pdf.folder.iterdir())
    plan = build_plan(alfa, files)
    by = {i.name: i for i in plan.items}

    assert by["folha_ativos_2026-09.pdf"].ready
    assert by["folha_ativos_2026-09.pdf"].profile == "Mensal - Ativos"
    assert any("ambíguos" in x for x in by["ferias_mensal_2026-08.pdf"].issues)
    assert any("competência" in x for x in by["sem_competencia_ativos.pdf"].issues)
    assert any("assinatura PDF" in x for x in by["falso.pdf"].issues)
    assert not plan.ready
    text = format_plan(plan)
    assert "nenhum envio executado" in text


def test_overrides_resolve_pending(alfa, make_pdf):
    f = make_pdf("ferias_mensal_2026-08.pdf")
    plan = build_plan(alfa, [f], profile_overrides={f.name: "Ferias"})
    assert plan.ready and plan.items[0].profile == "Ferias"
    plan = build_plan(alfa, [f], profile_overrides={f.name: "Nao existe"})
    assert not plan.ready


def test_same_content_twice_in_batch_blocked(alfa, make_pdf):
    a = make_pdf("folha_ativos_2026-09.pdf", label="X")
    b = make_pdf("copia_folha_ativos_2026-09.pdf", label="X")
    plan = build_plan(alfa, [a, b])
    assert plan.items[0].ready
    assert any("idêntico" in x for x in plan.items[1].issues)


def test_personal_looking_filename_warned_and_redacted(alfa, make_pdf, paths):
    f = make_pdf("folha_ativos_2026-09_123.456.789-09.pdf")
    plan = build_plan(alfa, [f])
    assert any("dado pessoal" in w for w in plan.items[0].warnings)
    _, res, _, hist = run_batch(alfa, plan, paths, FakeUploader("EMPRESA ALFA LTDA"))
    raw = paths.history_file.read_text(encoding="utf-8")
    assert "123.456.789-09" not in raw
    assert "[doc-id]" in raw


# -- execução ----------------------------------------------------------------

def test_runner_happy_path_and_history_has_no_content(alfa, three_ready, paths):
    plan = build_plan(alfa, three_ready)
    up = FakeUploader("EMPRESA ALFA LTDA")
    _, res, journal, hist = run_batch(alfa, plan, paths, up)
    assert res.done == [p.name for p in three_ready] and not res.aborted_reason
    assert journal.counts()[BROWSER_DONE] == 3
    events = list(hist.read())
    assert sum(e["event"] == EVENT_BROWSER_DONE for e in events) == 3
    raw = paths.history_file.read_text(encoding="utf-8")
    assert "DOCUMENTO FICTICIO" not in raw  # conteúdo do PDF nunca vai ao log
    assert all(len(e["sha256"]) == 64 for e in events if e["file_name"])


def test_runner_refuses_plan_with_pending(alfa, make_pdf, paths):
    plan = build_plan(alfa, [make_pdf("sem_nada.pdf")])
    with pytest.raises(BatchNotReady):
        BatchRunner(alfa, plan.items, FakeUploader("x"), None, None, "auto")


def test_wrong_tenant_aborts_before_any_upload(alfa, three_ready, paths):
    plan = build_plan(alfa, three_ready)
    up = FakeUploader("EMPRESA ALFA LTDA FILIAL NORTE")
    _, res, journal, _ = run_batch(alfa, plan, paths, up)
    assert up.sent == []
    assert "tenant" in res.aborted_reason
    assert journal.counts()[PENDING] == 3


def test_tenant_switch_mid_batch_stops_remaining(alfa, three_ready, paths):
    plan = build_plan(alfa, three_ready)
    up = FakeUploader("EMPRESA ALFA LTDA", switch_after=1)
    _, res, journal, _ = run_batch(alfa, plan, paths, up)
    assert len(up.sent) == 1
    assert res.pending == [p.name for p in three_ready[1:]]
    c = journal.counts()
    assert c[BROWSER_DONE] == 1 and c[PENDING] == 2


def test_failure_stops_batch_and_marks_failed(alfa, three_ready, paths):
    plan = build_plan(alfa, three_ready)
    up = FakeUploader("EMPRESA ALFA LTDA", fail_on=three_ready[1].name)
    _, res, journal, _ = run_batch(alfa, plan, paths, up)
    assert res.failed == [three_ready[1].name]
    states = {d["name"]: d["state"] for d in journal.data["documents"]}
    assert states == {three_ready[0].name: BROWSER_DONE, three_ready[1].name: FAILED, three_ready[2].name: PENDING}


def test_cancel_between_files(alfa, three_ready, paths):
    plan = build_plan(alfa, three_ready)
    holder = {}
    up = FakeUploader("EMPRESA ALFA LTDA", on_upload=lambda u, i: holder["r"].cancel())
    hist = HistoryLog(paths.history_file)
    journal = BatchJournal.create(paths.batches_dir, alfa, "auto", plan.items)
    runner = BatchRunner(alfa, plan.items, up, journal, hist, "auto")
    holder["r"] = runner
    res = runner.run()
    assert res.cancelled and len(up.sent) == 1 and len(res.pending) == 2


def test_pause_blocks_until_resume(alfa, three_ready, paths):
    import threading

    plan = build_plan(alfa, three_ready)
    paused = threading.Event()
    holder = {}

    def after_first(u, item):
        if len(u.sent) == 1:
            holder["r"].pause()

    up = FakeUploader("EMPRESA ALFA LTDA", on_upload=after_first)
    journal = BatchJournal.create(paths.batches_dir, alfa, "auto", plan.items)
    runner = BatchRunner(alfa, plan.items, up, journal, HistoryLog(paths.history_file), "auto",
                         on_message=lambda m: paused.set() if "pausado" in m else None)
    holder["r"] = runner
    t = threading.Thread(target=runner.run)
    t.start()
    assert paused.wait(5)
    assert len(up.sent) == 1
    runner.resume()
    t.join(5)
    assert len(up.sent) == 3


# -- duplicidade ---------------------------------------------------------------

def test_duplicate_hash_blocked_unless_allowed(alfa, three_ready, paths, store):
    plan = build_plan(alfa, three_ready)
    run_batch(alfa, plan, paths, FakeUploader("EMPRESA ALFA LTDA"))
    sent = SentIndex.build(HistoryLog(paths.history_file), paths.batches_dir)

    renamed = three_ready[0].with_name("folha_ativos_renomeado_2026-09.pdf")
    three_ready[0].rename(renamed)
    again = build_plan(alfa, [renamed], sent=sent)
    assert any("já percorreu" in x for x in again.items[0].issues)  # renomear não engana o hash
    assert build_plan(alfa, [renamed], sent=sent, allow_resend=True).ready

    filial = store.tenant("alfa-filial")
    other = build_plan(filial, [three_ready[2]], sent=sent, profile_overrides={three_ready[2].name: "Mensal - Todos"})
    assert any("OUTRO tenant" in w for w in other.items[0].warnings)


def test_started_items_count_as_uncertain(alfa, three_ready, paths):
    plan = build_plan(alfa, three_ready)
    journal = BatchJournal.create(paths.batches_dir, alfa, "auto", plan.items)
    journal.set_state(plan.items[0].sha256, STARTED)  # processo "caiu" durante o envio
    sent = SentIndex.build(None, paths.batches_dir)
    assert not build_plan(alfa, three_ready[:1], sent=sent).ready


# -- recuperação ---------------------------------------------------------------

def test_recovery_only_pending(alfa, three_ready, paths):
    plan = build_plan(alfa, three_ready)
    _, res, journal, _ = run_batch(alfa, plan, paths, FakeUploader("EMPRESA ALFA LTDA", switch_after=1))
    rec = prepare_recovery(BatchJournal.load(journal.path), alfa)
    assert rec.safe
    assert [d["name"] for d in rec.ready] == [p.name for p in three_ready[1:]]
    assert [d["name"] for d in rec.needs_review] == [three_ready[0].name]


def test_recovery_blocks_changed_file(alfa, three_ready, paths):
    plan = build_plan(alfa, three_ready)
    journal = BatchJournal.create(paths.batches_dir, alfa, "auto", plan.items)
    three_ready[1].write_bytes(three_ready[1].read_bytes() + b"\n% alterado")
    rec = prepare_recovery(journal, alfa)
    assert not rec.safe
    assert any("hash diferente" in i for i in rec.issues)


def test_recovery_blocks_changed_profile(alfa, three_ready, paths, store):
    plan = build_plan(alfa, three_ready)
    journal = BatchJournal.create(paths.batches_dir, alfa, "auto", plan.items)
    data = store.data
    data["tenants"]["alfa"]["profiles"]["Ferias"]["fields"]["audience"] = "ACTIVE"
    store.save(data)
    rec = prepare_recovery(journal, store.tenant("alfa"))
    assert any("foi alterado" in i for i in rec.issues)


def test_recovery_other_tenant_refused(alfa, three_ready, paths, store):
    plan = build_plan(alfa, three_ready)
    journal = BatchJournal.create(paths.batches_dir, alfa, "auto", plan.items)
    rec = prepare_recovery(journal, store.tenant("alfa-filial"))
    assert not rec.safe and rec.issues == ["lote pertence a outro tenant"]


def test_corrupted_journal_raises(paths):
    (paths.batches_dir / "batch_x.json").write_text(json.dumps({"documents": [{"state": "???"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        SentIndex.build(None, paths.batches_dir)
