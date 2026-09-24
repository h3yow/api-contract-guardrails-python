"""显式映射注册表：冲突必须报错，缺失必须被启动校验发现。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import BusinessError, ErrorCode, ErrorEnvelope
from app.handlers import business_error_handler
from app import mapping
from app.mapping import (
    FRAMEWORK_ERROR_MAP,
    MappingConflictError,
    declare_business_reason,
    register_framework_error,
)
from app.validation import run_startup_validation, validate_error_mappings


@pytest.fixture(autouse=True)
def restore_mappings():
    framework = dict(FRAMEWORK_ERROR_MAP)
    business = dict(mapping.BUSINESS_REASON_MAP)
    yield
    FRAMEWORK_ERROR_MAP.clear()
    FRAMEWORK_ERROR_MAP.update(framework)
    mapping.BUSINESS_REASON_MAP.clear()
    mapping.BUSINESS_REASON_MAP.update(business)


def test_framework_mapping_conflict_raises() -> None:
    class Dummy(Exception):
        pass

    register_framework_error(Dummy, ErrorCode.NOT_FOUND, "未找到")
    with pytest.raises(MappingConflictError, match="Dummy"):
        register_framework_error(Dummy, ErrorCode.INTERNAL_ERROR, "内部错误")


def test_framework_mapping_same_code_idempotent() -> None:
    class Dummy2(Exception):
        pass

    register_framework_error(Dummy2, ErrorCode.NOT_FOUND, "未找到")
    register_framework_error(Dummy2, ErrorCode.NOT_FOUND, "未找到")
    assert FRAMEWORK_ERROR_MAP[Dummy2][0] == ErrorCode.NOT_FOUND


def test_business_reason_conflict_raises() -> None:
    declare_business_reason("dup_reason", "文案甲")
    with pytest.raises(MappingConflictError, match="dup_reason"):
        declare_business_reason("dup_reason", "文案乙")


def test_missing_framework_mapping_detected() -> None:
    FRAMEWORK_ERROR_MAP.clear()
    problems = validate_error_mappings()
    assert len(problems) == 3
    joined = "\n".join(problems)
    assert "RequestValidationError" in joined
    assert "HTTPException" in joined
    assert "Exception" in joined


def test_startup_validation_fails_loud_when_mapping_missing() -> None:
    FRAMEWORK_ERROR_MAP.clear()
    app = FastAPI()
    with pytest.raises(RuntimeError, match="启动校验失败") as exc_info:
        run_startup_validation(app)
    # 必须指出具体缺失项，而不是笼统的通用失败
    assert "RequestValidationError" in str(exc_info.value)


def test_unregistered_business_reason_does_not_silently_degrade() -> None:
    """未登记 reason 不得伪装成普通业务拒绝，必须以 INTERNAL_ERROR 暴露。"""
    app = FastAPI()
    app.add_exception_handler(BusinessError, business_error_handler)

    @app.get("/unregistered")
    def unregistered() -> None:
        raise BusinessError("never_declared")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/unregistered")
    body = resp.json()["error"]
    assert resp.status_code == 500
    assert body["code"] == ErrorCode.INTERNAL_ERROR
    assert body["details"][0]["loc"] == "reason:unregistered"


def test_error_envelope_is_single_contract() -> None:
    # 防止误加并列错误结构：合同只有一个对外信封模型
    assert ErrorEnvelope.model_fields.keys() == {"error"}
