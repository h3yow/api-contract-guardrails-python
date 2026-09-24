"""业务异常 -> 对外错误码的显式映射注册表。

规则：
- 业务异常必须显式注册（或继承自已注册的基类，按 MRO 解析）；
- 同一个异常类型被重复注册到不同错误码 = 冲突，立即抛 ContractMappingError，
  使其在应用导入/启动阶段暴露，而不是请求期静默降级；
- ``resolve`` 的未命中会被记录，供校验流程指出“未处理映射”。
"""

from __future__ import annotations

from app.errors.codes import ErrorCode
from app.errors.exceptions import BusinessError, ContractMappingError


class ErrorMappingRegistry:
    """异常类型到错误码的显式映射表。"""

    def __init__(self) -> None:
        # 显式注册的精确映射
        self._map: dict[type[BaseException], ErrorCode] = {}
        # 请求期 resolve 未命中的异常类型（去重记录），供校验流程指出
        self.unmapped_seen: set[type[BaseException]] = set()

    def register(self, exc_type: type[BaseException], code: ErrorCode) -> None:
        """注册一条映射。

        - 同一类型重复注册到相同错误码：幂等，允许；
        - 同一类型注册到不同错误码：冲突，抛 ContractMappingError。
        """
        if not isinstance(code, ErrorCode):
            raise ContractMappingError(
                f"映射目标必须是 ErrorCode，收到: {code!r}"
            )
        existing = self._map.get(exc_type)
        if existing is not None and existing is not code:
            raise ContractMappingError(
                f"异常 {exc_type.__module__}.{exc_type.__name__} 存在冲突映射: "
                f"{existing.value} vs {code.value}"
            )
        self._map[exc_type] = code

    def resolve(self, exc: BaseException) -> ErrorCode | None:
        """按 MRO 查找已注册错误码；找不到返回 None 并记录未命中。"""
        for base in type(exc).__mro__:
            if base in self._map:
                return self._map[base]
        self.unmapped_seen.add(type(exc))
        return None

    def is_registered(self, exc_type: type[BaseException]) -> bool:
        """该异常类型（含基类）是否已有显式映射。"""
        return any(base in self._map for base in exc_type.__mro__)

    def entries(self) -> dict[str, str]:
        """导出映射快照（供文档/校验展示）。"""
        return {
            f"{t.__module__}.{t.__name__}": code.value for t, code in self._map.items()
        }

    def validate(
        self, expected: tuple[type[BaseException], ...] = ()
    ) -> list[str]:
        """启动期自检，返回问题描述列表（空列表表示通过）。

        只检查“声明为业务异常却未显式注册”的情况；``expected`` 中任何
        未注册类型都会被指出（未处理映射）。运行期未命中记录由
        ``runtime_unmapped_problems`` 单独暴露，供巡检/测试断言，
        因为普通未预期异常（如 RuntimeError）本就应走 500，
        不应与“业务异常漏注册”混为一谈。
        """
        problems: list[str] = []
        for exc_type in expected:
            if not self.is_registered(exc_type):
                problems.append(
                    "未注册的业务异常映射: "
                    f"{exc_type.__module__}.{exc_type.__name__}"
                )
        return problems

    def runtime_unmapped_problems(self) -> list[str]:
        """运行期 resolve 未命中的异常类型（巡检用，不阻断启动）。"""
        return [
            "运行期观察到未映射异常: "
            f"{exc_type.__module__}.{exc_type.__name__}"
            for exc_type in sorted(self.unmapped_seen, key=lambda t: t.__name__)
        ]


#: 进程级默认注册表
registry = ErrorMappingRegistry()

# BusinessError 基类默认映射：子类默认归入业务拒绝，
# 子类也可显式注册到更具体的错误码。
registry.register(BusinessError, ErrorCode.BUSINESS_RULE_VIOLATION)


def register_error(
    exc_type: type[BaseException], code: ErrorCode
) -> type[BaseException]:
    """类装饰器/函数：把业务异常显式绑定到错误码。"""
    registry.register(exc_type, code)
    return exc_type
