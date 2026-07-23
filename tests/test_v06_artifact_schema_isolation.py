import tempfile
import unittest
from pathlib import Path

from sos_mvp.artifacts import ArtifactConfig, ArtifactStore


class ArtifactSchemaIsolationTests(unittest.TestCase):
    def test_same_value_different_schema_uses_distinct_objects(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArtifactStore(ArtifactConfig(Path(temp), persist_all=True))
            loose = {"type": "object"}
            strict = {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "integer"}},
            }
            first = store.store({"value": 42}, loose)
            second = store.store({"value": 42}, strict)

            self.assertEqual(first.digest, second.digest)
            self.assertNotEqual(first.schema_digest, second.schema_digest)
            self.assertNotEqual(first.path, second.path)
            self.assertEqual(store.load(first, loose), {"value": 42})
            self.assertEqual(store.load(second, strict), {"value": 42})


if __name__ == "__main__":
    unittest.main()
