import tempfile
import unittest
from pathlib import Path

from sos_mvp.engine import execute_program_with_trace
from sos_mvp.parser import parse_text
from sos_mvp.planner import enrich_and_validate
from sos_mvp.provenance import (
    CacheConfig,
    ExecutionManifest,
    ManifestVerificationError,
    digest_value,
    program_digest,
    verify_manifest,
)


class V05ProvenanceTests(unittest.TestCase):
    def _program(self, message: str = "ERROR one\\nINFO two\\nERROR three"):
        return enrich_and_validate(
            parse_text(
                f'''source text = py{{
result = {message!r}
}}
extract errors = regex{{ERROR.*}} from text
'''
            )
        )

    def test_canonical_digest_ignores_mapping_order(self):
        self.assertEqual(
            digest_value({"b": 2, "a": 1}),
            digest_value({"a": 1, "b": 2}),
        )

    def test_program_digest_changes_with_source_definition(self):
        first = self._program("ERROR first")
        second = self._program("ERROR second")
        self.assertNotEqual(program_digest(first), program_digest(second))

    def test_planner_marks_only_deterministic_pure_node_cacheable(self):
        program = self._program()
        nodes = program.node_map()
        self.assertFalse(nodes["text"].deterministic)
        self.assertFalse(nodes["text"].cacheable)
        self.assertTrue(nodes["errors"].deterministic)
        self.assertTrue(nodes["errors"].cacheable)

    def test_second_run_hits_only_cacheable_regex_node(self):
        program = self._program()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = CacheConfig("read-write", root / "cache")
            first = execute_program_with_trace(
                program,
                cwd=root,
                db_path=root / "out.db",
                cache_config=cache,
            )
            second = execute_program_with_trace(
                program,
                cwd=root,
                db_path=root / "out.db",
                cache_config=cache,
            )

        self.assertEqual(first.cache_hits, {"text": False, "errors": False})
        self.assertEqual(second.cache_hits, {"text": False, "errors": True})
        self.assertEqual(first.output_digests, second.output_digests)
        self.assertEqual(first.outputs, second.outputs)

    def test_changed_input_invalidates_downstream_cache_key(self):
        first_program = self._program("ERROR first")
        second_program = self._program("ERROR second")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = CacheConfig("read-write", root / "cache")
            first = execute_program_with_trace(
                first_program,
                cwd=root,
                db_path=root / "out.db",
                cache_config=cache,
            )
            second = execute_program_with_trace(
                second_program,
                cwd=root,
                db_path=root / "out.db",
                cache_config=cache,
            )

        self.assertFalse(first.cache_hits["errors"])
        self.assertFalse(second.cache_hits["errors"])
        self.assertNotEqual(
            first.node_fingerprints["errors"],
            second.node_fingerprints["errors"],
        )

    def test_manifest_replay_ignores_cache_hit_but_checks_output_digest(self):
        program = self._program()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = CacheConfig("read-write", root / "cache")
            first = execute_program_with_trace(
                program,
                cwd=root,
                db_path=root / "out.db",
                cache_config=cache,
            )
            second = execute_program_with_trace(
                program,
                cwd=root,
                db_path=root / "out.db",
                cache_config=cache,
            )

        verify_manifest(first.manifest, second.manifest)
        self.assertFalse(first.manifest.nodes["errors"]["cache_hit"])
        self.assertTrue(second.manifest.nodes["errors"]["cache_hit"])
        self.assertNotIn("outputs", first.manifest.to_dict())

        payload = second.manifest.to_dict()
        payload["nodes"]["errors"]["output_digest"] = "0" * 64
        altered = ExecutionManifest.from_mapping(payload)
        with self.assertRaises(ManifestVerificationError):
            verify_manifest(first.manifest, altered)


if __name__ == "__main__":
    unittest.main()
