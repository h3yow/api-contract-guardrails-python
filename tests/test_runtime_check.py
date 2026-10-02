"""逐接口、逐状态码的错误结构运行时核对。

覆盖：
- 正常样例下真实返回核对通过（不误报）；
- 人为制造文档与真实返回不一致时能被抓住并定位到接口+方法+状态码+字段；
- 接口/状态码没被核对到时不允许给出通过结论；
- 日志可见请求标识与归一化前后的错误结论。
"""

from __future__ import annotations

import copy
import logging

import httpx
import pytest

from app.contracts.extractor import extract_openapi
from app.contracts.probes import ErrorProbe, all_probes
from app.contracts.runtime_check import verify_error_responses
from app.errors.context import REQUEST_ID_HEADER
from app.errors.exceptions import ContractMappingError
from app.contracts.checker import run_startup_checks
from app.main import create_app


def _clean_app_and_schema():
    app = create_app(run_checks=False)
    return app, extract_openapi(app)


def _error_body(**extra):
    body = {
        "code": "REQUEST_VALIDATION_FAILED",
        "category": "validation",
        "message": "请求参数校验失败",
        "request_id": "rid-fabricated",
        "details": [{"location": ["body", "name"], "reason": "missing",
                     "message": "Field required"}],
    }
    body.update(extra)
    return body


# ------------------------------------------------------------ 正常样例

def test_runtime_check_passes_on_clean_app() -> None:
    """真实打接口核对：文档声明与每个接口真实错误结构一致，不得误报。"""
    app, schema = _clean_app_and_schema()
    result = verify_error_responses(app, schema)
    assert result.ok, [f"{f.location}: {f.message}" for f in result.findings]
    assert result.findings == []


# ------------------------------------------------------------ 文档漂移被抓住并定位

def test_doc_declares_extra_field_is_located() -> None:
    """文档里先写了还没实现的字段 -> 失败并定位到接口+状态码+字段。"""
    app, schema = _clean_app_and_schema()
    drifted = copy.deepcopy(schema)
    drifted["components"]["schemas"]["ErrorResponse"]["properties"]["trace"] = {
        "type": "string"
    }
    result = verify_error_responses(app, drifted)
    assert result.ok is False
    finding = next(
        f for f in result.findings
        if f.location == "POST /items [422] -> trace"
    )
    assert "真实响应中不存在" in finding.message


def test_doc_missing_actual_field_is_located() -> None:
    """实现改了（文档删掉 request_id 声明）-> 真实多出的字段被定位。"""
    app, schema = _clean_app_and_schema()
    drifted = copy.deepcopy(schema)
    node = drifted["components"]["schemas"]["ErrorResponse"]
    node["properties"].pop("request_id")
    node["required"].remove("request_id")
    result = verify_error_responses(app, drifted)
    assert result.ok is False
    assert any(
        f.location == "GET /boom [500] -> request_id"
        and "文档未声明" in f.message
        for f in result.findings
    )


def test_doc_nested_type_drift_is_located() -> None:
    """嵌套数组元素内部字段类型漂移 -> 定位到 details[].message。"""
    app, schema = _clean_app_and_schema()
    drifted = copy.deepcopy(schema)
    drifted["components"]["schemas"]["ErrorDetail"]["properties"]["message"] = {
        "type": "integer"
    }
    result = verify_error_responses(app, drifted)
    assert result.ok is False
    finding = next(
        f for f in result.findings
        if f.location == "POST /items [422] -> details[].message"
    )
    assert "类型不一致" in finding.message


def test_doc_requiredness_drift_is_located() -> None:
    """必填性漂移（文档把 message 改成可选）-> 被抓住。"""
    app, schema = _clean_app_and_schema()
    drifted = copy.deepcopy(schema)
    node = drifted["components"]["schemas"]["ErrorDetail"]
    node["required"].remove("message")
    result = verify_error_responses(app, drifted)
    assert result.ok is False
    assert any(
        f.location == "POST /items [422] -> details[].message"
        and "必填性不一致" in f.message
        for f in result.findings
    )


