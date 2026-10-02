# api-contract-guardrails-python

在 FastAPI 服务上建立统一错误契约与接口兼容性校验：

- 五类入口（请求校验失败 / 404 / 405 / 业务拒绝 / 未预期异常）归一到同一错误结构；
- 业务异常到错误码的**显式映射**，冲突与漏注册在启动/校验期暴露，不静默降级；
- OpenAPI 文档中的错误模型与实际响应自动一致性校验；
- 逐接口、逐状态码的真实响应核对：文档声明的错误结构必须和真实返回一致，
  未被核对的接口/状态码一律不放行；
- 契约版本字段级比较（含嵌套对象/数组与逐接口错误声明），
  区分新增/删除/类型变化/必填性变化，破坏性变更可定位并使校验失败；
- 内部异常与堆栈不对外泄露，所有响应带可定位的 `X-Request-ID`。

## 快速开始

```bash
uv sync
.venv/bin/python -m pytest                 # 全量单测
.venv/bin/uvicorn main:app --port 8099     # 本地起服务
.venv/bin/python -m scripts.check_contract # 契约守卫
```

详见 [docs/error-contract.md](docs/error-contract.md)。
