from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .artifacts import ArtifactContracts, ArtifactError
from .capabilities import CapabilityPolicy
from .parser import ParseError, parse_text
from .planner import enrich_and_validate
from .provenance import digest_value, plan_digest, program_digest

INTENT_REQUEST_FORMAT = "ULCS-Intent-Request"
INTENT_BUNDLE_FORMAT = "ULCS-Intent-Bundle"
INTENT_VERSION = "0.7"

_PROFILE_LOG = "log-analysis"
_PROFILE_HTTP = "http-json-fetch"
_SUPPORTED_PROFILES = (_PROFILE_LOG, _PROFILE_HTTP)
_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_GLOB_RE = re.compile(r"(?<![\w.])(\*+\.[A-Za-z0-9_-]+)")
_UPPER_TERM_RE = re.compile(r"\b[A-Z][A-Z0-9_-]{2,}\b")
_RESERVED_TERMS = {
    "AI", "API", "CSV", "DB", "GET", "HTTP", "HTTPS", "JSON", "LOG",
    "SQL", "SQLITE", "ULCS", "URL", "UTF",
}


class IntentCompileError(ValueError):
    """Raised when an Intent Request or generated bundle is malformed."""


@dataclass(frozen=True, slots=True)
class IntentRequest:
    intent: str
    profile: str | None = None
    bindings: Mapping[str, Any] = field(default_factory=dict)
    preferences: Mapping[str, Any] = field(default_factory=dict)
    source: str = "inline"

    def __post_init__(self) -> None:
        if not isinstance(self.intent, str) or not self.intent.strip():
            raise IntentCompileError("intent 不可為空。")
        if self.profile is not None and self.profile not in _SUPPORTED_PROFILES:
            raise IntentCompileError(
                f"不支援的 intent profile：{self.profile}；"
                f"目前支援 {', '.join(_SUPPORTED_PROFILES)}。"
            )
        if not isinstance(self.bindings, Mapping):
            raise IntentCompileError("bindings 必須是 JSON object。")
        if not isinstance(self.preferences, Mapping):
            raise IntentCompileError("preferences 必須是 JSON object。")

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        source: str = "mapping",
    ) -> "IntentRequest":
        if not isinstance(payload, Mapping):
            raise IntentCompileError("Intent Request 根節點必須是 JSON object。")
        if payload.get("format") not in {None, INTENT_REQUEST_FORMAT}:
            raise IntentCompileError("Intent Request format 不相容。")
        if payload.get("version") is not None and str(payload["version"]) != INTENT_VERSION:
            raise IntentCompileError("Intent Request version 不相容。")
        intent = payload.get("intent")
        if not isinstance(intent, str):
            raise IntentCompileError("Intent Request 必須包含字串 intent。")
        profile = payload.get("profile")
        bindings = payload.get("bindings", {})
        preferences = payload.get("preferences", {})
        if not isinstance(bindings, Mapping) or not isinstance(preferences, Mapping):
            raise IntentCompileError("bindings 與 preferences 必須是 JSON object。")
        return cls(
            intent=intent,
            profile=str(profile) if profile is not None else None,
            bindings=dict(bindings),
            preferences=dict(preferences),
            source=source,
        )

    @classmethod
    def read(cls, path: str | Path) -> "IntentRequest":
        request_path = Path(path)
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise IntentCompileError(f"Intent Request 不是合法 JSON：{exc}") from exc
        return cls.from_mapping(payload, source=str(request_path.resolve()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": INTENT_REQUEST_FORMAT,
            "version": INTENT_VERSION,
            "intent": self.intent,
            "profile": self.profile,
            "bindings": dict(self.bindings),
            "preferences": dict(self.preferences),
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class IntentBundle:
    request: IntentRequest
    status: str
    profile: str | None
    confidence: float
    assumptions: tuple[str, ...]
    missing_fields: tuple[str, ...]
    risks: tuple[str, ...]
    steps: tuple[Mapping[str, Any], ...]
    workflow: str | None
    contract: Mapping[str, Any] | None
    policy: Mapping[str, Any] | None
    validation: Mapping[str, Any]
    compiler: str = "deterministic-rule-compiler"

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self, *, include_generated: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "format": INTENT_BUNDLE_FORMAT,
            "version": INTENT_VERSION,
            "compiler": self.compiler,
            "status": self.status,
            "profile": self.profile,
            "confidence": self.confidence,
            "request": self.request.to_dict(),
            "assumptions": list(self.assumptions),
            "missing_fields": list(self.missing_fields),
            "risks": list(self.risks),
            "steps": [dict(item) for item in self.steps],
            "validation": dict(self.validation),
        }
        if include_generated:
            payload["generated"] = {
                "workflow": self.workflow,
                "contract": dict(self.contract) if self.contract is not None else None,
                "policy": dict(self.policy) if self.policy is not None else None,
            }
        return payload

    def write(self, directory: str | Path) -> dict[str, str]:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        written: dict[str, str] = {}
        _record_json(target / "intent-plan.json", self.to_dict(include_generated=False), written, "plan")
        if self.workflow is not None:
            path = target / "workflow.sos"
            _atomic_write_text(path, self.workflow.rstrip() + "\n")
            written["workflow"] = str(path)
        if self.contract is not None:
            _record_json(target / "artifact-contract.json", self.contract, written, "contract")
        if self.policy is not None:
            _record_json(target / "capability-policy.json", self.policy, written, "policy")
        metadata = self.to_dict(include_generated=False)
        metadata["files"] = {key: Path(value).name for key, value in written.items()}
        _record_json(target / "intent-bundle.json", metadata, written, "bundle")
        return written


def compile_intent(request: IntentRequest) -> IntentBundle:
    profile = request.profile or _detect_profile(request)
    if profile is None:
        return _incomplete(
            request,
            profile=None,
            missing=("profile",),
            risks=(
                "目前編譯器只支援 log-analysis 與 http-json-fetch；"
                "未辨識的意圖不會被猜測成可執行程式。",
            ),
        )
    if profile == _PROFILE_LOG:
        return _compile_log(request)
    if profile == _PROFILE_HTTP:
        return _compile_http(request)
    raise IntentCompileError(f"不支援的 intent profile：{profile}")


def supported_profiles() -> tuple[str, ...]:
    return _SUPPORTED_PROFILES


def _detect_profile(request: IntentRequest) -> str | None:
    lowered = request.intent.lower()
    if "url" in request.bindings or _URL_RE.search(request.intent):
        if any(token in lowered for token in ("fetch", "request", "get ", "取得", "抓取", "請求", "下載")):
            return _PROFILE_HTTP
    if (
        "text" in request.bindings
        or "source_path" in request.bindings
        or any(token in lowered for token in ("log", "logs", "日誌", "紀錄檔", "記錄檔"))
        or any(term in request.intent.upper() for term in ("ERROR", "FATAL", "WARN"))
    ):
        return _PROFILE_LOG
    return None


def _compile_log(request: IntentRequest) -> IntentBundle:
    bindings = request.bindings
    preferences = request.preferences
    assumptions: list[str] = []
    missing: list[str] = []

    inline_text = bindings.get("text")
    source_path = bindings.get("source_path")
    pattern = bindings.get("pattern")
    if inline_text is not None and not isinstance(inline_text, str):
        raise IntentCompileError("bindings.text 必須是字串。")
    if source_path is not None and not isinstance(source_path, str):
        raise IntentCompileError("bindings.source_path 必須是字串。")
    if pattern is not None and not isinstance(pattern, str):
        raise IntentCompileError("bindings.pattern 必須是字串。")
    if inline_text is None and source_path is None:
        source_path = _extract_source_path(request.intent)
    if inline_text is None and not source_path:
        missing.append("bindings.text 或 bindings.source_path")

    terms = _extract_terms(request)
    if not terms:
        missing.append("bindings.terms")
    if pattern is None:
        pattern = _extract_glob(request.intent)
    if inline_text is None and pattern is None:
        pattern = "*.log"
        assumptions.append("未指定檔案 pattern，依 log-analysis profile 使用 *.log。")

    recursive = _as_bool(
        bindings.get("recursive", preferences.get("recursive")),
        default=_mentions_recursion(request.intent),
        field_name="recursive",
    )
    include_matches = _as_bool(
        preferences.get("include_matches"), default=True, field_name="include_matches"
    )
    persist_summary = _as_bool(
        preferences.get("persist_summary"), default=True, field_name="persist_summary"
    )
    if missing:
        return _incomplete(
            request,
            profile=_PROFILE_LOG,
            missing=tuple(missing),
            assumptions=tuple(assumptions),
            risks=("檔案來源或匹配詞不足時，不會自行選擇資料或正則表達式。",),
        )

    normalized_terms = tuple(
        dict.fromkeys(str(item).strip() for item in terms if str(item).strip())
    )
    if not normalized_terms:
        raise IntentCompileError("terms 不可全部為空。")
    if any("\n" in term or "\r" in term for term in normalized_terms):
        raise IntentCompileError("terms 不可包含換行。")
    if source_path and any(ch.isspace() for ch in source_path):
        return _incomplete(
            request,
            profile=_PROFILE_LOG,
            missing=("不含空白的 source_path，或改用 bindings.text",),
            assumptions=tuple(assumptions),
            risks=("v0.7 可攜式 PowerShell 子集無法可靠處理含空白的來源路徑。",),
        )
    if (
        inline_text is None
        and source_path
        and "/" not in source_path
        and "\\" not in source_path
        and not source_path.startswith(".")
    ):
        source_path = f"./{source_path}"
        assumptions.append("相對目錄已正規化為 ./ 前綴，以產生可審查的資源範圍。")

    regex_code = rf"(?i)\b({'|'.join(re.escape(term) for term in normalized_terms)})\b.*"
    terms_literal = json.dumps(list(normalized_terms), ensure_ascii=False)

    if inline_text is not None:
        source_node = "text"
        source_block = f"source text = py{{\nresult = {json.dumps(inline_text, ensure_ascii=False)}\n}}"
        source_schema: dict[str, Any] = {"type": "string"}
        assumptions.append("bindings.text 以不可變字串嵌入生成的 workflow。")
    else:
        assert source_path is not None and pattern is not None
        source_node = "files"
        recurse = " -Recurse" if recursive else ""
        source_block = (
            "source files = ps{\n"
            f"Get-ChildItem {_powershell_literal(source_path)} "
            f"-Filter {_powershell_literal(pattern)}{recurse}\n"
            "}"
        )
        source_schema = {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "path", "size", "text"],
                "properties": {
                    "name": {"type": "string"},
                    "path": {"type": "string"},
                    "size": {"type": "integer"},
                    "text": {"type": "string"},
                },
            },
        }

    summary = [
        f"terms = {terms_literal}",
        "counts = {term: 0 for term in terms}",
        "items = input or []",
        "for item in items:",
        "    groups = item.get('groups') or []",
        "    key = str(groups[0]) if groups else 'UNKNOWN'",
        "    canonical = next((term for term in terms if term.casefold() == key.casefold()), key)",
        "    counts[canonical] = counts.get(canonical, 0) + 1",
        (
            "result = {'total': len(items), 'counts': counts, 'matches': items}"
            if include_matches
            else "result = {'total': len(items), 'counts': counts}"
        ),
    ]
    summary_code = "\n".join(summary)
    workflow = (
        f"{source_block}\n\n"
        f"extract matches = regex{{\n{regex_code}\n}} from {source_node}\n\n"
        f"transform summary = py{{\n{summary_code}\n}} from matches\n"
    )

    match_schema = {
        "type": "object",
        "required": ["source", "line_number", "line", "match", "groups"],
        "properties": {
            "source": {"type": "string"},
            "line_number": {"type": "integer"},
            "line": {"type": "string"},
            "match": {"type": "string"},
            "groups": {"type": "array", "items": {"type": "string"}},
        },
    }
    summary_properties: dict[str, Any] = {
        "total": {"type": "integer"},
        "counts": {"type": "object"},
    }
    required = ["total", "counts"]
    if include_matches:
        summary_properties["matches"] = {"type": "array", "items": match_schema}
        required.append("matches")
    contract = {
        "format": "ULCS-Artifact-Contract",
        "version": "0.6",
        "nodes": {
            source_node: {"output_schema": source_schema},
            "matches": {
                "input_schema": source_schema,
                "output_schema": {"type": "array", "items": match_schema},
            },
            "summary": {
                "input_schema": {"type": "array", "items": match_schema},
                "output_schema": {
                    "type": "object",
                    "required": required,
                    "properties": summary_properties,
                    "additionalProperties": False,
                },
                "persist": persist_summary,
            },
        },
    }
    steps = (
        {
            "id": source_node,
            "action": "read-inline-text" if inline_text is not None else "enumerate-files",
            "language": "py" if inline_text is not None else "ps",
            "output": "text" if inline_text is not None else "file-list",
        },
        {"id": "matches", "action": "extract-lines", "language": "regex", "terms": list(normalized_terms)},
        {"id": "summary", "action": "aggregate-counts", "language": "py", "include_matches": include_matches},
    )
    risks = [
        "生成的 regex 只做逐行匹配，不處理跨行事件。",
        "來源內容會進入記憶體與可能的 Artifact Store。",
    ]
    if inline_text is None:
        risks.append("PowerShell 節點具有 process.execute 與 filesystem.read 能力。")
    return _finalize(
        request=request,
        profile=_PROFILE_LOG,
        assumptions=tuple(assumptions),
        risks=tuple(risks),
        steps=steps,
        workflow=workflow,
        contract=contract,
        confidence=0.94 if bindings.get("terms") else 0.86,
    )


