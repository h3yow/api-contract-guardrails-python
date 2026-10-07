"""逐接口、逐状态码的真实响应错误结构核对。

覆盖：
- 正常样例下真实响应核对通过，且日志可见请求标识、业务场景与归一化结论；
- 人为制造文档与真实返回不一致时能被抓住并定位到接口/方法/状态码/字段；
- 「文档声明了、真实返回却不存在的字段」无论必填/可选都判失败，
  且嵌套对象与数组元素内部同样兜底；
- 文档未声明真实返回的状态、声明了却没有探针核对、接口完全未覆盖，
  都不得给出「通过」结论。
"""

from __future__ import annotations

import copy
import logging

import pytest
from fastapi import FastAPI

from app.contracts.extractor import extract_openapi
from app.contracts.live_check import validate_live_error_structures
from app.contracts.probes import ERROR_PROBES, NO_ERROR_SURFACE
from app.main import create_app


@pytest.fixture()
def schema():
    app = create_app(run_checks=False)
    return app, extract_openapi(app)


def _check(app, openapi_schema, probes=ERROR_PROBES):
    return validate_live_error_structures(
        app, openapi_schema, probes, no_error_surface=NO_ERROR_SURFACE
    )


def test_live_check_passes_on_real_app(schema, caplog) -> None:
    """正常样例：每个声明的错误状态都被真实请求核对通过。"""
    app, openapi_schema = schema
    # 统一把捕获级别设为 INFO：归一化日志（app.errors, WARNING）
    # 与核对结论日志（app.contracts, INFO）都能进入 caplog
    caplog.set_level(logging.INFO)
    result = _check(app, openapi_schema)
    assert result.ok, [f.message for f in result.findings]

    text = "\n".join(r.getMessage() for r in caplog.records)
    # 日志可见请求标识与归一化前（原始异常）/归一化后（错误码）结论
    assert "error_normalized" in text
    assert "request_id=" in text
    assert "REQUEST_VALIDATION_FAILED" in text  # 归一化后的错误码
    assert "OutOfStockError" in text            # 归一化前的原始异常
    # 核对结论本身也带请求标识与业务场景说明
    assert "错误结构核对通过" in text
    assert "库存不足" in text          # 业务规则拒绝场景
    assert "请求校验失败" in text      # 请求校验失败场景
    assert "脱敏 500" in text          # 未预期异常场景


def test_drifted_field_type_is_caught_and_located(schema) -> None:
    """文档把 request_id 写成 integer（真实返回 string）必须被定位。"""
    app, openapi_schema = schema
    drifted = copy.deepcopy(openapi_schema)
    drifted["components"]["schemas"]["ErrorResponse"]["properties"]["request_id"] = {
        "type": "integer"
    }
    result = _check(app, drifted)
    assert result.ok is False
    finding = next(f for f in result.findings if "request_id" in f.location)
    # 定位到接口、方法、状态码、字段
    assert "POST /items" in finding.location
    assert "422" in finding.location
    assert "类型不一致" in finding.message


def test_undocumented_field_in_real_response_is_caught(schema) -> None:
    """真实响应包含文档未声明的字段（文档没跟上实现）必须失败。"""
    app, openapi_schema = schema
    drifted = copy.deepcopy(openapi_schema)
    er = drifted["components"]["schemas"]["ErrorResponse"]
    er["properties"].pop("message")
    er["required"].remove("message")
    result = _check(app, drifted)
    assert result.ok is False
    assert any(
        "message" in f.location and "未声明" in f.message for f in result.findings
    )


def test_missing_required_field_in_real_response_is_caught(schema) -> None:
    """文档声明了还没实现的必填字段（文档超前于实现）必须失败。"""
    app, openapi_schema = schema
    drifted = copy.deepcopy(openapi_schema)
    er = drifted["components"]["schemas"]["ErrorResponse"]
    er["properties"]["trace_id"] = {"type": "string"}
    er["required"].append("trace_id")
    result = _check(app, drifted)
    assert result.ok is False
    assert any(
        "trace_id" in f.location and "必填" in f.message for f in result.findings
    )


def test_missing_optional_field_in_real_response_is_caught(schema) -> None:
    """文档把真实不存在的字段标成「可选」同样必须失败（曾经的漏洞）。"""
    app, openapi_schema = schema
    drifted = copy.deepcopy(openapi_schema)
    er = drifted["components"]["schemas"]["ErrorResponse"]
    er["properties"]["trace_id"] = {"type": "string"}  # 可选：不进 required
    result = _check(app, drifted)
    assert result.ok is False
    finding = next(f for f in result.findings if "trace_id" in f.location)
    # 定位到接口、方法、状态码、字段
    assert "POST /items" in finding.location
    assert "422" in finding.location
    assert "可选" in finding.message and "不存在" in finding.message


def test_missing_required_field_inside_array_item_is_caught(schema) -> None:
    """数组元素内部：文档声明了必填字段而真实元素没有，必须定位到 details[i].x。"""
    app, openapi_schema = schema
    drifted = copy.deepcopy(openapi_schema)
    ed = drifted["components"]["schemas"]["ErrorDetail"]
    ed["properties"]["hint"] = {"type": "string"}
    ed["required"].append("hint")
    result = _check(app, drifted)
    assert result.ok is False
    finding = next(f for f in result.findings if "hint" in f.location)
    assert "details[" in finding.location
    assert "必填" in finding.message


def test_missing_optional_field_inside_array_item_is_caught(schema) -> None:
    """数组元素内部：可选字段「文档写了、真实没返回」同样必须失败。"""
    app, openapi_schema = schema
    drifted = copy.deepcopy(openapi_schema)
    ed = drifted["components"]["schemas"]["ErrorDetail"]
    ed["properties"]["hint"] = {"type": "string"}  # 可选
    result = _check(app, drifted)
    assert result.ok is False
    finding = next(f for f in result.findings if "hint" in f.location)
    assert "details[" in finding.location
    assert "可选" in finding.message and "不存在" in finding.message


