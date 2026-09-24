"""覆盖五类入口的错误归一：校验/404/405/业务/未预期。

每类都断言：
- 统一外壳结构字段齐全；
- 错误码/分类/HTTP 状态正确且不同入口结论一致；
- 原因可区分（reason/location）；
- 响应头与响应体里都有可定位的 request_id；
- 服务端日志打印了 request_id 与归一化结果。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import assert_log_contains_normalization

REQUIRED_KEYS = {"code", "category", "message", "request_id", "details"}


def _check_envelope(body: dict) -> None:
    assert REQUIRED_KEYS == set(body.keys()), body
    assert isinstance(body["details"], list) and body["request_id"]


def test_validation_error_normalized(client: TestClient, cap_error_logs) -> None:
    # 缺必填字段 name -> 请求校验失败
    resp = client.post("/items", json={"quantity": 1},
                       headers={"X-Request-ID": "req-validation-1"})
    assert resp.status_code == 422
    body = resp.json()
    _check_envelope(body)
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["category"] == "validation"
    assert body["request_id"] == "req-validation-1"
    assert resp.headers["X-Request-ID"] == "req-validation-1"
    # 可定位到具体字段与可区分原因
    detail = next(d for d in body["details"] if d["location"] == ["body", "name"])
    assert detail["reason"] == "missing"
    assert_log_contains_normalization(
        cap_error_logs, "req-validation-1", "REQUEST_VALIDATION_FAILED"
    )


def test_validation_error_consistent_across_entries(
    client: TestClient,
) -> None:
    """请求体校验与路径参数校验是不同入口，但归一结论必须一致。"""
    r1 = client.post("/items", json={"name": "x", "quantity": -1})
    r2 = client.get("/items/not-an-int")
    assert r1.status_code == r2.status_code == 422
    assert r1.json()["code"] == r2.json()["code"] == "REQUEST_VALIDATION_FAILED"
    # 但原因（reason）不同，可区分
    assert r1.json()["details"][0]["reason"] in {
        "greater_than_equal",
        "ge",
    }
    assert r2.json()["details"][0]["location"] == ["path", "item_id"]


def test_route_not_found_normalized(client: TestClient, cap_error_logs) -> None:
    resp = client.get("/no-such-path", headers={"X-Request-ID": "req-404"})
    assert resp.status_code == 404
    body = resp.json()
    _check_envelope(body)
    assert body["code"] == "ROUTE_NOT_FOUND"
    assert body["category"] == "not_found"
    assert body["details"][0]["reason"] == "route_not_found"
    assert body["request_id"] == resp.headers["X-Request-ID"] == "req-404"
    assert_log_contains_normalization(cap_error_logs, "req-404", "ROUTE_NOT_FOUND")


def test_method_not_allowed_normalized(client: TestClient, cap_error_logs) -> None:
    # /items 只注册了 POST
    resp = client.put("/items", json={}, headers={"X-Request-ID": "req-405"})
    assert resp.status_code == 405
    body = resp.json()
    _check_envelope(body)
    assert body["code"] == "METHOD_NOT_ALLOWED"
    assert body["category"] == "method_not_allowed"
    assert body["details"][0]["reason"] == "method_not_allowed"
    assert_log_contains_normalization(cap_error_logs, "req-405", "METHOD_NOT_ALLOWED")


def test_business_error_normalized(client: TestClient, cap_error_logs) -> None:
    resp = client.post(
        "/items",
        json={"name": "widget-out", "quantity": 3},
        headers={"X-Request-ID": "req-biz"},
    )
    assert resp.status_code == 422
    body = resp.json()
    _check_envelope(body)
    assert body["code"] == "BUSINESS_RULE_VIOLATION"
    assert body["category"] == "business"
    assert body["details"][0]["reason"] == "out_of_stock"
    assert body["details"][0]["location"] == ["body", "name"]
    assert_log_contains_normalization(
        cap_error_logs, "req-biz", "BUSINESS_RULE_VIOLATION"
    )


def test_unexpected_error_hides_details(client: TestClient, cap_error_logs) -> None:
    resp = client.get("/boom", headers={"X-Request-ID": "req-500"})
    assert resp.status_code == 500
    body = resp.json()
    _check_envelope(body)
    assert body["code"] == "INTERNAL_ERROR"
    assert body["category"] == "internal"
    assert body["request_id"] == resp.headers["X-Request-ID"] == "req-500"
    # 内部细节（token/异常类型/堆栈）绝不外泄
    raw = resp.text
    assert "super-secret" not in raw
    assert "RuntimeError" not in raw
    assert "Traceback" not in raw
    assert body["details"][0]["reason"] == "internal_error"
    assert_log_contains_normalization(cap_error_logs, "req-500", "INTERNAL_ERROR")
    # 服务端日志仍保留内部细节以便排查
    log_text = "\n".join(r.getMessage() for r in cap_error_logs.records)
    assert "req-500" in log_text and "RuntimeError" in log_text


def test_request_id_generated_when_absent(client: TestClient) -> None:
    resp = client.get("/no-such-path")
    rid = resp.json()["request_id"]
    assert rid and resp.headers["X-Request-ID"] == rid


def test_happy_path(client: TestClient) -> None:
    resp = client.post("/items", json={"name": "widget", "quantity": 2})
    assert resp.status_code == 200
    assert resp.json() == {"id": 1, "name": "widget", "quantity": 2}
    assert resp.headers.get("X-Request-ID")
