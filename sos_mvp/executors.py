from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import Node


class ExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    cwd: Path
    db_path: Path
    timeout: int = 60


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _extract_field(value: Any, field: str | None) -> Any:
    if field is None:
        return value
    if isinstance(value, dict):
        if field not in value:
            raise ExecutionError(f"輸入物件不存在欄位 {field!r}")
        return value[field]
    if isinstance(value, list):
        extracted = []
        for item in value:
            if not isinstance(item, dict) or field not in item:
                raise ExecutionError(f"列表項目不存在欄位 {field!r}")
            extracted.append(item[field])
        return extracted
    raise ExecutionError(f"無法從 {type(value).__name__} 擷取欄位 {field!r}")


def resolve_input(node: Node, outputs: dict[str, Any]) -> Any:
    if not node.inputs:
        return None
    values = [
        (ref.key, _extract_field(outputs[ref.node_id], ref.field))
        for ref in node.inputs
    ]
    if len(values) == 1:
        return values[0][1]
    return dict(values)


class LanguageAdapter(ABC):
    language: str
    aliases: tuple[str, ...] = ()
    accepted_input_types: frozenset[str] = frozenset({"Any"})
    output_type: str = "Any"

    @abstractmethod
    def runtime(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def effects(self, code: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def execute(self, code: str, input_value: Any, context: ExecutionContext) -> Any:
        raise NotImplementedError


def _file_record(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.resolve()),
        "size": stat.st_size,
        "text": text,
    }


class PowerShellAdapter(LanguageAdapter):
    language = "ps"
    aliases = ("powershell",)
    accepted_input_types = frozenset({"None", "Any", "Json", "Text", "FileList", "InputMap"})
    output_type = "FileList"

    def runtime(self) -> str:
        return shutil.which("pwsh") or shutil.which("powershell") or "portable-powershell-subset"

    def effects(self, code: str) -> list[str]:
        lowered = code.lower()
        effects = ["process.execute"]
        if any(token in lowered for token in ("get-childitem", "get-content", "test-path")):
            effects.append("filesystem.read")
        if any(token in lowered for token in ("set-content", "out-file", "copy-item", "move-item", "new-item")):
            effects.append("filesystem.write")
        if any(token in lowered for token in ("remove-item", "clear-content")):
            effects.append("filesystem.delete")
        if any(token in lowered for token in ("invoke-webrequest", "invoke-restmethod", "curl", "wget")):
            effects.append("network.access")
        return sorted(set(effects))

    def _portable(self, code: str, cwd: Path) -> list[dict[str, Any]]:
        normalized = " ".join(line.strip() for line in code.splitlines() if line.strip())
        match = re.search(
            r"Get-ChildItem(?:\s+-Path)?\s+(?P<path>[^\s|]+)"
            r"(?:\s+-Filter\s+(?P<filter>[^\s|]+))?(?P<rest>.*)$",
            normalized,
            re.IGNORECASE,
        )
        if not match:
            raise ExecutionError(
                "目前環境沒有 pwsh；可攜式替代器只支援 "
                "Get-ChildItem <path> -Filter <pattern> [-Recurse]。"
            )
        raw_path = match.group("path").strip("'\"")
        pattern = (match.group("filter") or "*").strip("'\"")
        recurse = "-recurse" in match.group("rest").lower()
        base = Path(os.path.expandvars(os.path.expanduser(raw_path)))
        if not base.is_absolute():
            base = cwd / base
        if not base.exists():
            raise ExecutionError(f"PowerShell 節點指定的路徑不存在：{base}")
        iterator = base.rglob("*") if recurse else base.glob("*")
        return [
            _file_record(path)
            for path in iterator
            if path.is_file() and fnmatch.fnmatch(path.name, pattern)
        ]

    def execute(self, code: str, input_value: Any, context: ExecutionContext) -> Any:
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            return self._portable(code, context.cwd)

        wrapper = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$result = & {{
{code}
}}
$result | Select-Object FullName, Name, Length, LastWriteTime | ConvertTo-Json -Compress -Depth 6
"""
        proc = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", wrapper],
            cwd=context.cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=context.timeout,
        )
        if proc.returncode != 0:
            raise ExecutionError(f"PowerShell 執行失敗：{proc.stderr.strip()}")
        raw = proc.stdout.strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExecutionError(f"PowerShell 未輸出可解析 JSON：{raw[:300]}") from exc
        if isinstance(data, dict):
            data = [data]
        records = []
        for item in data:
            full_name = item.get("FullName") or item.get("fullName")
            if full_name and Path(full_name).is_file():
                records.append(_file_record(Path(full_name)))
            else:
                records.append(_jsonable(item))
        return records


class RegexAdapter(LanguageAdapter):
    language = "regex"
    accepted_input_types = frozenset({"Text", "FileList", "Json", "Any", "InputMap"})
    output_type = "MatchList"

    def runtime(self) -> str:
        return "python-re"

    def effects(self, code: str) -> list[str]:
        return []

    def execute(self, code: str, input_value: Any, context: ExecutionContext) -> Any:
        pattern_text = code.strip()
        if len(pattern_text) >= 2 and pattern_text.startswith("/") and pattern_text.rfind("/") > 0:
            last = pattern_text.rfind("/")
            pattern_text = pattern_text[1:last]
        try:
            pattern = re.compile(pattern_text, re.MULTILINE)
        except re.error as exc:
            raise ExecutionError(f"Regex 無法編譯：{exc}") from exc

        sources: list[tuple[str, str]] = []
        if isinstance(input_value, str):
            sources.append(("input", input_value))
        elif isinstance(input_value, list):
            for index, item in enumerate(input_value):
                if isinstance(item, dict) and "text" in item:
                    sources.append((str(item.get("path") or item.get("name") or index), str(item["text"])))
                else:
                    sources.append((str(index), str(item)))
        elif isinstance(input_value, dict) and "text" in input_value:
            sources.append((str(input_value.get("path") or "input"), str(input_value["text"])))
        else:
            sources.append(("input", json.dumps(_jsonable(input_value), ensure_ascii=False)))

        matches: list[dict[str, Any]] = []
        for source, text in sources:
            for line_number, line in enumerate(text.splitlines(), start=1):
                for found in pattern.finditer(line):
                    matches.append(
                        {
                            "source": source,
                            "line_number": line_number,
                            "line": line,
                            "match": found.group(0),
                            "groups": list(found.groups()),
                        }
                    )
        return matches


class PythonAdapter(LanguageAdapter):
    language = "py"
    aliases = ("python",)
    accepted_input_types = frozenset(
        {"None", "Text", "FileList", "MatchList", "Json", "Table", "Any", "InputMap"}
    )
    output_type = "Json"

    def runtime(self) -> str:
        return "python-isolated-subprocess"

    def effects(self, code: str) -> list[str]:
        lowered = code.lower()
        effects = ["python.execute"]
        if re.search(r"\b(open|pathlib|os\.|shutil\.)", lowered):
            effects.append("filesystem.possible")
        if re.search(r"\b(requests|urllib|socket|httpx)\b", lowered):
            effects.append("network.possible")
        return sorted(set(effects))

    def execute(self, code: str, input_value: Any, context: ExecutionContext) -> Any:
        wrapper = r'''
import json
import sys

input = json.load(sys.stdin)
result = None
USER_CODE = __USER_CODE__
namespace = {"input": input, "result": result}
exec(compile(USER_CODE, "<sos-py-block>", "exec"), namespace, namespace)
json.dump(namespace.get("result"), sys.stdout, ensure_ascii=False, default=str)
'''.replace("__USER_CODE__", repr(code))

        with tempfile.TemporaryDirectory(prefix="ulcs-py-") as tmp:
            script = Path(tmp) / "runner.py"
            script.write_text(wrapper, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-I", str(script)],
                cwd=context.cwd,
                input=json.dumps(_jsonable(input_value), ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=context.timeout,
            )
        if proc.returncode != 0:
            raise ExecutionError(f"Python 區塊執行失敗：{proc.stderr.strip()}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ExecutionError(f"Python 區塊沒有輸出合法 JSON：{proc.stdout[:300]}") from exc


def _sql_statements(script: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        statements.append(buffer.strip())
    return statements


def _sql_params(input_value: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"input": json.dumps(_jsonable(input_value), ensure_ascii=False)}
    if isinstance(input_value, dict):
        for key, value in input_value.items():
            if value is None or isinstance(value, (str, int, float, bytes)):
                params[str(key)] = value
            else:
                params[str(key)] = json.dumps(_jsonable(value), ensure_ascii=False)
    else:
        params["payload"] = json.dumps(_jsonable(input_value), ensure_ascii=False)
    return params


class SQLiteAdapter(LanguageAdapter):
    language = "sql"
    aliases = ("sqlite",)
    accepted_input_types = frozenset(
        {"None", "Text", "FileList", "MatchList", "Json", "Table", "Any", "InputMap"}
    )
    output_type = "Table"

    def runtime(self) -> str:
        return "sqlite3"

    def effects(self, code: str) -> list[str]:
        lowered = code.lower()
        effects: list[str] = []
        if re.search(r"\b(select|pragma)\b", lowered):
            effects.append("database.read")
        if re.search(r"\b(insert|update|delete|create|drop|alter|replace)\b", lowered):
            effects.append("database.write")
        return sorted(set(effects))

    def execute(self, code: str, input_value: Any, context: ExecutionContext) -> Any:
        context.db_path.parent.mkdir(parents=True, exist_ok=True)
        params = _sql_params(input_value)
        results: list[dict[str, Any]] = []
        affected = 0
        with sqlite3.connect(context.db_path) as connection:
            connection.row_factory = sqlite3.Row
            for statement in _sql_statements(code):
                try:
                    cursor = connection.execute(statement, params)
                except sqlite3.Error as exc:
                    raise ExecutionError(
                        f"SQL 執行失敗：{exc}; statement={statement[:180]!r}"
                    ) from exc
                if cursor.description:
                    results = [dict(row) for row in cursor.fetchall()]
                elif cursor.rowcount and cursor.rowcount > 0:
                    affected += cursor.rowcount
            connection.commit()
        return {
            "database": str(context.db_path.resolve()),
            "affected_rows": affected,
            "rows": results,
        }


_ADAPTERS: dict[str, LanguageAdapter] = {}


def register_adapter(adapter: LanguageAdapter) -> None:
    names = (adapter.language, *adapter.aliases)
    for name in names:
        key = name.lower()
        if key in _ADAPTERS:
            raise RuntimeError(f"Runtime 適配器名稱重複：{key}")
        _ADAPTERS[key] = adapter


def get_adapter(language: str) -> LanguageAdapter:
    try:
        return _ADAPTERS[language.lower()]
    except KeyError as exc:
        supported = ", ".join(sorted({adapter.language for adapter in _ADAPTERS.values()}))
        raise ExecutionError(f"尚未支援語言：{language}；目前支援：{supported}") from exc


def registered_languages() -> list[str]:
    return sorted({adapter.language for adapter in _ADAPTERS.values()})


for _adapter in (PowerShellAdapter(), RegexAdapter(), PythonAdapter(), SQLiteAdapter()):
    register_adapter(_adapter)


def execute_node(
    node: Node,
    outputs: dict[str, Any],
    cwd: Path,
    db_path: Path,
    timeout: int = 60,
) -> Any:
    input_value = resolve_input(node, outputs)
    context = ExecutionContext(cwd=cwd, db_path=db_path, timeout=timeout)
    return get_adapter(node.language).execute(node.code, input_value, context)
