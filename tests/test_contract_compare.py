"""契约版本比较：新增/删除/类型变化/必填性变化必须被区分。"""

from __future__ import annotations

from app.contracts.compare import compare_contracts, compare_schemas
from app.contracts.models import ChangeType, Severity


def _schema(required, **props) -> dict:
    return {
        "type": "object",
        "required": list(required),
        "properties": {k: {"type": v} if isinstance(v, str) else v
                       for k, v in props.items()},
    }


BASE = _schema(["id"], id="integer", name="string")


def test_added_optional_field_is_compatible() -> None:
    new = _schema(["id"], id="integer", name="string", nickname="string")
    report = compare_schemas(BASE, new)
    assert report.compatible is True
    change = next(c for c in report.changes if c.field == "nickname")
    assert change.change_type is ChangeType.FIELD_ADDED
    assert change.severity is Severity.COMPATIBLE


def test_added_required_field_is_breaking() -> None:
    new = _schema(["id", "nickname"], id="integer", name="string", nickname="string")
    report = compare_schemas(BASE, new)
    assert report.compatible is False
    bc = report.breaking_changes
    assert len(bc) == 1
    assert bc[0].field == "nickname"
    assert bc[0].change_type is ChangeType.FIELD_ADDED


def test_removed_field_is_breaking_and_located() -> None:
    new = _schema(["id"], id="integer")
    report = compare_schemas(BASE, new)
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.FIELD_REMOVED
    assert bc.field == "name"
    assert bc.location == "name"  # 可定位到具体字段


def test_type_changed_is_breaking() -> None:
    new = _schema(["id"], id="string", name="string")
    report = compare_schemas(BASE, new)
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.TYPE_CHANGED
    assert bc.field == "id"
    assert "integer" in bc.detail and "string" in bc.detail


def test_became_required_is_breaking() -> None:
    old = _schema(["id"], id="integer", name="string")
    new = _schema(["id", "name"], id="integer", name="string")
    report = compare_schemas(old, new)
    assert report.compatible is False
    assert report.breaking_changes[0].change_type is ChangeType.BECAME_REQUIRED


def test_became_optional_is_compatible() -> None:
    old = _schema(["id", "name"], id="integer", name="string")
    new = _schema(["id"], id="integer", name="string")
    report = compare_schemas(old, new)
    assert report.compatible is True
    assert report.changes[0].change_type is ChangeType.BECAME_OPTIONAL
    assert report.breaking_changes == []


def test_array_item_type_change_is_breaking() -> None:
    old = _schema(["id"], id="integer",
                  tags={"type": "array", "items": {"type": "string"}})
    new = _schema(["id"], id="integer",
                  tags={"type": "array", "items": {"type": "integer"}})
    report = compare_schemas(old, new)
    assert report.compatible is False
    assert report.breaking_changes[0].change_type is ChangeType.TYPE_CHANGED


def test_compare_full_contracts_aggregates_schemas() -> None:
    old = {"components": {"schemas": {"Item": BASE}}}
    new = {"components": {"schemas": {"Item": _schema(
        ["id"], id="integer")}}}  # 删除了 name
    report = compare_contracts(old, new)
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert "components.schemas.Item.properties.name" == bc.location
    assert bc.change_type is ChangeType.FIELD_REMOVED


def test_no_change_is_compatible() -> None:
    report = compare_schemas(BASE, BASE)
    assert report.compatible is True and report.changes == []
