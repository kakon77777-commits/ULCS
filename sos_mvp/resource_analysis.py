from __future__ import annotations

import re
import shlex
import urllib.parse
from pathlib import PurePath

from .resources import CapabilityClaim


_URL_RE = re.compile(r"https?://[^\s'\"}]+", re.IGNORECASE)
_QUOTED_RE = re.compile(r'''(?:"([^"]+)"|'([^']+)')''')
_PATH_HINT_RE = re.compile(
    r"(?:^|[\s=])(?P<path>(?:\./|\.\./|/|~[/\\]|[A-Za-z]:[/\\])[^|;&\n]+)"
)


def analyze_claims(language: str, code: str, effects: list[str]) -> list[CapabilityClaim]:
    """Convert coarse adapter effects into resource-scoped claims.

    This analyzer is intentionally conservative. When a concrete resource cannot
    be extracted without executing user code, the claim uses ``*``.
    """
    language_key = language.lower()
    urls = _urls(code)
    paths = _paths(code)
    claims: list[CapabilityClaim] = []

    for capability in sorted(set(effects)):
        resources = _resources_for(
            capability=capability,
            language=language_key,
            urls=urls,
            paths=paths,
        )
        claims.extend(CapabilityClaim(capability, resource) for resource in resources)

    return sorted(set(claims))


def infer_taint_sources(claims: list[CapabilityClaim]) -> list[str]:
    labels: set[str] = set()
    for claim in claims:
        if claim.capability == "network.access":
            labels.add(f"external.network:{claim.resource}")
        elif claim.capability == "network.possible":
            labels.add("potential.network")
        elif claim.capability == "filesystem.read":
            labels.add(f"external.filesystem:{claim.resource}")
        elif claim.capability == "filesystem.possible":
            labels.add("potential.filesystem")
        elif claim.capability == "database.read":
            labels.add(f"external.database:{claim.resource}")
    return sorted(labels)


def _resources_for(
    *,
    capability: str,
    language: str,
    urls: list[str],
    paths: list[str],
) -> list[str]:
    if capability in {"network.access", "network.possible"}:
        return [_network_resource(url) for url in urls] or ["*"]
    if capability.startswith("filesystem."):
        return paths or ["*"]
    if capability.startswith("database."):
        return ["sqlite://workflow"]
    if capability == "python.execute":
        return ["runtime://python"]
    if capability == "javascript.execute":
        return ["runtime://node"]
    if capability == "process.execute":
        runtime = {
            "bash": "bash",
            "sh": "bash",
            "jq": "jq",
            "ps": "powershell",
            "powershell": "powershell",
            "js": "node",
            "javascript": "node",
            "node": "node",
        }.get(language, language)
        return [f"runtime://{runtime}"]
    if capability == "process.spawn.possible":
        return ["runtime://child-process"]
    return ["*"]


def _network_resource(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "*").lower()
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme.lower()}://{host}{port}"


def _urls(code: str) -> list[str]:
    return sorted(dict.fromkeys(match.rstrip(".,);]") for match in _URL_RE.findall(code)))


def _paths(code: str) -> list[str]:
    candidates: list[str] = []
    for left, right in _QUOTED_RE.findall(code):
        value = left or right
        if _looks_like_path(value):
            candidates.append(value)
    for match in _PATH_HINT_RE.finditer(code):
        value = match.group("path").strip().strip("'\"")
        if _looks_like_path(value):
            candidates.append(value)

    for line in code.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            tokens = shlex.split(stripped, posix=True)
        except ValueError:
            continue
        if not tokens:
            continue
        command = PurePath(tokens[0]).name.lower()
        if command in {
            "cat",
            "find",
            "ls",
            "grep",
            "head",
            "tail",
            "stat",
            "cp",
            "mv",
            "mkdir",
            "touch",
            "rm",
            "rmdir",
            "get-childitem",
            "get-content",
            "test-path",
            "set-content",
            "remove-item",
        }:
            for token in tokens[1:]:
                if token.startswith("-"):
                    continue
                if _looks_like_path(token):
                    candidates.append(token)
                    break

    return sorted(dict.fromkeys(_normalize_path(item) for item in candidates))


def _looks_like_path(value: str) -> bool:
    if not value or value.startswith(("http://", "https://")):
        return False
    return (
        value.startswith(("./", "../", "/", "~/", "~\\"))
        or bool(re.match(r"^[A-Za-z]:[/\\]", value))
        or "/" in value
        or "\\" in value
        or value.endswith((".txt", ".log", ".json", ".csv", ".db", ".sqlite"))
    )


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/")
