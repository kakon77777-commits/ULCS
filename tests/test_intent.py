from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sos_mvp.artifacts import ArtifactContracts
from sos_mvp.capabilities import CapabilityPolicy
from sos_mvp.engine import execute_program
from sos_mvp.intent import (
    IntentCompileError,
    IntentRequest,
    compile_intent,
)
from sos_mvp.parser import parse_text
from sos_mvp.planner import enrich_and_validate


class IntentCompilerTests(unittest.TestCase):
    def test_inline_log_intent_compiles_and_executes(self) -> None:
        request = IntentRequest(
            intent="分析以下日誌，找出 ERROR 與 FATAL，統計各類數量。",
            bindings={
                "text": "ERROR one\nINFO two\nFATAL three\nERROR four",
                "terms": ["ERROR", "FATAL"],
            },
            preferences={"include_matches": True},
        )

        bundle = compile_intent(request)

        self.assertTrue(bundle.ready)
        self.assertEqual(bundle.profile, "log-analysis")
        self.assertEqual(bundle.validation["parser"], "passed")
        self.assertEqual(bundle.validation["contract"], "passed")
        self.assertEqual(bundle.validation["graph"], "passed")
        self.assertEqual(bundle.validation["policy"], "passed")
        self.assertIn("python.execute@runtime://python", bundle.validation["required_claims"])

        program = parse_text(bundle.workflow or "")
        ArtifactContracts.from_mapping(bundle.contract or {}).apply(program)
        program = enrich_and_validate(program)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = execute_program(
                program,
                cwd=root,
                db_path=root / "output" / "intent.db",
                timeout=30,
            )

        self.assertEqual(outputs["summary"]["total"], 3)
        self.assertEqual(outputs["summary"]["counts"], {"ERROR": 2, "FATAL": 1})
        self.assertEqual(len(outputs["summary"]["matches"]), 3)

    def test_generated_policy_enforces_exact_claims(self) -> None:
        request = IntentRequest(
            intent="分析日誌中的 ERROR。",
            bindings={"text": "ERROR one", "terms": ["ERROR"]},
        )
        bundle = compile_intent(request)
        self.assertTrue(bundle.ready)

        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "policy.json"
            policy_path.write_text(
                json.dumps(bundle.policy, ensure_ascii=False),
                encoding="utf-8",
            )
            policy = CapabilityPolicy.from_file(policy_path)
            program = parse_text(bundle.workflow or "")
            ArtifactContracts.from_mapping(bundle.contract or {}).apply(program)
            program = enrich_and_validate(program)
            decisions = policy.check_program(program)

        self.assertTrue(all(decision.permitted for decision in decisions))
        self.assertEqual(policy.mode, "enforce")

    def test_unknown_intent_requires_clarification(self) -> None:
        bundle = compile_intent(IntentRequest(intent="幫我把事情處理好。"))

        self.assertEqual(bundle.status, "needs_clarification")
        self.assertIn("profile", bundle.missing_fields)
        self.assertIsNone(bundle.workflow)

    def test_log_intent_missing_terms_requires_clarification(self) -> None:
        bundle = compile_intent(
            IntentRequest(
                intent="分析這一段日誌。",
                bindings={"text": "hello"},
            )
        )

        self.assertEqual(bundle.status, "needs_clarification")
        self.assertIn("bindings.terms", bundle.missing_fields)

    def test_file_intent_normalizes_relative_path_and_scopes_claim(self) -> None:
        request = IntentRequest(
            intent="讀取 logs 目錄下所有 .log 檔，找出 ERROR 與 FATAL。",
            bindings={"terms": ["ERROR", "FATAL"]},
        )
        bundle = compile_intent(request)

        self.assertTrue(bundle.ready)
        self.assertIn("Get-ChildItem './logs'", bundle.workflow or "")
        self.assertIn("filesystem.read@./logs", bundle.validation["required_claims"])

    def test_powershell_bindings_use_non_interpolating_literals(self) -> None:
        request = IntentRequest(
            intent="分析 log 檔。",
            bindings={
                "source_path": "./$([System.Environment]::CurrentDirectory)",
                "pattern": "*.log$([System.Environment]::MachineName)",
                "terms": ["ERROR"],
            },
        )
        bundle = compile_intent(request)

        self.assertTrue(bundle.ready)
        workflow = bundle.workflow or ""
        self.assertIn(
            "Get-ChildItem './$([System.Environment]::CurrentDirectory)'",
            workflow,
        )
        self.assertIn(
            "-Filter '*.log$([System.Environment]::MachineName)'",
            workflow,
        )
        self.assertNotIn('Get-ChildItem "./$(', workflow)

    def test_path_with_spaces_is_not_guessed_safe(self) -> None:
        request = IntentRequest(
            intent="分析 log 檔。",
            bindings={
                "source_path": "./my logs",
                "pattern": "*.log",
                "terms": ["ERROR"],
            },
        )
        bundle = compile_intent(request)

        self.assertEqual(bundle.status, "needs_clarification")
        self.assertIsNone(bundle.workflow)

    def test_http_get_compiles_with_origin_scoped_network_claim(self) -> None:
        request = IntentRequest(
            intent="取得 https://example.com/data.json 並輸出 JSON。",
            bindings={"url": "https://example.com/data.json"},
        )
        bundle = compile_intent(request)

        self.assertTrue(bundle.ready)
        self.assertEqual(bundle.profile, "http-json-fetch")
        self.assertIn("network.access@https://example.com", bundle.validation["required_claims"])
        self.assertIn('"method": "GET"', bundle.workflow or "")

    def test_http_post_is_not_auto_generated(self) -> None:
        request = IntentRequest(
            intent="POST data to https://example.com/api",
            profile="http-json-fetch",
            bindings={"url": "https://example.com/api", "method": "POST"},
        )
        bundle = compile_intent(request)

        self.assertEqual(bundle.status, "needs_clarification")
        self.assertIsNone(bundle.workflow)

    def test_bundle_write_emits_reviewable_files(self) -> None:
        bundle = compile_intent(
            IntentRequest(
                intent="分析 ERROR。",
                bindings={"text": "ERROR one", "terms": ["ERROR"]},
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            written = bundle.write(tmp)
            expected = {
                "plan",
                "workflow",
                "contract",
                "policy",
                "bundle",
            }
            self.assertEqual(set(written), expected)
            for path in written.values():
                self.assertTrue(Path(path).is_file())
            metadata = json.loads(Path(written["bundle"]).read_text(encoding="utf-8"))
            self.assertEqual(metadata["format"], "ULCS-Intent-Bundle")
            self.assertEqual(metadata["status"], "ready")
            self.assertNotIn("generated", metadata)

    def test_request_version_mismatch_is_rejected(self) -> None:
        with self.assertRaises(IntentCompileError):
            IntentRequest.from_mapping(
                {
                    "format": "ULCS-Intent-Request",
                    "version": "9.9",
                    "intent": "Analyze logs.",
                }
            )


if __name__ == "__main__":
    unittest.main()
