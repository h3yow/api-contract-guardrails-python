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


_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def extract_documented_error_responses(
    openapi_schema: dict[str, Any],
) -> dict[tuple[str, str], dict[int, dict[str, Any]]]:
    """提取文档中每个 (接口路径, 方法) 声明的错误状态码及其响应 schema。

    只统计 >= 400 且带 JSON schema 的响应；"default" 等非常规键不参与。
    返回 ``{(path, method): {status: schema}}``。
    """
    out: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}
    paths = openapi_schema.get("paths", {}) or {}
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            for raw_status, response in (operation.get("responses", {}) or {}).items():
                try:
                    status = int(raw_status)
                except (TypeError, ValueError):
                    continue
                if status < 400 or not isinstance(response, dict):
                    continue
                schema = _response_schema(response)
                if schema is not None:
                    out.setdefault((path, method.lower()), {})[status] = schema
    return out


def _response_schema(response: dict[str, Any]) -> dict[str, Any] | None:
    content = response.get("content", {}) or {}
    if isinstance(content.get("application/json"), dict):
        schema = content["application/json"].get("schema")
        if isinstance(schema, dict):
            return schema
    for media_obj in content.values():
        if isinstance(media_obj, dict) and isinstance(media_obj.get("schema"), dict):
            return media_obj["schema"]
    return None