def _compile_http(request: IntentRequest) -> IntentBundle:
    raw_url = request.bindings.get("url")
    if raw_url is not None and not isinstance(raw_url, str):
        raise IntentCompileError("bindings.url 必須是字串。")
    url = raw_url or _extract_url(request.intent)
    if not url:
        return _incomplete(
            request,
            profile=_PROFILE_HTTP,
            missing=("bindings.url",),
            risks=("沒有 URL 時不會猜測網路端點。",),
        )
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise IntentCompileError("URL 必須是有效的 http 或 https URL。")
    method = str(request.bindings.get("method", "GET")).upper()
    if method not in {"GET", "HEAD"}:
        return _incomplete(
            request,
            profile=_PROFILE_HTTP,
            missing=("v0.7 僅接受 GET 或 HEAD；其他方法需明確後續版本治理",),
            risks=("具有請求主體或修改語義的 HTTP 方法不會自動生成。",),
        )
    persist = _as_bool(
        request.preferences.get("persist_response"),
        default=True,
        field_name="persist_response",
    )
    spec = json.dumps({"url": url, "method": method}, ensure_ascii=False, indent=2)
    workflow = (
        f"source response = http{{\n{spec}\n}}\n\n"
        "transform result = py{\n"
        "body = input.get('body') if isinstance(input, dict) else input\n"
        "result = {\n"
        "    'status': input.get('status') if isinstance(input, dict) else None,\n"
        "    'url': input.get('url') if isinstance(input, dict) else None,\n"
        "    'body': body,\n"
        "}\n"
        "} from response\n"
    )
    response_schema = {
        "type": "object",
        "required": ["status", "url", "headers", "body"],
        "properties": {
            "status": {"type": "integer"},
            "url": {"type": "string"},
            "headers": {"type": "object"},
        },
    }
    result_schema = {
        "type": "object",
        "required": ["status", "url", "body"],
        "properties": {
            "status": {"type": ["integer", "null"]},
            "url": {"type": ["string", "null"]},
        },
    }
    contract = {
        "format": "ULCS-Artifact-Contract",
        "version": "0.6",
        "nodes": {
            "response": {"output_schema": response_schema},
            "result": {
                "input_schema": response_schema,
                "output_schema": result_schema,
                "persist": persist,
            },
        },
    }
    steps = (
        {
            "id": "response",
            "action": "http-request",
            "language": "http",
            "method": method,
            "origin": f"{parsed.scheme}://{parsed.hostname}",
        },
        {
            "id": "result",
            "action": "project-response",
            "language": "py",
            "fields": ["status", "url", "body"],
        },
    )
    return _finalize(
        request=request,
        profile=_PROFILE_HTTP,
        assumptions=("HTTP 回應 body 可能是 JSON 或 UTF-8 文字。",),
        risks=(
            "此工作流會存取外部網路，回應內容視為不可信資料。",
            "HTTP adapter 有 2 MiB 回應上限，且不提供憑證或秘密注入。",
        ),
        steps=steps,
        workflow=workflow,
        contract=contract,
        confidence=0.96 if raw_url else 0.90,
    )


