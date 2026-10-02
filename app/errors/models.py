"""对外统一错误响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.errors.codes import ErrorCategory, ErrorCode


class ErrorDetail(BaseModel):
    """单条可定位的错误原因。

    - ``location``：字段定位（如 ["body", "age"]；路由级错误为 ["path"]）。
    - ``reason``：机器可读的原因码（如 "missing"、"value_error"、"out_of_stock"）。
    - ``message``：面向调用方的说明，不含内部堆栈/SQL 等敏感细节。

    三个字段在真实响应中始终出现（location 可为空数组），因此全部声明为
    必填——文档必须精确描述真实返回，这是逐接口严格核对的前提。
    """

    location: list[str]
    reason: str
    message: str


class ErrorResponse(BaseModel):
    """所有失败响应的唯一外壳结构。

    五个字段在真实响应中始终出现（details 可为空数组），全部必填。
    """

    code: ErrorCode
    category: ErrorCategory
    message: str
    request_id: str
    details: list[ErrorDetail]
