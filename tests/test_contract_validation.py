"""文档错误结构与实际响应一致性校验 + 启动守卫行为。"""

from __future__ import annotations

import copy

import pytest

from app.contracts.checker import run_startup_checks
from app.contracts.extractor import extract_openapi
from app.contracts.validation import validate_documented_error_schema
from app.errors.exceptions import ContractMappingError
from app.errors.models import ErrorResponse
from app.main import create_app


def test_documented_schema_matches_actual() -> None:
    """应用文档里的 ErrorResponse 必须与 pydantic 真实模型一致。"""
    app = create_app(run_checks=False)
    schema = extract_openapi(app)
    result = validate_documented_error_schema(app, schema)
    assert result.ok, [f.message for f in result.findings]
    # 文档字段与真实模型字段一一对应
    documented_props = set(
        schema["components"]["schemas"]["ErrorResponse"]["properties"]
    )
    assert documented_props == set(
        ErrorResponse.model_json_schema()["properties"]
    )


def test_missing_error_component_is_detected() -> None:
    app = create_app(run_checks=False)
    schema = extract_openapi(app)
    schema["components"]["schemas"].pop("ErrorResponse")
    result = validate_documented_error_schema(app, schema)
    assert result.ok is False
    assert result.findings[0].location == "components.schemas.ErrorResponse"


def test_documented_schema_drift_is_detected() -> None:
    """文档把 request_id 类型改写成 integer（偏离实际 string）必须被定位。"""
    app = create_app(run_checks=False)
    schema = extract_openapi(app)
    drifted = copy.deepcopy(schema)
    drifted["components"]["schemas"]["ErrorResponse"]["properties"]["request_id"] = {
        "type": "integer"
    }
    result = validate_documented_error_schema(app, drifted)
    assert result.ok is False
    finding = next(
        f for f in result.findings
        if f.location == "components.schemas.ErrorResponse.properties.request_id"
    )
    assert "类型不一致" in finding.message


def test_documented_extra_field_is_detected() -> None:
    """文档多出实际不存在的字段也要被捕获。"""
    app = create_app(run_checks=False)
    schema = copy.deepcopy(extract_openapi(app))
    schema["components"]["schemas"]["ErrorResponse"]["properties"]["trace"] = {
        "type": "string"
    }
    result = validate_documented_error_schema(app, schema)
    assert result.ok is False
    assert any(
        f.location.endswith("properties.trace") and "不存在" in f.message
        for f in result.findings
    )


def test_actual_response_matches_documented_schema(client) -> None:
    """端到端：真实 422 响应的键集合与文档 ErrorResponse 字段一致。"""
    resp = client.post("/items", json={})
    body = resp.json()
    documented = set(
        client.app.openapi()["components"]["schemas"]["ErrorResponse"]["properties"]
    )
    assert set(body) == documented


def test_startup_checks_pass_on_clean_app() -> None:
    app = create_app(run_checks=False)
    result, report = run_startup_checks(
        app, expected_business_errors=(
            __import__("app.api.router", fromlist=["OutOfStockError"]).OutOfStockError,
        )
    )
    assert result.ok and report is None


def test_startup_checks_fail_on_missing_mapping() -> None:
    """未注册的业务异常必须让启动守卫失败，而非静默降级。"""

    class DeclaredButUnregistered(Exception):
        pass

    app = create_app(run_checks=False)
    with pytest.raises(ContractMappingError) as exc:
        run_startup_checks(
            app, expected_business_errors=(DeclaredButUnregistered,)
        )
    assert "DeclaredButUnregistered" in str(exc.value)


def test_startup_checks_fail_on_breaking_baseline() -> None:
    """与基线契约相比出现破坏性变更时，strict 启动必须失败并定位字段。"""
    app = create_app(run_checks=False)
    current = extract_openapi(app)
    # 构造一个“基线”：比当前多一个字段，于是当前相对基线是“删除字段”
    baseline = copy.deepcopy(current)
    er = baseline["components"]["schemas"]["ErrorResponse"]
    er.setdefault("properties", {})["legacy_field"] = {"type": "string"}
    with pytest.raises(ContractMappingError) as exc:
        run_startup_checks(app, baseline_contract=baseline)
    msg = str(exc.value)
    assert "legacy_field" in msg and "破坏性变更" in msg


def test_non_strict_returns_findings_without_raising() -> None:
    app = create_app(run_checks=False)
    current = extract_openapi(app)
    baseline = copy.deepcopy(current)
    baseline["components"]["schemas"]["ErrorResponse"]["properties"]["x"] = {
        "type": "string"
    }
    result, report = run_startup_checks(
        app, baseline_contract=baseline, strict=False
    )
    assert result.ok is False and report is not None
    assert any("x" in f.location for f in result.findings)
