import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sos_mvp.artifacts import (
    ArtifactConfig,
    ArtifactContracts,
    ArtifactError,
    ArtifactRef,
    ArtifactStore,
    CheckpointError,
    ExecutionCheckpoint,
    SchemaValidationError,
    validate_schema,
)
from sos_mvp.engine import execute_program_with_trace
from sos_mvp.parser import parse_text
from sos_mvp.planner import enrich_and_validate


class V06ArtifactTests(unittest.TestCase):
    def _program(self):
        program = parse_text(
            '''source first = py{result = {"value": 20}}
transform second = py{result = {"value": input["value"] + 22}} from first
'''
        )
        contracts = ArtifactContracts.from_mapping(
            {
                "format": "ULCS-Artifact-Contract",
                "version": "0.6",
                "nodes": {
                    "first": {
                        "output_schema": {
                            "type": "object",
                            "required": ["value"],
                            "properties": {"value": {"type": "integer"}},
                            "additionalProperties": False,
                        }
                    },
                    "second": {
                        "input_schema": {
                            "type": "object",
                            "required": ["value"],
                            "properties": {"value": {"type": "integer"}},
                        },
                        "output_schema": {
                            "type": "object",
                            "required": ["value"],
                            "properties": {"value": {"type": "integer"}},
                        },
                        "persist": True,
                    },
                },
            }
        )
        contracts.apply(program)
        return enrich_and_validate(program)

    def test_schema_subset_checks_required_type_and_items(self):
        schema = {
            "type": "object",
            "required": ["values"],
            "properties": {
                "values": {"type": "array", "items": {"type": "integer"}}
            },
            "additionalProperties": False,
        }
        validate_schema({"values": [1, 2]}, schema)
        with self.assertRaises(SchemaValidationError):
            validate_schema({"values": [1, "2"]}, schema)
        with self.assertRaises(SchemaValidationError):
            validate_schema({"other": []}, schema)

    def test_contract_is_exposed_in_log_and_changes_program_definition(self):
        program = self._program()
        node = program.node_map()["second"]
        self.assertTrue(node.persist_output)
        self.assertEqual(node.input_schema["type"], "object")
        ir = program.to_dict()
        self.assertEqual(ir["version"], "0.6")
        self.assertTrue(ir["nodes"][1]["persist_output"])

    def test_artifact_roundtrip_detects_tamper_and_path_escape(self):
        schema = {"type": "object", "required": ["value"]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ArtifactStore(ArtifactConfig(root, persist_all=True))
            ref = store.store({"value": 42}, schema)
            self.assertEqual(store.load(ref, schema), {"value": 42})

            object_path = root / ref.path
            payload = json.loads(object_path.read_text(encoding="utf-8"))
            payload["value"]["value"] = 41
            object_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ArtifactError):
                store.load(ref, schema)

            escaped = ArtifactRef(
                digest=ref.digest,
                media_type=ref.media_type,
                encoding=ref.encoding,
                size=ref.size,
                path="../escape.json",
                schema_digest=ref.schema_digest,
            )
            with self.assertRaises(ArtifactError):
                store.load(escaped, schema)

    def test_checkpoint_resume_uses_artifacts_without_runtime(self):
        program = self._program()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = ArtifactConfig(root / "artifacts", persist_all=True)
            checkpoint_path = root / "checkpoint.json"
            first = execute_program_with_trace(
                program,
                cwd=root,
                db_path=root / "out.db",
                artifact_config=artifacts,
                checkpoint_path=checkpoint_path,
            )
            checkpoint = ExecutionCheckpoint.read(checkpoint_path)
            with patch("sos_mvp.engine.execute_node") as execute:
                second = execute_program_with_trace(
                    program,
                    cwd=root,
                    db_path=root / "out.db",
                    artifact_config=artifacts,
                    checkpoint_path=checkpoint_path,
                    resume_checkpoint=checkpoint,
                )
                execute.assert_not_called()

        self.assertEqual(first.outputs, second.outputs)
        self.assertEqual(second.resumed, {"first": True, "second": True})
        self.assertTrue(all(ref is not None for ref in second.artifacts.values()))
        self.assertEqual(
            second.manifest.nodes["second"]["artifact_digest"],
            second.artifacts["second"].digest,
        )

    def test_checkpoint_rejects_changed_program_or_contract(self):
        program = self._program()
        changed = parse_text(
            '''source first = py{result = {"value": 21}}
transform second = py{result = {"value": input["value"] + 22}} from first
'''
        )
        ArtifactContracts.from_mapping(
            {
                "format": "ULCS-Artifact-Contract",
                "version": "0.6",
                "nodes": {"second": {"persist": True}},
            }
        ).apply(changed)
        changed = enrich_and_validate(changed)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = ArtifactConfig(root / "artifacts", persist_all=True)
            checkpoint_path = root / "checkpoint.json"
            execute_program_with_trace(
                program,
                cwd=root,
                db_path=root / "out.db",
                artifact_config=artifacts,
                checkpoint_path=checkpoint_path,
            )
            checkpoint = ExecutionCheckpoint.read(checkpoint_path)
            with self.assertRaises(CheckpointError):
                execute_program_with_trace(
                    changed,
                    cwd=root,
                    db_path=root / "out.db",
                    artifact_config=artifacts,
                    resume_checkpoint=checkpoint,
                )

    def test_input_schema_rejects_before_runtime(self):
        program = enrich_and_validate(parse_text("source value = py{result = 1}"))
        program.node_map()["value"].input_schema = {"type": "string"}
        with tempfile.TemporaryDirectory() as temp, patch(
            "sos_mvp.engine.execute_node"
        ) as execute:
            with self.assertRaises(SchemaValidationError):
                execute_program_with_trace(
                    program,
                    cwd=Path(temp),
                    db_path=Path(temp) / "out.db",
                )
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
