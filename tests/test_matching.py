from pdf_batch_uploader.core.matching import suggest_profile
from pdf_batch_uploader.core.models import Profile


def P(name, keywords, exclude=(), bonus=False):
    return Profile(name=name, keywords=tuple(keywords), exclude_keywords=tuple(exclude), annual_bonus=bonus, fields={})


PROFILES = {
    "Mensal - Ativos": P("Mensal - Ativos", ["ativos", "mensal"], exclude=["aposentados"]),
    "Mensal - Aposentados": P("Mensal - Aposentados", ["aposentados"]),
    "13 - Ativos": P("13 - Ativos", ["ativos"], bonus=True),
    "Ferias": P("Ferias", ["ferias"]),
}


def test_single_match():
    assert suggest_profile("folha_ativos_2026-09.pdf", PROFILES).profile == "Mensal - Ativos"


def test_accent_and_case_insensitive():
    assert suggest_profile("FÉRIAS_2026-07.pdf", PROFILES).profile == "Ferias"


def test_exclude_keyword_disqualifies():
    s = suggest_profile("folha_ativos_aposentados_2026-09.pdf", PROFILES)
    assert s.profile == "Mensal - Aposentados"


def test_annual_bonus_profiles_only_for_bonus_files():
    assert suggest_profile("folha_ativos_13o_2025.pdf", PROFILES).profile == "13 - Ativos"
    assert suggest_profile("folha_ativos_2025-12.pdf", PROFILES).profile == "Mensal - Ativos"


def test_ambiguity_is_reported_not_guessed():
    s = suggest_profile("ferias_mensal_2026-08.pdf", PROFILES)
    assert s.profile is None
    assert s.ambiguous
    assert set(s.candidates) == {"Ferias", "Mensal - Ativos"}
    assert "ambíguos" in s.reason


def test_more_specific_profile_wins():
    s = suggest_profile("ativos_mensal_2026-09.pdf", {**PROFILES, "So ativos": P("So ativos", ["ativos"])})
    assert s.profile == "Mensal - Ativos"


def test_whole_word_only():
    # "inativos" não deve casar com a keyword "ativos"
    assert suggest_profile("folha_inativos_2026-09.pdf", PROFILES).profile is None


def test_no_match():
    s = suggest_profile("qualquer_2026-09.pdf", PROFILES)
    assert s.profile is None and not s.ambiguous
