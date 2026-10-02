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


# ---------- 嵌套结构（对象/数组内部）比较 ----------


def _nested_schema(required, **props) -> dict:
    return {
        "type": "object",
        "required": list(required),
        "properties": props,
    }


NESTED_BASE = _nested_schema(
    ["code"],
    code={"type": "string"},
    detail={
        "type": "object",
        "required": ["reason"],
        "properties": {
            "reason": {"type": "string"},
            "hint": {"type": "string"},
        },
    },
    tags={
        "type": "array",
        "items": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        },
    },
)


def test_nested_optional_field_added_is_compatible() -> None:
    import copy

    new = copy.deepcopy(NESTED_BASE)
    new["properties"]["detail"]["properties"]["extra"] = {"type": "integer"}
    report = compare_schemas(NESTED_BASE, new)
    assert report.compatible is True
    change = next(c for c in report.changes if c.field == "detail.extra")
    assert change.change_type is ChangeType.FIELD_ADDED
    assert change.severity is Severity.COMPATIBLE


def test_nested_required_field_added_is_breaking() -> None:
    import copy

    new = copy.deepcopy(NESTED_BASE)
    new["properties"]["detail"]["properties"]["extra"] = {"type": "integer"}
    new["properties"]["detail"]["required"].append("extra")
    report = compare_schemas(NESTED_BASE, new)
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.field == "detail.extra"
    assert bc.change_type is ChangeType.FIELD_ADDED


def test_nested_field_removed_is_breaking_and_located() -> None:
    import copy

    new = copy.deepcopy(NESTED_BASE)
    del new["properties"]["detail"]["properties"]["hint"]
    report = compare_schemas(NESTED_BASE, new)
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.FIELD_REMOVED
    assert bc.field == "detail.hint"


def test_nested_type_change_is_breaking() -> None:
    import copy

    new = copy.deepcopy(NESTED_BASE)
    new["properties"]["detail"]["properties"]["reason"] = {"type": "integer"}
    report = compare_schemas(NESTED_BASE, new)
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.TYPE_CHANGED
    assert bc.field == "detail.reason"


def test_nested_became_required_is_breaking() -> None:
    import copy

    new = copy.deepcopy(NESTED_BASE)
    new["properties"]["detail"]["required"].append("hint")
    report = compare_schemas(NESTED_BASE, new)
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.BECAME_REQUIRED
    assert bc.field == "detail.hint"


def test_array_item_object_field_removed_is_breaking() -> None:
    import copy

    new = copy.deepcopy(NESTED_BASE)
    del new["properties"]["tags"]["items"]["properties"]["name"]
    new["properties"]["tags"]["items"]["required"] = []
    report = compare_schemas(NESTED_BASE, new)
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.FIELD_REMOVED
    assert bc.field == "tags[].name"


def test_array_item_object_optional_field_added_is_compatible() -> None:
    import copy

    new = copy.deepcopy(NESTED_BASE)
    new["properties"]["tags"]["items"]["properties"]["weight"] = {"type": "number"}
    report = compare_schemas(NESTED_BASE, new)
    assert report.compatible is True
    change = next(c for c in report.changes if c.field == "tags[].weight")
    assert change.severity is Severity.COMPATIBLE


# ---------- 逐接口错误声明的版本比较 ----------


def _contract_with_errors(responses_by_op: dict) -> dict:
    """构造只含 paths 错误声明的最小契约。

    responses_by_op: {(path, method): {status: schema}}
    """
    paths: dict = {}
    for (path, method), responses in responses_by_op.items():
        paths.setdefault(path, {})[method] = {
            "responses": {
                str(status): {
                    "description": "err",
                    "content": {"application/json": {"schema": schema}},
                }
                for status, schema in responses.items()
            }
        }
    return {"paths": paths}


ERR_SCHEMA = _nested_schema(
    ["code"],
    code={"type": "string"},
    details={
        "type": "array",
        "items": {
            "type": "object",
            "required": ["reason"],
            "properties": {"reason": {"type": "string"}},
        },
    },
)


