from __future__ import annotations

import fnmatch
import importlib
import importlib.metadata
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from typing import Any

from .executors import (
    ExecutionContext,
    ExecutionError,
    LanguageAdapter,
    get_adapter,
    register_adapter,
)


_LOADED_BUILTINS = False
_LOADED_MODULES: set[str] = set()
_LOADED_ENTRYPOINTS: set[str] = set()
_MAX_HTTP_BYTES = 2 * 1024 * 1024


class BashAdapter(LanguageAdapter):
    language = "bash"
    aliases = ("sh",)
    accepted_input_types = frozenset(
        {"None", "Text", "FileList", "MatchList", "Json", "Table", "Any", "InputMap"}
    )
    output_type = "Any"

    def runtime(self) -> str:
        return shutil.which("bash") or "unavailable:bash"

    def effects(self, code: str) -> list[str]:
        lowered = code.lower()
        effects = ["process.execute"]
        if re.search(r"\b(cat|find|ls|grep|sed|awk|head|tail|stat)\b", lowered):
            effects.append("filesystem.read")
        if re.search(r"\b(cp|mv|mkdir|touch|tee|chmod|chown)\b|(^|\s)>(>|\s)", lowered):
            effects.append("filesystem.write")
        if re.search(r"\b(rm|rmdir|shred)\b", lowered):
            effects.append("filesystem.delete")
        if re.search(r"\b(curl|wget|nc|ssh|scp|rsync)\b", lowered):
            effects.append("network.access")
        return sorted(set(effects))

    def execute(self, code: str, input_value: Any, context: ExecutionContext) -> Any:
        executable = shutil.which("bash")
        if not executable:
            raise ExecutionError("找不到 Bash Runtime。")
        payload = json.dumps(input_value, ensure_ascii=False, default=str)
        env = os.environ.copy()
        env["ULCS_INPUT"] = payload
        proc = subprocess.run(
            [executable, "--noprofile", "--norc", "-c", code],
            cwd=context.cwd,
            input=payload,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=context.timeout,
            env=env,
        )
        if proc.returncode != 0:
            raise ExecutionError(f"Bash 執行失敗：{proc.stderr.strip()}")
        return _parse_json_or_text(proc.stdout)


class JavaScriptAdapter(LanguageAdapter):
    language = "js"
    aliases = ("javascript", "node")
    accepted_input_types = frozenset(
        {"None", "Text", "FileList", "MatchList", "Json", "Table", "Any", "InputMap"}
    )
    output_type = "Json"

    def runtime(self) -> str:
        return shutil.which("node") or "unavailable:node"

    def effects(self, code: str) -> list[str]:
        lowered = code.lower()
        effects = ["javascript.execute", "process.execute"]
        if re.search(r"\b(fs|readfile|readdir|stat)\b", lowered):
            effects.append("filesystem.possible")
        if re.search(r"\b(fetch|http|https|net|axios|undici)\b", lowered):
            effects.append("network.possible")
        if re.search(r"\b(child_process|spawn|exec|fork)\b", lowered):
            effects.append("process.spawn.possible")
        return sorted(set(effects))

    def execute(self, code: str, input_value: Any, context: ExecutionContext) -> Any:
        executable = shutil.which("node")
        if not executable:
            raise ExecutionError("找不到 Node.js Runtime。")
        wrapper = f"""
const chunks = [];
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => chunks.push(chunk));
process.stdin.on('end', async () => {{
  const raw = chunks.join('');
  const input = raw ? JSON.parse(raw) : null;
  let result = null;
  try {{
    await (async () => {{
{_indent(code, 6)}
    }})();
    process.stdout.write(JSON.stringify(result));
  }} catch (error) {{
    console.error(error && error.stack ? error.stack : String(error));
    process.exitCode = 1;
  }}
}});
"""
        proc = subprocess.run(
            [executable, "--input-type=commonjs", "-e", wrapper],
            cwd=context.cwd,
            input=json.dumps(input_value, ensure_ascii=False, default=str),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=context.timeout,
        )
        if proc.returncode != 0:
            raise ExecutionError(f"JavaScript 執行失敗：{proc.stderr.strip()}")
        try:
            return json.loads(proc.stdout or "null")
        except json.JSONDecodeError as exc:
            raise ExecutionError(f"JavaScript 沒有輸出合法 JSON：{proc.stdout[:300]}") from exc


