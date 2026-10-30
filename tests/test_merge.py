import copy

import pytest

from pdf_batch_uploader.core.merge import export_tenants, merge_package
from pdf_batch_uploader.core.schema import SchemaError


def test_export_only_selected_and_no_history(demo_db):
    pkg = export_tenants(demo_db, ["alfa"])
    assert list(pkg["tenants"]) == ["alfa"]
    assert pkg["kind"] == "export"
    assert set(pkg) <= {"schema_version", "kind", "exported_at", "tenants"}


def test_export_unknown_tenant(demo_db):
    with pytest.raises(KeyError):
        export_tenants(demo_db, ["nope"])


def test_merge_into_empty_adds_everything(demo_db):
    empty = {"schema_version": 1, "kind": "database", "tenants": {}}
    merged, rep = merge_package(empty, export_tenants(demo_db, ["alfa", "alfa-filial"]))
    assert set(rep.tenants_added) == {"alfa", "alfa-filial"}
    assert len(rep.profiles_added) == 5
    assert not rep.conflicts


def test_merge_never_overwrites(demo_db):
    local = copy.deepcopy(demo_db)
    pkg = export_tenants(demo_db, ["alfa"])
    prof = pkg["tenants"]["alfa"]["profiles"]
    prof["Ferias"]["fields"]["audience"] = "ACTIVE"  # conflito
    prof["Novo perfil"] = {"keywords": ["novo"], "fields": {"doc_type": "MONTHLY"}}  # novo
    merged, rep = merge_package(local, pkg)

    assert merged["tenants"]["alfa"]["profiles"]["Ferias"]["fields"]["audience"] == "ALL"  # local mantido
    assert "Novo perfil" in merged["tenants"]["alfa"]["profiles"]
    assert rep.profiles_added == ["alfa/Novo perfil"]
    assert [(c.tenant_id, c.item) for c in rep.conflicts] == [("alfa", "Ferias")]
    assert "alfa/Mensal - Ativos" in rep.identical_skipped
    assert local == demo_db  # entrada não é mutada


def test_tenant_matched_by_portal_name_not_id(demo_db):
    pkg = export_tenants(demo_db, ["alfa"])
    pkg["tenants"] = {"outro-id": pkg["tenants"]["alfa"]}
    merged, rep = merge_package(demo_db, pkg)
    assert "outro-id" not in merged["tenants"]
    assert not rep.tenants_added


def test_new_tenant_with_colliding_id_gets_new_id(demo_db):
    pkg = export_tenants(demo_db, ["alfa"])
    pkg["tenants"]["alfa"]["portal_name"] = "EMPRESA DELTA"
    merged, rep = merge_package(demo_db, pkg)
    assert rep.tenants_added == ["alfa-2"]
    assert merged["tenants"]["alfa"]["portal_name"] == "EMPRESA ALFA LTDA"


def test_portal_difference_reported(demo_db):
    pkg = export_tenants(demo_db, ["alfa"])
    pkg["tenants"]["alfa"]["portal"]["selectors"]["submit"] = "#outro"
    merged, rep = merge_package(demo_db, pkg)
    assert any(c.item == "portal" for c in rep.conflicts)
    assert merged["tenants"]["alfa"]["portal"]["selectors"]["submit"] == "#submit-btn"


def test_profile_without_local_selector_is_conflict(demo_db):
    pkg = export_tenants(demo_db, ["alfa-filial"])
    pkg["tenants"]["alfa-filial"]["portal"]["selectors"]["fields"]["match_by"] = "#match-by"
    pkg["tenants"]["alfa-filial"]["profiles"]["Com match"] = {"keywords": ["x"], "fields": {"match_by": "TAX_ID"}}
    merged, rep = merge_package(demo_db, pkg)
    assert "Com match" not in merged["tenants"]["alfa-filial"]["profiles"]
    assert any(c.item == "Com match" for c in rep.conflicts)


def test_invalid_package_rejected(demo_db):
    with pytest.raises(SchemaError):
        merge_package(demo_db, {"schema_version": 1, "tenants": {"x": {"display_name": "x"}}})
