"""JSON Schema do cadastro de tenants/perfis e validação semântica complementar.

O arquivo de configuração vem de fontes potencialmente não confiáveis (pacotes
exportados por colegas), por isso o schema é fechado (``additionalProperties:
false``), limita tamanhos e só aceita texto em campos de formulário. Nenhum campo
de senha/token existe — e chaves com esses nomes são recusadas explicitamente.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

SCHEMA_VERSION = 1

_SELECTOR = {"type": "string", "minLength": 1, "maxLength": 300}
_ID = {"type": "string", "pattern": r"^[a-z0-9][a-z0-9_-]{0,63}$"}
_NAME = {"type": "string", "minLength": 1, "maxLength": 200}
_KEYWORD_LIST = {
    "type": "array",
    "maxItems": 50,
    "items": {"type": "string", "minLength": 1, "maxLength": 80},
}

PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["keywords", "fields"],
    "properties": {
        "description": {"type": "string", "maxLength": 500},
        "keywords": _KEYWORD_LIST,
        "exclude_keywords": _KEYWORD_LIST,
        "annual_bonus": {"type": "boolean"},
        "fields": {
            "type": "object",
            "maxProperties": 30,
            "propertyNames": {"pattern": r"^[a-z][a-z0-9_]{0,40}$"},
            "additionalProperties": {"type": "string", "maxLength": 300},
        },
    },
}

SELECTORS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["logged_in", "active_tenant", "upload_form", "file_input", "submit", "success"],
    "properties": {
        "logged_in": _SELECTOR,
        "active_tenant": _SELECTOR,
        "tenant_picker": _SELECTOR,
        "tenant_activate": _SELECTOR,
        "upload_form": _SELECTOR,
        "file_input": _SELECTOR,
        "period_input": _SELECTOR,
        "annual_bonus_checkbox": _SELECTOR,
        "submit": _SELECTOR,
        "success": _SELECTOR,
        "error": _SELECTOR,
        "fields": {
            "type": "object",
            "maxProperties": 30,
            "propertyNames": {"pattern": r"^[a-z][a-z0-9_]{0,40}$"},
            "additionalProperties": _SELECTOR,
        },
    },
}

TENANT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["display_name", "portal_name", "portal", "profiles"],
    "properties": {
        "display_name": _NAME,
        "portal_name": _NAME,
        "portal_picker_label": _NAME,
        "portal": {
            "type": "object",
            "additionalProperties": False,
            "required": ["base_url", "selectors"],
            "properties": {
                "base_url": {"type": "string", "pattern": r"^https?://", "maxLength": 500},
                "upload_path": {"type": "string", "maxLength": 300},
                "selectors": SELECTORS_SCHEMA,
            },
        },
        "profiles": {
            "type": "object",
            "maxProperties": 500,
            "propertyNames": {"minLength": 1, "maxLength": 120},
            "additionalProperties": PROFILE_SCHEMA,
        },
    },
}

DB_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "tenants"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "kind": {"enum": ["database", "export"]},
        "exported_at": {"type": "string", "maxLength": 40},
        "tenants": {
            "type": "object",
            "maxProperties": 2000,
            "propertyNames": _ID,
            "additionalProperties": TENANT_SCHEMA,
        },
    },
}

_VALIDATOR = Draft202012Validator(DB_SCHEMA)
_SECRET_KEY = re.compile(
    r"^(?:.*[_-])?(?:password|passwd|senha|secret|token|api[_-]?key|cookie|credential)s?$", re.I
)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class SchemaError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors[:5]) + (" ..." if len(errors) > 5 else ""))


def _walk_keys(obj: Any, path: str = "$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.{k}", k
            yield from _walk_keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, f"{path}[{i}]")


def is_local_host(url: str) -> bool:
    return (urlparse(url).hostname or "") in _LOCAL_HOSTS


def validate_db(data: Any) -> list[str]:
    """Retorna a lista de erros (vazia se válido)."""
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<raiz>'}: {e.message}"
        for e in sorted(_VALIDATOR.iter_errors(data), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        return errors

    for path, key in _walk_keys(data):
        if _SECRET_KEY.search(key):
            errors.append(f"{path}: campos de credencial não são permitidos no cadastro")

    seen_portal_names: dict[str, str] = {}
    from .text import normalize_tenant_name  # import local evita ciclo

    for tid, tenant in data["tenants"].items():
        url = tenant["portal"]["base_url"]
        if url.startswith("http://") and not is_local_host(url):
            errors.append(f"tenants/{tid}/portal/base_url: HTTP sem TLS só é aceito para localhost")
        key = normalize_tenant_name(tenant["portal_name"])
        if key in seen_portal_names:
            errors.append(
                f"tenants/{tid}/portal_name: mesmo nome de portal do tenant '{seen_portal_names[key]}'"
            )
        seen_portal_names[key] = tid
        sel = tenant["portal"]["selectors"]
        for pname, profile in tenant["profiles"].items():
            for field in profile["fields"]:
                if field not in sel.get("fields", {}):
                    errors.append(
                        f"tenants/{tid}/profiles/{pname}/fields/{field}: sem seletor em portal.selectors.fields"
                    )
    return errors


def ensure_valid(data: Any) -> None:
    errors = validate_db(data)
    if errors:
        raise SchemaError(errors)
