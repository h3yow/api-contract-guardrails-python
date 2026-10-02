# 统一错误契约与接口兼容性校验

本服务把**所有入口的失败**归一到同一套对外错误结构，并提供启动期/CI 的
契约守卫，确保「实际响应、对外文档、自动化校验」三者一致、可解释。

## 1. 统一错误响应模型

任何失败响应都返回且只返回如下外壳（HTTP 状态码随 `category` 决定）：

```json
{
  "code": "REQUEST_VALIDATION_FAILED",
  "category": "validation",
  "message": "请求参数校验失败",
  "request_id": "86178631430946208c4177bc45a0da3a",
  "details": [
    {
      "location": ["body", "name"],
      "reason": "missing",
      "message": "Field required"
    }
  ]
}
```

| 字段 | 说明 |
| --- | --- |
| `code` | 对外稳定错误码（字符串枚举），同类错误在不同入口结论一致 |
| `category` | 高层分类，决定默认 HTTP 状态码 |
| `message` | 面向调用方的一句话结论 |
| `request_id` | 请求标识，可在服务端日志中据此定位 |
| `details[]` | 可定位的原因列表：`location` 定位字段，`reason` 是机器可读原因码 |

`request_id` 同时通过响应头 `X-Request-ID` 返回；请求若自带该头则原样复用，
否则服务端生成。**响应头与响应体中的 request_id 始终一致（含 500 场景）。**

### 错误码 ↔ 分类 ↔ HTTP 状态

| code | category | HTTP | 触发入口 |
| --- | --- | --- | --- |
| `REQUEST_VALIDATION_FAILED` | `validation` | 422 | 请求体/查询/路径参数校验失败（pydantic） |
| `ROUTE_NOT_FOUND` | `not_found` | 404 | 路径未匹配任何路由 |
| `METHOD_NOT_ALLOWED` | `method_not_allowed` | 405 | 路径存在但 HTTP 方法不允许 |
| `BUSINESS_RULE_VIOLATION` | `business` | 422 | 业务规则拒绝（显式映射的业务异常） |
| `INTERNAL_ERROR` | `internal` | 500 | 未预期异常 |

不同入口的**同类**错误得到相同 `code`（例如请求体缺字段与路径参数类型错误，
都是 `REQUEST_VALIDATION_FAILED`），但通过 `details[].reason` / `location`
区分具体原因，可定位到具体字段。

### 内部异常脱敏

未预期异常统一返回固定文案的 `INTERNAL_ERROR`，**绝不**把异常消息、异常类型、
堆栈、密钥等写入响应体；这些信息只输出到服务端日志，并附带 `request_id`
与完整堆栈，便于凭请求标识排查复现。

## 2. 业务错误与框架错误的显式映射

业务异常通过 `ErrorMappingRegistry` **显式注册**到错误码：

```python
from app.errors.codes import ErrorCode
from app.errors.exceptions import BusinessError
from app.errors.mapping import registry

class OutOfStockError(BusinessError):
    reason = "out_of_stock"

registry.register(OutOfStockError, ErrorCode.BUSINESS_RULE_VIOLATION)
```

- `BusinessError` 基类默认映射到 `BUSINESS_RULE_VIOLATION`，子类按 MRO 解析；
- **冲突**：同一异常类型注册到不同错误码时，`register` 立即抛
  `ContractMappingError`，在导入/启动期暴露，而不是请求期静默降级；
- **未处理**：`create_app` 通过 `EXPECTED_BUSINESS_ERRORS` 声明“必须已注册”
  的业务异常。任何漏注册都会让**启动守卫失败**；
- 运行期遇到未映射的普通异常会记录到 `registry.unmapped_seen`，
  可通过 `runtime_unmapped_problems()` 巡检；它属于未预期异常（走 500），
  与“业务异常漏注册”严格区分。

## 3. 契约一致性与兼容性校验

### 3.1 文档与实际一致

