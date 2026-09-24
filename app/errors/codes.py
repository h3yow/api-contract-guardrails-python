"""统一错误码与错误分类。

所有入口（请求校验、路由未匹配、方法不允许、业务拒绝、未预期异常）
都归一到同一套 ``ErrorCode``，保证同类错误结论一致。
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    """错误的高层分类（对外结论稳定，不随框架/内部实现变化）。"""

    VALIDATION = "validation"          # 请求校验失败
    NOT_FOUND = "not_found"            # 路径未匹配 / 资源不存在
    METHOD_NOT_ALLOWED = "method_not_allowed"
    BUSINESS = "business"              # 业务规则拒绝
    INTERNAL = "internal"              # 未预期异常（细节不对外泄露）


class ErrorCode(str, Enum):
    """对外稳定错误码。``value`` 即响应体中的 code 字符串。"""

    REQUEST_VALIDATION_FAILED = "REQUEST_VALIDATION_FAILED"
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    BUSINESS_RULE_VIOLATION = "BUSINESS_RULE_VIOLATION"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# 错误码 -> 分类 的唯一归属关系
CODE_CATEGORY: dict[ErrorCode, ErrorCategory] = {
    ErrorCode.REQUEST_VALIDATION_FAILED: ErrorCategory.VALIDATION,
    ErrorCode.ROUTE_NOT_FOUND: ErrorCategory.NOT_FOUND,
    ErrorCode.METHOD_NOT_ALLOWED: ErrorCategory.METHOD_NOT_ALLOWED,
    ErrorCode.BUSINESS_RULE_VIOLATION: ErrorCategory.BUSINESS,
    ErrorCode.INTERNAL_ERROR: ErrorCategory.INTERNAL,
}

# 分类 -> 默认 HTTP 状态码
CATEGORY_STATUS: dict[ErrorCategory, int] = {
    ErrorCategory.VALIDATION: 422,
    ErrorCategory.NOT_FOUND: 404,
    ErrorCategory.METHOD_NOT_ALLOWED: 405,
    ErrorCategory.BUSINESS: 422,
    ErrorCategory.INTERNAL: 500,
}