def test_nested_array_item_drift_is_caught(schema) -> None:
    """嵌套数组元素里的字段类型漂移也要被定位到 details[i].reason。"""
    app, openapi_schema = schema
    drifted = copy.deepcopy(openapi_schema)
    drifted["components"]["schemas"]["ErrorDetail"]["properties"]["reason"] = {
        "type": "integer"
    }
    result = _check(app, drifted)
    assert result.ok is False
    finding = next(f for f in result.findings if "reason" in f.location)
    assert "details[" in finding.location
    assert "GET /boom" in finding.location or "POST /items" in finding.location


def test_real_status_not_documented_is_caught(schema) -> None:
    """接口真实返回了文档未声明的错误状态（实现改了没同步文档）。"""
    app, openapi_schema = schema
    drifted = copy.deepcopy(openapi_schema)
    del drifted["paths"]["/boom"]["get"]["responses"]["500"]
    result = _check(app, drifted)
    assert result.ok is False
    assert any(
        "GET /boom 500" in f.location and "未声明" in f.message
        for f in result.findings
    )


def test_documented_status_without_probe_is_caught(schema) -> None:
    """文档声明了错误状态但没有任何探针核对 -> 不得放行。"""
    app, openapi_schema = schema
    drifted = copy.deepcopy(openapi_schema)
    drifted["paths"]["/items"]["post"]["responses"]["418"] = {
        "description": "teapot",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
            }
        },
    }
    result = _check(app, drifted)
    assert result.ok is False
    assert any(
        "POST /items 418" in f.location and "缺少代表性失败请求" in f.message
        for f in result.findings
    )


def test_uncovered_endpoint_is_caught() -> None:
    """接口完全没有错误声明且未豁免 -> 不得给出通过结论。"""
    app = FastAPI()

    @app.get("/silent")
    def silent() -> dict[str, str]:
        return {"ok": "yes"}

    result = validate_live_error_structures(
        app, app.openapi(), [], no_error_surface=frozenset()
    )
    assert result.ok is False
    assert any(
        "GET /silent" in f.location and "没有任何错误状态声明" in f.message
        for f in result.findings
    )


def test_probe_status_mismatch_is_caught(schema) -> None:
    """探针真实状态码与预期不符时必须失败并带请求标识。"""
    from app.contracts.probes import ErrorProbe

    app, openapi_schema = schema
    bad_probe = ErrorProbe(
        operation="/boom", method="get", expected_status=502,
        note="人为制造的状态码不符",
    )
    result = _check(app, openapi_schema, probes=[bad_probe])
    assert result.ok is False
    finding = next(f for f in result.findings if "502" in f.location)
    assert "GET /boom 502" in finding.location
    assert "状态码" in finding.message and "request_id=" in finding.message


# --- 嵌套对象场景：真实返回里带嵌套对象的独立小应用 -------------------------

from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.contracts.probes import ErrorProbe  # noqa: E402


class _NestedMeta(BaseModel):
    retryable: bool


class _NestedErrorBody(BaseModel):
    code: str
    meta: _NestedMeta


class _NestedError(BaseModel):
    error: _NestedErrorBody


def _nested_app() -> FastAPI:
    """错误响应含嵌套对象的最小应用，真实返回与声明完全一致。"""
    app = FastAPI()

    @app.get(
        "/widget",
        responses={400: {"model": _NestedError, "description": "嵌套错误"}},
    )
    def widget() -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "bad_widget", "meta": {"retryable": True}}},
        )

    return app


_NESTED_PROBE = ErrorProbe(
    operation="/widget", method="get", expected_status=400,
    note="嵌套对象错误结构",
)


def test_nested_object_passes_when_doc_matches() -> None:
    """嵌套对象结构与文档完全一致时允许通过（不误报）。"""
    app = _nested_app()
    result = validate_live_error_structures(
        app, app.openapi(), [_NESTED_PROBE], no_error_surface=frozenset()
    )
    assert result.ok, [f.message for f in result.findings]


def test_missing_optional_field_inside_nested_object_is_caught() -> None:
    """嵌套对象内部：可选字段「文档写了、真实没返回」必须定位到深层路径。"""
    app = _nested_app()
    drifted = copy.deepcopy(app.openapi())
    meta = drifted["components"]["schemas"]["_NestedMeta"]
    meta["properties"]["trace_id"] = {"type": "string"}  # 可选
    result = validate_live_error_structures(
        app, drifted, [_NESTED_PROBE], no_error_surface=frozenset()
    )
    assert result.ok is False
    finding = next(f for f in result.findings if "trace_id" in f.location)
    assert "GET /widget 400" in finding.location
    assert "error.meta.trace_id" in finding.location
    assert "可选" in finding.message and "不存在" in finding.message


def test_missing_required_field_inside_nested_object_is_caught() -> None:
    """嵌套对象内部：必填字段「文档写了、真实没返回」同样被定位。"""
    app = _nested_app()
    drifted = copy.deepcopy(app.openapi())
    meta = drifted["components"]["schemas"]["_NestedMeta"]
    meta["properties"]["trace_id"] = {"type": "string"}
    meta.setdefault("required", []).append("trace_id")
    result = validate_live_error_structures(
        app, drifted, [_NESTED_PROBE], no_error_surface=frozenset()
    )
    assert result.ok is False
    finding = next(f for f in result.findings if "trace_id" in f.location)
    assert "error.meta.trace_id" in finding.location
    assert "必填" in finding.message
