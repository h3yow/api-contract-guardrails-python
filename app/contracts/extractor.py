"""从运行中的 FastAPI 应用提取 OpenAPI 契约（事实来源）。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def extract_openapi(app: FastAPI) -> dict[str, Any]:
    """返回应用当前的 OpenAPI 文档（含组件 schema 与响应声明）。"""
    schema = app.openapi()
    if not isinstance(schema, dict):  # pragma: no cover - 防御性
        raise RuntimeError("FastAPI.openapi() 未返回 dict")
    return schema


def extract_error_response_components(
    openapi_schema: dict[str, Any],
) -> dict[str, Any]:
    """提取文档中声明的统一错误响应模型 ``components.schemas.ErrorResponse``。

    若文档里根本没有该组件，返回空 dict（由校验流程判定为缺失）。
    """
    schemas = (openapi_schema.get("components", {}) or {}).get("schemas", {}) or {}
    return dict(schemas.get("ErrorResponse", {}))
