"""业务异常基类与通用框架异常。

业务方通过抛出/注册这些异常，显式声明它对应哪个对外错误码。
"""

from __future__ import annotations

from app.errors.codes import ErrorCode


class BusinessError(Exception):
    """所有可预期业务拒绝的基类。

    子类通过类属性 ``error_code`` / ``reason`` 建立与错误契约的显式映射。
    """

    error_code: ErrorCode = ErrorCode.BUSINESS_RULE_VIOLATION
    reason: str = "business_rule_violation"

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        location: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if reason is not None:
            self.reason = reason
        self.location: list[str] = list(location or [])


class ContractMappingError(Exception):
    """错误映射配置本身有问题（未注册 / 冲突 / 不完整）。

    这是**启动期/校验期**错误：不得在请求期被静默降级为通用失败。
    """
