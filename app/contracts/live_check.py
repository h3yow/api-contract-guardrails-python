"""基于真实响应的逐接口、逐状态码错误结构核对。

不只比对模型定义是否自洽，而是对每个接口有代表性的失败请求
真实发请求、拿到错误响应，再与文档声明的结构逐字段核对
（含嵌套对象与数组）。任何未覆盖的接口/状态码都不得放行：

- 文档声明了某个错误状态但没有任何探针核对它 -> 失败；
- 探针真实返回了文档未声明的错误状态 -> 失败（实现改了没同步文档）；
- 接口完全没有错误声明且未显式豁免 -> 失败；
- 真实响应结构与文档声明有任一字段级偏差 -> 失败并定位到字段。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.contracts.extractor import extract_documented_error_responses
from app.contracts.probes import ErrorProbe
from app.contracts.validation import ValidationFinding, ValidationResult

logger = logging.getLogger("app.contracts")


def validate_live_error_structures(
    app: FastAPI,
    openapi_schema: dict[str, Any],
    probes: tuple[ErrorProbe, ...] | list[ErrorProbe],
    *,
    no_error_surface: frozenset[tuple[str, str]] | None = None,
) -> ValidationResult:
    """逐接口核对真实错误响应与文档声明是否一致。"""
    exempt = no_error_surface or frozenset()
    findings: list[ValidationFinding] = []
    documented = extract_documented_error_responses(openapi_schema)
    components = (openapi_schema.get("components", {}) or {})

    # 1. 每个接口都必须有错误声明覆盖或被显式豁免
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            key = (route.path, method.lower())
            if key in exempt:
                continue
            if not documented.get(key):
                findings.append(
                    ValidationFinding(
                        location=f"{method.upper()} {route.path}",
                        message="接口没有任何错误状态声明，也未被探针核对覆盖",
                    )
                )

    # 2. 文档声明的每个 (接口, 方法, 状态码) 都必须有探针
    probe_keys = {p.key() for p in probes}
    for (path, method), statuses in sorted(documented.items()):
        for status in sorted(statuses):
            if (path, method, status) not in probe_keys:
                findings.append(
                    ValidationFinding(
                        location=f"{method.upper()} {path} {status}",
                        message="文档声明了该错误状态，但缺少代表性失败请求，未核对",
                    )
                )

    # 3. 逐条探针真实发请求并核对结构
    client = TestClient(app, raise_server_exceptions=False)
    for probe in probes:
        findings.extend(_run_probe(client, probe, documented, components))

    return ValidationResult(ok=not findings, findings=findings)


def _run_probe(
    client: TestClient,
    probe: ErrorProbe,
    documented: dict[tuple[str, str], dict[int, dict[str, Any]]],
    components: dict[str, Any],
) -> list[ValidationFinding]:
    """执行一条探针：发真实请求，核对状态码与错误结构。"""
    method = (probe.request_method or probe.method).lower()
    path = probe.request_path or probe.operation
    where = f"{probe.method.upper()} {probe.operation} {probe.expected_status}"

    kwargs: dict[str, Any] = {}
    if probe.json_body is not None:
        kwargs["json"] = probe.json_body
    resp = client.request(method.upper(), path, **kwargs)

    request_id = resp.headers.get("x-request-id", "?")
    if resp.status_code != probe.expected_status:
        logger.warning(
            "探针状态码不符 request_id=%s %s 预期=%s 实际=%s",
            request_id, where, probe.expected_status, resp.status_code,
        )
        return [
            ValidationFinding(
                location=where,
                message=f"真实状态码与预期不符: 预期 {probe.expected_status} "
                f"实际 {resp.status_code}（request_id={request_id}）",
            )
        ]

    doc_schema = documented.get((probe.operation, probe.method.lower()), {}).get(
        probe.expected_status
    )
    if doc_schema is None:
        logger.warning(
            "探针返回了文档未声明的错误状态 request_id=%s %s", request_id, where,
        )
        return [
            ValidationFinding(
                location=where,
                message="真实返回了文档未声明的错误状态"
                "（实现与文档不同步，request_id=%s）" % request_id,
            )
        ]

    try:
        body = resp.json()
    except ValueError:
        return [
            ValidationFinding(
                location=where,
                message=f"错误响应不是合法 JSON（request_id={request_id}）",
            )
        ]

    diffs = _diff_instance(doc_schema, body, where, components)
    if diffs:
        logger.warning(
            "错误结构核对失败 request_id=%s %s 偏差=%d",
            request_id, where, len(diffs),
        )
    else:
        logger.info(
            "错误结构核对通过 request_id=%s %s", request_id, where,
        )
    return diffs


def _resolve(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """解析本地 $ref（#/components/schemas/...）。"""
    while isinstance(schema, dict) and "$ref" in schema:
        ref = str(schema["$ref"])
        name = ref.rsplit("/", 1)[-1]
        schema = (components.get("schemas", {}) or {}).get(name, {})
    return schema if isinstance(schema, dict) else {}


def _doc_variants(
    schema: dict[str, Any], components: dict[str, Any]
) -> list[dict[str, Any]]:
    """展开 anyOf/oneOf 变体（兼容可空写法）。"""
    schema = _resolve(schema, components)
    variants = schema.get("anyOf") or schema.get("oneOf")
    if not variants:
        return [schema]
    return [_resolve(v, components) for v in variants]


def _json_type_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _type_matches(doc_type: str, actual_type: str) -> bool:
    if doc_type == actual_type:
        return True
    # JSON 里整数是 number 的合法实例
    return doc_type == "number" and actual_type == "integer"


def _diff_instance(
    doc_schema: dict[str, Any],
    instance: Any,
    location: str,
    components: dict[str, Any],
) -> list[ValidationFinding]:
    """递归核对真实响应实例与文档声明结构（含嵌套对象/数组）。"""
    variants = _doc_variants(doc_schema, components)
    actual_type = _json_type_of(instance)

    # 可空：实例为 null 且任一变体允许 null 即通过
    if actual_type == "null":
        if any(v.get("type") == "null" for v in variants):
            return []
        return [
            ValidationFinding(
                location=location,
                message=f"真实返回 null，但文档声明类型为 "
                f"{[v.get('type', 'object') for v in variants]}",
            )
        ]

    # 选取与实例类型匹配的变体；没有则报类型不一致
    matched = [v for v in variants
               if _type_matches(str(v.get("type", "object")), actual_type)]
    if not matched:
        doc_types = [str(v.get("type", "object")) for v in variants]
        return [
            ValidationFinding(
                location=location,
                message=f"字段类型不一致: 文档={doc_types} 实际={actual_type}",
            )
        ]

    variant = matched[0]
    findings: list[ValidationFinding] = []

    if actual_type == "object" and isinstance(instance, dict):
        props = variant.get("properties", {}) or {}
        required = set(variant.get("required", []) or [])
        for name in sorted(required - set(instance)):
            findings.append(
                ValidationFinding(
                    location=f"{location}.{name}",
                    message="文档声明为必填字段，但真实响应缺失",
                )
            )
        for name, value in instance.items():
            child_loc = f"{location}.{name}"
            if name not in props:
                findings.append(
                    ValidationFinding(
                        location=child_loc,
                        message="真实响应包含文档未声明的字段",
                    )
                )
                continue
            findings.extend(_diff_instance(props[name], value, child_loc, components))
    elif actual_type == "array" and isinstance(instance, list):
        item_schema = variant.get("items", {})
        for index, element in enumerate(instance):
            findings.extend(
                _diff_instance(
                    item_schema, element, f"{location}[{index}]", components
                )
            )
    return findings
