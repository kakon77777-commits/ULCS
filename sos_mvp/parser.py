from __future__ import annotations

import re
from pathlib import Path

from .model import InputRef, Node, Program


_HEADER_RE = re.compile(
    r"(?P<role>source|extract|transform|store|run)\s+"
    r"(?P<node>[A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"
    r"(?P<lang>ps|powershell|py|python|regex|sql)\s*$",
    re.IGNORECASE,
)
_FROM_RE = re.compile(
    r"^\s*from\s+(?P<node>[A-Za-z_][A-Za-z0-9_-]*)(?:\.(?P<field>[A-Za-z_][A-Za-z0-9_-]*))?\s*",
    re.IGNORECASE,
)


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
            excerpt = header[:100]
            raise ParseError(f"無法解析區塊標頭：{excerpt!r}")

        code, after = _scan_block(text, open_pos)
        from_match = _FROM_RE.match(text[after:])
        input_ref = None
        if from_match:
            input_ref = InputRef(from_match.group("node"), from_match.group("field"))
            after += from_match.end()

        lang = match.group("lang").lower()
        if lang == "powershell":
            lang = "ps"
        elif lang == "python":
            lang = "py"

        nodes.append(
            Node(
                node_id=match.group("node"),
                role=match.group("role").lower(),
                language=lang,
                code=code.strip(),
                input_ref=input_ref,
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
        if node.input_ref and node.input_ref.node_id not in ids:
            raise ParseError(f"節點 {node.node_id} 引用了尚未定義的節點 {node.input_ref.node_id}")

    return Program(nodes)


def parse_file(path: str | Path) -> Program:
    return parse_text(Path(path).read_text(encoding="utf-8"))
