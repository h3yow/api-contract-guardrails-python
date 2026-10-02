"""逐接口错误结构核对所需的「代表性失败请求」定义。

每个 ``ErrorProbe`` 描述一次真实会触发某个接口某种错误状态的请求；
核对器（live_check）要求：文档中声明的每个 (接口, 方法, 错误状态码)
都必须有对应探针，否则不允许给出「通过」结论。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorProbe:
    """一条代表性失败请求。"""

    operation: str            # 路由模板，如 "/items/{item_id}"
    method: str               # 声明该错误的接口方法（文档中 responses 所在方法）
    expected_status: int      # 预期错误状态码
    request_method: str | None = None   # 实际发请求的方法（默认同 method）
    request_path: str | None = None     # 实际请求路径（默认用 operation）
    json_body: Any = None               # 请求体
    note: str = ""

    def key(self) -> tuple[str, str, int]:
        """(接口, 方法, 状态码) 定位键。"""
        return (self.operation, self.method.lower(), self.expected_status)


#: 样例应用的全部代表性失败请求。
#: 文档中每声明一个 (接口, 方法, 错误状态码)，这里都必须有对应探针。
ERROR_PROBES: tuple[ErrorProbe, ...] = (
    ErrorProbe(
        operation="/items",
        method="post",
        expected_status=422,
        json_body={},
        note="请求体缺字段 -> 请求校验失败",
    ),
    ErrorProbe(
        operation="/items",
        method="post",
        expected_status=422,
        json_body={"name": "widget-out", "quantity": 1},
        note="业务规则拒绝（库存不足）",
    ),
    ErrorProbe(
        operation="/items/{item_id}",
        method="get",
        expected_status=422,
        request_path="/items/not-an-int",
        note="路径参数类型错误 -> 请求校验失败",
    ),
    ErrorProbe(
        operation="/items/{item_id}",
        method="get",
        expected_status=422,
        request_path="/items/0",
        note="业务规则拒绝（商品不存在）",
    ),
    ErrorProbe(
        operation="/boom",
        method="get",
        expected_status=500,
        note="未预期异常 -> 脱敏 500",
    ),
)

#: 显式声明「没有可复现的接口级错误请求」的 (路由模板, 方法)。
#: 不在此列、又没有错误声明与探针覆盖的接口会让核对失败。
NO_ERROR_SURFACE: frozenset[tuple[str, str]] = frozenset({("/", "get")})
