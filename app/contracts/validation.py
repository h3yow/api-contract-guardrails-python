"""契约一致性校验：错误映射自检 + 文档错误模型与实际模型比对。

校验失败一律产出带 ``location`` 的 finding，绝不静默通过。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI

from app.errors.exceptions import ContractMappingError
from app.errors.mapping import ErrorMappingRegistry
from app.errors.models import ErrorResponse


@dataclass
class ValidationFinding:
    """一条可定位的校验问题。"""

    location: str
    message: str


@dataclass
class ValidationResult:
    """校验结论。``ok=False`` 时 findings 必须非空。"""

    ok: bool
    findings: list[ValidationFinding] = field(default_factory=list)

    def raise_if_invalid(self) -> None:
        """校验失败时抛出 ContractMappingError，阻断开发启动。"""
        if not self.ok:
            lines = [f"- [{f.location}] {f.message}" for f in self.findings]
            raise ContractMappingError(
                "错误契约校验未通过，共 %d 个问题:\n%s"
                % (len(self.findings), "\n".join(lines))
            )


def validate_error_mapping(
    registry: ErrorMappingRegistry,
    *,
    expected: tuple[type[BaseException], ...] = (),
) -> ValidationResult:
    """检查映射注册表：未注册/运行期未命中必须显式暴露。"""
    problems = registry.validate(expected)
    return ValidationResult(
        ok=not problems,
        findings=[
            ValidationFinding(location="error_mapping", message=p) for p in problems
        ],
    )


def validate_documented_error_schema(
    app: FastAPI, openapi_schema: dict[str, Any]
) -> ValidationResult:
    """比对文档中声明的 ErrorResponse 与运行时真实模型是否一致。

    任何偏离（组件缺失/字段缺失/字段类型不符）都会给出可定位 finding。
    """
    findings: list[ValidationFinding] = []
    schemas = (openapi_schema.get("components", {}) or {}).get("schemas", {}) or {}
    documented = schemas.get("ErrorResponse")

    if not isinstance(documented, dict):
        findings.append(
            ValidationFinding(
                location="components.schemas.ErrorResponse",
                message="OpenAPI 文档缺少统一错误模型 ErrorResponse 的声明",
            )
        )
        return ValidationResult(ok=False, findings=findings)

    actual = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
    actual_props = actual.get("properties", {})
    doc_props = documented.get("properties", {})

    for name, prop in actual_props.items():
        loc = f"components.schemas.ErrorResponse.properties.{name}"
        if name not in doc_props:
            findings.append(
                ValidationFinding(location=loc, message="实际响应存在该字段但文档缺失")
            )
            continue
        doc_type = _json_type(doc_props[name])
        actual_type = _json_type(prop)
        if doc_type != actual_type:
            findings.append(
                ValidationFinding(
                    location=loc,
                    message=f"字段类型不一致: 文档={doc_type} 实际={actual_type}",
                )
            )

    for name in doc_props.keys() - actual_props.keys():
        findings.append(
            ValidationFinding(
                location=f"components.schemas.ErrorResponse.properties.{name}",
                message="文档声明了该字段但实际错误响应中不存在",
            )
        )

    doc_required = set(documented.get("required", []))
    actual_required = set(actual.get("required", []))
    if doc_required != actual_required:
        findings.append(
            ValidationFinding(
                location="components.schemas.ErrorResponse.required",
                message=f"必填字段不一致: 文档={sorted(doc_required)} "
                f"实际={sorted(actual_required)}",
            )
        )

    return ValidationResult(ok=not findings, findings=findings)


def _json_type(prop: dict[str, Any]) -> str:
    """归一化出属性的类型描述，便于比较（兼容 $ref / anyOf）。"""
    if "$ref" in prop:
        return "ref:" + str(prop["$ref"]).rsplit("/", 1)[-1]
    if "anyOf" in prop:
        return "anyOf:" + ",".join(sorted(_json_type(v) for v in prop["anyOf"]))
    if prop.get("type") == "array":
        inner = prop.get("items", {})
        return f"array<{_json_type(inner)}>"
    return str(prop.get("type", "object"))
