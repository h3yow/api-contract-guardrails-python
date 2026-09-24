"""接口契约版本比较。

对两个 JSON Schema（通常取自不同版本的 OpenAPI 文档中同一接口的
请求/响应模型）做字段级比较，区分四类变化并给出兼容性结论：

==================  ====================  =========================
变化                判定                  说明
==================  ====================  =========================
字段删除            破坏性                旧客户端依赖的字段消失
字段新增-必填       破坏性                旧客户端不会发送该字段
字段新增-可选       兼容性                旧客户端不受影响
类型变化            破坏性                序列化/反序列化结果改变
必填->可选          兼容性                约束放宽
可选->必填          破坏性                约束收紧
==================  ====================  =========================

任何一条破坏性变化都会使整体校验失败，并定位到具体字段（点分路径）。
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ChangeKind(StrEnum):
    FIELD_ADDED = "field_added"
    FIELD_REMOVED = "field_removed"
    TYPE_CHANGED = "type_changed"
    REQUIRED_CHANGED = "required_changed"


class ChangeVerdict(StrEnum):
    BREAKING = "breaking"
    COMPATIBLE = "compatible"


class FieldChange(BaseModel):
    kind: ChangeKind
    field: str
    verdict: ChangeVerdict
    detail: str


class DiffReport(BaseModel):
    changes: list[FieldChange]

    @property
    def is_breaking(self) -> bool:
        return any(c.verdict == ChangeVerdict.BREAKING for c in self.changes)

    @property
    def breaking_changes(self) -> list[FieldChange]:
        return [c for c in self.changes if c.verdict == ChangeVerdict.BREAKING]


def _type_of(schema: dict[str, Any]) -> str:
    """提取字段类型的可比较表示。"""
    if "$ref" in schema:
        return f"ref:{schema['$ref']}"
    t = schema.get("type", "any")
    if t == "array":
        return f"array<{_type_of(schema.get('items', {}))}>"
    return str(t)


def _diff_object(
    old: dict[str, Any], new: dict[str, Any], prefix: str, out: list[FieldChange]
) -> None:
    old_props = old.get("properties", {})
    new_props = new.get("properties", {})
    old_required = set(old.get("required", []))
    new_required = set(new.get("required", []))

    for name in sorted(old_props.keys() - new_props.keys()):
        out.append(
            FieldChange(
                kind=ChangeKind.FIELD_REMOVED,
                field=f"{prefix}{name}",
                verdict=ChangeVerdict.BREAKING,
                detail="字段被删除",
            )
        )

    for name in sorted(new_props.keys() - old_props.keys()):
        required = name in new_required
        out.append(
            FieldChange(
                kind=ChangeKind.FIELD_ADDED,
                field=f"{prefix}{name}",
                verdict=ChangeVerdict.BREAKING if required else ChangeVerdict.COMPATIBLE,
                detail="新增必填字段" if required else "新增可选字段",
            )
        )

    for name in sorted(old_props.keys() & new_props.keys()):
        path = f"{prefix}{name}"
        old_t = _type_of(old_props[name])
        new_t = _type_of(new_props[name])
        if old_t != new_t:
            out.append(
                FieldChange(
                    kind=ChangeKind.TYPE_CHANGED,
                    field=path,
                    verdict=ChangeVerdict.BREAKING,
                    detail=f"类型由 {old_t} 变为 {new_t}",
                )
            )
        was_required = name in old_required
        is_required = name in new_required
        if was_required != is_required:
            breaking = is_required  # 收紧为必填是破坏性的；放宽是兼容的
            out.append(
                FieldChange(
                    kind=ChangeKind.REQUIRED_CHANGED,
                    field=path,
                    verdict=ChangeVerdict.BREAKING if breaking else ChangeVerdict.COMPATIBLE,
                    detail="由可选变为必填" if breaking else "由必填变为可选",
                )
            )
        # 递归比较嵌套对象
        if old_props[name].get("type") == "object" and new_props[name].get("type") == "object":
            _diff_object(old_props[name], new_props[name], f"{path}.", out)


def diff_schemas(old: dict[str, Any], new: dict[str, Any]) -> DiffReport:
    """比较两个 JSON Schema，返回字段级差异报告。"""
    changes: list[FieldChange] = []
    _diff_object(old, new, "", changes)
    return DiffReport(changes=changes)


def assert_compatible(old: dict[str, Any], new: dict[str, Any]) -> DiffReport:
    """校验新版本对旧版本向后兼容；存在破坏性变化时抛出并定位字段。"""
    report = diff_schemas(old, new)
    if report.is_breaking:
        located = "; ".join(
            f"{c.field}({c.kind}: {c.detail})" for c in report.breaking_changes
        )
        raise ContractBreakingError(f"发现破坏性契约变更: {located}", report)
    return report


class ContractBreakingError(Exception):
    """契约存在破坏性变更。"""

    def __init__(self, message: str, report: DiffReport) -> None:
        self.report = report
        super().__init__(message)