class JqAdapter(LanguageAdapter):
    language = "jq"
    accepted_input_types = frozenset({"Text", "Json", "Table", "Any", "InputMap"})
    output_type = "Json"

    def runtime(self) -> str:
        return shutil.which("jq") or "unavailable:jq"

    def effects(self, code: str) -> list[str]:
        return ["process.execute"]

    def execute(self, code: str, input_value: Any, context: ExecutionContext) -> Any:
        executable = shutil.which("jq")
        if not executable:
            raise ExecutionError("找不到 jq Runtime。")
        proc = subprocess.run(
            [executable, "-c", code.strip()],
            cwd=context.cwd,
            input=json.dumps(input_value, ensure_ascii=False, default=str),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=context.timeout,
        )
        if proc.returncode != 0:
            raise ExecutionError(f"jq 執行失敗：{proc.stderr.strip()}")
        values = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        if not values:
            return None
        return values[0] if len(values) == 1 else values


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpAdapter(LanguageAdapter):
    language = "http"
    aliases = ("https",)
    accepted_input_types = frozenset(
        {"None", "Text", "FileList", "MatchList", "Json", "Table", "Any", "InputMap"}
    )
    output_type = "Json"

    def runtime(self) -> str:
        return "python-urllib"

    def effects(self, code: str) -> list[str]:
        return ["network.access"]

    def execute(self, code: str, input_value: Any, context: ExecutionContext) -> Any:
        spec = _http_spec(code)
        url = str(spec["url"])
        _validate_http_url(url)
        _validate_host_allowlist(url)
        method = str(spec.get("method", "GET")).upper()
        headers_raw = spec.get("headers", {})
        if not isinstance(headers_raw, dict):
            raise ExecutionError("HTTP headers 必須是 object。")
        headers = {str(key): str(value) for key, value in headers_raw.items()}

        body_value = spec.get("body")
        if body_value is None and method not in {"GET", "HEAD"}:
            body_value = input_value
        data: bytes | None = None
        if body_value is not None:
            if isinstance(body_value, (dict, list, int, float, bool)):
                data = json.dumps(body_value, ensure_ascii=False).encode("utf-8")
                headers.setdefault("Content-Type", "application/json; charset=utf-8")
            else:
                data = str(body_value).encode("utf-8")

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        opener = urllib.request.build_opener(_SafeRedirectHandler())
        try:
            with opener.open(request, timeout=context.timeout) as response:
                raw = response.read(_MAX_HTTP_BYTES + 1)
                if len(raw) > _MAX_HTTP_BYTES:
                    raise ExecutionError("HTTP 回應超過 2 MiB 限制。")
                text = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
                return {
                    "status": response.status,
                    "url": response.geturl(),
                    "headers": dict(response.headers.items()),
                    "body": _parse_json_or_text(text),
                }
        except urllib.error.HTTPError as exc:
            raw = exc.read(_MAX_HTTP_BYTES + 1)
            text = raw[:_MAX_HTTP_BYTES].decode("utf-8", errors="replace")
            return {
                "status": exc.code,
                "url": exc.geturl(),
                "headers": dict(exc.headers.items()) if exc.headers else {},
                "body": _parse_json_or_text(text),
            }
        except urllib.error.URLError as exc:
            raise ExecutionError(f"HTTP 請求失敗：{exc.reason}") from exc


