"""接口契约的数据模型与兼容性结论。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ChangeType(str, Enum):
    """单个字段的变更类型（彼此可区分）。"""

    FIELD_ADDED = "field_added"            # 新增字段
    FIELD_REMOVED = "field_removed"        # 删除字段（破坏性）
    TYPE_CHANGED = "type_changed"          # 类型变化（破坏性）
    BECAME_REQUIRED = "became_required"    # 可选 -> 必填（破坏性）
    BECAME_OPTIONAL = "became_optional"    # 必填 -> 可选（兼容）
    NONE = "none"                          # 无变化


class Severity(str, Enum):
    """变更严重度。"""

    COMPATIBLE = "compatible"
    BREAKING = "breaking"


class FieldChange(BaseModel):
    """定位到具体字段的一条变更。"""

    location: str                 # 如 "components.schemas.Item.properties.id"
    field: str                    # 字段名
    change_type: ChangeType
    severity: Severity
    old: object | None = None
    new: object | None = None
    detail: str = ""


class CompatibilityReport(BaseModel):
    """一次契约版本比较的完整结论。"""

    compatible: bool
    changes: list[FieldChange] = Field(default_factory=list)

    @property
    def breaking_changes(self) -> list[FieldChange]:
        return [c for c in self.changes if c.severity == Severity.BREAKING]
