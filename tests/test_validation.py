"""文档一致性与启动校验测试。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.main import app as real_app
from app.validation import (
    run_startup_validation,
    validate_openapi_consistency,
)


def test_real_app_openapi_is_consistent() -> None:
    assert validate_openapi_consistency(real_app) == []


def test_documented_error_structure_drift_is_caught() -> None:
    class WrongEnvelope(BaseModel):
        detail: str  # 与实际错误模型不符

    from fastapi import FastAPI
    from app.mapping import FRAMEWORK_ERROR_MAP
    from app.errors import ErrorCode
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    # 映射完整，但文档结构偏离
    FRAMEWORK_ERROR_MAP.setdefault(RequestValidationError, (ErrorCode.VALIDATION_FAILED, "x"))
    FRAMEWORK_ERROR_MAP.setdefault(StarletteHTTPException, (ErrorCode.NOT_FOUND, "x"))
    FRAMEWORK_ERROR_MAP.setdefault(Exception, (ErrorCode.INTERNAL_ERROR, "x"))

    drift_app = FastAPI()

    @drift_app.get("/x", responses={404: {"model": WrongEnvelope}})
    def x() -> dict[str, str]:
        return {"ok": "1"}

    problems = validate_openapi_consistency(drift_app)
    assert any("GET /x 的 404" in p for p in problems)
    assert any("ErrorEnvelope" in p for p in problems)


def test_startup_validation_aggregates_all_problems() -> None:
    from fastapi import FastAPI
    from app.mapping import FRAMEWORK_ERROR_MAP

    saved = dict(FRAMEWORK_ERROR_MAP)
    FRAMEWORK_ERROR_MAP.clear()
    try:
        with pytest.raises(RuntimeError) as exc_info:
            run_startup_validation(FastAPI())
        message = str(exc_info.value)
        assert message.count("- ") >= 3
    finally:
        FRAMEWORK_ERROR_MAP.clear()
        FRAMEWORK_ERROR_MAP.update(saved)
