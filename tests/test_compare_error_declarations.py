"""错误响应声明的版本比较 + 嵌套结构递归比较。

规则：
- 接口新增声明错误状态：兼容；
- 删除已声明错误状态：破坏性（定位到接口+状态码）；
- 已声明错误的结构变化：按字段级规则，递归进嵌套对象与数组内部。
"""

from __future__ import annotations

import copy

from app.contracts.compare import compare_contracts, compare_schemas
from app.contracts.models import ChangeType, Severity

ERROR_SCHEMA = {
    "type": "object",
    "required": ["code", "details"],
    "properties": {
        "code": {"type": "string"},
        "details": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["reason", "message"],
                "properties": {
                    "location": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        },
    },
}


def _contract(*, statuses=("422",), error_schema=None):
    """构造只含单个接口错误声明的最小契约。"""
    schema = error_schema if error_schema is not None else ERROR_SCHEMA
    return {
        "paths": {
            "/items": {
                "post": {
                    "responses": {
                        s: {
                            "description": "err",
                            "content": {
                                "application/json": {"schema": copy.deepcopy(schema)}
                            },
                        }
                        for s in statuses
                    }
                }
            }
        },
        "components": {"schemas": {}},
    }


# ------------------------------------------------------------ 状态码级

def test_added_error_status_is_compatible() -> None:
    old = _contract(statuses=("422",))
    new = _contract(statuses=("422", "500"))
    report = compare_contracts(old, new)
    assert report.compatible is True
    change = next(
        c for c in report.changes
        if c.change_type is ChangeType.ERROR_STATUS_ADDED
    )
    assert change.severity is Severity.COMPATIBLE
    assert change.location == "paths./items.post.responses.500"
    assert report.breaking_changes == []


def test_removed_error_status_is_breaking_and_located() -> None:
    old = _contract(statuses=("422", "500"))
    new = _contract(statuses=("422",))
    report = compare_contracts(old, new)
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.ERROR_STATUS_REMOVED
    assert bc.location == "paths./items.post.responses.500"
    assert "POST /items" in bc.detail and "500" in bc.detail


def test_identical_declarations_have_no_changes() -> None:
    report = compare_contracts(_contract(), _contract())
    assert report.compatible is True and report.changes == []


# ------------------------------------------------------------ 结构级（含嵌套）

def test_nested_field_type_change_is_breaking_and_located() -> None:
    new_schema = copy.deepcopy(ERROR_SCHEMA)
    new_schema["properties"]["details"]["items"]["properties"]["message"] = {
        "type": "integer"
    }
    report = compare_contracts(_contract(), _contract(error_schema=new_schema))
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.TYPE_CHANGED
    assert bc.location == (
        "paths./items.post.responses.422.details[].message"
    )


def test_nested_optional_field_added_is_compatible() -> None:
    new_schema = copy.deepcopy(ERROR_SCHEMA)
    new_schema["properties"]["details"]["items"]["properties"]["hint"] = {
        "type": "string"
    }
    report = compare_contracts(_contract(), _contract(error_schema=new_schema))
    assert report.compatible is True
    change = report.changes[0]
    assert change.change_type is ChangeType.FIELD_ADDED
    assert change.severity is Severity.COMPATIBLE
    assert change.location.endswith("details[].hint")


def test_nested_required_field_added_is_breaking() -> None:
    new_schema = copy.deepcopy(ERROR_SCHEMA)
    item = new_schema["properties"]["details"]["items"]
    item["properties"]["hint"] = {"type": "string"}
    item["required"].append("hint")
    report = compare_contracts(_contract(), _contract(error_schema=new_schema))
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.FIELD_ADDED
    assert bc.location.endswith("details[].hint")


def test_nested_field_removed_is_breaking() -> None:
    new_schema = copy.deepcopy(ERROR_SCHEMA)
    item = new_schema["properties"]["details"]["items"]
    del item["properties"]["location"]
    report = compare_contracts(_contract(), _contract(error_schema=new_schema))
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.FIELD_REMOVED
    assert bc.location.endswith("details[].location")


def test_nested_became_optional_is_compatible() -> None:
    new_schema = copy.deepcopy(ERROR_SCHEMA)
    new_schema["properties"]["details"]["items"]["required"] = ["reason"]
    report = compare_contracts(_contract(), _contract(error_schema=new_schema))
    assert report.compatible is True
    change = report.changes[0]
    assert change.change_type is ChangeType.BECAME_OPTIONAL
    assert change.location.endswith("details[].message")


def test_nested_became_required_is_breaking() -> None:
    old_schema = copy.deepcopy(ERROR_SCHEMA)
    old_schema["properties"]["details"]["items"]["required"] = ["reason"]
    report = compare_contracts(
        _contract(error_schema=old_schema), _contract()
    )
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.BECAME_REQUIRED
    assert bc.location.endswith("details[].message")


# ------------------------------------------------------------ $ref 声明的递归解析

def test_ref_based_declaration_recurses_into_components() -> None:
    """错误声明通过 $ref 引用组件时，深层变化同样被定位到接口+状态码。"""
    def contract_with(message_type: str):
        return {
            "paths": {
                "/items": {
                    "post": {
                        "responses": {
                            "422": {
                                "description": "err",
                                "content": {"application/json": {"schema": {
                                    "$ref": "#/components/schemas/ErrorResponse"
                                }}},
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "ErrorResponse": {
                        "type": "object",
                        "required": ["message"],
                        "properties": {"message": {"type": message_type}},
                    }
                }
            },
        }

    report = compare_contracts(contract_with("string"), contract_with("integer"))
    assert report.compatible is False
    locations = [c.location for c in report.breaking_changes]
    assert "paths./items.post.responses.422.message" in locations


# ------------------------------------------------------------ 纯 schema 嵌套递归（不经过 paths）

def test_inline_nested_object_recursion() -> None:
    old = {
        "type": "object",
        "required": ["meta"],
        "properties": {
            "meta": {
                "type": "object",
                "required": ["trace"],
                "properties": {"trace": {"type": "string"},
                               "span": {"type": "string"}},
            }
        },
    }
    new = copy.deepcopy(old)
    del new["properties"]["meta"]["properties"]["span"]
    report = compare_schemas(old, new, location_prefix="root")
    assert report.compatible is False
    bc = report.breaking_changes[0]
    assert bc.change_type is ChangeType.FIELD_REMOVED
    assert bc.location == "root.meta.span"
