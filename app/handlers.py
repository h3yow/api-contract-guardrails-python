"""异常归一化处理器。

每个处理器都做三件事：

1. 取得/生成 ``request_id``（优先透传 ``X-Request-ID`` 头）；
2. 在服务端日志中同时记录归一化前的原始异常与归一化后的对外结构，
   便于按请求标识复现；
3. 返回统一 :class:`~app.errors.ErrorEnvelope` 结构，绝不包含堆栈或
   内部异常细节。
"""
from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import BusinessError, ErrorBody, ErrorCode, ErrorDetail, ErrorEnvelope
from app.mapping import FRAMEWORK_ERROR_MAP, business_message, is_business_reason_known

logger = logging.getLogger("app.errors")

REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id(request: Request) -> str:
    """优先使用调用方传入的请求标识，否则生成一个。"""
    incoming = request.headers.get(REQUEST_ID_HEADER)
    if incoming:
        return incoming
    return f"req-{uuid.uuid4().hex[:12]}"


def _framework_message(exc_type: type[Exception], fallback: str) -> str:
    entry = FRAMEWORK_ERROR_MAP.get(exc_type)
    return entry[1] if entry else fallback


def _emit(
    request: Request,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    details: list[ErrorDetail],
    raw: BaseException,
) -> JSONResponse:
    """记录归一化前后结果并构造统一响应。"""
    request_id = get_request_id(request)
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=code, message=message, request_id=request_id, details=details
        )
    )
    logger.info(
        "request_id=%s raw_error=%s: %s normalized=%s",
        request_id,
        type(raw).__name__,
        raw,
        envelope.model_dump_json(),
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers={REQUEST_ID_HEADER: request_id},
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = [
        ErrorDetail(
            loc=".".join(str(part) for part in err.get("loc", ())),
            reason=str(err.get("msg", "invalid")),
        )
        for err in exc.errors()
    ]
    return _emit(
        request,
        status_code=422,
        code=ErrorCode.VALIDATION_FAILED,
        message=_framework_message(RequestValidationError, "请求参数校验失败"),
        details=details,
        raw=exc,
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    if exc.status_code == 404:
        code, detail_loc = ErrorCode.NOT_FOUND, f"path:{request.url.path}"
    elif exc.status_code == 405:
        code, detail_loc = ErrorCode.METHOD_NOT_ALLOWED, f"method:{request.method}"
    else:
        code, detail_loc = ErrorCode.INTERNAL_ERROR, f"status:{exc.status_code}"
    return _emit(
        request,
        status_code=exc.status_code if code != ErrorCode.INTERNAL_ERROR else 500,
        code=code,
        message=_framework_message(StarletteHTTPException, "请求无法处理"),
        details=[ErrorDetail(loc=detail_loc, reason=str(exc.detail))],
        raw=exc,
    )


async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    message = business_message(exc.reason)
    if message is None:
        # 未登记的 reason 属于配置缺陷：不得静默降级为普通业务失败，
        # 以 INTERNAL_ERROR 对外并在日志中明确指出。
        logger.error(
            "request_id=%s 未登记的业务 reason: %r，请通过 declare_business_reason 登记",
            get_request_id(request),
            exc.reason,
        )
        return _emit(
            request,
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="服务内部错误",
            details=[ErrorDetail(loc="reason:unregistered", reason="业务原因未登记")],
            raw=exc,
        )
    return _emit(
        request,
        status_code=409,
        code=ErrorCode.BUSINESS_REJECTED,
        message=message,
        details=[ErrorDetail(loc=f"reason:{exc.reason}", reason=exc.message)],
        raw=exc,
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = get_request_id(request)
    # 堆栈只进服务端日志，绝不出现在响应体。
    logger.exception("request_id=%s 未预期异常", request_id, exc_info=exc)
    return _emit(
        request,
        status_code=500,
        code=ErrorCode.INTERNAL_ERROR,
        message=_framework_message(Exception, "服务内部错误"),
        details=[],
        raw=exc,
    )
