"""服务端错误日志：打印 request_id 与归一化前/后的错误结果。

这些日志只在服务端输出，绝不写入对外响应体。
"""

from __future__ import annotations

import logging
import traceback

logger = logging.getLogger("app.errors")


def log_normalized(
    *,
    request_id: str,
    raw: BaseException,
    normalized_code: str,
    status_code: int,
) -> None:
    """记录原始异常与归一化结论，便于复现。

    对未预期异常输出完整堆栈（仅日志侧）；对外响应仍保持脱敏。
    """
    raw_type = type(raw).__name__
    raw_msg = str(raw)
    logger.warning(
        "request_id=%s error_normalized raw=%s:%r -> code=%s status=%d",
        request_id,
        raw_type,
        raw_msg,
        normalized_code,
        status_code,
    )
    if status_code >= 500:
        logger.error(
            "request_id=%s unhandled exception traceback:\n%s",
            request_id,
            "".join(traceback.format_exception(type(raw), raw, raw.__traceback__)),
        )