def test_impl_drift_without_doc_sync_is_located() -> None:
    """实现多返回了字段却没同步文档（真实响应侧漂移）-> 被抓住。"""
    app, schema = _clean_app_and_schema()

    def drifted_request(client) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            422,
            json=_error_body(trace_id="abc123"),  # 实现悄悄多返回了 trace_id
            headers={REQUEST_ID_HEADER: "rid-drift"},
        )

    probe = ErrorProbe(
        method="post", path="/items", status=422,
        name="impl-drifted", request=drifted_request,
    )
    result = verify_error_responses(app, schema, probes=all_probes() + [probe])
    assert result.ok is False
    assert any(
        f.location == "POST /items [422] -> trace_id"
        and "文档未声明" in f.message
        for f in result.findings
    )


# ------------------------------------------------------------ 覆盖率：不许静默通过

def test_endpoint_without_any_probe_fails() -> None:
    """任何接口完全没有探针时，不允许给出通过结论。"""
    app, schema = _clean_app_and_schema()
    result = verify_error_responses(app, schema, probes=[])
    assert result.ok is False
    uncovered = [f for f in result.findings if "没有任何错误探针覆盖" in f.message]
    # 四个接口一个都不能漏
    assert {f.location for f in uncovered} == {
        "GET /", "POST /items", "GET /items/{item_id}", "GET /boom",
    }


def test_documented_status_without_probe_fails() -> None:
    """文档声明了错误状态但没有探针真实核对 -> 失败并定位。"""
    app, schema = _clean_app_and_schema()
    drifted = copy.deepcopy(schema)
    drifted["paths"]["/items"]["post"]["responses"]["404"] = {
        "description": "未实现的声明",
        "content": {"application/json": {"schema": {
            "$ref": "#/components/schemas/ErrorResponse"}}},
    }
    result = verify_error_responses(app, drifted)
    assert result.ok is False
    assert any(
        f.location == "POST /items [404]" and "没有探针真实核对" in f.message
        for f in result.findings
    )


def test_probe_status_not_documented_fails() -> None:
    """探针真实返回的错误状态未在文档中声明 -> 失败并定位。"""
    app, schema = _clean_app_and_schema()

    def teapot(client) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(418, json=_error_body())

    probe = ErrorProbe(
        method="post", path="/items", status=418,
        name="undocumented-status", request=teapot,
    )
    result = verify_error_responses(app, schema, probes=all_probes() + [probe])
    assert result.ok is False
    assert any(
        f.location == "POST /items [418]" and "未在文档中声明" in f.message
        for f in result.findings
    )


# ------------------------------------------------------------ 与启动守卫集成

def test_startup_checks_fail_on_runtime_doc_drift() -> None:
    """启动守卫（strict）在逐接口核对失败时抛错，finding 定位到字段。"""
    app, schema = _clean_app_and_schema()
    # 直接改缓存的文档：request_id 类型漂移
    schema["components"]["schemas"]["ErrorResponse"]["properties"]["request_id"] = {
        "type": "integer"
    }
    with pytest.raises(ContractMappingError) as exc:
        run_startup_checks(app)
    msg = str(exc.value)
    assert "POST /items [422] -> request_id" in msg


# ------------------------------------------------------------ 日志可复现

def test_probe_logs_show_request_id_and_normalization(caplog) -> None:
    """跑核对的日志里能看到请求标识与归一化前后的错误结论。"""
    caplog.set_level(logging.INFO)
    app, schema = _clean_app_and_schema()
    result = verify_error_responses(app, schema)
    assert result.ok

    text = "\n".join(r.getMessage() for r in caplog.records)
    # 归一化前后：原始异常 -> 归一化结论（含 request_id）
    assert "error_normalized" in text
    assert "raw=RuntimeError" in text and "code=INTERNAL_ERROR" in text
    # 每个探针一行结论，且都带 request_id
    probe_lines = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith("error_probe ")
    ]
    assert len(probe_lines) == len(all_probes())
    assert all("request_id=" in line for line in probe_lines)
    assert any("ok=True" in line for line in probe_lines)
