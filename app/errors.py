"""统一对外错误结构、错误码与业务异常定义。

对外错误响应固定结构::

    {
      "error": {
        "code": "VALIDATION_FAILED",
        "message": "请求参数校验失败",
        "request_id": "req-...",
        "details": [{"loc": "body.name", "reason": "..."}]
      }
    }

任何入口（请求校验、404、405、业务拒绝、未预期异常）都归一到该结构，
内部异常堆栈绝不放入响应体，只通过 request_id 在服务端日志中定位。
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    """对外统一错误码；同类错误在任何入口下结论一致。"""

    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    BUSINESS_REJECTED = "BUSINESS_REJECTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    """单条可区分、可定位的错误原因。"""

    loc: str = Field(..., description="错误定位，如 body.email、path:/missing、reason:order_locked")
    reason: str = Field(..., description="具体原因，不包含内部堆栈")


class ErrorBody(BaseModel):
    """对外统一错误响应体。"""

    code: ErrorCode
    message: str
    request_id: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorEnvelope(BaseModel):
    """错误响应最外层信封，与正常数据响应明确区分。"""

    error: ErrorBody


class BusinessError(Exception):
    """业务规则拒绝异常。

    ``reason`` 是稳定的机器可读原因（必须在显式映射表中登记），
    用于区分同一类业务拒绝的不同触发点。
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        self.message = message or reason
        super().__init__(f"{reason}: {self.message}")
