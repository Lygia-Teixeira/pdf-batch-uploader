import pytest

from pdf_batch_uploader.core.period import extract_period, has_annual_bonus_marker, is_valid_period


@pytest.mark.parametrize(
    "name, period",
    [
        ("folha_2026-09.pdf", "2026-09"),
        ("folha_09-2026.pdf", "2026-09"),
        ("folha 09.2026 ativos.pdf", "2026-09"),
        ("folha_202609.pdf", "2026-09"),
        ("FOLHA_2026_01.PDF", "2026-01"),
        ("relatorio_2026-09-13.pdf", "2026-09"),
    ],
)
def test_monthly_formats(name, period):
    info = extract_period(name)
    assert info.period == period
    assert info.annual_bonus is False


@pytest.mark.parametrize(
    "name, period",
    [
        ("folha_13o_2025.pdf", "2025-12"),
        ("folha_13º_2025.pdf", "2025-12"),
        ("folha_2025-13.pdf", "2025-12"),
        ("folha_13-2025.pdf", "2025-12"),
        ("Décimo Terceiro 2025.pdf", "2025-12"),
        ("gratificacao_natalina_2025.pdf", "2025-12"),
        ("folha_13o_2025-11.pdf", "2025-11"),  # mês explícito tem precedência
    ],
)
def test_annual_bonus(name, period):
    info = extract_period(name)
    assert info.annual_bonus is True
    assert info.period == period


def test_bare_13_is_not_annual_bonus():
    assert not has_annual_bonus_marker("lote_13_folha_2026-09.pdf")
    assert not has_annual_bonus_marker("relatorio_2026-09-13.pdf")


def test_missing_and_conflicting():
    assert extract_period("documento.pdf").period == ""
    conflict = extract_period("folha_2026-09_e_2026-10.pdf")
    assert conflict.period == ""
    assert "conflitantes" in conflict.note


def test_invalid_month_not_accepted():
    assert extract_period("folha_2026-00.pdf").period == ""
    assert extract_period("folha_1899-05.pdf").period == ""


@pytest.mark.parametrize("value, ok", [("2026-09", True), ("2026-13", False), ("26-09", False), ("", False)])
def test_is_valid_period(value, ok):
    assert is_valid_period(value) is ok
