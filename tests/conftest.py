"""pytest 夹具：TestClient、日志捕获与请求标识断言辅助。"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def app():
    # 测试环境跑完整启动守卫（映射自检 + 文档一致性）
    return create_app(run_checks=True)


@pytest.fixture()
def client(app) -> TestClient:
    # raise_server_exceptions=False：让兜底处理器生成统一 500，
    # 而不是 TestClient 直接抛出，便于断言脱敏响应。
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def cap_error_logs(caplog):
    """捕获 app.errors 日志，并暴露按 request_id 检索的能力。"""
    caplog.set_level(logging.WARNING, logger="app.errors")
    return caplog


def assert_log_contains_normalization(caplog, request_id: str, code: str) -> None:
    """断言日志中打印了该请求的 request_id 与归一化后的错误码。"""
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert request_id in text, f"日志缺少 request_id={request_id}: {text}"
    assert code in text, f"日志缺少归一化结果 code={code}: {text}"
