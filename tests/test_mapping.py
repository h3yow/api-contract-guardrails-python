"""错误映射注册：冲突检测、未注册检测、MRO 解析。

关键约束：冲突/未处理必须在启动或校验流程中显式失败，
不能在请求期静默降级为通用错误。
"""

from __future__ import annotations

import pytest

from app.contracts.validation import validate_error_mapping
from app.errors.codes import ErrorCode
from app.errors.exceptions import BusinessError, ContractMappingError
from app.errors.mapping import ErrorMappingRegistry, register_error


class OrderError(BusinessError):
    reason = "order_error"


class PaymentError(OrderError):
    reason = "payment_failed"


def test_register_is_idempotent_for_same_code() -> None:
    reg = ErrorMappingRegistry()
    reg.register(OrderError, ErrorCode.BUSINESS_RULE_VIOLATION)
    reg.register(OrderError, ErrorCode.BUSINESS_RULE_VIOLATION)
    assert reg.resolve(OrderError("x")) is ErrorCode.BUSINESS_RULE_VIOLATION


def test_conflicting_mapping_rejected_at_registration() -> None:
    """同一异常映射到不同错误码必须立即报错（启动/导入期暴露）。"""
    reg = ErrorMappingRegistry()
    reg.register(OrderError, ErrorCode.BUSINESS_RULE_VIOLATION)
    with pytest.raises(ContractMappingError) as exc:
        reg.register(OrderError, ErrorCode.INTERNAL_ERROR)
    assert "冲突映射" in str(exc.value)
    assert "OrderError" in str(exc.value)


def test_resolve_follows_mro() -> None:
    reg = ErrorMappingRegistry()
    reg.register(BusinessError, ErrorCode.BUSINESS_RULE_VIOLATION)
    # PaymentError 未直接注册，应沿 MRO 解析到 OrderError/BusinessError
    reg.register(OrderError, ErrorCode.BUSINESS_RULE_VIOLATION)
    assert reg.resolve(PaymentError("x")) is ErrorCode.BUSINESS_RULE_VIOLATION


def test_unmapped_business_error_detected_by_validation() -> None:
    """声明了但没注册的业务异常，校验必须判失败且给出可定位信息。"""
    reg = ErrorMappingRegistry()  # 空注册表
    result = validate_error_mapping(reg, expected=(OrderError,))
    assert result.ok is False
    assert result.findings[0].location == "error_mapping"
    assert "OrderError" in result.findings[0].message
    with pytest.raises(ContractMappingError):
        result.raise_if_invalid()


def test_runtime_unmapped_is_observable_but_separate() -> None:
    """运行期未命中被记录用于巡检，但不与业务漏注册混淆。"""
    reg = ErrorMappingRegistry()

    class RandomFailure(Exception):
        pass

    assert reg.resolve(RandomFailure()) is None
    problems = reg.runtime_unmapped_problems()
    assert any("RandomFailure" in p for p in problems)
    # 启动校验本身不因未预期异常而失败
    assert reg.validate(expected=()) == []


def test_register_error_decorator() -> None:
    """register_error 可作普通函数绑定，且绑定点是传入的异常类型。"""

    class _CouponError(BusinessError):
        reason = "coupon_expired"

    bound = register_error(_CouponError, ErrorCode.BUSINESS_RULE_VIOLATION)
    assert bound is _CouponError
    from app.errors.mapping import registry
    assert registry.is_registered(_CouponError)
