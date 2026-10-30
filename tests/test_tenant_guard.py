import pytest

from pdf_batch_uploader.core.tenant_guard import TenantMismatch, assert_tenant, pick_unique_option, same_tenant


def test_exact_after_light_normalization():
    assert same_tenant("  Empresa   Alfa ltda ", "EMPRESA ALFA LTDA")
    assert same_tenant("COOPERATIVA GAMÁ", "cooperativa gama")


def test_prefix_or_contains_is_not_enough():
    assert not same_tenant("EMPRESA ALFA LTDA FILIAL NORTE", "EMPRESA ALFA LTDA")
    assert not same_tenant("EMPRESA ALFA", "EMPRESA ALFA LTDA")
    assert not same_tenant("", "")


def test_assert_tenant_errors():
    with pytest.raises(TenantMismatch, match="não encontrado"):
        assert_tenant([], "X")
    with pytest.raises(TenantMismatch, match="ambíguo"):
        assert_tenant(["X", "X"], "X")
    with pytest.raises(TenantMismatch, match="difere"):
        assert_tenant(["EMPRESA BETA S.A."], "EMPRESA ALFA LTDA")
    assert assert_tenant(["EMPRESA ALFA LTDA"], "empresa alfa ltda") == "EMPRESA ALFA LTDA"


def test_pick_unique_option():
    opts = ["— selecione —", "EMPRESA ALFA LTDA", "EMPRESA ALFA LTDA FILIAL NORTE", "EMPRESA BETA S.A."]
    assert pick_unique_option(opts, "EMPRESA ALFA LTDA") == 1
    with pytest.raises(TenantMismatch):
        pick_unique_option(opts, "EMPRESA ALFA")
    with pytest.raises(TenantMismatch, match="ambígua"):
        pick_unique_option(opts + ["empresa alfa ltda"], "EMPRESA ALFA LTDA")
