"""Interface Tkinter mínima. Toda regra de negócio está em ``core``.

A UI apenas: escolhe tenant e pasta, mostra a simulação, pede confirmação,
dispara o ``BatchRunner`` numa thread de trabalho e repassa pausa/cancelamento.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..core.files import list_pdfs
from ..core.history import HistoryLog
from ..core.journal import BatchJournal, prepare_recovery
from ..core.planner import Plan, PlanItem, SentIndex, build_plan
from ..core.runner import BatchRunner
from ..core.store import TenantStore
from ..core.text import normalize_tenant_name
from ..paths import DataPaths


class App:
    def __init__(self, root: tk.Tk, data_dir: str | None = None):
        self.root = root
        self.paths = DataPaths.default(data_dir).ensure()
        self.store = TenantStore(self.paths.tenants_file, self.paths.backups_dir).load()
        self.history = HistoryLog(self.paths.history_file)
        self.messages: queue.Queue[str] = queue.Queue()
        self.plan: Plan | None = None
        self.items_override: list[PlanItem] | None = None
        self.runner: BatchRunner | None = None
        self.recovery_source: BatchJournal | None = None
        self.folder = tk.StringVar()
        self.tenant_var = tk.StringVar()
        self.mode = tk.StringVar(value="manual")
        self._build()
        self.root.after(150, self._drain)

    # -- layout -------------------------------------------------------------
    def _build(self) -> None:
        self.root.title("PDF Batch Uploader")
        self.root.geometry("980x620")
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Tenant").grid(row=0, column=0, sticky="w")
        tenants = list(self.store.tenants())
        self.tenant_box = ttk.Combobox(top, values=tenants, textvariable=self.tenant_var, state="readonly", width=30)
        self.tenant_box.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(top, text="Pasta de PDFs…", command=self._pick_folder).grid(row=0, column=2, padx=6)
        ttk.Label(top, textvariable=self.folder).grid(row=0, column=3, sticky="w")

        bar = ttk.Frame(self.root, padding=(10, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Simular", command=self.simulate).pack(side="left")
        ttk.Radiobutton(bar, text="Manual", value="manual", variable=self.mode).pack(side="left", padx=(16, 0))
        ttk.Radiobutton(bar, text="Automático", value="auto", variable=self.mode).pack(side="left")
        self.start_btn = ttk.Button(bar, text="Iniciar no navegador", command=self.start)
        self.start_btn.pack(side="left", padx=12)
        self.pause_btn = ttk.Button(bar, text="Pausar após arquivo", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left")
        self.cancel_btn = ttk.Button(bar, text="Cancelar após arquivo", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=6)
        ttk.Button(bar, text="Recuperar pendentes", command=self.recover).pack(side="right")
        ttk.Button(bar, text="Exportar histórico CSV", command=self.export_csv).pack(side="right", padx=6)

        cols = ("arquivo", "competencia", "perfil", "hash", "situacao")
        self.tree = ttk.Treeview(self.root, columns=cols, show="headings", height=14)
        for c, w in zip(cols, (260, 100, 180, 110, 300)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, padx=10, pady=8)

        self.log = tk.Text(self.root, height=9, state="disabled")
        self.log.pack(fill="x", padx=10, pady=(0, 10))

    # -- ações ----------------------------------------------------------------
    def _say(self, msg: str) -> None:
        self.messages.put(msg)

    def _drain(self) -> None:
        while not self.messages.empty():
            msg = self.messages.get_nowait()
            if msg == "__DONE__":
                self._set_running(False)
                continue
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(150, self._drain)

    def _pick_folder(self) -> None:
        d = filedialog.askdirectory(title="Pasta com os PDFs")
        if d:
            self.folder.set(d)
            self.items_override = None
            self.recovery_source = None

    def _tenant(self):
        tid = self.tenant_var.get()
        if not tid:
            messagebox.showwarning("Tenant", "Selecione um tenant.")
            return None
        return self.store.tenant(tid)

    def simulate(self) -> bool:
        tenant = self._tenant()
        if tenant is None:
            return False
        if self.items_override is not None:
            self.plan = Plan(tenant.id, self.items_override)
        else:
            if not self.folder.get():
                messagebox.showwarning("Pasta", "Escolha a pasta com os PDFs.")
                return False
            sent = SentIndex.build(self.history, self.paths.batches_dir)
            self.plan = build_plan(tenant, list_pdfs(Path(self.folder.get())), sent=sent)
        self.tree.delete(*self.tree.get_children())
        for i in self.plan.items:
            status = "OK" if i.ready else "; ".join(i.issues)
            if i.warnings:
                status += "  (aviso: " + "; ".join(i.warnings) + ")"
            self.tree.insert("", "end", values=(i.name, i.period or "—", i.profile or "—", i.sha256[:12], status))
        self._say(f"Simulação: {len(self.plan.items)} arquivo(s), {self.plan.problems} com pendência. Nada enviado.")
        return self.plan.ready

    def start(self) -> None:
        if self.runner is not None:
            return
        if not self.simulate():
            messagebox.showerror("Lote bloqueado", "Resolva as pendências da simulação antes de enviar.")
            return
        tenant = self.store.tenant(self.plan.tenant_id)
        mode = self.mode.get()
        if mode == "auto":
            typed = simpledialog.askstring(
                "Confirmar modo automático",
                f"{len(self.plan.items)} arquivo(s) serão enviados ao tenant:\n\n{tenant.portal_name}\n\n"
                "Digite o nome exato do tenant para confirmar:",
                parent=self.root,
            )
            if typed is None or normalize_tenant_name(typed) != normalize_tenant_name(tenant.portal_name):
                messagebox.showinfo("Cancelado", "Confirmação não confere. Nada foi enviado.")
                return
        elif not messagebox.askyesno(
            "Confirmar", f"Abrir o navegador para {len(self.plan.items)} arquivo(s) do tenant {tenant.portal_name}?"
        ):
            return
        threading.Thread(target=self._worker, args=(tenant, list(self.plan.items), mode), daemon=True).start()

    def _worker(self, tenant, items, mode) -> None:
        from ..automation.playwright_uploader import PlaywrightUploader

        if self.recovery_source is not None:
            # O lote original deixa de oferecer esses itens; o novo lote passa a rastreá-los.
            for it in items:
                self.recovery_source.set_state(it.sha256, "skipped", "retomado em novo lote")
        journal = BatchJournal.create(self.paths.batches_dir, tenant, mode, items)
        uploader = PlaywrightUploader(mode, on_message=self._say)
        try:
            self.runner = BatchRunner(tenant, items, uploader, journal, self.history, mode, on_message=self._say)
            self.root.after(0, lambda: self._set_running(True))
            res = self.runner.run()
            self._say(f"Resultado: {len(res.done)} no navegador, {len(res.failed)} falha(s), {len(res.pending)} pendente(s).")
        except Exception as exc:
            self._say(f"Erro: {exc}")
        finally:
            uploader.close()
            self.runner = None
            self.items_override = None
            self.recovery_source = None
            self._say("__DONE__")

    def _set_running(self, running: bool) -> None:
        self.start_btn.configure(state="disabled" if running else "normal")
        self.pause_btn.configure(state="normal" if running else "disabled", text="Pausar após arquivo")
        self.cancel_btn.configure(state="normal" if running else "disabled")
        self.tenant_box.configure(state="disabled" if running else "readonly")

    def toggle_pause(self) -> None:
        if not self.runner:
            return
        if self.runner.pause_event.is_set():
            self.runner.resume()
            self.pause_btn.configure(text="Pausar após arquivo")
        else:
            self.runner.pause()
            self.pause_btn.configure(text="Retomar")
            self._say("Pausa solicitada: o envio atual não é interrompido.")

    def cancel(self) -> None:
        if self.runner and messagebox.askyesno("Cancelar", "Cancelar após o arquivo atual?"):
            self.runner.cancel()

    def recover(self) -> None:
        tenant = self._tenant()
        if tenant is None:
            return
        journals = BatchJournal.list_for_tenant(self.paths.batches_dir, tenant.id)
        journals = [j for j in journals if j.counts()["pending"]]
        if not journals:
            messagebox.showinfo("Recuperação", "Nenhum lote com pendentes para este tenant.")
            return
        journal = journals[0]
        res = prepare_recovery(journal, tenant)
        review = "\n".join(f"- {d['name']} [{d['state']}]" for d in res.needs_review[:8])
        if res.issues:
            messagebox.showerror("Não é seguro retomar", "\n".join(res.issues[:8]))
            return
        if not messagebox.askyesno(
            "Recuperar",
            f"Lote {journal.id}: {len(res.ready)} pendente(s) íntegro(s).\n"
            + (f"\nConferir no portal (não serão reenviados):\n{review}\n" if review else "")
            + "\nPreparar somente os pendentes?",
        ):
            return
        self.recovery_source = journal
        self.items_override = [
            PlanItem(Path(d["path"]), d["name"], d["size"], d["sha256"], d["period"], d["annual_bonus"], d["profile"])
            for d in res.ready
        ]
        self.mode.set(journal.data.get("mode", "manual"))
        self.simulate()

    def export_csv(self) -> None:
        target = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if target:
            n = self.history.export_csv(Path(target))
            self._say(f"{n} evento(s) exportado(s).")


def main(data_dir: str | None = None) -> int:
    root = tk.Tk()
    App(root, data_dir)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
