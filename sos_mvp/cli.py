from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .artifacts import (
    ArtifactConfig,
    ArtifactContracts,
    ArtifactError,
    CheckpointError,
    ExecutionCheckpoint,
    SchemaValidationError,
)
from .capabilities import (
    KNOWN_CAPABILITIES,
    CapabilityDeniedError,
    CapabilityError,
    CapabilityPolicy,
    decision_line,
)
from .engine import ExecutionEvent, execute_program_with_trace, final_result
from .executors import ExecutionError, registered_languages
from .extensions import ensure_runtime_extensions
from .model import GraphError
from .parser import ParseError, parse_file
from .planner import enrich_and_validate, plan_lines
from .provenance import (
    CacheConfig,
    CacheError,
    ExecutionManifest,
    ManifestVerificationError,
    plan_digest,
    program_digest,
    verify_manifest,
)
from .resources import ResourceLimitError


def _configure_stdio() -> None:
    """Use deterministic UTF-8 diagnostics on Windows and redirected shells."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _write_json(path: str | Path, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs",
        description=(
            "ULCS 多語計算終端 v0.6：Artifact Contract、schema、"
            "checkpoint／resume 與可驗證持久化"
        ),
    )
    parser.add_argument("program", nargs="?", help=".sos 程式路徑")
    parser.add_argument("--cwd", help="工作目錄；預設為 .sos 文件所在目錄")
    parser.add_argument("--db", help="SQLite 路徑；預設 output/ulcs.db")
    parser.add_argument("--emit-ir", help="輸出 Language Operator Graph JSON")
    parser.add_argument("--emit-trace", help="執行後輸出結果、污染、摘要、快取與 Artifact 追蹤 JSON")
    parser.add_argument("--emit-manifest", help="輸出不含完整值的可驗證執行清單 JSON")
    parser.add_argument("--verify-manifest", help="執行後與既有 manifest 比對")
    parser.add_argument("--output", help="指定要顯示的輸出節點；預設顯示 sink 節點")
    parser.add_argument("--timeout", type=int, default=60, help="每個 Runtime 的逾時秒數")
    parser.add_argument("--dry-run", action="store_true", help="只解析、政策檢查與顯示安全預覽")
    parser.add_argument("--yes", action="store_true", help="不詢問，直接執行")
    parser.add_argument("--json", action="store_true", help="以 JSON 顯示最後結果")
    parser.add_argument("--list-languages", action="store_true", help="列出已註冊語言適配器")
    parser.add_argument("--list-capabilities", action="store_true", help="列出核心已知能力名稱")
    parser.add_argument(
        "--plugin",
        action="append",
        default=[],
        metavar="MODULE",
        help="載入 Runtime 外掛模組，可重複",
    )
    parser.add_argument("--policy", help="JSON 能力政策檔；未寫 mode 時預設 enforce")
    parser.add_argument("--contract", help="ULCS v0.6 Artifact Contract JSON")
    parser.add_argument(
        "--artifact-mode",
        choices=("off", "auto", "all"),
        default="off",
        help="Artifact 持久化模式；預設 off",
    )
    parser.add_argument("--artifact-dir", help="Artifact Store；預設為工作目錄下的 .ulcs-artifacts")
    parser.add_argument(
        "--artifact-threshold-bytes",
        type=int,
        default=262_144,
        help="auto 模式下自動持久化的輸出大小門檻",
    )
    parser.add_argument("--checkpoint", help="每個完成層後原子寫入 checkpoint")
    parser.add_argument("--resume", help="從既有 v0.6 checkpoint 恢復；未指定 --checkpoint 時更新原檔")
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="PATTERN",
        help="允許能力或 capability@resource 模式，可重複",
    )
    parser.add_argument(
        "--deny",
        action="append",
        default=[],
        metavar="PATTERN",
        help="拒絕能力或 capability@resource 模式，可重複",
    )
    parser.add_argument(
        "--enforce-capabilities",
        action="store_true",
        help="要求所有節點能力與資源範圍明確符合 allow",
    )
    parser.add_argument("--max-nodes", type=int, help="單次工作流最大節點數")
    parser.add_argument("--max-workers", type=int, help="同一 DAG 層最大平行工作數")
    parser.add_argument("--max-output-bytes", type=int, help="單節點最大 JSON 輸出 bytes")
    parser.add_argument(
        "--max-total-output-bytes",
        type=int,
        help="整個工作流最大累積 JSON 輸出 bytes",
    )
    parser.add_argument(
        "--cache-mode",
        choices=("off", "read", "write", "read-write"),
        default="off",
        help="內容定址快取模式；預設 off",
    )
    parser.add_argument(
        "--cache-dir",
        help="快取目錄；預設為工作目錄下的 .ulcs-cache",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)

    try:
        ensure_runtime_extensions(args.plugin)
    except ExecutionError as exc:
        print(f"[外掛失敗] {exc}", file=sys.stderr)
        return 5

    if args.list_languages:
        print("\n".join(registered_languages()))
        return 0
    if args.list_capabilities:
        print("\n".join(KNOWN_CAPABILITIES))
        return 0
    if not args.program:
        print("[參數錯誤] 必須提供 .sos 程式路徑。", file=sys.stderr)
        return 2
    if args.dry_run and args.verify_manifest:
        print("[參數錯誤] --verify-manifest 必須實際執行，不能搭配 --dry-run。", file=sys.stderr)
        return 2
    if args.dry_run and args.resume:
        print("[參數錯誤] --resume 必須實際執行，不能搭配 --dry-run。", file=sys.stderr)
        return 2

    program_path = Path(args.program).resolve()
    cwd = Path(args.cwd).resolve() if args.cwd else program_path.parent
    db_path = Path(args.db).resolve() if args.db else cwd / "output" / "ulcs.db"
    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else cwd / ".ulcs-cache"
    artifact_dir = (
        Path(args.artifact_dir).resolve() if args.artifact_dir else cwd / ".ulcs-artifacts"
    )
    checkpoint_path = (
        Path(args.checkpoint).resolve()
        if args.checkpoint
        else Path(args.resume).resolve()
        if args.resume
        else None
    )

    try:
        cache_config = CacheConfig(mode=args.cache_mode, directory=cache_dir)
        artifact_enabled = args.artifact_mode != "off" or checkpoint_path is not None
        artifact_config = (
            ArtifactConfig(
                directory=artifact_dir,
                threshold_bytes=args.artifact_threshold_bytes,
                persist_all=args.artifact_mode == "all" or checkpoint_path is not None,
            )
            if artifact_enabled
            else None
        )
        policy = CapabilityPolicy.compose(
            policy_path=args.policy,
            allow=args.allow,
            deny=args.deny,
            enforce=args.enforce_capabilities,
            max_nodes=args.max_nodes,
            max_workers=args.max_workers,
            max_output_bytes=args.max_output_bytes,
            max_total_output_bytes=args.max_total_output_bytes,
        )
        program = parse_file(program_path)
        if args.contract:
            ArtifactContracts.read(args.contract).apply(program)
        program = enrich_and_validate(program)
        resume_checkpoint = ExecutionCheckpoint.read(args.resume) if args.resume else None
    except (
        OSError,
        ParseError,
        GraphError,
        TypeError,
        CapabilityError,
        CacheError,
        ArtifactError,
        CheckpointError,
    ) as exc:
        print(f"[解析失敗] {exc}", file=sys.stderr)
        return 2

    if args.emit_ir:
        _write_json(args.emit_ir, program.to_dict())

    print("=== ULCS v0.6 Artifact 與恢復預覽 ===")
    print(f"程式：{program_path}")
    print(f"工作目錄：{cwd}")
    print(f"SQLite：{db_path}")
    print(f"能力政策：{policy.summary()}")
    print(f"快取：{cache_config.summary()}")
    print(f"Artifact：{artifact_config.summary() if artifact_config else 'off'}")
    print(f"Checkpoint：{checkpoint_path or 'off'}")
    print(f"Program digest：{program_digest(program)}")
    print(f"Plan digest：{plan_digest(program)}")
    print(
        "執行層："
        + " | ".join(
            f"L{index}=" + ",".join(node.node_id for node in layer)
            for index, layer in enumerate(program.execution_layers(), start=1)
        )
    )
    for line in plan_lines(program):
        print(line)

    decisions = policy.evaluate_program(program)
    print("--- 能力與資源決策 ---")
    for decision in decisions:
        print(decision_line(decision))

    try:
        policy.check_program(program)
        if len(program.nodes) > policy.limits.max_nodes:
            raise ResourceLimitError(
                f"節點數 {len(program.nodes)} 超過上限 {policy.limits.max_nodes}。"
            )
    except (CapabilityDeniedError, ResourceLimitError) as exc:
        print(f"[政策拒絕] {exc}", file=sys.stderr)
        return 4

    if args.dry_run:
        print("\n[DRY RUN] 已完成圖、政策、契約、schema、摘要與 Artifact 預檢，未執行任何節點。")
        return 0

    if not args.yes:
        answer = input("\n執行以上跨語言 DAG？[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消。")
            return 0

    def report(event: ExecutionEvent) -> None:
        preview = json.dumps(event.value, ensure_ascii=False, default=str)
        suffix = "…" if len(preview) > 240 else ""
        taints = ", ".join(event.taints) or "clean"
        source = "RESUME" if event.resumed else "CACHE" if event.cache_hit else "RUNTIME"
        artifact = event.artifact.digest[:12] if event.artifact is not None else "memory"
        print(
            f"[完成] L{event.layer} {event.node_id} [{source}] "
            f"({event.output_bytes}B; taints={taints}; "
            f"digest={event.output_digest[:12]}; artifact={artifact}): "
            f"{preview[:240]}{suffix}"
        )

    try:
        trace = execute_program_with_trace(
            program,
            cwd=cwd,
            db_path=db_path,
            timeout=args.timeout,
            policy=policy,
            cache_config=cache_config,
            artifact_config=artifact_config,
            checkpoint_path=checkpoint_path,
            resume_checkpoint=resume_checkpoint,
            on_complete=report,
        )
        final = final_result(program, trace.outputs, args.output)
    except (
        ExecutionError,
        CapabilityDeniedError,
        ResourceLimitError,
        CacheError,
        ArtifactError,
        CheckpointError,
        SchemaValidationError,
        KeyError,
    ) as exc:
        print(f"[執行失敗] {exc}", file=sys.stderr)
        return 3

    if args.emit_trace:
        _write_json(args.emit_trace, trace.to_dict())

    if args.verify_manifest:
        try:
            expected = ExecutionManifest.read(args.verify_manifest)
            verify_manifest(expected, trace.manifest)
        except (OSError, ManifestVerificationError) as exc:
            print(f"[重放驗證失敗] {exc}", file=sys.stderr)
            return 6
        print(f"[重放驗證通過] {Path(args.verify_manifest).resolve()}")

    if args.emit_manifest:
        _write_json(args.emit_manifest, trace.manifest.to_dict())

    print("\n=== 最終結果 ===")
    if args.json or isinstance(final, (dict, list)):
        print(json.dumps(final, ensure_ascii=False, indent=2, default=str))
    else:
        print(final)
    hit_count = sum(trace.cache_hits.values())
    resumed_count = sum(trace.resumed.values())
    artifact_count = sum(ref is not None for ref in trace.artifacts.values())
    print(
        f"追蹤：總輸出 {trace.total_output_bytes}B；"
        f"層數 {len(trace.execution_layers)}；"
        f"最大平行度 {policy.limits.max_workers}；"
        f"快取命中 {hit_count}/{len(trace.cache_hits)}；"
        f"恢復 {resumed_count}/{len(trace.resumed)}；"
        f"Artifact {artifact_count}/{len(trace.artifacts)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
