import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from sos_mvp.capabilities import CapabilityDeniedError, CapabilityPolicy
from sos_mvp.engine import execute_program_with_trace
from sos_mvp.parser import parse_text
from sos_mvp.planner import enrich_and_validate
from sos_mvp.resources import ExecutionLimits, ResourceLimitError


class V04RuntimeTests(unittest.TestCase):
    def test_scoped_network_allow_matches_host(self):
        program = enrich_and_validate(
            parse_text("source remote = http{https://api.example.com/data}")
        )
        policy = CapabilityPolicy(
            mode="enforce",
            allow=("network.access@https://api.example.com",),
        )
        decision = policy.check_program(program)[0]
        self.assertEqual(
            decision.allowed_claims,
            ("network.access@https://api.example.com",),
        )

    def test_scoped_network_allow_rejects_other_host(self):
        program = enrich_and_validate(
            parse_text("source remote = http{https://api.example.com/data}")
        )
        policy = CapabilityPolicy(
            mode="enforce",
            allow=("network.access@https://other.example.com",),
        )
        with self.assertRaises(CapabilityDeniedError):
            policy.check_program(program)

    def test_policy_file_loads_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            path.write_text(
                json.dumps(
                    {
                        "mode": "audit",
                        "limits": {
                            "max_nodes": 10,
                            "max_workers": 3,
                            "max_output_bytes": 100,
                            "max_total_output_bytes": 200,
                        },
                    }
                ),
                encoding="utf-8",
            )
            policy = CapabilityPolicy.from_file(path)
        self.assertEqual(policy.limits.max_workers, 3)
        self.assertEqual(policy.limits.max_nodes, 10)

    def test_node_quota_is_checked_before_runtime(self):
        program = enrich_and_validate(
            parse_text(
                """
source one = py{result = 1}
source two = py{result = 2}
"""
            )
        )
        limits = ExecutionLimits(max_nodes=1)
        with tempfile.TemporaryDirectory() as temp, patch(
            "sos_mvp.engine.execute_node"
        ) as execute:
            with self.assertRaises(ResourceLimitError):
                execute_program_with_trace(
                    program,
                    cwd=Path(temp),
                    db_path=Path(temp) / "db.sqlite",
                    limits=limits,
                )
            execute.assert_not_called()

    def test_output_quota_rejects_large_value(self):
        program = enrich_and_validate(parse_text("source value = py{result = 1}"))
        limits = ExecutionLimits(
            max_output_bytes=10,
            max_total_output_bytes=100,
        )
        with tempfile.TemporaryDirectory() as temp, patch(
            "sos_mvp.engine.execute_node", return_value="x" * 100
        ):
            with self.assertRaises(ResourceLimitError):
                execute_program_with_trace(
                    program,
                    cwd=Path(temp),
                    db_path=Path(temp) / "db.sqlite",
                    limits=limits,
                )

    def test_independent_nodes_run_in_parallel_layer(self):
        program = enrich_and_validate(
            parse_text(
                """
source one = py{result = 1}
source two = py{result = 2}
"""
            )
        )
        barrier = threading.Barrier(2)

        def execute(node, *_args, **_kwargs):
            barrier.wait(timeout=3)
            return node.node_id

        with tempfile.TemporaryDirectory() as temp, patch(
            "sos_mvp.engine.execute_node", side_effect=execute
        ):
            trace = execute_program_with_trace(
                program,
                cwd=Path(temp),
                db_path=Path(temp) / "db.sqlite",
                limits=ExecutionLimits(max_workers=2),
            )

        self.assertEqual(trace.execution_layers, (("one", "two"),))
        self.assertEqual(trace.outputs, {"one": "one", "two": "two"})

    def test_taint_propagates_across_edges(self):
        program = enrich_and_validate(
            parse_text(
                """
source remote = http{https://example.com/data}
transform derived = py{result = input} from remote
"""
            )
        )

        def execute(node, *_args, **_kwargs):
            return {"node": node.node_id}

        with tempfile.TemporaryDirectory() as temp, patch(
            "sos_mvp.engine.execute_node", side_effect=execute
        ):
            trace = execute_program_with_trace(
                program,
                cwd=Path(temp),
                db_path=Path(temp) / "db.sqlite",
                limits=ExecutionLimits(max_workers=2),
            )

        label = "external.network:https://example.com"
        self.assertIn(label, trace.taints["remote"])
        self.assertIn(label, trace.taints["derived"])
        self.assertEqual(trace.total_output_bytes, sum(trace.output_bytes.values()))


if __name__ == "__main__":
    unittest.main()