def _finalize(
    *,
    request: IntentRequest,
    profile: str,
    assumptions: tuple[str, ...],
    risks: tuple[str, ...],
    steps: tuple[Mapping[str, Any], ...],
    workflow: str,
    contract: Mapping[str, Any],
    confidence: float,
) -> IntentBundle:
    errors: list[str] = []
    validation: dict[str, Any] = {
        "parser": "pending",
        "graph": "pending",
        "contract": "pending",
        "policy": "pending",
        "errors": errors,
    }
    policy_payload: dict[str, Any] | None = None
    try:
        program = parse_text(workflow)
        validation["parser"] = "passed"
        ArtifactContracts.from_mapping(contract).apply(program)
        validation["contract"] = "passed"
        program = enrich_and_validate(program)
        validation["graph"] = "passed"

        claims = sorted({claim.token for node in program.nodes for claim in node.claims})
        used = {claim.capability for node in program.nodes for claim in node.claims}
        candidates = (
            "filesystem.delete@*",
            "filesystem.write@*",
            "network.possible@*",
            "process.spawn.possible@*",
        )
        deny = [item for item in candidates if item.split("@", 1)[0] not in used]
        limits = {
            "max_nodes": max(8, len(program.nodes) + 2),
            "max_workers": 4,
            "max_output_bytes": 2_097_152,
            "max_total_output_bytes": 8_388_608,
        }
        policy_payload = {
            "format": "ULCS-Capability-Policy",
            "version": INTENT_VERSION,
            "mode": "enforce",
            "allow": claims,
            "deny": deny,
            "limits": limits,
        }
        policy = CapabilityPolicy(
            mode="enforce",
            allow=tuple(claims),
            deny=tuple(deny),
            source="intent-compiler",
        )
        decisions = policy.check_program(program)
        validation.update(
            {
                "policy": "passed",
                "required_claims": claims,
                "decisions": [
                    {
                        "node_id": item.node_id,
                        "allowed_claims": list(item.allowed_claims),
                        "denied_claims": list(item.denied_claims),
                    }
                    for item in decisions
                ],
                "program_digest": program_digest(program),
                "plan_digest": plan_digest(program),
                "log_digest": digest_value(program.to_dict()),
                "execution_layers": [
                    [node.node_id for node in layer]
                    for layer in program.execution_layers()
                ],
            }
        )
    except (ParseError, TypeError, ArtifactError, ValueError, PermissionError) as exc:
        errors.append(str(exc))
        return IntentBundle(
            request=request,
            status="rejected",
            profile=profile,
            confidence=0.0,
            assumptions=assumptions,
            missing_fields=(),
            risks=risks + ("生成產物未通過 ULCS validator，因此不可執行。",),
            steps=steps,
            workflow=workflow,
            contract=contract,
            policy=policy_payload,
            validation=validation,
        )
    return IntentBundle(
        request=request,
        status="ready",
        profile=profile,
        confidence=max(0.0, min(1.0, confidence)),
        assumptions=assumptions,
        missing_fields=(),
        risks=risks,
        steps=steps,
        workflow=workflow,
        contract=contract,
        policy=policy_payload,
        validation=validation,
    )


