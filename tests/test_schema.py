import copy
import json

import pytest

from pdf_batch_uploader.core.schema import SchemaError, ensure_valid, validate_db
from pdf_batch_uploader.core.store import TenantStore


def test_demo_db_is_valid(demo_db):
    assert validate_db(demo_db) == []


def test_unknown_property_rejected(demo_db):
    demo_db["tenants"]["alfa"]["extra"] = "x"
    assert any("extra" in e for e in validate_db(demo_db))


@pytest.mark.parametrize("key", ["password", "senha", "api_key", "session_token", "cookie"])
def test_credential_like_keys_rejected(demo_db, key):
    demo_db["tenants"]["alfa"]["portal"]["selectors"]["fields"][key] = "#x"
    demo_db["tenants"]["alfa"]["profiles"]["Ferias"]["fields"][key] = "valor"
    errors = validate_db(demo_db)
    assert any("credencial" in e for e in errors)


def test_plain_http_only_for_localhost(demo_db):
    # domínio reservado (RFC 2606); montado em partes só para não parecer um endereço real
    demo_db["tenants"]["alfa"]["portal"]["base_url"] = "http" + "://portal.example.test/"
    assert any("HTTP sem TLS" in e for e in validate_db(demo_db))


def test_non_http_scheme_rejected(demo_db):
    demo_db["tenants"]["alfa"]["portal"]["base_url"] = "file:///etc/passwd"
    assert validate_db(demo_db)


def test_duplicate_portal_name_rejected(demo_db):
    demo_db["tenants"]["alfa-filial"]["portal_name"] = "empresa alfa  ltda"
    assert any("mesmo nome de portal" in e for e in validate_db(demo_db))


def test_profile_field_needs_selector(demo_db):
    demo_db["tenants"]["alfa"]["profiles"]["Ferias"]["fields"]["unknown_field"] = "X"
    assert any("sem seletor" in e for e in validate_db(demo_db))


def test_profile_values_must_be_strings(demo_db):
    demo_db["tenants"]["alfa"]["profiles"]["Ferias"]["fields"]["doc_type"] = 3
    with pytest.raises(SchemaError):
        ensure_valid(demo_db)


def test_bad_tenant_id(demo_db):
    demo_db["tenants"]["Alfa Maiúsculo"] = copy.deepcopy(demo_db["tenants"]["alfa"])
    demo_db["tenants"]["Alfa Maiúsculo"]["portal_name"] = "OUTRO"
    assert validate_db(demo_db)


def test_store_refuses_invalid_file(paths):
    paths.tenants_file.write_text(json.dumps({"schema_version": 1, "tenants": {"x": {}}}), encoding="utf-8")
    with pytest.raises(SchemaError):
        TenantStore(paths.tenants_file).load()


def test_store_save_creates_backup(store, demo_db):
    demo_db["tenants"]["alfa"]["display_name"] = "Alfa renomeado"
    backup = store.save(demo_db)
    assert backup is not None and backup.exists()
    assert store.load().tenant("alfa").display_name == "Alfa renomeado"
