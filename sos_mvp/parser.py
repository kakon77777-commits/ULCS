from __future__ import annotations

import re
from pathlib import Path

from .model import InputRef, Node, Program


_HEADER_RE = re.compile(
    r"(?P<role>source|extract|transform|store|run)\s+"
    r"(?P<node>[A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"
    r"(?P<lang>[A-Za-z_][A-Za-z0-9_-]*)\s*$",
    re.IGNORECASE,
)
_FROM_RE = re.compile(
    r"^[ \t\r\n]*from[ \t]+(?P<refs>[^\n#]+)",
    re.IGNORECASE,
)
_REF_RE = re.compile(
    r"^(?P<node>[A-Za-z_][A-Za-z0-9_-]*)"
    r"(?:\.(?P<field>[A-Za-z_][A-Za-z0-9_-]*))?"
    r"(?:[ \t]+as[ \t]+(?P<alias>[A-Za-z_][A-Za-z0-9_-]*))?$",
    re.IGNORECASE,
)

_LANGUAGE_ALIASES = {
    "powershell": "ps",
    "python": "py",
    "sqlite": "sql",
}


class ParseError(ValueError):
    pass


def _skip_space_and_comments(text: str, pos: int) -> int:
    length = len(text)
    while pos < length:
        if text[pos].isspace():
            pos += 1
            continue
        if text.startswith("#", pos):
            newline = text.find("\n", pos)
            return length if newline == -1 else _skip_space_and_comments(text, newline + 1)
        return pos
    return pos


def _find_open_brace(text: str, pos: int) -> tuple[int, str]:
    brace = text.find("{", pos)
    if brace == -1:
        raise ParseError("找不到語言區塊的開啟大括號。")
    return brace, text[pos:brace].strip()


def _scan_block(text: str, open_pos: int) -> tuple[str, int]:
    depth = 1
    pos = open_pos + 1
    start = pos
    quote: str | None = None
    triple = False
    escaped = False

    while pos < len(text):
        ch = text[pos]

        if quote is not None:
            if escaped:
                escaped = False
                pos += 1
                continue
            if ch == "\\":
                escaped = True
                pos += 1
                continue
            if triple:
                if text.startswith(quote * 3, pos):
                    quote = None
                    triple = False
                    pos += 3
                    continue
            elif ch == quote:
                quote = None
                pos += 1
                continue
            pos += 1
            continue

        if ch == "\\":
            pos += 2
            continue
        if ch in ("'", '"'):
            if text.startswith(ch * 3, pos):
                quote = ch
                triple = True
                pos += 3
            else:
                quote = ch
                triple = False
                pos += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:pos].strip("\n"), pos + 1
        pos += 1

    raise ParseError("語言區塊的大括號沒有閉合。")


def _parse_inputs(text: str, after: int) -> tuple[list[InputRef], int]:
    match = _FROM_RE.match(text[after:])
    if not match:
        return [], after

    refs: list[InputRef] = []
    for raw in match.group("refs").split(","):
        item = raw.strip()
        ref_match = _REF_RE.match(item)
        if not ref_match:
            raise ParseError(f"無法解析輸入引用：{item!r}")
        refs.append(
            InputRef(
                node_id=ref_match.group("node"),
                field=ref_match.group("field"),
                alias=ref_match.group("alias"),
            )
        )
    return refs, after + match.end()


def parse_text(text: str) -> Program:
    nodes: list[Node] = []
    pos = 0
    while True:
        pos = _skip_space_and_comments(text, pos)
        if pos >= len(text):
            break

        open_pos, header = _find_open_brace(text, pos)
        match = _HEADER_RE.match(header)
        if not match:
            raise ParseError(f"無法解析區塊標頭：{header[:100]!r}")

        code, after = _scan_block(text, open_pos)
        inputs, after = _parse_inputs(text, after)
        language = match.group("lang").lower()
        language = _LANGUAGE_ALIASES.get(language, language)

        nodes.append(
            Node(
                node_id=match.group("node"),
                role=match.group("role").lower(),
                language=language,
                code=code.strip(),
                inputs=inputs,
            )
        )
        pos = after

    if not nodes:
        raise ParseError("文件中沒有可執行節點。")

    ids: set[str] = set()
    for node in nodes:
        if node.node_id in ids:
            raise ParseError(f"節點名稱重複：{node.node_id}")
        ids.add(node.node_id)
        keys = [ref.key for ref in node.inputs]
        if len(keys) != len(set(keys)):
            raise ParseError(f"節點 {node.node_id} 的多輸入鍵重複；請使用 as 別名。")

    return Program(nodes)


def parse_file(path: str | Path) -> Program:
    return parse_text(Path(path).read_text(encoding="utf-8"))
