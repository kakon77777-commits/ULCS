from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .executors import ExecutionError, execute_node
from .parser import ParseError, parse_file
from .planner import enrich_and_validate, plan_lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sos-mvp",
        description="SOS 多語計算終端 MVP：PowerShell + Regex + Python + SQLite",
    )
    parser.add_argument("program", help=".sos 程式路徑")
    parser.add_argument("--cwd", help="工作目錄；預設為 .sos 文件所在目錄")
    parser.add_argument("--db", help="SQLite 路徑；預設 output/sos_mvp.db")
    parser.add_argument("--emit-ir", help="輸出 Language Operator Graph JSON")
    parser.add_argument("--dry-run", action="store_true", help="只解析、型別檢查與顯示安全預覽")
    parser.add_argument("--yes", action="store_true", help="不詢問，直接執行")
    parser.add_argument("--json", action="store_true", help="以 JSON 顯示最後結果")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    program_path = Path(args.program).resolve()
    cwd = Path(args.cwd).resolve() if args.cwd else program_path.parent
    db_path = Path(args.db).resolve() if args.db else cwd / "output" / "sos_mvp.db"

    try:
        program = enrich_and_validate(parse_file(program_path))
    except (OSError, ParseError, TypeError) as exc:
        print(f"[解析失敗] {exc}", file=sys.stderr)
        return 2

    if args.emit_ir:
        target = Path(args.emit_ir)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(program.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== SOS/ULCS 安全預覽 ===")
    print(f"程式：{program_path}")
    print(f"工作目錄：{cwd}")
    print(f"SQLite：{db_path}")
    for line in plan_lines(program):
        print(line)

    if args.dry_run:
        print("\n[DRY RUN] 未執行任何節點。")
        return 0

    if not args.yes:
        answer = input("\n執行以上跨語言流程？[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消。")
            return 0

    outputs = {}
    try:
        for node in program.nodes:
            outputs[node.node_id] = execute_node(node, outputs, cwd, db_path)
            preview = json.dumps(outputs[node.node_id], ensure_ascii=False, default=str)
            print(f"[完成] {node.node_id}: {preview[:240]}{'…' if len(preview) > 240 else ''}")
    except (ExecutionError, KeyError) as exc:
        print(f"[執行失敗] 節點 {node.node_id}: {exc}", file=sys.stderr)
        return 3

    final = outputs[program.nodes[-1].node_id]
    print("\n=== 最終結果 ===")
    if args.json or isinstance(final, (dict, list)):
        print(json.dumps(final, ensure_ascii=False, indent=2, default=str))
    else:
        print(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