`ErrorResponse`/`ErrorDetail` 的 OpenAPI 组件直接由 pydantic 模型生成并注入
`components.schemas`，避免手写两份导致漂移。
`validate_documented_error_schema` 会比对文档与真实模型：

- 文档缺少错误模型组件；
- 字段在文档/实际中任一侧缺失；
- 字段类型不一致；
- 必填集合不一致。

任何偏离都会产出带 `location`（定位到
`components.schemas.ErrorResponse.properties.<字段>`）的 finding。

### 3.2 逐接口、逐状态码的运行时核对

`validate_documented_error_schema` 只保证"文档里的错误模型与代码模型自洽"，
但兜不住"某个接口真实返回的错误结构和它对外声明的对不上"。
`verify_error_responses`（`app/contracts/runtime_check.py`）把这条变成硬约束：

**事实来源是真实请求，不是模型定义。** 每个接口登记若干"错误探针"
（`app/contracts/probes.py`，在 `app/api/router.py` 随接口定义一起注册），
每个探针是一次有代表性的失败请求。核对时用 TestClient **真实打接口**，
把真实返回的 JSON 推断成结构，与文档中该接口该状态码声明的结构逐字段比对。

比对是双向、递归的，以下偏离全部判失败：

- 字段多一个（真实返回有、文档没声明）或少一个（文档声明了、真实不返回）；
- 字段类型不同；
- 必填性不同（文档必填但真实未返回，或文档可选但真实始终返回）；
- 以上发生在嵌套对象或数组元素内部（如 `details[].message`），同样算。

每条 finding 直接定位到接口+方法+状态码+字段，例如
`POST /items [422] -> details[].message`。

**覆盖范围（不允许静默通过）：**

- 每个接口至少一个探针；任何接口没有探针 -> 失败；
- 文档声明了某错误状态但没有探针真实核对 -> 失败
  （挡住"文档先写了还没实现的错误结构"）；
- 探针真实返回了某错误状态但文档未声明 -> 失败
  （挡住"实现改了错误结构却忘了同步文档"）。

文档中每个接口的错误响应声明（`paths.*.*.responses.<4xx/5xx>`）由探针计划
自动生成（见 `app/main.py` 的 `_declare_error_responses`），声明与核对同源，
人为改动任何一侧都会被上述规则抓住。404（路径未匹配）属于路由级行为，
不在逐接口声明范围内，由统一归一逻辑与既有测试覆盖。

核对日志（`app.contracts` logger）逐探针打印
`probe=<名> <方法> <路径> [<状态码>] request_id=<标识> ok=<结论>`；
同时 `app.errors` 的归一化日志照常输出 `raw=<原始异常> -> code=<错误码>`，
两侧都带 request_id，可据此复现每一次被核对的失败请求。

### 3.3 版本兼容性判定

`compare_contracts` / `compare_schemas` 对字段级变更分类：

| 变更 | change_type | 结论 |
| --- | --- | --- |
| 新增**可选**字段 | `field_added` | 兼容 |
| 新增**必填**字段 | `field_added` | **破坏性** |
| 删除字段 | `field_removed` | **破坏性** |
| 字段类型变化（含数组元素类型） | `type_changed` | **破坏性** |
| 可选 → 必填 | `became_required` | **破坏性** |
| 必填 → 可选 | `became_optional` | 兼容 |

比较会**递归进入嵌套对象与数组元素内部**：深层字段的新增/删除/类型/
必填变化不会被漏掉，数组元素内部的新增可选字段也只算兼容（不误判）。
深层变更的定位形如
`components.schemas.ErrorResponse.properties.details[].message`。

错误响应声明（`paths.<接口>.<方法>.responses.<4xx/5xx>`）也纳入版本比较：

| 变更 | change_type | 结论 |
| --- | --- | --- |
| 接口**新增声明**一个以前没写过的错误状态 | `error_status_added` | 兼容 |
| **删除**已声明的错误状态 | `error_status_removed` | **破坏性** |
| 已声明错误的**结构变化** | 按上表字段级规则 | 视变更而定 |

