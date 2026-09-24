"""业务错误与框架错误的显式映射注册表。

设计要点：

* 所有框架级异常类型（请求校验、404、405、兜底 Exception）必须显式登记到
  统一 :class:`~app.errors.ErrorCode`，不允许依赖 FastAPI 默认行为。
* 所有业务 reason 必须通过 :func:`declare_business_reason` 显式登记对外文案，
  不允许在路由里随手写一个未登记的 reason。
* 同一异常类型/同一 reason 被登记到不同结论时抛出
  :class:`MappingConflictError`；缺失登记在 :mod:`app.validation` 的启动/校验
  流程中以 :class:`MappingMissingError` 暴露，绝不静默降级。
"""
from __future__ import annotations

from app.errors import ErrorCode


class MappingError(Exception):
    """映射配置问题的基类。"""


class MappingConflictError(MappingError):
    """同一来源被登记到相互冲突的结论。"""


class MappingMissingError(MappingError):
    """存在必须显式登记但未登记的来源。"""


# 框架异常类 -> (对外错误码, 对外 message)
FRAMEWORK_ERROR_MAP: dict[type[Exception], tuple[ErrorCode, str]] = {}

# 业务 reason -> 对外 message
BUSINESS_REASON_MAP: dict[str, str] = {}

# 必须登记的框架异常在 validation 中按名字检查，避免在此处 import starlette
REQUIRED_FRAMEWORK_ERRORS: tuple[str, ...] = (
    "RequestValidationError",
    "HTTPException",
    "Exception",
)


def register_framework_error(
    exc_type: type[Exception], code: ErrorCode, message: str
) -> None:
    """登记框架异常类型到统一错误码的映射。

    重复登记同一类型时允许幂等覆盖（结论一致）；结论冲突则抛
    :class:`MappingConflictError`，不得静默采用后注册者。
    """
    existing = FRAMEWORK_ERROR_MAP.get(exc_type)
    if existing is not None and existing[0] != code:
        raise MappingConflictError(
            f"框架异常 {exc_type.__name__} 已映射到 {existing[0]}，"
            f"不能再映射到 {code}"
        )
    FRAMEWORK_ERROR_MAP[exc_type] = (code, message)


def declare_business_reason(reason: str, message: str) -> str:
    """登记业务 reason 及其对外文案，返回 reason 本身。

    用法::

        ORDER_LOCKED = declare_business_reason(
            "order_locked", "订单已锁定，不允许修改"
        )
        raise BusinessError(ORDER_LOCKED)

    同一 reason 用不同文案重复登记视为冲突。
    """
    existing = BUSINESS_REASON_MAP.get(reason)
    if existing is not None and existing != message:
        raise MappingConflictError(
            f"业务 reason {reason!r} 已登记文案 {existing!r}，"
            f"不能再登记 {message!r}"
        )
    BUSINESS_REASON_MAP[reason] = message
    return reason


def business_message(reason: str) -> str | None:
    """返回已登记 reason 的对外文案；未登记返回 None（由调用方决定如何失败）。"""
    return BUSINESS_REASON_MAP.get(reason)


def is_business_reason_known(reason: str) -> bool:
    return reason in BUSINESS_REASON_MAP


def reset_mappings() -> None:
    """测试辅助：清空注册表。"""
    FRAMEWORK_ERROR_MAP.clear()
    BUSINESS_REASON_MAP.clear()
