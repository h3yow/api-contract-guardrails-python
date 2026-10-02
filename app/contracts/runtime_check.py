"""逐接口、逐状态码的错误结构运行时核对。

与 validate_documented_error_schema（只比模型定义）不同，这里**真实打接口**：
对每个接口有代表性的失败请求（探针），把真实返回的错误结构推断出来，
与文档中该接口该状态码声明的结构逐字段比对（含嵌套对象与数组内部）。

判定为失败的偏离（双向）：
- 字段多一个 / 少一个；
- 字段类型不同；
- 必填性不同（文档必填但真实未返回，或文档可选但真实始终返回）；
- 以上发生在嵌套对象或数组元素内部，同样算。

覆盖率（不允许静默通过）：
- 任何接口没有任何探针 -> 失败；
- 文档声明了某错误状态但没有探针真实核对 -> 失败；
- 探针真实返回了某错误状态但文档未声明 -> 失败。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.contracts.probes import ErrorProbe, all_probes
from app.contracts.validation import ValidationFinding, ValidationResult
from app.errors.context import REQUEST_ID_HEADER

logger = logging.getLogger("app.contracts")

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}


def verify_error_responses(
    app: FastAPI,
    openapi_schema: dict[str, Any],
    *,
    probes: list[ErrorProbe] | None = None,
) -> ValidationResult:
    """真实打接口，逐接口逐状态码核对错误结构与文档声明一致。"""
    probes = all_probes() if probes is None else list(probes)
    findings: list[ValidationFinding] = []
    paths = openapi_schema.get("paths", {}) or {}

    documented = _documented_error_schemas(paths)
    components = openapi_schema.get("components", {}) or {}
    probed: dict[tuple[str, str], set[int]] = {}
    for p in probes:
        probed.setdefault((p.method, p.path), set()).add(p.status)

    findings.extend(_coverage_findings(paths, documented, probed))

    if probes:
        client = TestClient(app, raise_server_exceptions=False)
        for probe in probes:
            findings.extend(_run_probe(client, probe, documented, components))

    return ValidationResult(ok=not findings, findings=findings)


# ---------------------------------------------------------------- 覆盖率

def _documented_error_schemas(
    paths: dict[str, Any],
) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    """提取文档中每个接口声明的错误状态码 -> 响应体 schema。"""
    out: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            for status, resp in (operation.get("responses", {}) or {}).items():
                if not (str(status).startswith(("4", "5")) or status == "default"):
                    continue
                schema = (
                    (resp or {}).get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                if isinstance(schema, dict):
                    out.setdefault((method, path), {})[str(status)] = schema
    return out


def _coverage_findings(
    paths: dict[str, Any],
    documented: dict[tuple[str, str], dict[str, dict[str, Any]]],
    probed: dict[tuple[str, str], set[int]],
) -> list[ValidationFinding]:
    """接口/状态码"没被核对到"时不允许给出通过结论。"""
    findings: list[ValidationFinding] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method in (m for m in item if m in _HTTP_METHODS):
            key = (method, path)
            loc = f"{method.upper()} {path}"
            if key not in probed:
                findings.append(ValidationFinding(
                    location=loc,
                    message="接口没有任何错误探针覆盖，不允许给出通过结论",
                ))
                continue
            for status in sorted(documented.get(key, {})):
                if not status.isdigit() or int(status) not in probed[key]:
                    findings.append(ValidationFinding(
                        location=f"{loc} [{status}]",
                        message="文档声明了该错误状态但没有探针真实核对",
                    ))
            for status in sorted(probed[key]):
                if str(status) not in documented.get(key, {}):
                    findings.append(ValidationFinding(
                        location=f"{loc} [{status}]",
                        message="探针真实返回的错误状态未在文档中声明",
                    ))
    return findings


# ---------------------------------------------------------------- 探针执行

def _run_probe(
    client: TestClient,
    probe: ErrorProbe,
    documented: dict[tuple[str, str], dict[str, dict[str, Any]]],
    components: dict[str, Any],
) -> list[ValidationFinding]:
    """执行单个探针并比对真实错误结构与文档声明。"""
    where = f"{probe.method.upper()} {probe.path} [{probe.status}]"
    resp = probe.request(client)
    request_id = resp.headers.get(REQUEST_ID_HEADER, "")

    findings: list[ValidationFinding] = []
    if resp.status_code != probe.status:
        findings.append(ValidationFinding(
            location=where,
            message=f"探针 {probe.name} 期望状态 {probe.status}，"
                    f"实际返回 {resp.status_code}",
        ))
        _log_probe(probe, request_id, ok=False, diffs=1)
        return findings

    try:
        body = resp.json()
    except ValueError:
        findings.append(ValidationFinding(
            location=where, message=f"探针 {probe.name} 的错误响应不是 JSON"
        ))
        _log_probe(probe, request_id, ok=False, diffs=1)
        return findings

    doc_schema = documented.get((probe.method, probe.path), {}).get(str(probe.status))
    if doc_schema is None:
        # 覆盖率检查已报告"未声明"，这里不重复
        _log_probe(probe, request_id, ok=False, diffs=1)
        return findings

    actual_schema = infer_schema(body)
    diffs = diff_exact(doc_schema, actual_schema, "", components=components)
    for field_path, message in diffs:
        loc = f"{where} -> {field_path}" if field_path else where
        findings.append(ValidationFinding(location=loc, message=message))
    _log_probe(probe, request_id, ok=not diffs, diffs=len(diffs))
    return findings


def _log_probe(probe: ErrorProbe, request_id: str, *, ok: bool, diffs: int) -> None:
    logger.info(
        "error_probe probe=%s %s %s [%d] request_id=%s ok=%s diffs=%d",
        probe.name, probe.method.upper(), probe.path, probe.status,
        request_id or "-", ok, diffs,
    )


# ---------------------------------------------------------------- 结构推断

def infer_schema(value: Any) -> dict[str, Any]:
    """从真实 JSON 值推断结构（object 的 required = 本次观测到的全部键）。"""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        schema: dict[str, Any] = {"type": "array"}
        if value:
            items = infer_schema(value[0])
            for element in value[1:]:
                items = _merge_schemas(items, infer_schema(element))
            schema["items"] = items
        return schema
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {k: infer_schema(v) for k, v in value.items()},
            "required": sorted(value.keys()),
        }
    return {"type": "string"}  # pragma: no cover - 防御性


def _merge_schemas(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """合并同一数组多个元素的推断结构（required 取交集，属性取并集）。"""
    if a.get("type") != b.get("type"):
        return a
    if a.get("type") == "object":
        props: dict[str, Any] = {}
        for name in a.get("properties", {}) | b.get("properties", {}):
            in_a = a.get("properties", {}).get(name)
            in_b = b.get("properties", {}).get(name)
            if in_a is not None and in_b is not None:
                props[name] = _merge_schemas(in_a, in_b)
            else:
                props[name] = in_a if in_a is not None else in_b
        required = sorted(
            set(a.get("required", [])) & set(b.get("required", []))
        )
        return {"type": "object", "properties": props, "required": required}
    if a.get("type") == "array" and "items" in a and "items" in b:
        return {"type": "array", "items": _merge_schemas(a["items"], b["items"])}
    return a


# ---------------------------------------------------------------- 精确比对

def diff_exact(
    documented: dict[str, Any],
    actual: dict[str, Any],
    location: str,
    *,
    components: dict[str, Any] | None = None,
    _seen_refs: frozenset[str] = frozenset(),
) -> list[tuple[str, str]]:
    """逐字段比对文档声明与真实结构，返回 ``(字段路径, 问题描述)`` 列表。

    任何偏离（双向）都算失败；嵌套对象与数组元素内部同样递归。
    """
    documented = _resolve_ref(documented, components or {}, _seen_refs)
    out: list[tuple[str, str]] = []

    doc_type = str(documented.get("type", "object" if "properties" in documented else ""))
    actual_type = str(actual.get("type", ""))
    if doc_type != actual_type:
        out.append((location, f"字段类型不一致: 文档={doc_type} 实际={actual_type}"))
        return out

    if doc_type == "object":
        doc_props = documented.get("properties", {}) or {}
        actual_props = actual.get("properties", {}) or {}
        doc_required = set(documented.get("required", []) or [])
        actual_required = set(actual.get("required", []) or [])

        for name in sorted(doc_props.keys() - actual_props.keys()):
            out.append((_child(location, name),
                        "文档声明了该字段但真实响应中不存在"))
        for name in sorted(actual_props.keys() - doc_props.keys()):
            out.append((_child(location, name),
                        "真实响应包含文档未声明的字段"))
        for name in sorted(doc_props.keys() & actual_props.keys()):
            doc_req = name in doc_required
            actual_req = name in actual_required
            child = _child(location, name)
            if doc_req != actual_req:
                out.append((child, "必填性不一致: 文档=%s 实际=%s" % (
                    "必填" if doc_req else "可选",
                    "必填" if actual_req else "可选",
                )))
            out.extend(diff_exact(
                doc_props[name], actual_props[name], child,
                components=components, _seen_refs=_seen_refs,
            ))
    elif doc_type == "array":
        doc_items = documented.get("items")
        actual_items = actual.get("items")
        item_loc = f"{location}[]"
        if doc_items is not None and actual_items is None:
            out.append((item_loc, "真实响应数组为空，无法核对文档声明的元素结构"))
        elif doc_items is None and actual_items is not None:
            out.append((item_loc, "真实响应包含数组元素但文档未声明元素结构"))
        elif doc_items is not None and actual_items is not None:
            out.extend(diff_exact(
                doc_items, actual_items, item_loc,
                components=components, _seen_refs=_seen_refs,
            ))
    return out


def _resolve_ref(
    schema: dict[str, Any],
    components: dict[str, Any],
    seen: frozenset[str],
) -> dict[str, Any]:
    """解析文档 schema 的 $ref（带循环保护）。"""
    ref = schema.get("$ref")
    if not ref:
        return schema
    name = str(ref).rsplit("/", 1)[-1]
    if name in seen:
        return schema  # pragma: no cover - 循环引用防御
    target = (components.get("schemas", {}) or {}).get(name)
    if not isinstance(target, dict):
        return schema
    return _resolve_ref(target, components, seen | {name})


def _child(location: str, name: str) -> str:
    return f"{location}.{name}" if location else name
