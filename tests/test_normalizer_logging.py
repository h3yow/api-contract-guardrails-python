"""归一化单测：直接对异常归一，断言日志包含 request_id 与归一化前后结果。"""

from __future__ import annotations

import logging

from fastapi.exceptions import RequestValidationError

from app.errors.normalizer import normalize


def test_log_records_request_id_and_before_after(caplog) -> None:
    exc = RequestValidationError(
        [{"loc": ("body", "age"), "msg": "Field required", "type": "missing"}]
    )
    with caplog.at_level(logging.WARNING, logger="app.errors"):
        resp, status = normalize(exc, request_id="unit-rid-1")

    assert resp.request_id == "unit-rid-1" and status == 422
    text = "\n".join(r.getMessage() for r in caplog.records)
    # 归一化前（原始异常类型/内容）与归一化后（错误码/状态）都要打印
    assert "unit-rid-1" in text
    assert "RequestValidationError" in text          # 归一化前
    assert "REQUEST_VALIDATION_FAILED" in text      # 归一化后
    assert "status=422" in text


def test_log_includes_traceback_for_internal_only(caplog) -> None:
    secret = RuntimeError("kaboom-internal")
    with caplog.at_level(logging.DEBUG, logger="app.errors"):
        resp, status = normalize(secret, request_id="unit-rid-2")
    assert status == 500
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "unit-rid-2" in text and "kaboom-internal" in text
    # 但对外响应不含内部信息
    assert "kaboom-internal" not in resp.model_dump_json()
