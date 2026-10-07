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

### 3.2 逐接口、逐状态码的真实响应核对

仅靠模型自洽不够：文档声明的错误结构必须和**每个接口真实返回**的结构一致。
每个接口通过路由的 `responses=` 逐状态码声明错误响应（进入 OpenAPI 的
`paths.<path>.<method>.responses.<status>`），`app/contracts/probes.py`
为每个声明的 `(接口, 方法, 错误状态码)` 定义代表性失败请求（`ErrorProbe`）。

`validate_live_error_structures`（启动守卫与 CI 都会执行）对每条探针
**真实发请求**，把拿到的错误响应与文档声明逐字段递归核对
（含嵌套对象与数组元素），以下任一情况都会失败并定位到
`方法 路径 状态码.字段`（如 `POST /items 422.details[0].reason`）：

- 字段多一个/少一个，**两个方向都兜**：
  - 真实响应含文档未声明的字段（实现超前于文档）；
  - 文档声明了字段但该状态码的代表性真实返回中**不存在**该字段
    （文档超前于实现）。**字段标成必填还是可选不影响这条判定**：
    可选只表示「调用方解析时该字段可能缺席」，不表示「允许文档凭空
    声明一个实现从不返回的字段」。文档里写了的字段，就必须能在
    真实错误返回里核对到；
- 字段类型不一致（含嵌套对象、数组元素内部）；
- 接口真实返回了文档未声明的错误状态（实现改了没同步文档）；
- 文档声明了错误状态但没有任何探针核对它（文档超前于实现也算在内）；
- 接口完全没有错误声明且未在 `NO_ERROR_SURFACE` 中显式豁免。

**递归覆盖的层级**：上述「文档写了、真实没返回」与「真实多了」两条
字段级判定都随同一套递归进入深层结构，不仅看最外层：

- 嵌套对象：`POST /items 422.error.context.trace_id` 这类深层路径
  缺失/多出都会被定位；
- 数组元素：按实际下标核对每个元素，路径形如
  `POST /items 422.details[0].reason`；
- 数组元素内部再嵌对象：路径继续下钻，如
  `GET /wobble 500.details[0].meta.doc_only`。

也就是说，只有「文档声明的字段集合（含深层）与真实返回逐字段、
逐类型完全对得上」时该探针才算核对通过，少声明、多声明、标错类型
都会失败，不会因字段可选而漏报，也不会对完全一致的正常样例误报。

**覆盖范围**：所有声明了错误状态的接口方法逐状态码核对；`GET /` 这类
没有可复现接口级错误请求的入口需在 `NO_ERROR_SURFACE` 显式登记，
否则按「未覆盖」处理，不允许给出「通过」结论。框架级 404/405
不属于单个接口的声明，由错误归一化测试覆盖。

每条探针的核对结论都会带着 `request_id` 写入日志（`app.contracts`），
与 `app.errors` 的归一化日志（原始异常 -> 归一化错误码）对照即可复现。

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

破坏性变更会使校验失败，并且每条都带字段定位（如
`components.schemas.Item.properties.name`）与说明；兼容变更不会被误判。

比较会**递归进入嵌套对象与对象数组**：嵌套字段以 `a.b`、数组元素字段以
`items[].x` 定位，深层字段的新增/删除/类型/必填性变化按上表同一套规则判定，
既不会漏掉也不会被误判成破坏性。

错误响应声明同样纳入版本比较（`compare_error_declarations`，由
`compare_contracts` 自动聚合），规则：

| 变更 | change_type | 结论 |
| --- | --- | --- |
| 接口**新增**声明一个以前没写过的错误状态码 | `error_status_added` | 兼容 |
| **删除**已声明的错误状态码 | `error_status_removed` | **破坏性** |
| 已声明错误的结构变化 | 按字段级规则（含嵌套）递归判定 | 同上表 |

每条变更都定位到接口加状态码（如 `paths./items.post.responses.422`），
结构差异进一步定位到字段（如
`paths./items.post.responses.422.details[].reason`）。

### 3.4 启动守卫

`create_app()` 默认（`APP_ENV != prod`）执行：

1. 错误映射自检（冲突/未注册）；
2. 文档错误模型与真实模型一致性；
3. 逐接口真实响应核对（探针真实发请求，见 3.2）；
4. 若提供基线契约，则做兼容性比较（含逐接口错误声明）。

strict 模式下任何一项失败都会抛 `ContractMappingError` 阻断启动。

## 4. 本地验证方法

```bash
# 安装依赖（含 pytest）
uv sync

# 运行全部单测（默认 -v：按业务文件分组列出每个用例与通过/失败情况；
# 覆盖正常/异常入口、映射、契约比较、逐接口真实响应核对、CLI、日志）
.venv/bin/python -m pytest

# 只看逐接口核对的用例（含「文档多声明必填/可选字段而真实缺失」、
# 嵌套对象/数组元素内部同类问题的回归用例；--log-cli-level=INFO 可看
# request_id 与归一化前/后的错误结论）
.venv/bin/python -m pytest tests/test_live_check.py -v --log-cli-level=INFO

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

# 契约守卫（映射 + 文档一致性）
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
  （含嵌套递归与逐接口错误声明比较）；
- `app/contracts/extractor.py` / `validation.py` / `checker.py`：
  OpenAPI 提取、文档一致性、启动守卫；
- `app/contracts/probes.py` / `live_check.py`：代表性失败请求定义与
  逐接口、逐状态码的真实响应核对；
- `scripts/check_contract.py`：本地/CI 守卫命令。