def ensure_runtime_extensions(module_names: Iterable[str] = ()) -> None:
    global _LOADED_BUILTINS
    if not _LOADED_BUILTINS:
        for adapter in (BashAdapter(), JavaScriptAdapter(), JqAdapter(), HttpAdapter()):
            _register_if_missing(adapter)
        _LOADED_BUILTINS = True
        load_entrypoint_adapters()

    env_modules = [name.strip() for name in os.getenv("ULCS_ADAPTER_MODULES", "").split(",") if name.strip()]
    for module_name in (*env_modules, *module_names):
        load_module_adapters(module_name)


def load_entrypoint_adapters(group: str = "ulcs.adapters") -> list[str]:
    loaded: list[str] = []
    entry_points = importlib.metadata.entry_points()
    selected = entry_points.select(group=group) if hasattr(entry_points, "select") else entry_points.get(group, [])
    for entry_point in selected:
        key = f"{entry_point.group}:{entry_point.name}:{entry_point.value}"
        if key in _LOADED_ENTRYPOINTS:
            continue
        try:
            _register_loaded(entry_point.load())
        except Exception as exc:
            raise ExecutionError(f"載入 Runtime 外掛 {entry_point.name!r} 失敗：{exc}") from exc
        _LOADED_ENTRYPOINTS.add(key)
        loaded.append(entry_point.name)
    return loaded


def load_module_adapters(module_name: str) -> list[str]:
    if module_name in _LOADED_MODULES:
        return []
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise ExecutionError(f"無法載入 Runtime 外掛模組 {module_name!r}：{exc}") from exc

    loaded: list[str] = []
    register = getattr(module, "register", None)
    if callable(register):
        result = register()
        if result is not None:
            loaded.extend(_register_loaded(result))
    elif hasattr(module, "ULCS_ADAPTERS"):
        loaded.extend(_register_loaded(getattr(module, "ULCS_ADAPTERS")))
    else:
        raise ExecutionError(
            f"外掛模組 {module_name!r} 必須提供 register() 或 ULCS_ADAPTERS。"
        )
    _LOADED_MODULES.add(module_name)
    return loaded


def _register_loaded(value: Any) -> list[str]:
    if isinstance(value, type) and issubclass(value, LanguageAdapter):
        value = value()
    if isinstance(value, LanguageAdapter):
        _register_if_missing(value)
        return [value.language]
    if callable(value):
        return _register_loaded(value())
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        loaded: list[str] = []
        for item in value:
            loaded.extend(_register_loaded(item))
        return loaded
    raise ExecutionError(f"外掛沒有回傳 LanguageAdapter：{type(value).__name__}")


def _register_if_missing(adapter: LanguageAdapter) -> None:
    try:
        get_adapter(adapter.language)
    except ExecutionError:
        register_adapter(adapter)


def _http_spec(code: str) -> dict[str, Any]:
    stripped = code.strip()
    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ExecutionError(f"HTTP 區塊 JSON 無法解析：{exc}") from exc
        if not isinstance(value, dict) or "url" not in value:
            raise ExecutionError("HTTP 區塊 JSON 必須包含 url。")
        return value
    if not stripped:
        raise ExecutionError("HTTP 區塊不可為空。")
    return {"url": stripped}


def _validate_http_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ExecutionError("HTTP 適配器只允許 http 與 https。")
    if not parsed.hostname:
        raise ExecutionError("HTTP URL 缺少主機名稱。")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ExecutionError("HTTP 適配器拒絕本機或 .local 目標。")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ExecutionError(f"HTTP 主機無法解析：{host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ExecutionError(f"HTTP 適配器拒絕非公網位址：{address}")


def _validate_host_allowlist(url: str) -> None:
    raw = os.getenv("ULCS_HTTP_ALLOW_HOSTS", "").strip()
    if not raw:
        return
    host = urllib.parse.urlsplit(url).hostname or ""
    patterns = [item.strip() for item in raw.split(",") if item.strip()]
    if not any(fnmatch.fnmatchcase(host, pattern) for pattern in patterns):
        raise ExecutionError(f"HTTP 主機不在 ULCS_HTTP_ALLOW_HOSTS：{host}")


def _parse_json_or_text(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line for line in text.splitlines())
