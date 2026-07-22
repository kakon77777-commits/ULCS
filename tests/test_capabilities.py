import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sos_mvp.capabilities import CapabilityDeniedError, CapabilityPolicy
from sos_mvp.engine import execute_program
from sos_mvp.parser import parse_text
from sos_mvp.planner import enrich_and_validate


class CapabilityPolicyTests(unittest.TestCase):
    def test_enforce_rejects_unlisted_capability(self):
        program = enrich_and_validate(parse_text("source value = py{result = 1}"))
        with self.assertRaises(CapabilityDeniedError):
            CapabilityPolicy(mode="enforce").check_program(program)

    def test_wildcard_allow(self):
        program = enrich_and_validate(parse_text("source value = py{result = 1}"))
        CapabilityPolicy(mode="enforce", allow=("python.*",)).check_program(program)

    def test_explicit_deny_blocks_audit_mode(self):
        program = enrich_and_validate(parse_text("source remote = http{https://example.com}"))
        with self.assertRaises(CapabilityDeniedError):
            CapabilityPolicy(mode="audit", deny=("network.*",)).check_program(program)

    def test_policy_file_honors_explicit_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            path.write_text(
                json.dumps({"mode": "audit", "deny": ["network.*"]}),
                encoding="utf-8",
            )
            policy = CapabilityPolicy.compose(policy_path=path)
            self.assertEqual(policy.mode, "audit")

    def test_entire_graph_is_checked_before_first_runtime(self):
        program = enrich_and_validate(
            parse_text(
                """
source first = py{result = 1}
source second = http{https://example.com}
"""
            )
        )
        policy = CapabilityPolicy(
            mode="enforce",
            allow=("python.execute",),
            deny=("network.*",),
        )
        with tempfile.TemporaryDirectory() as temp, patch("sos_mvp.engine.execute_node") as execute:
            with self.assertRaises(CapabilityDeniedError):
                execute_program(
                    program,
                    cwd=Path(temp),
                    db_path=Path(temp) / "db.sqlite",
                    policy=policy,
                )
            execute.assert_not_called()

    def test_log_v03_exposes_capabilities(self):
        program = enrich_and_validate(parse_text("source value = py{result = 1}"))
        payload = program.to_dict()
        self.assertEqual(payload["version"], "0.3")
        self.assertEqual(payload["nodes"][0]["capabilities"], ["python.execute"])


if __name__ == "__main__":
    unittest.main()
