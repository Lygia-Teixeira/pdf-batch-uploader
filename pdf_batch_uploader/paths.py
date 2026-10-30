"""Localização dos dados locais.

Os dados do operador ficam fora da pasta do código (e fora do repositório):

* Windows: ``%LOCALAPPDATA%\\PdfBatchUploader``
* Linux/macOS: ``$XDG_DATA_HOME/pdf-batch-uploader`` ou ``~/.local/share/pdf-batch-uploader``

A variável ``PBU_DATA_DIR`` (ou a opção ``--data-dir`` da CLI) sobrescreve o local,
o que é útil em testes e demonstrações.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME_WINDOWS = "PdfBatchUploader"
APP_DIR_NAME_POSIX = "pdf-batch-uploader"


def default_data_dir() -> Path:
    override = os.environ.get("PBU_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME_WINDOWS
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_NAME_POSIX


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def tenants_file(self) -> Path:
        return self.root / "tenants.json"

    @property
    def history_file(self) -> Path:
        return self.root / "history.jsonl"

    @property
    def batches_dir(self) -> Path:
        return self.root / "batches"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    def ensure(self) -> "DataPaths":
        for d in (self.root, self.batches_dir, self.backups_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def default(cls, root: str | Path | None = None) -> "DataPaths":
        return cls(Path(root) if root else default_data_dir())
