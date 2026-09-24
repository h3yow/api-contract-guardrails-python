"""ASGI request_id 中间件与 FastAPI/Starlette 统一异常处理器。

覆盖入口：
- ``RequestValidationError`` -> 请求校验失败；
- Starlette 404 -> 路径未匹配；
- Starlette 405 -> 方法不允许；
- ``BusinessError`` / 已注册业务异常 -> 业务规则拒绝；
- 其它任意 ``Exception`` -> 未预期异常（500，细节不泄露）。

为什么直接包裹 ``app.middleware_stack``：
FastAPI 构建的栈最外层是 ServerErrorMiddleware（兜底 Exception 生成 500）。
``add_middleware`` 注册的中间件位于它内侧，500 响应会绕过内侧中间件的响应头
回写，造成响应头与响应体 request_id 不一致。因此在 FastAPI 正常构建栈之后，
再于其**最外层**包一层纯 ASGI 中间件，既保留 FastAPI 内部中间件，
又能在任何异常路径（含 500）统一注入 request_id 响应头。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.errors.context import REQUEST_ID_HEADER, ensure_request_id
from app.errors.normalizer import normalize
from app.errors.models import ErrorResponse

STATE_KEY = "request_id"


class RequestIdMiddleware:
    """最外层 ASGI 中间件：确定 request_id 并保证响应头与响应体一致。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin1").lower(): v.decode("latin1")
            for k, v in scope.get("headers", [])
        }
        rid = ensure_request_id(headers.get(REQUEST_ID_HEADER.lower()))
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state[STATE_KEY] = rid

        async def send_with_rid(message):
            if message["type"] == "http.response.start":
                raw = message.setdefault("headers", [])
                if not any(
                    name.decode("latin1").lower() == REQUEST_ID_HEADER.lower()
                    for name, _value in raw
                ):
                    raw.append(
                        (REQUEST_ID_HEADER.encode("latin1"), rid.encode("latin1"))
                    )
            await send(message)

        await self.app(scope, receive, send_with_rid)


def _rid_of(request: Request) -> str | None:
    """读取由最外层中间件写入 scope['state'] 的 request_id。"""
    state = request.scope.get("state")
    if isinstance(state, dict):
        return state.get(STATE_KEY)
    return getattr(state, STATE_KEY, None)


def _error_json(response: ErrorResponse, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
        headers={REQUEST_ID_HEADER: response.request_id},
    )


async def handle_validation(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    body, status = normalize(exc, request_id=_rid_of(request))
    return _error_json(body, status)


async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    body, status = normalize(exc, request_id=_rid_of(request))
    return _error_json(body, status)


async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """兜底：未预期异常，脱敏后返回 500。"""
    body, status = normalize(exc, request_id=_rid_of(request))
    return _error_json(body, status)


def install_error_handlers(app: FastAPI) -> None:
    """注册统一异常处理器，并把 request_id 中间件置于中间件栈最外层。"""
    app.add_exception_handler(RequestValidationError, handle_validation)
    app.add_exception_handler(StarletteHTTPException, handle_http)
    app.add_exception_handler(Exception, handle_unexpected)

    # 触发 FastAPI 按其自身规则构建栈（保留 AsyncExitStackMiddleware 等），
    # 再在最外层包裹本中间件。
    built_stack = app.build_middleware_stack()
    app.middleware_stack = RequestIdMiddleware(built_stack)
