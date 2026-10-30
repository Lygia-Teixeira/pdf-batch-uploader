"""Manifesto de lote (journal) e recuperação segura de pendentes.

Cada lote grava um manifesto em ``batches/batch_<id>.json`` com o estado de cada
documento. Estados:

* ``pending``      – ainda não tocado: **único estado que pode ser retomado**.
* ``started``      – o envio começou; resultado incerto (o navegador pode ter
  enviado o arquivo). Exige conferência no portal; nunca é reenviado sozinho.
* ``browser_done`` – fluxo no navegador concluído (não comprova importação).
* ``failed``       – erro durante o envio; exige conferência.
* ``skipped``      – removido do lote pelo operador.

Na recuperação, cada pendente só é liberado se o arquivo ainda existir com o
mesmo SHA-256 e o perfil atual for idêntico ao snapshot gravado no lote.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .files import sha256_file
from .history import now_iso
from .models import Tenant
from .store import atomic_write_json

PENDING = "pending"
STARTED = "started"
BROWSER_DONE = "browser_done"
FAILED = "failed"
SKIPPED = "skipped"
STATES = {PENDING, STARTED, BROWSER_DONE, FAILED, SKIPPED}
SENT_OR_UNCERTAIN = {STARTED, BROWSER_DONE, FAILED}


def new_run_id() -> str:
    return "batch_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]


class BatchJournal:
    def __init__(self, path: Path, data: dict[str, Any]):
        self.path = Path(path)
        self.data = data
        self._lock = threading.Lock()

    # -- criação / leitura -------------------------------------------------
    @classmethod
    def create(cls, batches_dir: Path, tenant: Tenant, mode: str, items: Iterable[Any]) -> "BatchJournal":
        run_id = new_run_id()
        docs = []
        for it in items:
            docs.append(
                {
                    "path": str(it.path),
                    "name": it.name,
                    "sha256": it.sha256,
                    "size": it.size,
                    "period": it.period,
                    "annual_bonus": it.annual_bonus,
                    "profile": it.profile,
                    "profile_snapshot": tenant.profiles[it.profile].to_dict(),
                    "state": PENDING,
                    "detail": "",
                    "updated_at": None,
                }
            )
        data = {
            "version": 1,
            "id": run_id,
            "tenant_id": tenant.id,
            "portal_name": tenant.portal_name,
            "mode": mode,
            "created_at": now_iso(),
            "finished_at": None,
            "documents": docs,
        }
        journal = cls(Path(batches_dir) / f"{run_id}.json", data)
        journal.flush()
        return journal

    @classmethod
    def load(cls, path: Path) -> "BatchJournal":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
            raise ValueError(f"manifesto inválido: {Path(path).name}")
        for d in data["documents"]:
            if d.get("state") not in STATES:
                raise ValueError(f"estado desconhecido em {Path(path).name}: {d.get('state')!r}")
        return cls(path, data)

    @staticmethod
    def list_for_tenant(batches_dir: Path, tenant_id: str) -> list["BatchJournal"]:
        out = []
        if not Path(batches_dir).exists():
            return out
        for p in sorted(Path(batches_dir).glob("batch_*.json"), reverse=True):
            j = BatchJournal.load(p)
            if j.data.get("tenant_id") == tenant_id:
                out.append(j)
        return out

    # -- atualização -------------------------------------------------------
    @property
    def id(self) -> str:
        return self.data["id"]

    def flush(self) -> None:
        with self._lock:
            atomic_write_json(self.path, self.data)

    def set_state(self, sha256: str, state: str, detail: str = "") -> None:
        if state not in STATES:
            raise ValueError(state)
        with self._lock:
            for d in self.data["documents"]:
                if d["sha256"] == sha256:
                    d["state"] = state
                    d["detail"] = detail[:300]
                    d["updated_at"] = now_iso()
                    break
            else:
                raise KeyError(sha256)
        self.flush()

    def finish(self) -> None:
        self.data["finished_at"] = now_iso()
        self.flush()

    def counts(self) -> dict[str, int]:
        c = {s: 0 for s in STATES}
        for d in self.data["documents"]:
            c[d["state"]] += 1
        return c


@dataclass
class RecoveryResult:
    batch_id: str
    ready: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    needs_review: list[dict[str, Any]] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.issues and bool(self.ready)


def prepare_recovery(journal: BatchJournal, tenant: Tenant) -> RecoveryResult:
    """Seleciona só os ``pending`` íntegros; o resto vai para revisão manual."""
    res = RecoveryResult(batch_id=journal.id)
    if journal.data.get("tenant_id") != tenant.id:
        res.issues.append("lote pertence a outro tenant")
        return res
    for d in journal.data["documents"]:
        if d["state"] != PENDING:
            if d["state"] in SENT_OR_UNCERTAIN:
                res.needs_review.append(d)
            continue
        path = Path(d["path"])
        try:
            if not path.is_file():
                raise ValueError("arquivo não encontrado")
            if sha256_file(path) != d["sha256"]:
                raise ValueError("arquivo alterado desde o lote original (hash diferente)")
            current = tenant.profiles.get(d["profile"])
            if current is None:
                raise ValueError(f"perfil '{d['profile']}' não existe mais")
            if current.to_dict() != d["profile_snapshot"]:
                raise ValueError(f"perfil '{d['profile']}' foi alterado; revise antes de reenviar")
            res.ready.append(d)
        except (OSError, ValueError) as exc:
            res.issues.append(f"{d['name']}: {exc}")
    return res
