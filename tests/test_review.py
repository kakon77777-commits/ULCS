from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sos_mvp.approved_cli import main as approved_main
from sos_mvp.review import (
    ApprovalRecord,
    ProviderProposal,
    ReviewBundle,
    ReviewError,
    compile_provider_proposal,
    verify_approval,
)


_KEY = b"0123456789abcdef0123456789abcdef"


def _proposal_payload() -> dict[str, object]:
    return {
        "format": "ULCS-Intent-Provider-Proposal",
        "version": "0.8",
        "provider": {
            "id": "test-provider",
            "model": "deterministic-fixture",
        },
        "request": {
            "format": "ULCS-Intent-Request",
            "version": "0.7",
            "intent": "分析以下日誌，找出 ERROR 與 FATAL，統計各類數量。",
            "profile": "log-analysis",
            "bindings": {
                "text": "ERROR one\nINFO two\nFATAL three",
                "terms": ["ERROR", "FATAL"],
            },
            "preferences": {
                "include_matches": True,
                "persist_summary": True,
            },
        },
        "notes": ["Provider 只提出意圖資料，不提供 workflow 或 policy。"],
        "confidence": 0.8,
    }


class ProviderContractTests(unittest.TestCase):
    def test_provider_cannot_claim_ready_or_supply_workflow(self) -> None:
        for field, value in (("status", "ready"), ("workflow", "run x = py{}")):
            payload = _proposal_payload()
            payload[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ReviewError):
                    ProviderProposal.from_mapping(payload)

    def test_ready_proposal_builds_verifiable_review_bundle(self) -> None:
        proposal = ProviderProposal.from_mapping(_proposal_payload())
        with tempfile.TemporaryDirectory() as tmp:
            intent_bundle, review = compile_provider_proposal(proposal, tmp)

            self.assertTrue(intent_bundle.ready)
            self.assertIsNotNone(review)
            assert review is not None
            self.assertEqual(review.proposal_digest, proposal.digest)
            self.assertTrue((Path(tmp) / "review-bundle.json").is_file())
            loaded = ReviewBundle.read(tmp)
            self.assertEqual(loaded.digest, review.digest)
            self.assertEqual(
                set(loaded.files),
                {
                    "provider-proposal.json",
                    "intent-plan.json",
                    "workflow.sos",
                    "artifact-contract.json",
                    "capability-policy.json",
                    "intent-bundle.json",
                },
            )

    def test_incomplete_proposal_cannot_enter_approval_flow(self) -> None:
        payload = _proposal_payload()
        payload["request"] = {
            "format": "ULCS-Intent-Request",
            "version": "0.7",
            "intent": "幫我處理一下。",
        }
        proposal = ProviderProposal.from_mapping(payload)
        with tempfile.TemporaryDirectory() as tmp:
            intent_bundle, review = compile_provider_proposal(proposal, tmp)
            self.assertEqual(intent_bundle.status, "needs_clarification")
            self.assertIsNone(review)
            self.assertFalse((Path(tmp) / "review-bundle.json").exists())

    def test_changed_generated_file_invalidates_review_bundle(self) -> None:
        proposal = ProviderProposal.from_mapping(_proposal_payload())
        with tempfile.TemporaryDirectory() as tmp:
            _, review = compile_provider_proposal(proposal, tmp)
            assert review is not None
            workflow = Path(tmp) / "workflow.sos"
            workflow.write_text(
                workflow.read_text(encoding="utf-8") + "\n# changed\n",
                encoding="utf-8",
            )
            with self.assertRaises(ReviewError):
                ReviewBundle.read(tmp)


class ApprovalGateTests(unittest.TestCase):
    def _build(self, root: str) -> ReviewBundle:
        proposal = ProviderProposal.from_mapping(_proposal_payload())
        _, review = compile_provider_proposal(proposal, root)
        assert review is not None
        return review

    def test_approved_record_verifies_and_wrong_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            review = self._build(tmp)
            approval = ApprovalRecord.create(
                review,
                decision="approve",
                approver="reviewer@example",
                key=_KEY,
                scopes=("execute",),
                reason="Reviewed generated graph and exact claims.",
                issued_at="2026-07-23T00:00:00+00:00",
            )
            verify_approval(review, approval, key=_KEY)
            with self.assertRaises(ReviewError):
                verify_approval(review, approval, key=b"x" * 32)

    def test_reject_record_never_authorizes_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            review = self._build(tmp)
            approval = ApprovalRecord.create(
                review,
                decision="reject",
                approver="reviewer@example",
                key=_KEY,
                scopes=("review",),
                issued_at="2026-07-23T00:00:00+00:00",
            )
            with self.assertRaises(ReviewError):
                verify_approval(review, approval, key=_KEY)

    def test_missing_execute_scope_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            review = self._build(tmp)
            approval = ApprovalRecord.create(
                review,
                decision="approve",
                approver="reviewer@example",
                key=_KEY,
                scopes=("inspect",),
                issued_at="2026-07-23T00:00:00+00:00",
            )
            with self.assertRaises(ReviewError):
                verify_approval(review, approval, key=_KEY)

    def test_approved_runner_delegates_only_after_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            review = self._build(tmp)
            approval = ApprovalRecord.create(
                review,
                decision="approve",
                approver="reviewer@example",
                key=_KEY,
                scopes=("execute",),
                issued_at="2026-07-23T00:00:00+00:00",
            )
            approval_path = Path(tmp) / "approval.json"
            approval.write(approval_path)
            with patch.dict(os.environ, {"ULCS_TEST_APPROVAL_KEY": _KEY.decode()}):
                with patch("sos_mvp.approved_cli.runtime_main", return_value=0) as runtime:
                    result = approved_main(
                        [
                            str(Path(tmp) / "review-bundle.json"),
                            str(approval_path),
                            "--key-env",
                            "ULCS_TEST_APPROVAL_KEY",
                            "--",
                            "--yes",
                            "--json",
                        ]
                    )
            self.assertEqual(result, 0)
            delegated = runtime.call_args.args[0]
            self.assertIn("--policy", delegated)
            self.assertIn("--contract", delegated)
            self.assertIn("--yes", delegated)
            self.assertIn("--json", delegated)

    def test_approved_runner_rejects_policy_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            review = self._build(tmp)
            approval = ApprovalRecord.create(
                review,
                decision="approve",
                approver="reviewer@example",
                key=_KEY,
                scopes=("execute",),
                issued_at="2026-07-23T00:00:00+00:00",
            )
            approval_path = Path(tmp) / "approval.json"
            approval.write(approval_path)
            with patch.dict(os.environ, {"ULCS_TEST_APPROVAL_KEY": _KEY.decode()}):
                with patch("sos_mvp.approved_cli.runtime_main") as runtime:
                    result = approved_main(
                        [
                            str(Path(tmp) / "review-bundle.json"),
                            str(approval_path),
                            "--key-env",
                            "ULCS_TEST_APPROVAL_KEY",
                            "--",
                            "--policy",
                            "other.json",
                        ]
                    )
            self.assertEqual(result, 4)
            runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
