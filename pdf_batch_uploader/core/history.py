"""Histórico local append-only (JSONL) e exportação CSV.

Cada linha registra **apenas metadados operacionais**: quando, lote, tenant,
evento, nome do arquivo (com redação de padrões pessoais), SHA-256, tamanho,
competência, perfil e um detalhe curto também redigido. Nunca conteúdo do PDF.

O evento ``browser_step_done`` significa "o fluxo no navegador foi concluído",
não "o portal processou/importou o documento". A confirmação final deve ser
feita no próprio portal.
"""

from __future__ import annotations

import csv
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from .text import redact

EVENT_BATCH_STARTED = "batch_started"
EVENT_TENANT_VERIFIED = "tenant_verified"
EVENT_TENANT_MISMATCH = "tenant_mismatch"
EVENT_ITEM_STARTED = "item_started"
EVENT_BROWSER_DONE = "browser_step_done"
EVENT_ITEM_FAILED = "item_failed"
EVENT_BATCH_PAUSED = "batch_paused"
EVENT_BATCH_CANCELLED = "batch_cancelled"
EVENT_BATCH_ABORTED = "batch_aborted"
EVENT_BATCH_FINISHED = "batch_finished"

CSV_COLUMNS = ["ts", "run_id", "tenant_id", "mode", "event", "file_name", "sha256", "size", "period", "profile", "detail"]
_MAX_DETAIL = 300


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _csv_safe(value: Any) -> str:
    """Neutraliza injeção de fórmula ao abrir o CSV em planilhas (=, +, -, @, tab, CR)."""
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


class HistoryLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(
        self,
        event: str,
        *,
        run_id: str = "",
        tenant_id: str = "",
        mode: str = "",
        file_name: str = "",
        sha256: str = "",
        size: int | None = None,
        period: str = "",
        profile: str = "",
        detail: str = "",
    ) -> dict[str, Any]:
        entry = {
            "ts": now_iso(),
            "run_id": run_id,
            "tenant_id": tenant_id,
            "mode": mode,
            "event": event,
            "file_name": redact(file_name),
            "sha256": sha256,
            "size": size,
            "period": period,
            "profile": profile,
            "detail": redact(str(detail))[:_MAX_DETAIL],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return iter(())
        def gen():
            with open(self.path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"histórico corrompido na linha {n}: {exc}") from exc
        return gen()

    def export_csv(self, target: Path, events: Iterable[dict[str, Any]] | None = None) -> int:
        rows = list(events if events is not None else self.read())
        with open(target, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(CSV_COLUMNS)
            for e in rows:
                writer.writerow([_csv_safe(e.get(c, "")) for c in CSV_COLUMNS])
        return len(rows)
