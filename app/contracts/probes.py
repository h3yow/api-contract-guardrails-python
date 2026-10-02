"""逐接口错误探针：为每个接口登记"有代表性的失败请求"。

探针是"真实跑接口核对错误结构"的事实来源：
- 每个探针绑定到 ``(method, path)`` 接口与预期错误状态码；
- ``request`` 拿到 TestClient 后真实发起一次会失败的请求；
- OpenAPI 文档中每个接口的错误响应声明由探针计划生成，
  因此"文档声明的错误状态"与"被真实核对的错误状态"天然一一对应；
  任何一侧被人为改动（文档多写/漏写、接口漏探针）都会被覆盖率检查发现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi.testclient import TestClient
from httpx import Response


@dataclass(frozen=True)
class ErrorProbe:
    """一次有代表性的失败请求。"""

    method: str  # 接口方法（小写），如 "post"
    path: str  # 路由模板，如 "/items/{item_id}"
    status: int  # 预期错误状态码，如 422
    name: str  # 探针名（日志/定位用）
    request: Callable[[TestClient], Response]  # 真实发起失败请求


_PROBES: list[ErrorProbe] = []


def register_probe(
    method: str,
    path: str,
    status: int,
    name: str,
    request: Callable[[TestClient], Response],
) -> None:
    """登记一个错误探针（幂等：同 method/path/status/name 不重复）。"""
    probe = ErrorProbe(
        method=method.lower(), path=path, status=status, name=name, request=request
    )
    if probe not in _PROBES:
        _PROBES.append(probe)


def all_probes() -> list[ErrorProbe]:
    """返回当前登记的全部探针。"""
    return list(_PROBES)


def declared_statuses() -> dict[tuple[str, str], set[int]]:
    """按 ``(method, path)`` 聚合探针覆盖的错误状态码集合。"""
    out: dict[tuple[str, str], set[int]] = {}
    for p in _PROBES:
        out.setdefault((p.method, p.path), set()).add(p.status)
    return out
