"""Persistência do cadastro de tenants (JSON) com escrita atômica e backup."""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Tenant
from .schema import SCHEMA_VERSION, ensure_valid


def empty_db() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "kind": "database", "tenants": {}}


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_json(path: Path) -> Any:
    # utf-8-sig tolera BOM de arquivos editados no Bloco de Notas.
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class TenantStore:
    def __init__(self, path: Path, backups_dir: Path | None = None):
        self.path = Path(path)
        self.backups_dir = backups_dir or self.path.parent / "backups"
        self.data: dict[str, Any] = empty_db()

    def load(self) -> "TenantStore":
        if self.path.exists():
            data = load_json(self.path)
            ensure_valid(data)
            self.data = data
        else:
            self.data = empty_db()
        return self

    def save(self, data: dict[str, Any] | None = None, backup: bool = True) -> Path | None:
        new = copy.deepcopy(data if data is not None else self.data)
        new["kind"] = "database"
        ensure_valid(new)
        backup_path = None
        if backup and self.path.exists():
            self.backups_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup_path = self.backups_dir / f"tenants_{stamp}.json"
            backup_path.write_bytes(self.path.read_bytes())
        atomic_write_json(self.path, new)
        self.data = new
        return backup_path

    def tenants(self) -> dict[str, Tenant]:
        return {tid: Tenant.from_dict(tid, t) for tid, t in self.data["tenants"].items()}

    def tenant(self, tid: str) -> Tenant:
        if tid not in self.data["tenants"]:
            raise KeyError(f"tenant desconhecido: {tid}")
        return Tenant.from_dict(tid, self.data["tenants"][tid])