def _incomplete(
    request: IntentRequest,
    *,
    profile: str | None,
    missing: tuple[str, ...],
    assumptions: tuple[str, ...] = (),
    risks: tuple[str, ...] = (),
) -> IntentBundle:
    return IntentBundle(
        request=request,
        status="needs_clarification",
        profile=profile,
        confidence=0.0,
        assumptions=assumptions,
        missing_fields=missing,
        risks=risks,
        steps=(),
        workflow=None,
        contract=None,
        policy=None,
        validation={
            "parser": "not-run",
            "graph": "not-run",
            "contract": "not-run",
            "policy": "not-run",
            "errors": [],
        },
    )


def _extract_terms(request: IntentRequest) -> list[str]:
    raw = request.bindings.get("terms")
    if raw is not None:
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise IntentCompileError("bindings.terms 必須是字串陣列。")
        return raw
    terms = [
        term
        for term in _UPPER_TERM_RE.findall(request.intent)
        if term.upper() not in _RESERVED_TERMS
    ]
    return list(dict.fromkeys(terms))


def _extract_glob(text: str) -> str | None:
    match = _GLOB_RE.search(text)
    if match:
        return match.group(1)
    extension = re.search(r"(?<![\w*])\.([A-Za-z0-9_-]{1,12})\b", text)
    return f"*.{extension.group(1)}" if extension else None


