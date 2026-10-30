"""Normalização de texto e redação de dados pessoais em mensagens de log."""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")

# Padrões de dados pessoais que nunca devem chegar ao histórico/log.
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    # CPF com ou sem pontuação (000.000.000-00 / 00000000000)
    (re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)"), "[doc-id]"),
    # CNPJ
    (re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)"), "[doc-id]"),
    # Sequências longas de dígitos (matrículas, contas, telefones)
    (re.compile(r"(?<!\d)\d{9,}(?!\d)"), "[num]"),
]


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_key(value: str) -> str:
    """Minúsculas, sem acentos, espaços colapsados. Usado para comparar palavras-chave."""
    return _WS.sub(" ", strip_accents(value).lower()).strip()


def normalize_tenant_name(value: str) -> str:
    """Forma canônica para comparar o nome do tenant exibido no portal com o esperado.

    Remove acentos, ignora caixa e colapsa espaços, mas **não** remove palavras nem
    pontuação: "EMPRESA ALFA" e "EMPRESA ALFA FILIAL" continuam diferentes.
    """
    return _WS.sub(" ", strip_accents(value).upper()).strip()


def redact(value: str) -> str:
    """Remove padrões de dados pessoais de um texto livre destinado a log."""
    out = value
    for pattern, replacement in _REDACTIONS:
        out = pattern.sub(replacement, out)
    return out


def looks_personal(value: str) -> bool:
    return redact(value) != value
