"""请求级上下文：生成并传播可定位的 request_id。"""

from __future__ import annotations

import contextvars
import uuid

# 当前请求标识；未在请求上下文时为 None
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

REQUEST_ID_HEADER = "X-Request-ID"


def new_request_id() -> str:
    """生成一个新的请求标识（短 UUID，无歧义、可检索）。"""
    return uuid.uuid4().hex


def current_request_id() -> str | None:
    """读取当前请求标识（可能为 None）。"""
    return request_id_var.get()


def ensure_request_id(inbound: str | None) -> str:
    """优先复用调用方传入的 request_id，否则生成新的并写入上下文。

    返回最终生效的 request_id。
    """
    rid = (inbound or "").strip() or new_request_id()
    request_id_var.set(rid)
    return rid
