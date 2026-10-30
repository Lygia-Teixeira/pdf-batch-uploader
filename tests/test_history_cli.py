import csv
import json

from pdf_batch_uploader.cli import main
from pdf_batch_uploader.core.history import HistoryLog
from pdf_batch_uploader.core.text import redact
from pdf_batch_uploader.demo import write_samples


def test_redact_patterns():
    s = "fulano@exemplo.com.br 123.456.789-09 12345678909 12.345.678/0001-90 matricula 1234567890"
    out = redact(s)
    for secret in ("fulano@", "123.456.789-09", "12345678909", "12.345.678/0001-90", "1234567890"):
        assert secret not in out


def test_csv_export_neutralizes_formulas(tmp_path):
    h = HistoryLog(tmp_path / "h.jsonl")
    h.append("item_failed", file_name="=HYPERLINK(1).pdf", detail="+cmd")
    target = tmp_path / "out.csv"
    assert h.export_csv(target) == 1
    rows = list(csv.reader(target.open(encoding="utf-8-sig"), delimiter=";"))
    assert rows[1][5].startswith("'=")
    assert rows[1][10].startswith("'+")


def test_cli_simulate_json(tmp_path, capsys):
    data = tmp_path / "data"
    samples = tmp_path / "amostras"
    assert main(["--data-dir", str(data), "demo-setup", "--samples", str(samples)]) == 0
    capsys.readouterr()
    rc = main(["--data-dir", str(data), "simulate", "--tenant", "alfa", "--dir", str(samples), "--json"])
    items = {i["name"]: i for i in json.loads(capsys.readouterr().out)}
    assert rc == 1  # amostras incluem pendências de propósito
    assert items["folha_ativos_2026-09.pdf"]["profile"] == "Mensal - Ativos"
    assert items["folha_ativos_13o_2025.pdf"]["period"] == "2025-12"
    assert not items["ferias_mensal_2026-08.pdf"]["ready"]
    assert not items["documento_sem_competencia.pdf"]["ready"]

    rc = main([
        "--data-dir", str(data), "simulate", "--tenant", "alfa", "--dir", str(samples),
        "--profile", "ferias_mensal_2026-08.pdf=Ferias",
        "--period", "documento_sem_competencia.pdf=2026-06",
        "--profile", "documento_sem_competencia.pdf=Mensal - Ativos",
    ])
    assert rc == 0, capsys.readouterr().out


def test_cli_profiles_roundtrip(tmp_path, capsys):
    data = tmp_path / "data"
    main(["--data-dir", str(data), "demo-setup"])
    out = tmp_path / "alfa.json"
    assert main(["--data-dir", str(data), "profiles", "export", "--tenant", "alfa", "--out", str(out)]) == 0
    capsys.readouterr()
    assert main(["--data-dir", str(data), "profiles", "import", str(out), "--dry-run"]) == 0
    assert "Idênticos ignorados: 4" in capsys.readouterr().out


def test_cli_validate_reports_errors(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": 1, "tenants": {"x": {"portal_name": 1}}}), encoding="utf-8")
    assert main(["--data-dir", str(tmp_path / "d"), "tenants", "validate", "--file", str(bad)]) == 1
    assert "ERRO" in capsys.readouterr().out


def test_write_samples_are_pdfs(tmp_path):
    for p in write_samples(tmp_path):
        assert p.read_bytes().startswith(b"%PDF-")
