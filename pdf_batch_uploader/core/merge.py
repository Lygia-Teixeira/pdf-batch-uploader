"""Exportação e importação de tenants/perfis sem sobrescrever dados locais.

* Exporta somente cadastro (tenants, seletores e perfis). Nunca histórico,
  manifestos de lote, arquivos ou credenciais.
* Importa por **merge**: tenants novos são adicionados; tenants existentes
  (mesmo ``portal_name`` normalizado) recebem apenas perfis inexistentes.
  Perfis com o mesmo nome e conteúdo diferente viram **conflito** e o local é
  mantido. O relatório informa tudo o que foi adicionado, ignorado e conflitante.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .history import now_iso
from .schema import SCHEMA_VERSION, ensure_valid
from .text import normalize_tenant_name


@dataclass
class Conflict:
    tenant_id: str
    item: str
    reason: str


@dataclass
class MergeReport:
    tenants_added: list[str] = field(default_factory=list)
    profiles_added: list[str] = field(default_factory=list)
    identical_skipped: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"Tenants adicionados: {len(self.tenants_added)}",
            f"Perfis adicionados: {len(self.profiles_added)}",
            f"Idênticos ignorados: {len(self.identical_skipped)}",
            f"Conflitos (mantido o local): {len(self.conflicts)}",
        ]
        lines += [f"  - {c.tenant_id} / {c.item}: {c.reason}" for c in self.conflicts]
        return "\n".join(lines)


def export_tenants(db: dict[str, Any], tenant_ids: Iterable[str]) -> dict[str, Any]:
    ids = list(tenant_ids)
    missing = [t for t in ids if t not in db["tenants"]]
    if missing:
        raise KeyError("tenants inexistentes: " + ", ".join(missing))
    package = {
        "schema_version": SCHEMA_VERSION,
        "kind": "export",
        "exported_at": now_iso(),
        "tenants": {t: copy.deepcopy(db["tenants"][t]) for t in ids},
    }
    ensure_valid(package)
    return package


def _free_id(base: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_-]", "-", base.lower())[:56] or "tenant"
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def merge_package(current: dict[str, Any], package: dict[str, Any]) -> tuple[dict[str, Any], MergeReport]:
    """Retorna (novo_db, relatório). ``current`` não é modificado."""
    ensure_valid(package)
    merged = copy.deepcopy(current)
    report = MergeReport()
    by_portal = {normalize_tenant_name(t["portal_name"]): tid for tid, t in merged["tenants"].items()}

    for in_id, incoming in package["tenants"].items():
        key = normalize_tenant_name(incoming["portal_name"])
        local_id = by_portal.get(key)
        if local_id is None:
            new_id = _free_id(in_id, set(merged["tenants"]))
            merged["tenants"][new_id] = copy.deepcopy(incoming)
            by_portal[key] = new_id
            report.tenants_added.append(new_id)
            report.profiles_added += [f"{new_id}/{p}" for p in incoming["profiles"]]
            continue

        local = merged["tenants"][local_id]
        if local["portal"] != incoming["portal"]:
            report.conflicts.append(Conflict(local_id, "portal", "URL/seletores diferentes; mantida a configuração local"))
        for pname, prof in incoming["profiles"].items():
            if pname not in local["profiles"]:
                missing_sel = [f for f in prof["fields"] if f not in local["portal"]["selectors"].get("fields", {})]
                if missing_sel:
                    report.conflicts.append(
                        Conflict(local_id, pname, "campos sem seletor local: " + ", ".join(missing_sel))
                    )
                    continue
                local["profiles"][pname] = copy.deepcopy(prof)
                report.profiles_added.append(f"{local_id}/{pname}")
            elif local["profiles"][pname] == prof:
                report.identical_skipped.append(f"{local_id}/{pname}")
            else:
                report.conflicts.append(Conflict(local_id, pname, "perfil com mesmo nome e conteúdo diferente"))

    merged["kind"] = "database"
    merged.pop("exported_at", None)
    ensure_valid(merged)
    return merged, report
