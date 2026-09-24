"""正常与异常入口的统一错误契约测试。"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.errors import ErrorCode
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_normal_entry(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"Hello": "World"}


def test_normal_business_entry(client: TestClient) -> None:
    resp = client.post("/orders", json={"item": "book", "quantity": 2})
    assert resp.status_code == 200
    assert resp.json()["order_id"] == "ord-1"


def test_validation_failed(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.errors"):
        resp = client.post(
            "/orders",
            json={"item": "", "quantity": 0},
            headers={"X-Request-ID": "req-test-validation"},
        )
    body = resp.json()["error"]
    assert resp.status_code == 422
    assert body["code"] == ErrorCode.VALIDATION_FAILED
    assert body["request_id"] == "req-test-validation"
    # 原因可区分、可定位到字段
    locs = {d["loc"] for d in body["details"]}
    assert locs == {"body.item", "body.quantity"}
    # 日志包含请求标识与归一化前后的错误结果
    assert "req-test-validation" in caplog.text
    assert "RequestValidationError" in caplog.text
    assert "VALIDATION_FAILED" in caplog.text


def test_not_found(client: TestClient) -> None:
    resp = client.get("/no-such-path")
    body = resp.json()["error"]
    assert resp.status_code == 404
    assert body["code"] == ErrorCode.NOT_FOUND
    assert body["details"][0]["loc"] == "path:/no-such-path"
    assert body["request_id"]


def test_method_not_allowed(client: TestClient) -> None:
    resp = client.post("/")
    body = resp.json()["error"]
    assert resp.status_code == 405
    assert body["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert body["details"][0]["loc"] == "method:POST"


def test_business_rejected(client: TestClient) -> None:
    resp = client.post("/orders", json={"item": "locked", "quantity": 1})
    body = resp.json()["error"]
    assert resp.status_code == 409
    assert body["code"] == ErrorCode.BUSINESS_REJECTED
    # 业务原因可区分
    assert body["details"][0]["loc"] == "reason:order_locked"
    assert body["message"] == "订单已锁定，不允许修改"


def test_unexpected_error_no_stack_leak(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.errors"):
        resp = client.get("/boom", headers={"X-Request-ID": "req-test-boom"})
    body = resp.json()["error"]
    assert resp.status_code == 500
    assert body["code"] == ErrorCode.INTERNAL_ERROR
    assert body["request_id"] == "req-test-boom"
    # 内部异常细节与堆栈不得对外泄露
    raw = resp.text
    assert "something broke internally" not in raw
    assert "Traceback" not in raw
    assert "RuntimeError" not in raw
    # 但服务端日志中可按请求标识定位
    assert "req-test-boom" in caplog.text


def test_request_id_generated_when_absent(client: TestClient) -> None:
    resp = client.get("/missing")
    body = resp.json()["error"]
    assert body["request_id"].startswith("req-")
    assert resp.headers["X-Request-ID"] == body["request_id"]


def test_error_codes_distinguishable(client: TestClient) -> None:
    """同类结构下，不同入口的错误码与原因必须可区分。"""
    codes = {
        client.post("/orders", json={"item": "", "quantity": 0}).json()["error"]["code"],
        client.get("/missing").json()["error"]["code"],
        client.post("/").json()["error"]["code"],
        client.post("/orders", json={"item": "locked", "quantity": 1}).json()["error"][
            "code"
        ],
        client.get("/boom").json()["error"]["code"],
    }
    assert codes == {
        "VALIDATION_FAILED",
        "NOT_FOUND",
        "METHOD_NOT_ALLOWED",
        "BUSINESS_REJECTED",
        "INTERNAL_ERROR",
    }
