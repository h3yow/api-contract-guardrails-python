"""字段级 JSON Schema 抽象（契约比较的最小单元）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FieldSchema(BaseModel):
    """单个字段的可比较视图。"""

    name: str
    json_type: str  # string/integer/number/boolean/array/object
    required: bool
    enum: list[Any] | None = None
    item_type: str | None = None  # array 元素类型
    nullable: bool = False

    @classmethod
    def from_schema(
        cls, name: str, schema: dict[str, Any], required_names: list[str]
    ) -> FieldSchema:
        """从一段 JSON Schema 的 property 构造字段视图。

        兼容 pydantic v2 的 ``anyOf`` 可空写法与旧式 ``nullable`` 字段。
        """
        json_type, nullable, item_type = _resolve_type(schema)
        return cls(
            name=name,
            json_type=json_type,
            required=name in required_names,
            enum=list(schema["enum"]) if "enum" in schema else None,
            item_type=item_type,
            nullable=nullable,
        )


def _resolve_type(
    schema: dict[str, Any],
) -> tuple[str, bool, str | None]:
    nullable = bool(schema.get("nullable", False))
    item_type: str | None = None

    if "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf") or schema.get("oneOf") or []
        non_null = [v for v in variants if v.get("type") != "null"]
        nullable = nullable or len(non_null) != len(variants)
        chosen = non_null[0] if non_null else {"type": "object"}
        json_type = str(chosen.get("type", "object"))
        if json_type == "array":
            item_type = str(chosen.get("items", {}).get("type", "object"))
        return json_type, nullable, item_type

    json_type = str(schema.get("type", "object"))
    if json_type == "array":
        item_type = str(schema.get("items", {}).get("type", "object"))
    return json_type, nullable, item_type


def flatten_object_schema(schema: dict[str, Any]) -> dict[str, FieldSchema]:
    """把一个 object JSON Schema 展平为 ``{字段名: FieldSchema}``。

    非 object schema 返回空字典；当前只比较一层字段（按需可递归扩展）。
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return {}
    required_names = list(schema.get("required", []))
    props = schema.get("properties", {})
    return {
        name: FieldSchema.from_schema(name, sub, required_names)
        for name, sub in props.items()
    }
