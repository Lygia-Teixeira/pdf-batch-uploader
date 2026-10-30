"""Montagem do plano de envio e simulação (dry-run).

A simulação nunca abre navegador: lista cada arquivo com competência, perfil
sugerido, hash e pendências. Um lote só pode ser executado se **nenhum** item
tiver pendência (``issues``); avisos (``warnings``) não bloqueiam.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .files import MAX_FILE_BYTES, inspect_file
from .history import EVENT_BROWSER_DONE, HistoryLog
from .journal import SENT_OR_UNCERTAIN, BatchJournal
from .matching import suggest_profile
from .models import Tenant
from .period import extract_period, is_valid_period
from .text import looks_personal


@dataclass
class PlanItem:
    path: Path
    name: str
    size: int
    sha256: str
    period: str
    annual_bonus: bool
    profile: str | None
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.issues and self.profile is not None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["path"] = str(self.path)
        d["ready"] = self.ready
        return d


@dataclass
class Plan:
    tenant_id: str
    items: list[PlanItem]

    @property
    def ready(self) -> bool:
        return bool(self.items) and all(i.ready for i in self.items)

    @property
    def problems(self) -> int:
        return sum(1 for i in self.items if not i.ready)


@dataclass
class SentIndex:
    """Índice de hashes já enviados (ou de envio incerto) por tenant."""

    by_tenant: dict[str, set[str]] = field(default_factory=dict)

    def add(self, tenant_id: str, sha256: str) -> None:
        if tenant_id and sha256:
            self.by_tenant.setdefault(tenant_id, set()).add(sha256)

    def tenants_for(self, sha256: str) -> set[str]:
        return {t for t, hashes in self.by_tenant.items() if sha256 in hashes}

    @classmethod
    def build(cls, history: HistoryLog | None, batches_dir: Path | None) -> "SentIndex":
        idx = cls()
        if history is not None:
            for e in history.read():
                if e.get("event") == EVENT_BROWSER_DONE:
                    idx.add(e.get("tenant_id", ""), e.get("sha256", ""))
        if batches_dir is not None and Path(batches_dir).exists():
            for p in Path(batches_dir).glob("batch_*.json"):
                j = BatchJournal.load(p)  # manifesto ilegível => exceção: melhor parar que reenviar
                for d in j.data["documents"]:
                    if d["state"] in SENT_OR_UNCERTAIN:
                        idx.add(j.data.get("tenant_id", ""), d.get("sha256", ""))
        return idx


def build_plan(
    tenant: Tenant,
    files: Iterable[Path],
    *,
    profile_overrides: Mapping[str, str] | None = None,
    period_overrides: Mapping[str, str] | None = None,
    sent: SentIndex | None = None,
    allow_resend: bool = False,
) -> Plan:
    """``*_overrides`` são indexados pelo nome do arquivo (não pelo caminho)."""
    profile_overrides = dict(profile_overrides or {})
    period_overrides = dict(period_overrides or {})
    sent = sent or SentIndex()
    items: list[PlanItem] = []
    seen_hashes: dict[str, str] = {}

    for f in files:
        facts = inspect_file(Path(f))
        info = extract_period(facts.name)
        period = period_overrides.get(facts.name, info.period)
        item = PlanItem(
            path=facts.path,
            name=facts.name,
            size=facts.size,
            sha256=facts.sha256,
            period=period,
            annual_bonus=info.annual_bonus,
            profile=None,
        )

        if not facts.is_pdf:
            item.issues.append("arquivo não tem assinatura PDF (%PDF-)")
        if facts.size == 0:
            item.issues.append("arquivo vazio")
        elif facts.size > MAX_FILE_BYTES:
            item.issues.append(f"arquivo acima do limite de {MAX_FILE_BYTES // (1024 * 1024)} MB")

        if not is_valid_period(period):
            item.issues.append("competência inválida ou ausente" + (f" ({info.note})" if info.note else ""))
        elif info.note and facts.name not in period_overrides:
            item.warnings.append(info.note)

        explicit = profile_overrides.get(facts.name)
        if explicit:
            if explicit not in tenant.profiles:
                item.issues.append(f"perfil '{explicit}' não existe neste tenant")
            else:
                item.profile = explicit
                if tenant.profiles[explicit].annual_bonus != info.annual_bonus:
                    item.warnings.append("perfil manual difere da marcação de 13º do nome do arquivo")
        else:
            s = suggest_profile(facts.name, tenant.profiles)
            item.profile = s.profile
            if s.profile is None:
                item.issues.append(s.reason)

        if facts.sha256 in seen_hashes:
            item.issues.append(f"conteúdo idêntico a '{seen_hashes[facts.sha256]}' no mesmo lote")
        seen_hashes.setdefault(facts.sha256, facts.name)

        prior_tenants = sent.tenants_for(facts.sha256)
        if tenant.id in prior_tenants:
            msg = "mesmo arquivo (SHA-256) já percorreu o fluxo neste tenant"
            if allow_resend:
                item.warnings.append(msg + " — reenvio autorizado pelo operador")
            else:
                item.issues.append(msg + "; confira no portal antes de reenviar")
        others = sorted(prior_tenants - {tenant.id})
        if others:
            item.warnings.append("mesmo arquivo já enviado a OUTRO tenant: " + ", ".join(others) + " — confira o destino")

        if looks_personal(facts.name):
            item.warnings.append("nome do arquivo parece conter dado pessoal; será redigido no histórico")

        items.append(item)

    return Plan(tenant_id=tenant.id, items=items)


def format_plan(plan: Plan) -> str:
    lines = []
    for i in plan.items:
        status = "OK" if i.ready else "PENDENTE: " + "; ".join(i.issues)
        bonus = " [13º]" if i.annual_bonus else ""
        lines.append(f"{i.name} | {i.period or '—'}{bonus} | {i.profile or 'SEM PERFIL'} | {i.sha256[:12]} | {status}")
        for w in i.warnings:
            lines.append(f"    aviso: {w}")
    lines.append(f"Total: {len(plan.items)}; com pendência: {plan.problems}. Simulação — nenhum envio executado.")
    return "\n".join(lines)
