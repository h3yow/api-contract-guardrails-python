"""启动/校验流程：把配置缺陷暴露在启动阶段，而不是运行时静默降级。

校验两类问题：

1. 映射完整性：必须显式登记的框架异常是否都已登记到统一错误码；
2. 文档一致性：OpenAPI 文档中出现的所有 4xx/5xx 错误响应结构必须
   与 :class:`~app.errors.ErrorEnvelope` 的实际模型结构一致。

每个校验函数返回问题描述列表；空列表表示通过。调用方（应用启动或
CI 校验脚本）应在列表非空时直接失败并打印全部问题。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import ErrorEnvelope
from app.mapping import FRAMEWORK_ERROR_MAP

# 必须显式登记的框架异常：缺少任何一项都意味着该入口可能逃逸出统一错误结构。
REQUIRED_FRAMEWORK_EXCEPTIONS: tuple[type[Exception], ...] = (
    RequestValidationError,  # 请求校验失败
    StarletteHTTPException,  # 404 / 405 等框架级 HTTP 错误
    Exception,  # 未预期异常兜底
)


def validate_error_mappings() -> list[str]:
    """检查框架异常映射是否完整，返回问题列表。"""
    problems: list[str] = []
    for exc_type in REQUIRED_FRAMEWORK_EXCEPTIONS:
        if exc_type not in FRAMEWORK_ERROR_MAP:
            problems.append(
                f"框架异常 {exc_type.__name__} 未显式登记到统一错误码，"
                f"该入口将逃逸统一错误结构"
            )
    return problems


def _canonical(schema: Any) -> Any:
    """把模型导出的 schema 规范化到 OpenAPI 组件布局，消除引用写法差异。

    pydantic 的 ``model_json_schema`` 使用 ``#/$defs/X`` 引用，而 FastAPI
    生成的 OpenAPI 使用 ``#/components/schemas/X``；两者语义相同，比较前
    统一改写为组件引用形式。
    """
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "$defs":
                continue  # 嵌套定义由 _expected_components 提升到组件层
            if key == "$ref" and isinstance(value, str) and value.startswith("#/$defs/"):
                out[key] = "#/components/schemas/" + value.rsplit("/", 1)[-1]
            else:
                out[key] = _canonical(value)
        return out
    if isinstance(schema, list):
        return [_canonical(item) for item in schema]
    return schema


def _expected_components() -> dict[str, dict[str, Any]]:
    """从实际错误模型导出期望的 OpenAPI 组件集合。"""
    raw = ErrorEnvelope.model_json_schema()
    components: dict[str, dict[str, Any]] = {
        "ErrorEnvelope": _canonical({k: v for k, v in raw.items() if k != "$defs"})
    }
    for name, sub in raw.get("$defs", {}).items():
        components[name] = _canonical(sub)
    return components


def _resolve_ref(doc: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not ref:
        return schema
    name = ref.rsplit("/", 1)[-1]
    return doc.get("components", {}).get("schemas", {}).get(name, {})


def _iter_error_response_schemas(doc: dict[str, Any]):
    """遍历文档中所有 4xx/5xx 响应的内容 schema。"""
    for path, item in doc.get("paths", {}).items():
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            for status, resp in op.get("responses", {}).items():
                if not str(status).startswith(("4", "5")):
                    continue
                content = resp.get("content", {}).get("application/json", {})
                schema = content.get("schema")
                if schema is not None:
                    yield path, method, status, schema


def validate_openapi_consistency(app: FastAPI) -> list[str]:
    """校验文档中的错误结构与实际 ErrorEnvelope 模型一致。"""
    problems: list[str] = []
    doc = app.openapi()
    expected = _expected_components()
    components = doc.get("components", {}).get("schemas", {})

    # 1) 文档中登记的错误组件必须与实际模型结构一致
    for name, want in expected.items():
        got = components.get(name)
        if got is None:
            problems.append(f"OpenAPI 文档缺少 {name} 组件，错误结构未对外声明")
        elif json.dumps(got, sort_keys=True) != json.dumps(want, sort_keys=True):
            problems.append(
                f"OpenAPI 文档中的 {name} 结构与实际错误模型不一致，文档已偏离真实响应"
            )

    # 2) 每个 4xx/5xx 响应都必须使用统一错误结构
    envelope = expected["ErrorEnvelope"]
    for path, method, status, schema in _iter_error_response_schemas(doc):
        resolved = _resolve_ref(doc, schema)
        if json.dumps(resolved, sort_keys=True) != json.dumps(envelope, sort_keys=True):
            problems.append(
                f"{method.upper()} {path} 的 {status} 响应未使用统一 ErrorEnvelope 结构"
            )
    return problems


def run_startup_validation(app: FastAPI) -> None:
    """启动校验入口：任一问题存在即失败并列出全部问题。"""
    problems = validate_error_mappings() + validate_openapi_consistency(app)
    if problems:
        joined = "\n".join(f"  - {p}" for p in problems)
        raise RuntimeError(f"启动校验失败，发现 {len(problems)} 个问题:\n{joined}")
