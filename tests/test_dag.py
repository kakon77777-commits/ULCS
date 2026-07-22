import tempfile
import unittest
from pathlib import Path

from sos_mvp.engine import execute_program, final_result
from sos_mvp.model import GraphError
from sos_mvp.parser import parse_text
from sos_mvp.planner import enrich_and_validate


class DagTests(unittest.TestCase):
    def test_forward_reference_and_stable_topological_order(self):
        program = enrich_and_validate(
            parse_text(
                """
transform merged = py{
result = {"sum": input["left"] + input["right"]}
} from left, right
source right = py{result = 7}
source left = py{result = 5}
"""
            )
        )
        self.assertEqual(
            [node.node_id for node in program.topological_nodes()],
            ["right", "left", "merged"],
        )
        self.assertEqual(
            [[node.node_id for node in layer] for layer in program.execution_layers()],
            [["right", "left"], ["merged"]],
        )
        self.assertEqual(program.node_map()["merged"].input_type, "InputMap")

    def test_multiple_inputs_with_aliases_execute(self):
        program = enrich_and_validate(
            parse_text(
                """
source first = py{result = 2}
source second = py{result = 3}
transform total = py{
result = input["a"] + input["b"]
} from first as a, second as b
"""
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = execute_program(program, cwd=root, db_path=root / "out.db")
        self.assertEqual(final_result(program, outputs), 5)

    def test_cycle_is_rejected(self):
        program = parse_text(
            """
transform a = py{result = input} from b
transform b = py{result = input} from a
"""
        )
        with self.assertRaises(GraphError):
            enrich_and_validate(program)

    def test_ir_contains_edges_sinks_capabilities_layers_and_repeatability(self):
        program = enrich_and_validate(
            parse_text(
                """
source a = py{result = 1}
transform b = py{result = input + 1} from a
"""
            )
        )
        ir = program.to_dict()
        self.assertEqual(ir["version"], "0.5")
        self.assertEqual(ir["edges"][0]["from"], "a")
        self.assertEqual(ir["sinks"], ["b"])
        self.assertEqual(ir["nodes"][0]["capabilities"], ["python.execute"])
        self.assertEqual(ir["execution_layers"], [["a"], ["b"]])
        self.assertIn("deterministic", ir["nodes"][0])
        self.assertIn("cacheable", ir["nodes"][0])


if __name__ == "__main__":
    unittest.main()
