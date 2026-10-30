"""Interface de linha de comando.

Exemplos::

    python -m pdf_batch_uploader demo-setup --samples ./amostras
    python -m pdf_batch_uploader tenants list
    python -m pdf_batch_uploader simulate --tenant alfa --dir ./amostras
    python -m pdf_batch_uploader run --tenant alfa --dir ./amostras --mode auto
    python -m pdf_batch_uploader recover --tenant alfa
    python -m pdf_batch_uploader history --export-csv historico.csv
    python -m pdf_batch_uploader profiles export --tenant alfa --out alfa.json
    python -m pdf_batch_uploader profiles import alfa.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core.files import list_pdfs
from .core.history import HistoryLog
from .core.journal import BatchJournal, prepare_recovery
from .core.merge import export_tenants, merge_package
from .core.planner import PlanItem, SentIndex, build_plan, format_plan
from .core.runner import BatchRunner
from .core.schema import SchemaError, validate_db
from .core.store import TenantStore, atomic_write_json, load_json
from .core.text import normalize_tenant_name
from .paths import DataPaths

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _ctx(args) -> tuple[DataPaths, TenantStore, HistoryLog]:
    paths = DataPaths.default(args.data_dir).ensure()
    store = TenantStore(paths.tenants_file, paths.backups_dir).load()
    return paths, store, HistoryLog(paths.history_file)


def _kv(pairs: list[str] | None, what: str) -> dict[str, str]:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"{what} deve ser ARQUIVO=VALOR: {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _plan(args, paths, store, history):
    tenant = store.tenant(args.tenant)
    files = list_pdfs(Path(args.dir), recursive=getattr(args, "recursive", False))
    sent = SentIndex.build(history, paths.batches_dir)
    plan = build_plan(
        tenant,
        files,
        profile_overrides=_kv(args.profile, "--profile"),
        period_overrides=_kv(args.period, "--period"),
        sent=sent,
        allow_resend=getattr(args, "allow_resend", False),
    )
    return tenant, plan


def _confirm(tenant, count: int, mode: str, given: str | None) -> bool:
    print(f"\nModo {mode.upper()}: {count} arquivo(s) para o tenant {tenant.portal_name!r} ({tenant.id}).")
    print("Para confirmar, digite exatamente o nome do tenant no portal.")
    answer = given if given is not None else input("> ")
    return normalize_tenant_name(answer) == normalize_tenant_name(tenant.portal_name)


def _execute(tenant, items: list[PlanItem], mode: str, args, paths, history) -> int:
    from .automation.playwright_uploader import PlaywrightUploader

    journal = BatchJournal.create(paths.batches_dir, tenant, mode, items)
    uploader = PlaywrightUploader(mode, headless=args.headless, login_timeout_s=args.login_timeout, on_message=print)
    runner = BatchRunner(tenant, items, uploader, journal, history, mode, on_message=print)
    print(f"Lote {journal.id}. Ctrl+C cancela após o arquivo atual.")
    result = None
    try:
        result = runner.run()
    except KeyboardInterrupt:
        runner.cancel()
        print("Cancelamento solicitado.")
    finally:
        uploader.close()
    if result is None:
        return 130
    print(json.dumps({"done": result.done, "failed": result.failed, "pending": result.pending,
                      "aborted": result.aborted_reason, "cancelled": result.cancelled}, ensure_ascii=False, indent=2))
    return 0 if not (result.failed or result.aborted_reason or result.cancelled) else 2


# --- comandos ---------------------------------------------------------------

def cmd_tenants(args) -> int:
    paths, store, _ = _ctx(args)
    if args.action == "list":
        for tid, t in store.tenants().items():
            print(f"{tid:20} {t.portal_name!r:45} perfis: {', '.join(t.profiles) or '—'}")
        return 0
    data = load_json(Path(args.file)) if args.file else store.data
    errors = validate_db(data)
    for e in errors:
        print("ERRO:", e)
    print("válido" if not errors else f"{len(errors)} erro(s)")
    return 0 if not errors else 1


def cmd_simulate(args) -> int:
    paths, store, history = _ctx(args)
    _, plan = _plan(args, paths, store, history)
    if args.json:
        print(json.dumps([i.to_dict() for i in plan.items], ensure_ascii=False, indent=2))
    else:
        print(format_plan(plan))
    return 0 if plan.ready else 1


def cmd_run(args) -> int:
    paths, store, history = _ctx(args)
    tenant, plan = _plan(args, paths, store, history)
    print(format_plan(plan))
    if not plan.ready:
        print("\nLote bloqueado: resolva as pendências acima (simulação obrigatória).")
        return 1
    if args.mode == "auto" and not _confirm(tenant, len(plan.items), args.mode, args.confirm_tenant):
        print("Confirmação não confere. Nada foi enviado.")
        return 1
    return _execute(tenant, plan.items, args.mode, args, paths, history)


def cmd_recover(args) -> int:
    paths, store, history = _ctx(args)
    tenant = store.tenant(args.tenant)
    journals = BatchJournal.list_for_tenant(paths.batches_dir, tenant.id)
    if args.batch:
        journals = [j for j in journals if j.id == args.batch]
    if not journals:
        print("Nenhum lote encontrado para este tenant.")
        return 1
    if not args.batch:
        for j in journals:
            print(j.id, j.data["created_at"], j.data["mode"], j.counts())
        print("\nUse --batch ID para preparar a retomada de um lote.")
        return 0
    res = prepare_recovery(journals[0], tenant)
    for d in res.needs_review:
        print(f"CONFERIR NO PORTAL (não será reenviado): {d['name']} [{d['state']}]")
    for i in res.issues:
        print("BLOQUEIO:", i)
    print(f"{len(res.ready)} pendente(s) íntegro(s).")
    if not res.safe or not args.run:
        return 0 if res.safe else 1
    items = [
        PlanItem(Path(d["path"]), d["name"], d["size"], d["sha256"], d["period"], d["annual_bonus"], d["profile"])
        for d in res.ready
    ]
    mode = journals[0].data.get("mode", "manual")
    if mode == "auto" and not _confirm(tenant, len(items), mode, args.confirm_tenant):
        print("Confirmação não confere. Nada foi enviado.")
        return 1
    # Marca os itens retomados como "skipped" no lote original para não serem contados duas vezes.
    for d in res.ready:
        journals[0].set_state(d["sha256"], "skipped", "retomado em novo lote")
    return _execute(tenant, items, mode, args, paths, history)


def cmd_history(args) -> int:
    paths, _, history = _ctx(args)
    if args.export_csv:
        n = history.export_csv(Path(args.export_csv))
        print(f"{n} evento(s) exportado(s) para {args.export_csv}")
        return 0
    for e in list(history.read())[-args.tail:]:
        print(f"{e['ts']} {e['tenant_id']:12} {e['event']:18} {e['file_name']} {e['sha256'][:12]} {e['detail']}")
    return 0


def cmd_profiles(args) -> int:
    paths, store, _ = _ctx(args)
    if args.action == "export":
        package = export_tenants(store.data, args.tenant)
        atomic_write_json(Path(args.out), package)
        print(f"Exportado(s) {len(args.tenant)} tenant(s) para {args.out} (sem histórico, lotes ou credenciais).")
        return 0
    package = load_json(Path(args.file))
    merged, report = merge_package(store.data, package)
    print(report.summary())
    if args.dry_run:
        print("(dry-run: nada foi gravado)")
        return 0
    backup = store.save(merged)
    if backup:
        print(f"Backup do cadastro anterior: {backup}")
    return 0


def cmd_demo_setup(args) -> int:
    from .demo import write_samples

    paths, store, _ = _ctx(args)
    merged, report = merge_package(store.data, load_json(EXAMPLES_DIR / "tenants.demo.json"))
    store.save(merged)
    print(report.summary())
    if args.samples:
        for p in write_samples(Path(args.samples)):
            print("amostra:", p)
    print(f"Dados locais em: {paths.root}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pdf_batch_uploader", description="Envio em lote de PDFs para portais multi-tenant")
    p.add_argument("--data-dir", help="pasta de dados (padrão: %%LOCALAPPDATA%%\\PdfBatchUploader)")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tenants", help="listar/validar cadastro")
    t.add_argument("action", choices=["list", "validate"])
    t.add_argument("--file", help="validar um arquivo específico")
    t.set_defaults(func=cmd_tenants)

    def plan_args(sp):
        sp.add_argument("--tenant", required=True)
        sp.add_argument("--dir", required=True, help="pasta com os PDFs")
        sp.add_argument("--recursive", action="store_true")
        sp.add_argument("--profile", action="append", metavar="ARQUIVO=PERFIL", help="perfil manual por arquivo")
        sp.add_argument("--period", action="append", metavar="ARQUIVO=AAAA-MM", help="competência manual por arquivo")
        sp.add_argument("--allow-resend", action="store_true", help="permite reenviar hash já registrado (após conferir no portal)")

    s = sub.add_parser("simulate", help="dry-run: lista arquivos, competência, perfil e pendências")
    plan_args(s)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_simulate)

    def exec_args(sp):
        sp.add_argument("--headless", action="store_true", help="sem janela (apenas para o portal demo/testes)")
        sp.add_argument("--login-timeout", type=float, default=180)
        sp.add_argument("--confirm-tenant", help="confirmação não interativa (modo auto)")

    r = sub.add_parser("run", help="executa o lote no navegador")
    plan_args(r)
    r.add_argument("--mode", choices=["manual", "auto"], default="manual")
    exec_args(r)
    r.set_defaults(func=cmd_run)

    rc = sub.add_parser("recover", help="lista lotes e retoma somente pendentes")
    rc.add_argument("--tenant", required=True)
    rc.add_argument("--batch")
    rc.add_argument("--run", action="store_true", help="executa os pendentes íntegros")
    exec_args(rc)
    rc.set_defaults(func=cmd_recover)

    h = sub.add_parser("history", help="mostra/exporta histórico local")
    h.add_argument("--export-csv")
    h.add_argument("--tail", type=int, default=30)
    h.set_defaults(func=cmd_history)

    pr = sub.add_parser("profiles", help="exportar/importar tenants e perfis")
    prs = pr.add_subparsers(dest="action", required=True)
    ex = prs.add_parser("export")
    ex.add_argument("--tenant", action="append", required=True)
    ex.add_argument("--out", required=True)
    im = prs.add_parser("import")
    im.add_argument("file")
    im.add_argument("--dry-run", action="store_true")
    pr.set_defaults(func=cmd_profiles)

    d = sub.add_parser("demo-setup", help="importa tenants de demonstração e gera PDFs fictícios")
    d.add_argument("--samples", help="pasta onde gerar PDFs fictícios")
    d.set_defaults(func=cmd_demo_setup)

    sub.add_parser("ui", help="abre a interface Tkinter").set_defaults(func=lambda a: _ui(a))
    return p


def _ui(args) -> int:
    from .ui.app import main as ui_main

    return ui_main(args.data_dir)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SchemaError as exc:
        print("Cadastro inválido:", file=sys.stderr)
        for e in exc.errors:
            print("  -", e, file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
