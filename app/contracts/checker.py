"""开发期/CI 统一守卫入口。

职责：
1. 错误映射自检（未处理/冲突不得静默降级）；
2. 文档中的错误结构与真实响应模型一致性；
3. 与基线 OpenAPI 契约的兼容性比较。

``strict=True`` 时任一项失败都抛错，阻断开发启动。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.contracts.compare import compare_contracts
from app.contracts.extractor import extract_openapi
from app.contracts.live_check import validate_live_error_structures
from app.contracts.models import CompatibilityReport
from app.contracts.probes import ERROR_PROBES, NO_ERROR_SURFACE, ErrorProbe
from app.contracts.validation import (
    ValidationFinding,
    ValidationResult,
    validate_documented_error_schema,
    validate_error_mapping,
)
from app.errors.mapping import registry


def run_startup_checks(
    app: FastAPI,
    *,
    baseline_contract: dict[str, Any] | None = None,
    strict: bool = True,
    expected_business_errors: tuple[type[BaseException], ...] = (),
    probes: tuple[ErrorProbe, ...] | list[ErrorProbe] = ERROR_PROBES,
    no_error_surface: frozenset[tuple[str, str]] = NO_ERROR_SURFACE,
) -> tuple[ValidationResult, CompatibilityReport | None]:
    """执行全部启动期守卫。

    返回 ``(映射+文档+真实响应核对聚合结果, 兼容性报告或 None)``。
    strict 模式下任何问题都会抛出异常阻断启动。
    """
    openapi_schema = extract_openapi(app)

    mapping_result = validate_error_mapping(
        registry, expected=expected_business_errors
    )
    doc_result = validate_documented_error_schema(app, openapi_schema)
    live_result = validate_live_error_structures(
        app, openapi_schema, probes, no_error_surface=no_error_surface
    )

    combined = ValidationResult(
        ok=mapping_result.ok and doc_result.ok and live_result.ok,
        findings=mapping_result.findings + doc_result.findings + live_result.findings,
    )

    report: CompatibilityReport | None = None
    if baseline_contract is not None:
        report = compare_contracts(baseline_contract, openapi_schema)
        if not report.compatible:
            breaking_findings = [
                ValidationFinding(
                    location=c.location,
                    message=f"[破坏性变更:{c.change_type.value}] {c.detail}",
                )
                for c in report.breaking_changes
            ]
            combined = ValidationResult(
                ok=False, findings=combined.findings + breaking_findings
            )

    if strict:
        combined.raise_if_invalid()
    return combined, report


def load_baseline(path: str | Path) -> dict[str, Any]:
    """从 JSON 文件加载基线 OpenAPI 契约。"""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"基线契约必须是 JSON 对象: {path}")
    return data
