"""守卫 CLI 的退出码行为。"""

from __future__ import annotations

import json

from scripts.check_contract import main

BASELINE = "tests/contracts/openapi.baseline.json"


def test_cli_passes_with_matching_baseline() -> None:
    assert main(["--baseline", BASELINE]) == 0


def test_cli_fails_on_breaking_baseline(tmp_path) -> None:
    base = json.load(open(BASELINE, encoding="utf-8"))
    node = base["components"]["schemas"]["ErrorResponse"]
    node["properties"].pop("request_id", None)
    if "request_id" in node.get("required", []):
        node["required"].remove("request_id")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(base), encoding="utf-8")

    rc = main(["--baseline", str(bad)])
    assert rc == 1


def test_cli_no_strict_returns_zero_even_with_findings(tmp_path, capsys) -> None:
    base = json.load(open(BASELINE, encoding="utf-8"))
    node = base["components"]["schemas"]["ErrorResponse"]
    node["properties"].pop("request_id", None)
    if "request_id" in node.get("required", []):
        node["required"].remove("request_id")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(base), encoding="utf-8")

    rc = main(["--baseline", str(bad), "--no-strict"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "request_id" in out and "破坏性" in out
