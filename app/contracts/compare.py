"""两个契约版本之间的字段级比较与兼容性判定。

判定规则（面向服务端响应/请求模型的消费方视角）：
- 新增「可选」字段：兼容；新增「必填」字段：破坏性；
- 删除字段：破坏性；
- 类型变化：破坏性；
- 可选 -> 必填：破坏性；必填 -> 可选：兼容。

比较会**递归进入嵌套对象与数组元素内部**，深层字段的新增/删除/
类型/必填变化不会被漏掉；数组元素内部的新增可选字段同样只算兼容。

错误响应声明（``paths.<path>.<method>.responses.<4xx/5xx>``）：
- 接口新增声明一个以前没写过的错误状态：兼容；
- 删除已声明的错误状态：破坏性（定位到接口+状态码）；
- 已声明错误的结构变化：按字段级规则判定（定位到接口+状态码+字段）。
"""

from __future__ import annotations

from typing import Any

from app.contracts.models import (
    ChangeType,
    CompatibilityReport,
    FieldChange,
    Severity,
)
from app.contracts.schema import FieldSchema, flatten_object_schema, resolve_schema

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}


def compare_schemas(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    location_prefix: str = "",
    old_components: dict[str, Any] | None = None,
    new_components: dict[str, Any] | None = None,
    resolve_refs: bool = False,
) -> CompatibilityReport:
    """比较两个 object JSON Schema，给出字段级变更与破坏性结论。"""
    if resolve_refs:
        # 根节点本身可能是 $ref（如错误响应声明），先解析再比较
        old, _ = resolve_schema(old, old_components or {}, True, frozenset())
        new, _ = resolve_schema(new, new_components or {}, True, frozenset())
    old_fields = flatten_object_schema(
        old, components=old_components, resolve_refs=resolve_refs
    )
    new_fields = flatten_object_schema(
        new, components=new_components, resolve_refs=resolve_refs
    )
    changes = _diff_children(old_fields, new_fields, location_prefix)
    return CompatibilityReport(
        compatible=not any(c.severity is Severity.BREAKING for c in changes),
        changes=changes,
    )


def _join(parent: str, name: str) -> str:
    return f"{parent}.{name}" if parent else name


def _diff_children(
    old_fields: dict[str, FieldSchema],
    new_fields: dict[str, FieldSchema],
    location: str,
) -> list[FieldChange]:
    """比较同一层级的字段集合（新增/删除/共有字段递归）。"""
    changes: list[FieldChange] = []

    for name in sorted(new_fields.keys() - old_fields.keys()):
        nf = new_fields[name]
        if nf.required:
            changes.append(
                _change(name, ChangeType.FIELD_ADDED, Severity.BREAKING,
                        None, nf, _join(location, name),
                        "新增必填字段，旧版本调用方无法满足")
            )
        else:
            changes.append(
                _change(name, ChangeType.FIELD_ADDED, Severity.COMPATIBLE,
                        None, nf, _join(location, name), "新增可选字段")
            )

    for name in sorted(old_fields.keys() - new_fields.keys()):
        of = old_fields[name]
        changes.append(
            _change(name, ChangeType.FIELD_REMOVED, Severity.BREAKING,
                    of, None, _join(location, name),
                    "字段被删除，旧消费方仍依赖该字段")
        )

    for name in sorted(old_fields.keys() & new_fields.keys()):
        changes.extend(
            _diff_field(old_fields[name], new_fields[name],
                        name, _join(location, name))
        )
    return changes


def _diff_field(
    old: FieldSchema, new: FieldSchema, name: str, location: str
) -> list[FieldChange]:
    """比较共有字段：类型/必填性，并递归嵌套对象与数组元素。"""
    out: list[FieldChange] = []
    if old.json_type != new.json_type or old.item_type != new.item_type:
        out.append(
            _change(name, ChangeType.TYPE_CHANGED, Severity.BREAKING, old, new,
                    location,
                    f"字段类型变化: {_type_desc(old)} -> {_type_desc(new)}")
        )
        return out  # 类型都变了，不再深入

    if not old.required and new.required:
        out.append(
            _change(name, ChangeType.BECAME_REQUIRED, Severity.BREAKING, old, new,
                    location, "字段由可选变为必填")
        )
    elif old.required and not new.required:
        out.append(
            _change(name, ChangeType.BECAME_OPTIONAL, Severity.COMPATIBLE, old, new,
                    location, "字段由必填变为可选")
        )

    if old.json_type == "object":
        out.extend(_diff_children(old.children, new.children, location))
    elif old.json_type == "array" and old.items is not None and new.items is not None:
        out.extend(_diff_field(old.items, new.items, "[]", f"{location}[]"))
    return out


