import unittest

from sos_mvp.parser import parse_text
from sos_mvp.planner import enrich_and_validate


class ParserTests(unittest.TestCase):
    def test_nested_braces_in_python(self):
        program = parse_text(
            """
source a = py{
result = {"outer": {"inner": 1}}
}
transform b = py{
result = input["outer"]
} from a
"""
        )
        self.assertEqual(len(program.nodes), 2)
        self.assertIn('{"outer": {"inner": 1}}', program.nodes[0].code)
        self.assertEqual(program.nodes[1].input_ref.node_id, "a")

    def test_types(self):
        program = enrich_and_validate(
            parse_text(
                """
source files = ps{Get-ChildItem ./logs -Filter *.log}
extract hits = regex{ERROR} from files
transform summary = py{result = {"count": len(input)}} from hits
"""
            )
        )
        self.assertEqual(program.nodes[0].output_type, "FileList")
        self.assertEqual(program.nodes[1].input_type, "FileList")
        self.assertEqual(program.nodes[2].input_type, "MatchList")

    def test_escaped_regex_braces(self):
        program = parse_text(r"extract hits = regex{\{[0-9]{2}\}}")
        self.assertEqual(program.nodes[0].code, r"\{[0-9]{2}\}")


if __name__ == "__main__":
    unittest.main()
