"""应用装配：显式登记映射、注册统一异常处理器、声明文档中的错误结构。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import BusinessError, ErrorCode, ErrorEnvelope
from app.handlers import (
    business_error_handler,
    http_exception_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from app.mapping import declare_business_reason, register_framework_error
from app.validation import run_startup_validation

# --- 显式映射登记（启动期执行，冲突立即抛错） ---
register_framework_error(
    RequestValidationError, ErrorCode.VALIDATION_FAILED, "请求参数校验失败"
)
register_framework_error(
    StarletteHTTPException, ErrorCode.NOT_FOUND, "请求无法处理"
)
register_framework_error(Exception, ErrorCode.INTERNAL_ERROR, "服务内部错误")

# --- 业务 reason 显式登记 ---
ORDER_LOCKED = declare_business_reason("order_locked", "订单已锁定，不允许修改")

ERROR_RESPONSES = {
    409: {"model": ErrorEnvelope, "description": "业务规则拒绝"},
    422: {"model": ErrorEnvelope, "description": "请求参数校验失败"},
    500: {"model": ErrorEnvelope, "description": "服务内部错误"},
}


class OrderIn(BaseModel):
    item: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class OrderOut(BaseModel):
    order_id: str
    item: str
    quantity: int


def create_app(validate: bool = True) -> FastAPI:
    app = FastAPI(title="api-contract-guardrails", version="1.0.0")

    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

    @app.get("/")
    def read_root() -> dict[str, str]:
        return {"Hello": "World"}

    @app.post("/orders", response_model=OrderOut, responses=ERROR_RESPONSES)
    def create_order(payload: OrderIn) -> OrderOut:
        if payload.item == "locked":
            raise BusinessError(ORDER_LOCKED)
        return OrderOut(order_id="ord-1", item=payload.item, quantity=payload.quantity)

    @app.get("/boom", responses={500: ERROR_RESPONSES[500]})
    def boom() -> None:
        raise RuntimeError("something broke internally")

    if validate:
        run_startup_validation(app)
    return app


app = create_app()
