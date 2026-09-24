"""本地/CI 可用的契约校验命令行。

用法::

    uv run python -m app.cli diff-contract old_schema.json new_schema.json
    uv run python -m app.cli check-mappings

* ``diff-contract``：比较两个 JSON Schema 片段；存在破坏性变更时以非零码
  退出，并定位到具体字段。
* ``check-mappings``：对当前应用执行映射完整性与 OpenAPI 文档一致性校验。
"""
from __future__ import annotations

import argparse
import json
import sys

from app.contract import ContractBreakingError, assert_compatible
from app.main import app
from app.validation import run_startup_validation


def _load_schema(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def diff_contract(old_path: str, new_path: str) -> int:
    old = _load_schema(old_path)
    new = _load_schema(new_path)
    try:
        report = assert_compatible(old, new)
    except ContractBreakingError as exc:
        print(f"契约校验失败（{len(exc.report.breaking_changes)} 处破坏性变更）：")
        for change in exc.report.breaking_changes:
            print(f"  [breaking] {change.field}: {change.kind} — {change.detail}")
        return 1
    print(f"契约校验通过（{len(report.changes)} 处兼容性变更）")
    for change in report.changes:
        print(f"  [compatible] {change.field}: {change.kind} — {change.detail}")
    return 0


def check_mappings() -> int:
    try:
        run_startup_validation(app)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    print("映射与文档一致性校验通过")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    diff_p = sub.add_parser("diff-contract", help="比较两个 JSON Schema 的兼容性")
    diff_p.add_argument("old_schema")
    diff_p.add_argument("new_schema")
    sub.add_parser("check-mappings", help="校验映射完整性与文档一致性")
    args = parser.parse_args(argv)
    if args.command == "diff-contract":
        return diff_contract(args.old_schema, args.new_schema)
    return check_mappings()


if __name__ == "__main__":
    sys.exit(main())
