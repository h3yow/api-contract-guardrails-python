"""本地/CI 契约守卫命令。

用法：
    python -m scripts.check_contract --baseline tests/contracts/openapi.baseline.json

退出码：0 通过；1 发现映射/文档问题或破坏性变更。
"""

from __future__ import annotations

import argparse
import sys

from app.contracts.checker import load_baseline, run_startup_checks
from app.main import EXPECTED_BUSINESS_ERRORS, create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="错误契约与接口兼容性守卫")
    parser.add_argument("--baseline", help="基线 OpenAPI JSON；提供时做兼容性比较")
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="非严格模式：只打印问题不返回非零（默认严格，失败即退出 1）",
    )
    args = parser.parse_args(argv)
    strict = not args.no_strict

    # CLI 是显式校验流程：create_app 时先不跑守卫，统一在下面收集结论
    app = create_app(run_checks=False)
    baseline = load_baseline(args.baseline) if args.baseline else None
    result, report = run_startup_checks(
        app,
        baseline_contract=baseline,
        strict=False,
        expected_business_errors=EXPECTED_BUSINESS_ERRORS,
    )

    if report is not None:
        print(
            f"[契约兼容性] compatible={report.compatible} "
            f"变更={len(report.changes)} 破坏性={len(report.breaking_changes)}"
        )
        for c in report.changes:
            print(
                f"  - [{c.severity.value}:{c.change_type.value}] "
                f"{c.location}: {c.detail}"
            )

    if result.findings:
        print(f"[错误契约校验] 发现 {len(result.findings)} 个问题:")
        for f in result.findings:
            print(f"  - [{f.location}] {f.message}")
    else:
        print("[错误契约校验] 映射、文档一致性与逐接口运行时核对全部通过")

    ok = result.ok and (report is None or report.compatible)
    if not ok and strict:
        print("守卫失败：存在映射/文档问题或破坏性契约变更。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
