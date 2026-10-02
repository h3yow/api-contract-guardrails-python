"""字段级 JSON Schema 抽象（契约比较的最小单元）。

``flatten_object_schema`` 会把嵌套对象与对象数组递归展平：
- 嵌套对象字段以 ``a.b`` 形式定位；
- 对象数组的元素字段以 ``items[].x`` 形式定位；
- 本地 ``$ref``（#/components/schemas/...）在提供 components 时可解析，
  循环引用会安全终止（按叶子处理）。
"""

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
        # name 可能是递归展平后的定位路径（如 "details[].reason"），
        # 而 required_names 是当前层级 object 的裸字段名，故取叶子名比对。
        leaf = name.rsplit(".", 1)[-1]
        return cls(
            name=name,
            json_type=json_type,
            required=leaf in required_names,
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


def _deref(
    schema: dict[str, Any],
    components: dict[str, Any] | None,
    _depth: int = 0,
) -> dict[str, Any]:
    """解析本地 $ref 链；无法解析时返回原样。"""
    while (
        isinstance(schema, dict)
        and "$ref" in schema
        and components is not None
        and _depth < 20
    ):
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        target = (components.get("schemas", {}) or {}).get(name)
        if not isinstance(target, dict):
            break
        schema = target
        _depth += 1
    return schema if isinstance(schema, dict) else {}


def _effective_schema(
    schema: dict[str, Any],
    components: dict[str, Any] | None,
) -> dict[str, Any]:
    """deref 后，对 anyOf/oneOf 取第一个非 null 变体（与 _resolve_type 一致）。"""
    schema = _deref(schema, components)
    variants = schema.get("anyOf") or schema.get("oneOf")
    if variants:
        non_null = [v for v in variants if v.get("type") != "null"]
        if non_null:
            return _deref(non_null[0], components)
    return schema


def flatten_object_schema(
    schema: dict[str, Any],
    components: dict[str, Any] | None = None,
    _prefix: str = "",
    _seen_refs: frozenset[str] = frozenset(),
) -> dict[str, FieldSchema]:
    """把一个 object JSON Schema 递归展平为 ``{定位路径: FieldSchema}``。

    嵌套对象属性以 ``a.b`` 定位，对象数组元素属性以 ``items[].x`` 定位；
    非 object schema 返回空字典。
    """
    effective = _effective_schema(schema, components)
    if not isinstance(effective, dict) or effective.get("type") != "object":
        return {}
    required_names = list(effective.get("required", []))
    props = effective.get("properties", {}) or {}
    out: dict[str, FieldSchema] = {}
    for name, sub in props.items():
        path = _prefix + name
        out[path] = FieldSchema.from_schema(path, sub, required_names)

        # 递归进入嵌套结构（带循环引用保护）
        ref_name = _ref_name(sub)
        if ref_name is not None and ref_name in _seen_refs:
            continue
        seen = _seen_refs | ({ref_name} if ref_name else frozenset())
        eff_sub = _effective_schema(sub, components)
        if eff_sub.get("type") == "object":
            out.update(
                flatten_object_schema(eff_sub, components, path + ".", seen)
            )
        elif eff_sub.get("type") == "array":
            item_eff = _effective_schema(eff_sub.get("items", {}) or {}, components)
            item_ref = _ref_name(eff_sub.get("items", {}) or {})
            if item_ref is not None and item_ref in seen:
                continue
            if item_eff.get("type") == "object":
                out.update(
                    flatten_object_schema(
                        item_eff,
                        components,
                        path + "[].",
                        seen | ({item_ref} if item_ref else frozenset()),
                    )
                )
    return out


def _ref_name(schema: dict[str, Any]) -> str | None:
    if isinstance(schema, dict) and "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    return None
