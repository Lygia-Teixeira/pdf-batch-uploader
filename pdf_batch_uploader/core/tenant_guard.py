"""Validação do tenant ativo — a principal barreira contra envio ao cliente errado.

Princípios:

* Comparação **exata** após normalização leve (acentos, caixa, espaços). Sem
  prefixo, sem "contém", sem similaridade: "EMPRESA ALFA" ≠ "EMPRESA ALFA FILIAL".
* Ao escolher um tenant numa lista do portal, exige **exatamente uma** opção
  correspondente; nunca escolhe "a primeira".
* Leitura vazia, múltiplos elementos ou divergência => ``TenantMismatch`` e o lote
  é interrompido antes de qualquer envio.
"""

from __future__ import annotations

from typing import Sequence

from .text import normalize_tenant_name


class TenantMismatch(RuntimeError):
    """O tenant ativo no portal não corresponde ao esperado."""


def same_tenant(active: str, expected: str) -> bool:
    a, e = normalize_tenant_name(active or ""), normalize_tenant_name(expected or "")
    return bool(a) and bool(e) and a == e


def assert_tenant(active_texts: Sequence[str], expected: str) -> str:
    """Recebe os textos encontrados no indicador de tenant ativo e valida."""
    texts = [t.strip() for t in active_texts if t and t.strip()]
    if not texts:
        raise TenantMismatch("indicador de tenant ativo não encontrado na página")
    if len(texts) > 1:
        raise TenantMismatch(f"{len(texts)} indicadores de tenant ativo encontrados; seletor ambíguo")
    if not same_tenant(texts[0], expected):
        raise TenantMismatch(f"tenant ativo {texts[0]!r} difere do esperado {expected!r}")
    return texts[0]


def pick_unique_option(options: Sequence[str], label: str) -> int:
    """Índice da única opção cujo rótulo corresponde exatamente a ``label``."""
    matches = [i for i, o in enumerate(options) if same_tenant(o, label)]
    if not matches:
        raise TenantMismatch(f"opção {label!r} não encontrada no seletor de tenant")
    if len(matches) > 1:
        raise TenantMismatch(f"{len(matches)} opções correspondem a {label!r}; seleção ambígua")
    return matches[0]
