# 统一错误契约与接口兼容性校验

## 1. 对外错误模型

所有失败响应（无论来自哪个入口）统一为同一结构：

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "请求参数校验失败",
    "request_id": "req-1ffaa2ce88ed",
    "details": [{"loc": "body.quantity", "reason": "Input should be greater than or equal to 1"}]
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `code` | 机器可读错误码，同类错误在任何入口下结论一致（见下表） |
| `message` | 面向调用方的稳定文案，来自显式映射登记 |
| `request_id` | 请求标识：优先透传 `X-Request-ID` 请求头，否则服务端生成并随响应头返回 |
| `details` | 可区分、可定位的原因列表；`loc` 形如 `body.field`、`path:/x`、`method:POST`、`reason:order_locked` |

错误码与入口的对应关系：

| 入口 | HTTP 状态 | code |
| --- | --- | --- |
| 请求参数校验失败 | 422 | `VALIDATION_FAILED` |
| 路径未匹配 | 404 | `NOT_FOUND` |
| 方法不允许 | 405 | `METHOD_NOT_ALLOWED` |
| 业务规则拒绝 | 409 | `BUSINESS_REJECTED` |
| 未预期异常 | 500 | `INTERNAL_ERROR` |

**泄露防护**：内部异常类型、消息与堆栈只写入服务端日志（`app.errors` logger，
并附带 `request_id`），绝不出现在响应体中。每条日志同时记录归一化前的原始
异常与归一化后的对外结构，便于按 `request_id` 复现。

## 2. 显式映射规则（`app/mapping.py`）

* 框架异常（`RequestValidationError`、`HTTPException`、`Exception`）必须通过
  `register_framework_error` 显式登记到统一错误码；业务原因必须通过
  `declare_business_reason` 登记对外文案后才能 `raise BusinessError(reason)`。
* **冲突**：同一异常类型/同一 reason 被登记到不同结论时，立即抛出
  `MappingConflictError`，不采用后注册者。
* **缺失**：启动时 `run_startup_validation(app)` 检查必需映射是否齐全，缺失即
  启动失败并逐项指出；运行期出现未登记的业务 reason 时，不静默降级为普通业务
  失败，而是以 `INTERNAL_ERROR` + `reason:unregistered` 对外并在日志中告警。
* **文档一致性**：`validate_openapi_consistency(app)` 校验 OpenAPI 文档中所有
  4xx/5xx 响应均使用 `ErrorEnvelope`，且文档中的组件结构与实际模型一致；
  偏离会在启动校验或 CI 中被捕获。

## 3. 契约兼容性判定规则（`app/contract.py`）

对同一接口模型的两个 JSON Schema 版本做字段级比较：

| 变化 | 判定 |
| --- | --- |
| 字段删除 | 破坏性 |
| 新增必填字段 | 破坏性 |
| 新增可选字段 | 兼容性 |
| 字段类型变化（含数组元素类型、`$ref` 目标） | 破坏性 |
| 可选 → 必填（约束收紧） | 破坏性 |
| 必填 → 可选（约束放宽） | 兼容性 |

存在任一破坏性变化时校验失败，错误信息定位到具体字段（嵌套对象使用点分路径，
如 `addr.city`）；纯兼容性变化不得被误判为破坏性。

## 4. 本地验证方法

```bash
# 安装依赖
uv sync

# 运行全部测试（日志中可见 request_id 与归一化前后结果）
uv run pytest -q

# 校验映射完整性与文档一致性（应用启动时也会自动执行）
uv run python -m app.cli check-mappings

# 比较两个契约版本（破坏性变更时退出码为 1 并定位字段）
uv run python -m app.cli diff-contract examples/order_v1.json examples/order_v2_compatible.json
uv run python -m app.cli diff-contract examples/order_v1.json examples/order_v2_breaking.json

# 启动服务
uv run uvicorn main:app --reload
```
