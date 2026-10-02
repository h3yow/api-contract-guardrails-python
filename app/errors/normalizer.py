"""把各类入口异常归一为统一的 ErrorResponse。

归一来源覆盖五个入口：
1. ``RequestValidationError`` 请求校验失败；
2. Starlette 404 路径未匹配；
3. Starlette 405 方法不允许；
4. ``BusinessError`` / 已注册业务异常（业务规则拒绝）；
5. 其它任意 ``Exception``（未预期异常，脱敏为通用 500）。
"""

from __future__ import annotations

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors.context import new_request_id
from app.errors.codes import CATEGORY_STATUS, CODE_CATEGORY, ErrorCode
from app.errors.exceptions import BusinessError
from app.errors.logging_setup import log_normalized
from app.errors.mapping import registry
from app.errors.models import ErrorDetail, ErrorResponse

# 每个错误码对外的稳定一句话结论
CODE_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.REQUEST_VALIDATION_FAILED: "请求参数校验失败",
    ErrorCode.ROUTE_NOT_FOUND: "请求的资源路径不存在",
    ErrorCode.METHOD_NOT_ALLOWED: "HTTP 方法不被允许",
    ErrorCode.BUSINESS_RULE_VIOLATION: "业务规则校验未通过",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误，请稍后重试",
}

# 未预期异常对外的固定脱敏文案
INTERNAL_DETAIL = ErrorDetail(
    location=["server"],
    reason="internal_error",
    message="服务端处理时发生未预期异常，内部细节已记录并可凭请求标识排查",
)


def _details_from_validation(exc: RequestValidationError) -> list[ErrorDetail]:
    details: list[ErrorDetail] = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ())]
        msg = str(err.get("msg", "校验失败"))
        # pydantic v2 对 value_error 会加 "Value error, " 前缀，去掉更干净
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        details.append(
            ErrorDetail(
                location=loc,
                reason=str(err.get("type", "validation_error")),
                message=msg,
            )
        )
    return details


def normalize(
    exc: BaseException,
    *,
    request_id: str | None = None,
    debug: bool = False,
) -> tuple[ErrorResponse, int]:
    """把任意异常归一为 ``(统一错误体, HTTP 状态码)``。

    内部异常细节与堆栈永不进入对外消息；``debug`` 只影响服务端日志，
    不改变响应体内容。
    """
    rid = request_id or new_request_id()

    code, details, message = _classify(exc)
    category = CODE_CATEGORY[code]
    status = CATEGORY_STATUS[category]

    response = ErrorResponse(
        code=code,
        category=category,
        message=message,
        request_id=rid,
        details=details,
    )

    log_normalized(
        request_id=rid,
        raw=exc,
        normalized_code=code.value,
        status_code=status,
    )
    return response, status


def _classify(
    exc: BaseException,
) -> tuple[ErrorCode, list[ErrorDetail], str]:
    # 1. 请求体/参数校验失败
    if isinstance(exc, RequestValidationError):
        return (
            ErrorCode.REQUEST_VALIDATION_FAILED,
            _details_from_validation(exc),
            CODE_MESSAGE[ErrorCode.REQUEST_VALIDATION_FAILED],
        )

    # 2/3. Starlette 路由级 HTTP 异常
    if isinstance(exc, StarletteHTTPException):
        return _classify_http(exc)

    # 4. 业务规则拒绝：优先显式映射，其次 BusinessError 基类映射
    if isinstance(exc, BusinessError):
        return _classify_business(exc)

    mapped = registry.resolve(exc)
    if mapped is not None:
        return mapped, [], CODE_MESSAGE[mapped]

    # 5. 未预期异常：脱敏，绝不回传 str(exc)/堆栈
    return (
        ErrorCode.INTERNAL_ERROR,
        [INTERNAL_DETAIL],
        CODE_MESSAGE[ErrorCode.INTERNAL_ERROR],
    )


def _classify_http(
    exc: StarletteHTTPException,
) -> tuple[ErrorCode, list[ErrorDetail], str]:
    if exc.status_code == 404:
        code = ErrorCode.ROUTE_NOT_FOUND
        detail = ErrorDetail(
            location=["path"], reason="route_not_found", message="路径未匹配任何路由"
        )
    elif exc.status_code == 405:
        code = ErrorCode.METHOD_NOT_ALLOWED
        detail = ErrorDetail(
            location=["method"],
            reason="method_not_allowed",
            message="该路径不支持当前 HTTP 方法",
        )
    else:
        # 其它主动抛出的 HTTPException 按其状态语义归入通用内部之外的稳定结论
        code = ErrorCode.BUSINESS_RULE_VIOLATION
        detail = ErrorDetail(
            location=[],
            reason=f"http_{exc.status_code}",
            message="请求未被处理",
        )
    return code, [detail], CODE_MESSAGE[code]


def _classify_business(
    exc: BusinessError,
) -> tuple[ErrorCode, list[ErrorDetail], str]:
    code = registry.resolve(exc) or ErrorCode.BUSINESS_RULE_VIOLATION
    detail = ErrorDetail(
        location=list(exc.location),
        reason=exc.reason,
        message=exc.message,
    )
    return code, [detail], exc.message
