"""字段级 JSON Schema 抽象（契约比较的最小单元）。

``FieldSchema`` 是一棵**递归**字段树：
- object 字段的子字段在 ``children``；
- array 字段的元素结构在 ``items``（``item_type`` 保留为元素类型的标量摘要）；
- ``$ref`` 可按需解析（``resolve_refs=True``，带循环保护），
  不解析时作为 ``ref:<名称>`` 叶子类型参与比较。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FieldSchema(BaseModel):
    """单个字段的可比较视图（递归）。"""

    name: str
    json_type: str  # string/integer/number/boolean/array/object/ref:<名>
    required: bool
    enum: list[Any] | None = None
    item_type: str | None = None  # array 元素类型（标量摘要）
    nullable: bool = False
    children: dict[str, FieldSchema] = Field(default_factory=dict)
    items: FieldSchema | None = None  # array 元素的字段树

    @classmethod
    def from_schema(
        cls,
        name: str,
        schema: dict[str, Any],
        required_names: list[str],
        *,
        components: dict[str, Any] | None = None,
        resolve_refs: bool = False,
        _seen_refs: frozenset[str] = frozenset(),
    ) -> FieldSchema:
        """从一段 JSON Schema 的 property 构造字段树。

        兼容 pydantic v2 的 ``anyOf`` 可空写法与旧式 ``nullable`` 字段。
        """
        resolved, nullable = resolve_schema(
            schema, components or {}, resolve_refs, _seen_refs
        )
        json_type = str(resolved.get("type", "object"))
        item_type: str | None = None
        items: FieldSchema | None = None
        children: dict[str, FieldSchema] = {}

        if json_type == "object":
            sub_required = list(resolved.get("required", []))
            for prop_name, prop_schema in (resolved.get("properties", {}) or {}).items():
                children[prop_name] = cls.from_schema(
                    prop_name,
                    prop_schema,
                    sub_required,
                    components=components,
                    resolve_refs=resolve_refs,
                    _seen_refs=_seen_refs,
                )
        elif json_type == "array":
            raw_items = resolved.get("items", {}) or {}
            item_resolved, _ = resolve_schema(
                raw_items, components or {}, resolve_refs, _seen_refs
            )
            item_type = str(item_resolved.get("type", "object"))
            items = cls.from_schema(
                "[]",
                raw_items,
                [],
                components=components,
                resolve_refs=resolve_refs,
                _seen_refs=_seen_refs,
            )

        return cls(
            name=name,
            json_type=json_type,
            required=name in required_names,
            enum=list(resolved["enum"]) if "enum" in resolved else None,
            item_type=item_type,
            nullable=nullable,
            children=children,
            items=items,
        )


def resolve_schema(
    schema: dict[str, Any],
    components: dict[str, Any],
    resolve_refs: bool,
    seen_refs: frozenset[str],
) -> tuple[dict[str, Any], bool]:
    """解析 $ref 与 anyOf/oneOf，返回 ``(选定子schema, 是否可空)``。"""
    nullable = bool(schema.get("nullable", False))

    ref = schema.get("$ref")
    if ref:
        name = str(ref).rsplit("/", 1)[-1]
        if not resolve_refs or name in seen_refs:
            return {"type": f"ref:{name}"}, nullable
        target = (components.get("schemas", {}) or {}).get(name)
        if not isinstance(target, dict):
            return {"type": f"ref:{name}"}, nullable
        resolved, sub_nullable = resolve_schema(
            target, components, resolve_refs, seen_refs | {name}
        )
        return resolved, nullable or sub_nullable

    if "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf") or schema.get("oneOf") or []
        non_null = [v for v in variants if v.get("type") != "null"]
        nullable = nullable or len(non_null) != len(variants)
        chosen = non_null[0] if non_null else {"type": "object"}
        resolved, sub_nullable = resolve_schema(
            chosen, components, resolve_refs, seen_refs
        )
        return resolved, nullable or sub_nullable

    return schema, nullable


def flatten_object_schema(
    schema: dict[str, Any],
    *,
    components: dict[str, Any] | None = None,
    resolve_refs: bool = False,
) -> dict[str, FieldSchema]:
    """把一个 object JSON Schema 展平为 ``{字段名: FieldSchema}`` 字段树。

    非 object schema 返回空字典；嵌套对象与数组元素递归展开。
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return {}
    required_names = list(schema.get("required", []))
    props = schema.get("properties", {})
    return {
        name: FieldSchema.from_schema(
            name,
            sub,
            required_names,
            components=components,
            resolve_refs=resolve_refs,
        )
        for name, sub in props.items()
    }