def _type_desc(f: FieldSchema) -> str:
    return f"array<{f.item_type}>" if f.json_type == "array" else f.json_type


def _change(
    name: str,
    change_type: ChangeType,
    severity: Severity,
    old: FieldSchema | None,
    new: FieldSchema | None,
    location: str,
    detail: str,
) -> FieldChange:
    return FieldChange(
        location=location,
        field=name,
        change_type=change_type,
        severity=severity,
        old=old.model_dump(mode="json") if old else None,
        new=new.model_dump(mode="json") if new else None,
        detail=detail,
    )


def compare_contracts(
    old_contract: dict[str, Any], new_contract: dict[str, Any]
) -> CompatibilityReport:
    """比较两份完整 OpenAPI 文档。

    覆盖两块：
    1. ``components.schemas`` 下所有模型的字段级比较（$ref 作为叶子，
       命名模型各自比较，避免重复报告）；
    2. 每个接口每个错误状态码的响应声明比较（解析 $ref 递归到深层字段，
       变更定位到接口+状态码+字段）。
    """
    old_components = old_contract.get("components", {}) or {}
    new_components = new_contract.get("components", {}) or {}
    old_schemas = old_components.get("schemas", {}) or {}
    new_schemas = new_components.get("schemas", {}) or {}

    aggregated = CompatibilityReport(compatible=True, changes=[])
    for model in sorted(set(old_schemas) | set(new_schemas)):
        old_model = old_schemas.get(model, {"type": "object", "properties": {}})
        new_model = new_schemas.get(model, {"type": "object", "properties": {}})
        report = compare_schemas(
            old_model, new_model,
            location_prefix=f"components.schemas.{model}.properties",
        )
        aggregated.changes.extend(report.changes)

    aggregated.changes.extend(
        _compare_error_declarations(old_contract, new_contract)
    )
    aggregated.compatible = not aggregated.breaking_changes
    return aggregated


def _compare_error_declarations(
    old_contract: dict[str, Any], new_contract: dict[str, Any]
) -> list[FieldChange]:
    """逐接口、逐状态码比较错误响应声明（新增兼容/删除破坏/结构按字段规则）。"""
    old_paths = old_contract.get("paths", {}) or {}
    new_paths = new_contract.get("paths", {}) or {}
    old_components = old_contract.get("components", {}) or {}
    new_components = new_contract.get("components", {}) or {}

    changes: list[FieldChange] = []
    for path in sorted(set(old_paths) | set(new_paths)):
        old_item = old_paths.get(path) or {}
        new_item = new_paths.get(path) or {}
        methods = {
            m
            for m in set(old_item) | set(new_item)
            if m in _HTTP_METHODS
        }
        for method in sorted(methods):
            old_errors = _error_responses_of(old_item.get(method) or {})
            new_errors = _error_responses_of(new_item.get(method) or {})
            base = f"paths.{path}.{method}.responses"
            label = f"{method.upper()} {path}"

            for status in sorted(set(new_errors) - set(old_errors)):
                changes.append(FieldChange(
                    location=f"{base}.{status}",
                    field=status,
                    change_type=ChangeType.ERROR_STATUS_ADDED,
                    severity=Severity.COMPATIBLE,
                    detail=f"{label} 新增声明错误状态 {status}（兼容）",
                ))
            for status in sorted(set(old_errors) - set(new_errors)):
                changes.append(FieldChange(
                    location=f"{base}.{status}",
                    field=status,
                    change_type=ChangeType.ERROR_STATUS_REMOVED,
                    severity=Severity.BREAKING,
                    detail=f"{label} 删除了已声明的错误状态 {status}，"
                           "调用方按旧文档处理的错误结构失去保障",
                ))
            for status in sorted(set(old_errors) & set(new_errors)):
                report = compare_schemas(
                    old_errors[status], new_errors[status],
                    location_prefix=f"{base}.{status}",
                    old_components=old_components,
                    new_components=new_components,
                    resolve_refs=True,
                )
                changes.extend(report.changes)
    return changes


def _error_responses_of(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """提取单个接口声明的错误状态码 -> 响应体 schema。"""
    out: dict[str, dict[str, Any]] = {}
    for status, resp in (operation.get("responses", {}) or {}).items():
        if not (str(status).startswith(("4", "5")) or status == "default"):
            continue
        schema = (
            (resp or {}).get("content", {})
            .get("application/json", {})
            .get("schema")
        )
        if isinstance(schema, dict):
            out[str(status)] = schema
    return out
