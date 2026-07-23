import tempfile
import unittest
from pathlib import Path

from sos_mvp.artifacts import ArtifactConfig
from sos_mvp.engine import execute_program_with_trace
from sos_mvp.parser import parse_text
from sos_mvp.planner import enrich_and_validate
from sos_mvp.resources import ExecutionLimits, ResourceLimitError


class ArtifactQuotaOrderingTests(unittest.TestCase):
    def test_output_quota_is_checked_before_artifact_write(self):
        program = enrich_and_validate(
            parse_text('source value = py{result = "x" * 100}')
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact_root = root / "artifacts"
            with self.assertRaises(ResourceLimitError):
                execute_program_with_trace(
                    program,
                    cwd=root,
                    db_path=root / "out.db",
                    limits=ExecutionLimits(
                        max_nodes=4,
                        max_workers=1,
                        max_output_bytes=10,
                        max_total_output_bytes=1000,
                    ),
                    artifact_config=ArtifactConfig(
                        directory=artifact_root,
                        persist_all=True,
                    ),
                )

            self.assertFalse(any(artifact_root.rglob("*.json")))


if __name__ == "__main__":
    unittest.main()
