"""对外统一错误响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.errors.codes import ErrorCategory, ErrorCode


class ErrorDetail(BaseModel):
    """单条可定位的错误原因。

    - ``location``：字段定位（如 ["body", "age"]；路由级错误为 ["path"]）。
    - ``reason``：机器可读的原因码（如 "missing"、"value_error"、"out_of_stock"）。
    - ``message``：面向调用方的说明，不含内部堆栈/SQL 等敏感细节。
    """

    location: list[str] = Field(default_factory=list)
    reason: str
    message: str


class ErrorResponse(BaseModel):
    """所有失败响应的唯一外壳结构。"""

    code: ErrorCode
    category: ErrorCategory
    message: str
    request_id: str
    details: list[ErrorDetail] = Field(default_factory=list)
