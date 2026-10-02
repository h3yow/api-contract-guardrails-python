"""FastAPI 应用工厂：装配统一错误契约与启动期守卫。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI

from app.api.router import OutOfStockError, router
from app.contracts.checker import load_baseline, run_startup_checks
from app.contracts.extractor import extract_openapi
from app.contracts.probes import declared_statuses
from app.errors.handlers import install_error_handlers
from app.errors.models import ErrorDetail, ErrorResponse

logger = logging.getLogger("app")

#: 启动期要求必须显式注册的业务异常（未注册会让启动失败）
EXPECTED_BUSINESS_ERRORS = (OutOfStockError,)

#: 各错误状态码在文档中的对外描述
_ERROR_STATUS_DESCRIPTIONS = {
    404: "路径未匹配（统一错误结构）",
    405: "HTTP 方法不允许（统一错误结构）",
    422: "请求校验失败或业务规则拒绝（统一错误结构）",
    500: "未预期异常（已脱敏的统一错误结构）",
}


def _error_response_declaration(status: int) -> dict:
    """生成某个错误状态码的 OpenAPI 响应声明（引用统一错误模型）。"""
    return {
        "description": _ERROR_STATUS_DESCRIPTIONS.get(status, "统一错误结构"),
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
            }
        },
    }


def _augment_openapi_with_error_models(app: FastAPI) -> None:
    """把统一错误模型注入 components.schemas，使文档声明真实错误结构。

    直接来自 pydantic 模型，避免文档与实现各写一份导致漂移；
    任何人为改动文档都会被 validate_documented_error_schema 抓到。
    """
    original_openapi = app.openapi

    def get_openapi():
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = original_openapi()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for model in (ErrorResponse, ErrorDetail):
            generated = model.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            # 把内部 $defs 提升为顶层 schema（如 ErrorDetail）
            for def_name, def_schema in generated.pop("$defs", {}).items():
                components.setdefault(def_name, def_schema)
            components[model.__name__] = generated
        _declare_error_responses(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = get_openapi  # type: ignore[method-assign]


def _declare_error_responses(schema: dict) -> None:
    """按探针计划为每个接口声明逐状态码的错误响应结构。

    声明与探针同源：文档里写了哪些错误状态，运行时核对就真实打哪些，
    二者不允许各自漂移（被 verify_error_responses 的覆盖率检查兜住）。
    """
    paths = schema.get("paths", {})
    for (method, path), statuses in declared_statuses().items():
        operation = (paths.get(path) or {}).get(method)
        if not isinstance(operation, dict):
            continue
        responses = operation.setdefault("responses", {})
        for status in sorted(statuses):
            responses[str(status)] = _error_response_declaration(status)


def create_app(
    *,
    run_checks: bool | None = None,
    baseline_path: str | Path | None = None,
) -> FastAPI:
    """创建并返回 FastAPI 应用。

    - 始终装配 request_id 中间件与统一异常处理器；
    - 开发模式（``APP_ENV != prod`` 或显式 run_checks=True）执行启动期守卫，
      映射缺失/冲突、文档漂移、破坏性契约变更都会让启动直接失败，
      而不是在请求期静默降级为通用错误。
    """
    app = FastAPI(title="api-contract-guardrails", version="0.1.0")
    app.include_router(router)
    install_error_handlers(app)
    _augment_openapi_with_error_models(app)

    if run_checks is None:
        run_checks = os.getenv("APP_ENV", "dev").lower() != "prod"

    if run_checks:
        baseline = None
        if baseline_path is not None:
            baseline = load_baseline(baseline_path)
        result, report = run_startup_checks(
            app,
            baseline_contract=baseline,
            expected_business_errors=EXPECTED_BUSINESS_ERRORS,
        )
        if report is not None:
            logger.info(
                "契约兼容性: compatible=%s breaking=%d",
                report.compatible,
                len(report.breaking_changes),
            )
        if not result.ok:  # pragma: no cover - strict 已在内部抛错
            result.raise_if_invalid()

    # 触发一次 OpenAPI 生成，确保文档注入在首次请求前生效
    extract_openapi(app)
    return app


app = create_app()
