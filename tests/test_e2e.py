import tempfile
import unittest
from pathlib import Path

from sos_mvp.engine import execute_program
from sos_mvp.parser import parse_text
from sos_mvp.planner import enrich_and_validate


class EndToEndTests(unittest.TestCase):
    def test_portable_ps_regex_python_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "one.log").write_text(
                "INFO ok\nERROR bad\nFATAL stop\n",
                encoding="utf-8",
            )
            program = enrich_and_validate(
                parse_text(
                    """
source logs = ps{Get-ChildItem ./logs -Filter *.log}
extract errors = regex{ERROR|FATAL} from logs
transform report = py{
result = {"payload": {"count": len(input)}}
} from errors
store saved = sql{
CREATE TABLE IF NOT EXISTS reports(payload TEXT);
INSERT INTO reports(payload) VALUES (:payload);
SELECT payload FROM reports;
} from report
"""
                )
            )
            outputs = execute_program(program, cwd=root, db_path=root / "out.db")
            self.assertEqual(len(outputs["errors"]), 2)
            self.assertEqual(outputs["report"]["payload"]["count"], 2)
            self.assertEqual(len(outputs["saved"]["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