def test_error_status_added_is_compatible() -> None:
    from app.contracts.compare import compare_error_declarations

    old = _contract_with_errors({("/items", "post"): {422: ERR_SCHEMA}})
    new = _contract_with_errors(
        {("/items", "post"): {422: ERR_SCHEMA, 500: ERR_SCHEMA}}
    )
    report = compare_error_declarations(old, new)
    assert report.compatible is True
    change = report.changes[0]
    assert change.change_type is ChangeType.ERROR_STATUS_ADDED
    assert change.severity is Severity.COMPATIBLE
    assert change.location == "paths./items.post.responses.500"


def test_error_status_removed_is_breaking_and_located() -> None:
    from app.contracts.compare import compare_error_declarations

    old = _contract_with_errors(
        {("/items", "post"): {422: ERR_SCHEMA, 500: ERR_SCHEMA}}
    )
    new = _contract_with_errors({("/items", "post"): {422: ERR_SCHEMA}})
    report = compare_error_declarations(old, new)
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.ERROR_STATUS_REMOVED
    assert bc.location == "paths./items.post.responses.500"


def test_error_schema_nested_change_is_breaking_and_located() -> None:
    """已声明错误结构里嵌套数组元素的字段类型变化：破坏性，定位到状态码+字段。"""
    import copy

    from app.contracts.compare import compare_error_declarations

    changed = copy.deepcopy(ERR_SCHEMA)
    changed["properties"]["details"]["items"]["properties"]["reason"] = {
        "type": "integer"
    }
    old = _contract_with_errors({("/items", "post"): {422: ERR_SCHEMA}})
    new = _contract_with_errors({("/items", "post"): {422: changed}})
    report = compare_error_declarations(old, new)
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.TYPE_CHANGED
    assert bc.location == "paths./items.post.responses.422.details[].reason"


def test_error_schema_nested_optional_add_is_compatible() -> None:
    import copy

    from app.contracts.compare import compare_error_declarations

    changed = copy.deepcopy(ERR_SCHEMA)
    changed["properties"]["details"]["items"]["properties"]["hint"] = {
        "type": "string"
    }
    old = _contract_with_errors({("/items", "post"): {422: ERR_SCHEMA}})
    new = _contract_with_errors({("/items", "post"): {422: changed}})
    report = compare_error_declarations(old, new)
    assert report.compatible is True
    assert report.breaking_changes == []
    change = report.changes[0]
    assert change.location == "paths./items.post.responses.422.details[].hint"


def test_error_declarations_compare_resolves_refs() -> None:
    """错误声明里的 $ref 必须解析到 components 后再递归比较。"""
    from app.contracts.compare import compare_error_declarations

    ref = {"$ref": "#/components/schemas/ErrorResponse"}
    old = _contract_with_errors({("/boom", "get"): {500: ref}})
    old["components"] = {"schemas": {"ErrorResponse": ERR_SCHEMA}}
    changed = _nested_schema(["code"], code={"type": "integer"})
    new = _contract_with_errors({("/boom", "get"): {500: ref}})
    new["components"] = {"schemas": {"ErrorResponse": changed}}
    report = compare_error_declarations(old, new)
    assert report.compatible is False
    assert any(
        c.location == "paths./boom.get.responses.500.code"
        and c.change_type is ChangeType.TYPE_CHANGED
        for c in report.breaking_changes
    )


def test_compare_contracts_includes_error_declarations() -> None:
    """compare_contracts 聚合组件比较与逐接口错误声明比较。"""
    from app.contracts.compare import compare_contracts

    old = _contract_with_errors({("/items", "post"): {422: ERR_SCHEMA}})
    new = _contract_with_errors({})  # 删除了全部错误声明
    report = compare_contracts(old, new)
    assert report.compatible is False
    assert any(
        c.change_type is ChangeType.ERROR_STATUS_REMOVED
        and c.location == "paths./items.post.responses.422"
        for c in report.breaking_changes
    )
