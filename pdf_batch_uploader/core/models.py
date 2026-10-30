"""Modelos imutáveis do domínio, construídos a partir do JSON já validado."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Profile:
    name: str
    keywords: tuple[str, ...]
    fields: Mapping[str, str]
    exclude_keywords: tuple[str, ...] = ()
    annual_bonus: bool = False
    description: str = ""

    @classmethod
    def from_dict(cls, name: str, d: Mapping[str, Any]) -> "Profile":
        return cls(
            name=name,
            keywords=tuple(d.get("keywords", ())),
            exclude_keywords=tuple(d.get("exclude_keywords", ())),
            fields=dict(d.get("fields", {})),
            annual_bonus=bool(d.get("annual_bonus", False)),
            description=d.get("description", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"keywords": list(self.keywords), "fields": dict(self.fields)}
        if self.exclude_keywords:
            out["exclude_keywords"] = list(self.exclude_keywords)
        if self.annual_bonus:
            out["annual_bonus"] = True
        if self.description:
            out["description"] = self.description
        return out


@dataclass(frozen=True)
class Selectors:
    logged_in: str
    active_tenant: str
    upload_form: str
    file_input: str
    submit: str
    success: str
    tenant_picker: str = ""
    tenant_activate: str = ""
    period_input: str = ""
    annual_bonus_checkbox: str = ""
    error: str = ""
    fields: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Selectors":
        known = {k: v for k, v in d.items() if k != "fields"}
        return cls(**known, fields=dict(d.get("fields", {})))


@dataclass(frozen=True)
class Tenant:
    id: str
    display_name: str
    portal_name: str  # nome exato exibido como "tenant ativo" no portal
    base_url: str
    selectors: Selectors
    profiles: Mapping[str, Profile]
    portal_picker_label: str = ""  # rótulo da opção no seletor de tenant (se diferente)
    upload_path: str = ""

    @property
    def picker_label(self) -> str:
        return self.portal_picker_label or self.portal_name

    @classmethod
    def from_dict(cls, tid: str, d: Mapping[str, Any]) -> "Tenant":
        portal = d["portal"]
        return cls(
            id=tid,
            display_name=d["display_name"],
            portal_name=d["portal_name"],
            portal_picker_label=d.get("portal_picker_label", ""),
            base_url=portal["base_url"],
            upload_path=portal.get("upload_path", ""),
            selectors=Selectors.from_dict(portal["selectors"]),
            profiles={n: Profile.from_dict(n, p) for n, p in d["profiles"].items()},
        )
