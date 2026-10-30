"""Execução sequencial do lote com pausa/cancelamento entre arquivos.

O ``BatchRunner`` não sabe nada de navegador: recebe um ``Uploader`` (protocolo)
que a automação Playwright implementa — e que os testes substituem por um falso.

Garantias:

* O tenant é validado **antes de cada arquivo**; divergência aborta o lote e os
  itens restantes permanecem ``pending`` (recuperáveis).
* O item é marcado ``started`` *antes* do envio. Se o processo cair no meio,
  o item fica incerto e não é reenviado automaticamente.
* Pausa e cancelamento só têm efeito **entre** arquivos — um envio em andamento
  nunca é interrompido pela metade.
* Por padrão, qualquer erro de envio interrompe o lote (``stop_on_error``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol

from . import history as H
from .history import HistoryLog
from .journal import BROWSER_DONE, FAILED, STARTED, BatchJournal
from .models import Tenant
from .planner import PlanItem
from .tenant_guard import TenantMismatch
from .text import redact


class Uploader(Protocol):
    def prepare(self, tenant: Tenant) -> None:
        """Aguarda login manual, troca de tenant (modo auto) e abre a tela de envio."""

    def verify_tenant(self, tenant: Tenant) -> str:
        """Lê o tenant ativo; levanta ``TenantMismatch`` se divergir."""

    def upload(self, tenant: Tenant, item: PlanItem) -> str:
        """Envia um arquivo e retorna um detalhe curto do resultado no navegador."""


class BatchNotReady(RuntimeError):
    pass


@dataclass
class RunResult:
    run_id: str
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    aborted_reason: str = ""
    cancelled: bool = False


class BatchRunner:
    def __init__(
        self,
        tenant: Tenant,
        items: list[PlanItem],
        uploader: Uploader,
        journal: BatchJournal,
        history: HistoryLog,
        mode: str,
        *,
        on_message: Callable[[str], None] | None = None,
        stop_on_error: bool = True,
    ):
        not_ready = [i.name for i in items if not i.ready]
        if not items or not_ready:
            raise BatchNotReady("lote com pendências: " + ", ".join(not_ready[:5]) if not_ready else "lote vazio")
        self.tenant = tenant
        self.items = items
        self.uploader = uploader
        self.journal = journal
        self.history = history
        self.mode = mode
        self.on_message = on_message or (lambda _m: None)
        self.stop_on_error = stop_on_error
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self._resume = threading.Event()
        self._resume.set()

    # -- controle (chamado pela UI/CLI em outra thread) --------------------
    def pause(self) -> None:
        self._resume.clear()
        self.pause_event.set()

    def resume(self) -> None:
        self.pause_event.clear()
        self._resume.set()

    def cancel(self) -> None:
        self.cancel_event.set()
        self._resume.set()

    # -- execução ----------------------------------------------------------
    def _log(self, event: str, item: PlanItem | None = None, detail: str = "") -> None:
        kw = {}
        if item is not None:
            kw = dict(file_name=item.name, sha256=item.sha256, size=item.size, period=item.period, profile=item.profile or "")
        self.history.append(event, run_id=self.journal.id, tenant_id=self.tenant.id, mode=self.mode, detail=detail, **kw)

    def _say(self, msg: str) -> None:
        self.on_message(redact(msg))

    def _wait_if_paused(self) -> None:
        if not self._resume.is_set():
            self._log(H.EVENT_BATCH_PAUSED)
            self._say("Lote pausado; nenhum novo arquivo será iniciado até retomar.")
            self._resume.wait()

    def run(self) -> RunResult:
        res = RunResult(run_id=self.journal.id)
        self._log(H.EVENT_BATCH_STARTED, detail=f"{len(self.items)} arquivo(s)")
        try:
            self.uploader.prepare(self.tenant)
            self._say(f"Tenant confirmado: {self.uploader.verify_tenant(self.tenant)}")
            self._log(H.EVENT_TENANT_VERIFIED)
        except TenantMismatch as exc:
            return self._abort(res, 0, f"validação de tenant falhou: {exc}", H.EVENT_TENANT_MISMATCH)
        except Exception as exc:  # navegador fechado, timeout de login, etc.
            return self._abort(res, 0, f"preparação falhou: {exc}")

        for idx, item in enumerate(self.items):
            self._wait_if_paused()
            if self.cancel_event.is_set():
                res.cancelled = True
                res.pending = [i.name for i in self.items[idx:]]
                self._log(H.EVENT_BATCH_CANCELLED, detail=f"{len(res.pending)} pendente(s)")
                self._say("Lote cancelado entre arquivos; pendentes podem ser recuperados.")
                self.journal.finish()
                return res
            try:
                self.uploader.verify_tenant(self.tenant)
            except TenantMismatch as exc:
                return self._abort(res, idx, f"tenant mudou durante o lote: {exc}", H.EVENT_TENANT_MISMATCH)
            except Exception as exc:
                return self._abort(res, idx, f"não foi possível validar o tenant: {exc}")

            self.journal.set_state(item.sha256, STARTED)
            self._log(H.EVENT_ITEM_STARTED, item)
            self._say(f"[{idx + 1}/{len(self.items)}] Enviando {item.name} ({item.period}, {item.profile})")
            try:
                detail = self.uploader.upload(self.tenant, item)
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                self.journal.set_state(item.sha256, FAILED, redact(msg))
                self._log(H.EVENT_ITEM_FAILED, item, msg)
                res.failed.append(item.name)
                self._say(f"Falha em {item.name}: {msg}")
                if self.stop_on_error:
                    return self._abort(res, idx + 1, "interrompido após falha (stop_on_error)")
                continue
            self.journal.set_state(item.sha256, BROWSER_DONE, redact(detail or ""))
            self._log(H.EVENT_BROWSER_DONE, item, detail or "")
            res.done.append(item.name)

        self._log(H.EVENT_BATCH_FINISHED, detail=f"{len(res.done)} concluído(s) no navegador")
        self._say(
            "Lote concluído no navegador. Isso NÃO comprova a importação: confira o status no portal."
        )
        self.journal.finish()
        return res

    def _abort(self, res: RunResult, from_idx: int, reason: str, event: str = H.EVENT_BATCH_ABORTED) -> RunResult:
        res.aborted_reason = reason
        res.pending = [i.name for i in self.items[from_idx:] if i.name not in res.failed]
        self._log(event, detail=reason)
        if event != H.EVENT_BATCH_ABORTED:
            self._log(H.EVENT_BATCH_ABORTED, detail=reason)
        self._say(f"Lote interrompido: {reason}. Nenhum arquivo adicional foi enviado.")
        self.journal.finish()
        return res