结构变化的每条变更都定位到接口+状态码+字段，例如
`paths./items.post.responses.422.details[].message`；
声明为 `$ref` 时会解析引用后递归比较。

破坏性变更会使校验失败，并且每条都带字段定位（如
`components.schemas.Item.properties.name`）与说明；兼容变更不会被误判。

### 3.4 启动守卫

`create_app()` 默认（`APP_ENV != prod`）执行：

1. 错误映射自检（冲突/未注册）；
2. 文档错误模型与真实模型一致性；
3. 逐接口、逐状态码的错误结构运行时核对（真实打接口）；
4. 若提供基线契约，则做兼容性比较。

strict 模式下任何一项失败都会抛 `ContractMappingError` 阻断启动。

## 4. 本地验证方法

```bash
# 安装依赖（含 pytest）
uv sync

# 运行全部单测（含正常/异常入口、映射、契约比较、文档一致性、
# 逐接口运行时核对、CLI、日志）
.venv/bin/python -m pytest

# 只跑逐接口运行时核对（日志里能看到每个探针的 request_id 与归一化结论）
.venv/bin/python -m pytest tests/test_runtime_check.py -v --log-cli-level=INFO

# 本地起服务手工验证五类入口
.venv/bin/uvicorn main:app --port 8099
curl -i localhost:8099/no-such-path                       # 404
curl -i -X DELETE localhost:8099/items                   # 405
curl -i -X POST localhost:8099/items -d '{}' \
  -H 'Content-Type: application/json'                   # 422 validation
curl -i -X POST localhost:8099/items \
  -d '{"name":"widget-out","quantity":1}' \
  -H 'Content-Type: application/json'                   # 422 business
curl -i localhost:8099/boom                              # 500 脱敏

# 契约守卫（映射 + 文档一致性 + 逐接口运行时核对）
.venv/bin/python -m scripts.check_contract

# 与基线契约做兼容性比较；破坏性变更退出码为 1 并定位字段
.venv/bin/python -m scripts.check_contract \
  --baseline tests/contracts/openapi.baseline.json

# 重新生成基线（确认当前版本即新基线时）
.venv/bin/python -c "import json; from app.main import create_app; \
from app.contracts.extractor import extract_openapi; \
json.dump(extract_openapi(create_app(run_checks=False)), \
open('tests/contracts/openapi.baseline.json','w'), \
ensure_ascii=False, indent=2, sort_keys=True)"
```

### 生产环境

设置 `APP_ENV=prod` 时 `create_app` 不在启动时自动跑守卫（避免依赖本地基线），
但应在 CI 中显式执行 `scripts.check_contract` 把关；运行期的错误归一、
脱敏与 request_id 行为在所有环境保持一致。

## 5. 代码地图

- `app/errors/codes.py`：错误码、分类、默认 HTTP 状态；
- `app/errors/models.py`：`ErrorResponse` / `ErrorDetail`；
- `app/errors/exceptions.py`：`BusinessError` / `ContractMappingError`；
- `app/errors/mapping.py`：显式映射注册表（冲突/未注册检测、MRO）；
- `app/errors/normalizer.py`：五类异常归一与脱敏；
- `app/errors/handlers.py`：最外层 request_id ASGI 中间件与异常处理器；
- `app/errors/context.py` / `logging_setup.py`：请求标识与服务端日志；
- `app/contracts/models.py` / `schema.py` / `compare.py`：变更模型与比较规则
  （递归进入嵌套对象与数组，含错误响应声明的状态码级比较）；
- `app/contracts/probes.py` / `runtime_check.py`：逐接口错误探针注册表，
  以及基于真实请求的运行时核对（结构推断、双向精确比对、覆盖率检查）；
- `app/contracts/extractor.py` / `validation.py` / `checker.py`：
  OpenAPI 提取、文档一致性、启动守卫；
- `scripts/check_contract.py`：本地/CI 守卫命令。
