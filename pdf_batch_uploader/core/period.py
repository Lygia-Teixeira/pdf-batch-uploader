"""Extração da competência (mês de referência) a partir do nome do arquivo.

Formatos reconhecidos (separadores ``-``, ``_``, ``.`` ou espaço):

* ``2026-09``  -> 2026-09
* ``09-2026``  -> 2026-09
* ``202609``   -> 2026-09
* 13º salário: ``2025-13``, ``13-2025``, ``13o``, ``13º``, ``13°``,
  ``decimo terceiro``, ``13 salario``, ``gratificacao natalina``.
  Quando não houver mês explícito, a competência do 13º segue a convenção
  ``AAAA-12`` (configurável via ``annual_bonus_month``).

Um "13" isolado **não** marca 13º (evita falso positivo em datas como 2026-09-13).
Se mais de uma competência distinta aparecer no nome, o resultado é ambíguo e
retorna vazio — o operador precisa informar manualmente.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .text import normalize_key

PERIOD_RE = re.compile(r"^(?:19|20)\d{2}-(?:0[1-9]|1[0-2])$")

_SEP = r"[-_. ]"
_YEAR = r"(?:19|20)\d{2}"
_MONTH = r"(?:0[1-9]|1[0-2])"

_YEAR_MONTH = re.compile(rf"(?<!\d)({_YEAR}){_SEP}({_MONTH})(?!\d)")
_MONTH_YEAR = re.compile(rf"(?<!\d)({_MONTH}){_SEP}({_YEAR})(?!\d)")
_COMPACT = re.compile(rf"(?<!\d)({_YEAR})({_MONTH})(?!\d)")
_BONUS_YEAR_13 = re.compile(rf"(?<!\d)({_YEAR}){_SEP}13(?!\d)")
_BONUS_13_YEAR = re.compile(rf"(?<!\d)13{_SEP}({_YEAR})(?!\d)")
_YEAR_ONLY = re.compile(rf"(?<!\d)({_YEAR})(?!\d)")

_BONUS_WORDS = [
    re.compile(r"(?<![a-z0-9])13(?:o|º|°|a)(?![a-z0-9])"),
    re.compile(r"(?<![a-z0-9])decimo[-_ ]?(?:terceiro|3o?)(?![a-z0-9])"),
    re.compile(r"(?<![a-z0-9])13[-_ ]?salario(?![a-z0-9])"),
    re.compile(r"(?<![a-z0-9])gratificacao[-_ ]natalina(?![a-z0-9])"),
]


@dataclass(frozen=True)
class PeriodInfo:
    period: str  # "AAAA-MM" ou "" quando não identificado/ambíguo
    annual_bonus: bool
    note: str = ""


def is_valid_period(value: str) -> bool:
    return bool(PERIOD_RE.fullmatch(value or ""))


def has_annual_bonus_marker(name: str) -> bool:
    text = normalize_key(Path(name).stem)
    if any(p.search(text) for p in _BONUS_WORDS):
        return True
    return bool(_BONUS_YEAR_13.search(text) or _BONUS_13_YEAR.search(text))


def extract_period(name: str, annual_bonus_month: str = "12") -> PeriodInfo:
    stem = normalize_key(Path(name).stem)
    bonus = has_annual_bonus_marker(name)

    found: set[str] = set()
    for m in _YEAR_MONTH.finditer(stem):
        found.add(f"{m.group(1)}-{m.group(2)}")
    for m in _MONTH_YEAR.finditer(stem):
        found.add(f"{m.group(2)}-{m.group(1)}")
    if not found:
        for m in _COMPACT.finditer(stem):
            found.add(f"{m.group(1)}-{m.group(2)}")

    if len(found) == 1:
        return PeriodInfo(found.pop(), bonus)
    if len(found) > 1:
        return PeriodInfo("", bonus, "competências conflitantes no nome: " + ", ".join(sorted(found)))

    if bonus:
        years = {m.group(1) for m in _BONUS_YEAR_13.finditer(stem)}
        years |= {m.group(1) for m in _BONUS_13_YEAR.finditer(stem)}
        if not years:
            years = {m.group(1) for m in _YEAR_ONLY.finditer(stem)}
        if len(years) == 1:
            return PeriodInfo(f"{years.pop()}-{annual_bonus_month}", True, "13º sem mês explícito: convenção aplicada")
        return PeriodInfo("", True, "13º sem ano identificável")

    return PeriodInfo("", False, "competência não encontrada no nome")
