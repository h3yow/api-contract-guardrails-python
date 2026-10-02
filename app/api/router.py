"""示例业务入口：覆盖正常与各类异常入口。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.errors.codes import ErrorCode
from app.errors.exceptions import BusinessError
from app.errors.mapping import registry
from app.errors.models import ErrorResponse

router = APIRouter()

#: 逐接口对外声明的错误响应（进入 OpenAPI，供契约核对与版本比较）
RESP_422 = {422: {"model": ErrorResponse, "description": "请求校验失败或业务规则拒绝"}}
RESP_500 = {500: {"model": ErrorResponse, "description": "未预期异常（已脱敏）"}}


class ItemIn(BaseModel):
    """创建商品的入参（缺字段/类型错误会触发请求校验失败入口）。"""

    name: str = Field(min_length=1, description="商品名称")
    quantity: int = Field(ge=0, description="库存数量")


class OutOfStockError(BusinessError):
    """业务规则拒绝示例：库存不足。"""

    reason = "out_of_stock"


def _register_sample_errors() -> None:
    """把示例业务异常显式注册进全局映射表（幂等）。"""
    registry.register(OutOfStockError, ErrorCode.BUSINESS_RULE_VIOLATION)
    registry.register(BusinessError, ErrorCode.BUSINESS_RULE_VIOLATION)


_register_sample_errors()


@router.get("/")
def read_root() -> dict[str, str]:
    """正常入口。"""
    return {"Hello": "World"}


@router.post("/items", responses=RESP_422)
def create_item(item: ItemIn) -> dict[str, object]:
    """正常入口；特定商品名触发业务规则拒绝。"""
    if item.name == "widget-out":
        raise OutOfStockError(
            "该商品库存不足",
            location=["body", "name"],
        )
    return {"id": 1, "name": item.name, "quantity": item.quantity}


@router.get("/items/{item_id}", responses=RESP_422)
def get_item(item_id: int) -> dict[str, object]:
    """路径参数类型错误（非整数）由框架触发请求校验失败。"""
    if item_id <= 0:
        raise BusinessError(
            "商品不存在", reason="item_not_found", location=["path", "item_id"]
        )
    return {"id": item_id, "name": "widget"}


@router.get("/boom", responses=RESP_500)
def boom() -> dict[str, object]:
    """未预期异常入口：细节不得对外泄露。"""
    raise RuntimeError("simulated internal failure: token=super-secret")
