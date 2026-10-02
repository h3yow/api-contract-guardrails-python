"""示例业务入口：覆盖正常与各类异常入口。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.contracts.probes import register_probe
from app.errors.codes import ErrorCode
from app.errors.exceptions import BusinessError
from app.errors.mapping import registry

router = APIRouter()


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


def _register_sample_probes() -> None:
    """为每个接口登记有代表性的失败请求（错误结构运行时核对的事实来源）。

    每个接口至少一个探针；405 探针挂在路径主方法下（用另一种方法触发）。
    新增接口时必须同步登记探针，否则启动守卫会因"接口未被核对"而失败。
    """
    # GET / ：只允许 GET，POST 触发 405
    register_probe("get", "/", 405, "method-not-allowed",
                   lambda c: c.post("/"))
    # POST /items ：请求校验失败 + 业务规则拒绝 + 405
    register_probe("post", "/items", 422, "validation-missing-fields",
                   lambda c: c.post("/items", json={}))
    register_probe("post", "/items", 422, "business-out-of-stock",
                   lambda c: c.post("/items",
                                    json={"name": "widget-out", "quantity": 1}))
    register_probe("post", "/items", 405, "method-not-allowed",
                   lambda c: c.get("/items"))
    # GET /items/{item_id} ：路径参数类型错误 + 业务规则拒绝 + 405
    register_probe("get", "/items/{item_id}", 422, "path-param-type-error",
                   lambda c: c.get("/items/abc"))
    register_probe("get", "/items/{item_id}", 422, "business-item-not-found",
                   lambda c: c.get("/items/0"))
    register_probe("get", "/items/{item_id}", 405, "method-not-allowed",
                   lambda c: c.post("/items/1"))
    # GET /boom ：未预期异常（500 脱敏）+ 405
    register_probe("get", "/boom", 500, "unexpected-error",
                   lambda c: c.get("/boom"))
    register_probe("get", "/boom", 405, "method-not-allowed",
                   lambda c: c.post("/boom"))


_register_sample_probes()


@router.get("/")
def read_root() -> dict[str, str]:
    """正常入口。"""
    return {"Hello": "World"}


@router.post("/items")
def create_item(item: ItemIn) -> dict[str, object]:
    """正常入口；特定商品名触发业务规则拒绝。"""
    if item.name == "widget-out":
        raise OutOfStockError(
            "该商品库存不足",
            location=["body", "name"],
        )
    return {"id": 1, "name": item.name, "quantity": item.quantity}


@router.get("/items/{item_id}")
def get_item(item_id: int) -> dict[str, object]:
    """路径参数类型错误（非整数）由框架触发请求校验失败。"""
    if item_id <= 0:
        raise BusinessError(
            "商品不存在", reason="item_not_found", location=["path", "item_id"]
        )
    return {"id": item_id, "name": "widget"}


@router.get("/boom")
def boom() -> dict[str, object]:
    """未预期异常入口：细节不得对外泄露。"""
    raise RuntimeError("simulated internal failure: token=super-secret")
