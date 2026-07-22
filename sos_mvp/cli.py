from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import ExecutionEvent, execute_program, final_result
from .executors import ExecutionError, registered_languages
from .model import GraphError
from .parser import ParseError, parse_file
from .planner import enrich_and_validate, plan_lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs",
        description="ULCS 多語計算終端 v0.2：可型別化的多輸入語言算子 DAG",
    )
    parser.add_argument("program", nargs="?", help=".sos 程式路徑")
    parser.add_argument("--cwd", help="工作目錄；預設為 .sos 文件所在目錄")
    parser.add_argument("--db", help="SQLite 路徑；預設 output/ulcs.db")
    parser.add_argument("--emit-ir", help="輸出 Language Operator Graph JSON")
    parser.add_argument("--output", help="指定要顯示的輸出節點；預設顯示 sink 節點")
    parser.add_argument("--timeout", type=int, default=60, help="每個 Runtime 的逾時秒數")
    parser.add_argument("--dry-run", action="store_true", help="只解析、型別檢查與顯示安全預覽")
    parser.add_argument("--yes", action="store_true", help="不詢問，直接執行")
    parser.add_argument("--json", action="store_true", help="以 JSON 顯示最後結果")
    parser.add_argument("--list-languages", action="store_true", help="列出已註冊語言適配器")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_languages:
        print("\n".join(registered_languages()))
        return 0
    if not args.program:
        print("[參數錯誤] 必須提供 .sos 程式路徑。", file=sys.stderr)
        return 2

    program_path = Path(args.program).resolve()
    cwd = Path(args.cwd).resolve() if args.cwd else program_path.parent
    db_path = Path(args.db).resolve() if args.db else cwd / "output" / "ulcs.db"

    try:
        program = enrich_and_validate(parse_file(program_path))
    except (OSError, ParseError, GraphError, TypeError) as exc:
        print(f"[解析失敗] {exc}", file=sys.stderr)
        return 2

    if args.emit_ir:
        target = Path(args.emit_ir)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(program.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print("=== ULCS v0.2 安全預覽 ===")
    print(f"程式：{program_path}")
    print(f"工作目錄：{cwd}")
    print(f"SQLite：{db_path}")
    for line in plan_lines(program):
        print(line)

    if args.dry_run:
        print("\n[DRY RUN] 未執行任何節點。")
        return 0

    if not args.yes:
        answer = input("\n執行以上跨語言 DAG？[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消。")
            return 0

    def report(event: ExecutionEvent) -> None:
        preview = json.dumps(event.value, ensure_ascii=False, default=str)
        suffix = "…" if len(preview) > 240 else ""
        print(f"[完成] {event.node_id}: {preview[:240]}{suffix}")

    try:
        outputs = execute_program(
            program,
            cwd=cwd,
            db_path=db_path,
            timeout=args.timeout,
            on_complete=report,
        )
        final = final_result(program, outputs, args.output)
    except (ExecutionError, KeyError) as exc:
        print(f"[執行失敗] {exc}", file=sys.stderr)
        return 3

    print("\n=== 最終結果 ===")
    if args.json or isinstance(final, (dict, list)):
        print(json.dumps(final, ensure_ascii=False, indent=2, default=str))
    else:
        print(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
