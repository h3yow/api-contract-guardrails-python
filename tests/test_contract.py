"""契约版本比较：四类变化的区分与破坏性定位。"""
from __future__ import annotations

import pytest

from app.contract import (
    ChangeKind,
    ChangeVerdict,
    ContractBreakingError,
    assert_compatible,
    diff_schemas,
)

V1 = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "nickname": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "name"],
}


def _change_map(report) -> dict[str, tuple[str, str, str]]:
    return {c.field: (c.kind, c.verdict, c.detail) for c in report.changes}


def test_field_removed_is_breaking_and_located() -> None:
    new = {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    report = diff_schemas(V1, new)
    assert report.is_breaking
    removed = report.breaking_changes[0]
    assert removed.kind == ChangeKind.FIELD_REMOVED
    assert removed.field == "nickname"
    with pytest.raises(ContractBreakingError, match="nickname"):
        assert_compatible(V1, new)


def test_optional_field_added_is_compatible() -> None:
    new = dict(V1, properties={**V1["properties"], "remark": {"type": "string"}})
    report = diff_schemas(V1, new)
    assert not report.is_breaking
    change = _change_map(report)["remark"]
    assert change[0] == ChangeKind.FIELD_ADDED
    assert change[1] == ChangeVerdict.COMPATIBLE


def test_required_field_added_is_breaking() -> None:
    new = dict(
        V1,
        properties={**V1["properties"], "remark": {"type": "string"}},
        required=["id", "name", "remark"],
    )
    change = _change_map(diff_schemas(V1, new))["remark"]
    assert change[0] == ChangeKind.FIELD_ADDED
    assert change[1] == ChangeVerdict.BREAKING


def test_type_change_is_breaking_and_located() -> None:
    new = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},  # integer -> string
            "name": {"type": "string"},
        },
        "required": ["id", "name"],
    }
    report = diff_schemas(V1, new)
    change = _change_map(report)["id"]
    assert change[0] == ChangeKind.TYPE_CHANGED
    assert change[1] == ChangeVerdict.BREAKING
    with pytest.raises(ContractBreakingError) as exc_info:
        assert_compatible(V1, new)
    assert "id" in str(exc_info.value)


def test_required_to_optional_is_compatible() -> None:
    new = dict(V1, required=["id"])
    change = _change_map(diff_schemas(V1, new))["name"]
    assert change[0] == ChangeKind.REQUIRED_CHANGED
    assert change[1] == ChangeVerdict.COMPATIBLE
    assert assert_compatible(V1, new).is_breaking is False


def test_optional_to_required_is_breaking() -> None:
    new = dict(V1, required=["id", "name", "nickname"])
    change = _change_map(diff_schemas(V1, new))["nickname"]
    assert change[0] == ChangeKind.REQUIRED_CHANGED
    assert change[1] == ChangeVerdict.BREAKING


def test_multiple_changes_locate_every_breaking_field() -> None:
    new = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "integer"},  # 类型变化
            "remark": {"type": "string"},  # 新增必填
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["id", "name", "remark"],
    }
    report = diff_schemas(V1, new)
    located = {c.field for c in report.breaking_changes}
    assert located == {"name", "remark", "nickname"}


def test_nested_object_change_located_with_dotted_path() -> None:
    old = {
        "type": "object",
        "properties": {"addr": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        }},
    }
    new = {
        "type": "object",
        "properties": {"addr": {
            "type": "object",
            "properties": {"zip": {"type": "string"}},
            "required": ["zip"],
        }},
    }
    located = {c.field for c in diff_schemas(old, new).breaking_changes}
    assert "addr.city" in located
