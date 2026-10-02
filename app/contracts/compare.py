"""两个契约版本之间的字段级比较与兼容性判定。

判定规则（面向服务端响应/请求模型的消费方视角）：
- 新增「可选」字段：兼容；新增「必填」字段：破坏性；
- 删除字段：破坏性；
- 类型变化：破坏性；
- 可选 -> 必填：破坏性；必填 -> 可选：兼容。

错误响应声明（逐接口、逐状态码）：
- 接口新增声明一个以前没写过的错误状态：兼容；
- 删除已声明的错误状态：破坏性；
- 已声明错误的结构变化按字段级规则判定；
- 比较会递归进入嵌套对象与对象数组（a.b / items[].x 定位）。
"""

from __future__ import annotations

from typing import Any

from app.contracts.extractor import extract_documented_error_responses
from app.contracts.models import (
    ChangeType,
    CompatibilityReport,
    FieldChange,
    Severity,
)
from app.contracts.schema import FieldSchema, flatten_object_schema


def compare_schemas(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    location_prefix: str = "",
    old_components: dict[str, Any] | None = None,
    new_components: dict[str, Any] | None = None,
) -> CompatibilityReport:
    """比较两个 object JSON Schema，给出字段级变更与破坏性结论。

    嵌套对象与对象数组会被递归展平比较；提供 components 时可解析 $ref。
    """
    old_fields = flatten_object_schema(old, old_components)
    new_fields = flatten_object_schema(new, new_components)
    changes: list[FieldChange] = []

    for name in sorted(new_fields.keys() - old_fields.keys()):
        nf = new_fields[name]
        if nf.required:
            changes.append(
                _change(name, ChangeType.FIELD_ADDED, Severity.BREAKING,
                        None, nf, location_prefix,
                        "新增必填字段，旧版本调用方无法满足")
            )
        else:
            changes.append(
                _change(name, ChangeType.FIELD_ADDED, Severity.COMPATIBLE,
                        None, nf, location_prefix, "新增可选字段")
            )

    for name in sorted(old_fields.keys() - new_fields.keys()):
        of = old_fields[name]
        changes.append(
            _change(name, ChangeType.FIELD_REMOVED, Severity.BREAKING,
                    of, None, location_prefix, "字段被删除，旧消费方仍依赖该字段")
        )

    for name in sorted(old_fields.keys() & new_fields.keys()):
        changes.extend(_compare_field(name, old_fields[name], new_fields[name],
                                      location_prefix))

    return CompatibilityReport(
        compatible=not any(c.severity is Severity.BREAKING for c in changes),
        changes=changes,
    )


def _compare_field(
    name: str, old: FieldSchema, new: FieldSchema, prefix: str
) -> list[FieldChange]:
    out: list[FieldChange] = []
    if old.json_type != new.json_type or old.item_type != new.item_type:
        out.append(
            _change(name, ChangeType.TYPE_CHANGED, Severity.BREAKING, old, new,
                    prefix,
                    f"字段类型变化: {_type_desc(old)} -> {_type_desc(new)}")
        )
    elif not old.required and new.required:
        out.append(
            _change(name, ChangeType.BECAME_REQUIRED, Severity.BREAKING, old, new,
                    prefix, "字段由可选变为必填")
        )
    elif old.required and not new.required:
        out.append(
            _change(name, ChangeType.BECAME_OPTIONAL, Severity.COMPATIBLE, old, new,
                    prefix, "字段由必填变为可选")
        )
    return out


def _type_desc(f: FieldSchema) -> str:
    return f"array<{f.item_type}>" if f.json_type == "array" else f.json_type


def _change(
    name: str,
    change_type: ChangeType,
    severity: Severity,
    old: FieldSchema | None,
    new: FieldSchema | None,
    prefix: str,
    detail: str,
) -> FieldChange:
    location = f"{prefix}.{name}".lstrip(".")
    return FieldChange(
        location=location,
        field=name,
        change_type=change_type,
        severity=severity,
        old=old.model_dump(mode="json") if old else None,
        new=new.model_dump(mode="json") if new else None,
        detail=detail,
    )


def compare_error_declarations(
    old_contract: dict[str, Any], new_contract: dict[str, Any]
) -> CompatibilityReport:
    """比较两版契约中逐接口、逐状态码的错误响应声明。

    - 新增声明错误状态：兼容；
    - 删除已声明错误状态：破坏性；
    - 共有状态的结构差异按字段级规则递归比较（含嵌套对象/数组）。
    """
    old_doc = extract_documented_error_responses(old_contract)
    new_doc = extract_documented_error_responses(new_contract)
    old_components = old_contract.get("components", {}) or {}
    new_components = new_contract.get("components", {}) or {}

    changes: list[FieldChange] = []
    for key in sorted(set(old_doc) | set(new_doc)):
        path, method = key
        old_statuses = old_doc.get(key, {})
        new_statuses = new_doc.get(key, {})
        base = f"paths.{path}.{method}.responses"

        for status in sorted(set(new_statuses) - set(old_statuses)):
            changes.append(
                FieldChange(
                    location=f"{base}.{status}",
                    field=str(status),
                    change_type=ChangeType.ERROR_STATUS_ADDED,
                    severity=Severity.COMPATIBLE,
                    detail="接口新增声明错误状态码",
                )
            )
        for status in sorted(set(old_statuses) - set(new_statuses)):
            changes.append(
                FieldChange(
                    location=f"{base}.{status}",
                    field=str(status),
                    change_type=ChangeType.ERROR_STATUS_REMOVED,
                    severity=Severity.BREAKING,
                    detail="已声明的错误状态被删除，调用方无法再据此处理该错误",
                )
            )
        for status in sorted(set(old_statuses) & set(new_statuses)):
            report = compare_schemas(
                old_statuses[status],
                new_statuses[status],
                location_prefix=f"{base}.{status}",
                old_components=old_components,
                new_components=new_components,
            )
            changes.extend(report.changes)

    return CompatibilityReport(
        compatible=not any(c.severity is Severity.BREAKING for c in changes),
        changes=changes,
    )


def compare_contracts(
    old_contract: dict[str, Any], new_contract: dict[str, Any]
) -> CompatibilityReport:
    """比较两份完整 OpenAPI 文档（组件模型 + 逐接口错误声明）。"""
    old_schemas = (old_contract.get("components", {}) or {}).get("schemas", {}) or {}
    new_schemas = (new_contract.get("components", {}) or {}).get("schemas", {}) or {}

    aggregated = CompatibilityReport(compatible=True, changes=[])
    for model in sorted(set(old_schemas) | set(new_schemas)):
        old_model = old_schemas.get(model, {"type": "object", "properties": {}})
        new_model = new_schemas.get(model, {"type": "object", "properties": {}})
        report = compare_schemas(
            old_model, new_model,
            location_prefix=f"components.schemas.{model}.properties",
            old_components=old_contract.get("components", {}) or {},
            new_components=new_contract.get("components", {}) or {},
        )
        aggregated.changes.extend(report.changes)

    aggregated.changes.extend(
        compare_error_declarations(old_contract, new_contract).changes
    )
    aggregated.compatible = not aggregated.breaking_changes
    return aggregated
