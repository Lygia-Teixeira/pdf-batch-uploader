"""Implementação Playwright do protocolo ``Uploader``.

Todos os seletores vêm do cadastro do tenant (``portal.selectors``); nada de
seletor fixo no código. Decisões de segurança:

* Contexto de navegador **efêmero** (sem perfil persistente): cookies e sessão
  somem ao fechar. O login é feito manualmente pelo operador; a ferramenta
  apenas espera o indicador ``logged_in`` aparecer.
* Antes de cada arquivo o tenant ativo é relido e comparado (``tenant_guard``).
* No modo ``auto`` a troca de tenant escolhe a **única** opção com rótulo exato;
  no modo ``manual`` o operador troca o tenant e a ferramenta só confere.
* Navegação restrita à origem configurada: HTTP só para localhost.
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import urljoin, urlparse

from ..core.models import Tenant
from ..core.planner import PlanItem
from ..core.schema import is_local_host
from ..core.tenant_guard import TenantMismatch, assert_tenant, pick_unique_option

MODES = ("manual", "auto")


class PlaywrightUploader:
    def __init__(
        self,
        mode: str = "manual",
        *,
        headless: bool = False,
        login_timeout_s: float = 180,
        action_timeout_s: float = 30,
        on_message: Callable[[str], None] | None = None,
    ):
        if mode not in MODES:
            raise ValueError(f"modo inválido: {mode}")
        self.mode = mode
        self.headless = headless
        self.login_timeout_ms = int(login_timeout_s * 1000)
        self.action_timeout_ms = int(action_timeout_s * 1000)
        self.on_message = on_message or (lambda _m: None)
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None

    # -- ciclo de vida ------------------------------------------------------
    def open(self, tenant: Tenant) -> None:
        url = urljoin(tenant.base_url, tenant.upload_path or "")
        scheme = urlparse(url).scheme
        if scheme not in ("http", "https") or (scheme == "http" and not is_local_host(url)):
            raise ValueError("URL recusada: use HTTPS (HTTP apenas para localhost)")
        if self.page is not None:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(accept_downloads=False)
        self._context.set_default_timeout(self.action_timeout_ms)
        self.page = self._context.new_page()
        self.page.goto(url, wait_until="domcontentloaded")

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        if self._pw is not None:
            self._pw.stop()
        self._pw = self._browser = self._context = self.page = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- protocolo Uploader -------------------------------------------------
    def prepare(self, tenant: Tenant) -> None:
        self.open(tenant)
        sel = tenant.selectors
        self.on_message("Aguardando login manual do operador no navegador…")
        self.page.locator(sel.logged_in).first.wait_for(state="visible", timeout=self.login_timeout_ms)
        if self.mode == "auto":
            self._switch_tenant(tenant)
        else:
            self.on_message(f"Modo manual: ative o tenant {tenant.portal_name!r} no portal.")
            self._wait_for_tenant(tenant, self.login_timeout_ms)
        self.page.locator(sel.upload_form).first.wait_for(state="visible")

    def verify_tenant(self, tenant: Tenant) -> str:
        texts = self.page.locator(tenant.selectors.active_tenant).all_inner_texts()
        return assert_tenant(texts, tenant.portal_name)

    def upload(self, tenant: Tenant, item: PlanItem) -> str:
        sel = tenant.selectors
        profile = tenant.profiles[item.profile]
        page = self.page
        page.locator(sel.file_input).set_input_files(str(item.path))
        if sel.period_input:
            page.locator(sel.period_input).fill(item.period)
        for field, value in profile.fields.items():
            loc = page.locator(sel.fields[field])
            tag = loc.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                loc.select_option(value=value)
            else:
                loc.fill(value)
        if sel.annual_bonus_checkbox:
            page.locator(sel.annual_bonus_checkbox).set_checked(profile.annual_bonus)
        # Última conferência imediatamente antes do clique.
        self.verify_tenant(tenant)
        page.locator(sel.submit).click()
        ok = page.locator(sel.success).filter(has_text=item.name)
        if sel.error:
            err = page.locator(sel.error)
            ok.or_(err).first.wait_for(state="visible")
            if err.count() and err.first.is_visible():
                raise RuntimeError("portal recusou o envio: " + err.first.inner_text()[:200])
        else:
            ok.first.wait_for(state="visible")
        return ok.first.inner_text()[:200]

    # -- internos -----------------------------------------------------------
    def _wait_for_tenant(self, tenant: Tenant, timeout_ms: int) -> None:
        deadline = timeout_ms
        step = 500
        last = None
        while deadline > 0:
            try:
                self.verify_tenant(tenant)
                return
            except TenantMismatch as exc:
                last = exc
            self.page.wait_for_timeout(step)
            deadline -= step
        raise TenantMismatch(str(last) if last else "tempo esgotado aguardando tenant")

    def _switch_tenant(self, tenant: Tenant) -> None:
        sel = tenant.selectors
        try:
            self.verify_tenant(tenant)
            self.on_message("Tenant esperado já está ativo.")
            return
        except TenantMismatch:
            pass
        if not sel.tenant_picker or not sel.tenant_activate:
            raise TenantMismatch("modo auto exige seletores tenant_picker e tenant_activate")
        picker = self.page.locator(sel.tenant_picker)
        labels = picker.locator("option").all_inner_texts()
        index = pick_unique_option(labels, tenant.picker_label)
        picker.select_option(index=index)
        self.page.locator(sel.tenant_activate).click()
        self._wait_for_tenant(tenant, self.action_timeout_ms)
        self.on_message(f"Tenant trocado e confirmado: {tenant.portal_name}")
