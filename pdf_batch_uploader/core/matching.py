"""Sugestão de perfil por palavras-chave, com detecção de ambiguidade.

Regras (determinísticas, sem "melhor palpite"):

1. Um perfil é candidato se ao menos uma ``keyword`` aparece como palavra inteira
   no nome do arquivo (comparação sem acentos/caixa).
2. Qualquer ``exclude_keyword`` presente desqualifica o perfil.
3. Perfis de 13º (``annual_bonus``) só disputam arquivos marcados como 13º, e
   vice-versa.
4. A pontuação é o número de keywords distintas encontradas. Vence o maior escore;
   empate no topo => **ambíguo** e nenhum perfil é escolhido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .models import Profile
from .period import has_annual_bonus_marker
from .text import normalize_key


@dataclass(frozen=True)
class Suggestion:
    profile: str | None
    reason: str = ""
    candidates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ambiguous(self) -> bool:
        return self.profile is None and len(self.candidates) > 1


def _tokenize(text: str) -> str:
    # Separadores comuns de nome de arquivo viram espaço para que "folha_ativos"
    # case com a keyword "ativos".
    return " " + re.sub(r"[-_.()\[\]]+", " ", normalize_key(text)) + " "


def _contains_word(text: str, keyword: str) -> bool:
    key = normalize_key(keyword)
    if not key:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", text) is not None


def suggest_profile(file_name: str, profiles: Mapping[str, Profile]) -> Suggestion:
    text = _tokenize(Path(file_name).stem)
    is_bonus = has_annual_bonus_marker(file_name)

    scored: list[tuple[str, int]] = []
    for name, profile in profiles.items():
        if profile.annual_bonus != is_bonus:
            continue
        if any(_contains_word(text, k) for k in profile.exclude_keywords):
            continue
        hits = {normalize_key(k) for k in profile.keywords if _contains_word(text, k)}
        if hits:
            scored.append((name, len(hits)))

    if not scored:
        return Suggestion(None, "nenhum perfil compatível: selecione manualmente ou revise as palavras-chave")
    best = max(s for _, s in scored)
    top = tuple(sorted(n for n, s in scored if s == best))
    if len(top) == 1:
        return Suggestion(top[0], "", top)
    return Suggestion(None, "perfis ambíguos: " + ", ".join(top), top)