def _extract_source_path(text: str) -> str | None:
    patterns = (
        r"(?:目錄|資料夾)\s*[`\"']?([./~A-Za-z0-9_\\-]+)[`\"']?",
        r"[`\"']?([./~A-Za-z0-9_\\-]+)[`\"']?\s*(?:目錄|資料夾)",
        r"(?:under|in|from)\s+[`\"']?([./~A-Za-z0-9_\\-]+)[`\"']?\s+(?:directory|folder|path)",
        r"(?:directory|folder|path)\s+[`\"']?([./~A-Za-z0-9_\\-]+)[`\"']?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,);]") if match else None


def _mentions_recursion(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in ("recursive", "recursively", "subdirector", "遞迴", "子目錄", "所有層級")
    )


def _as_bool(value: Any, *, default: bool, field_name: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise IntentCompileError(f"{field_name} 必須是 boolean。")


def _powershell_literal(value: str) -> str:
    if any(ch in value for ch in ("\x00", "\r", "\n")):
        raise IntentCompileError("PowerShell literal 不可包含 NUL 或換行。")
    return "'" + value.replace("'", "''") + "'"


def _record_json(
    path: Path,
    payload: Mapping[str, Any],
    written: dict[str, str],
    key: str,
) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    written[key] = str(path)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass
