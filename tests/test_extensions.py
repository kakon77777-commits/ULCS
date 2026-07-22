import importlib
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from sos_mvp.executors import ExecutionContext, ExecutionError, registered_languages
from sos_mvp.extensions import (
    BashAdapter,
    JavaScriptAdapter,
    JqAdapter,
    _http_spec,
    _validate_http_url,
    ensure_runtime_extensions,
    load_module_adapters,
)


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        ensure_runtime_extensions()

    def test_builtin_extension_languages_are_registered(self):
        self.assertTrue({"bash", "js", "jq", "http"} <= set(registered_languages()))

    def test_http_spec_accepts_json_or_plain_url(self):
        self.assertEqual(_http_spec("https://example.com")["url"], "https://example.com")
        self.assertEqual(
            _http_spec('{"url":"https://example.com","method":"GET"}')["method"],
            "GET",
        )

    def test_http_rejects_localhost_before_request(self):
        with self.assertRaises(ExecutionError):
            _validate_http_url("http://localhost/admin")

    def test_module_plugin_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            module_name = "ulcs_test_echo_plugin"
            (root / f"{module_name}.py").write_text(
                textwrap.dedent(
                    """
                    from sos_mvp.executors import LanguageAdapter

                    class EchoAdapter(LanguageAdapter):
                        language = "test_echo"
                        accepted_input_types = frozenset({"None", "Any", "Json", "Text"})
                        output_type = "Any"
                        def runtime(self): return "test-runtime"
                        def effects(self, code): return []
                        def execute(self, code, input_value, context): return input_value

                    ULCS_ADAPTERS = [EchoAdapter()]
                    """
                ),
                encoding="utf-8",
            )
            sys.path.insert(0, str(root))
            try:
                loaded = load_module_adapters(module_name)
                self.assertEqual(loaded, ["test_echo"])
                self.assertIn("test_echo", registered_languages())
            finally:
                sys.path.remove(str(root))
                sys.modules.pop(module_name, None)
                importlib.invalidate_caches()

    @unittest.skipUnless(shutil.which("bash"), "Bash not installed")
    def test_bash_adapter_executes_json_output(self):
        with tempfile.TemporaryDirectory() as temp:
            context = ExecutionContext(Path(temp), Path(temp) / "db.sqlite", 10)
            self.assertEqual(BashAdapter().execute("printf '{\"sum\":3}'", None, context), {"sum": 3})

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_javascript_adapter_executes(self):
        with tempfile.TemporaryDirectory() as temp:
            context = ExecutionContext(Path(temp), Path(temp) / "db.sqlite", 10)
            result = JavaScriptAdapter().execute(
                "result = {sum: input.a + input.b};",
                {"a": 1, "b": 2},
                context,
            )
            self.assertEqual(result, {"sum": 3})

    @unittest.skipUnless(shutil.which("jq"), "jq not installed")
    def test_jq_adapter_executes(self):
        with tempfile.TemporaryDirectory() as temp:
            context = ExecutionContext(Path(temp), Path(temp) / "db.sqlite", 10)
            result = JqAdapter().execute(
                ".items | map(.value) | add",
                {"items": [{"value": 2}, {"value": 5}]},
                context,
            )
            self.assertEqual(result, 7)


if __name__ == "__main__":
    unittest.main()
