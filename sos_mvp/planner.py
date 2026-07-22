from __future__ import annotations

import re
import shutil

from .model import Program


_OUTPUT_TYPES = {
    "ps": "FileList",
    "regex": "MatchList",
    "py": "Json",
    "sql": "Table",
}
_INPUT_TYPES = {
    "ps": {"None", "Any", "Json", "Text", "FileList"},
    "regex": {"Text", "FileList", "Json", "Any"},
    "py": {"None", "Text", "FileList", "MatchList", "Json", "Table", "Any"},
    "sql": {"None", "Text", "FileList", "MatchList", "Json", "Table", "Any"},
}


def _effects(language: str, code: str) -> list[str]:
    lowered = code.lower()
    effects: list[str] = []
    if language == "ps":
        if any(token in lowered for token in ("get-childitem", "get-content", "test-path")):
            effects.append("filesystem.read")
        if any(token in lowered for token in ("set-content", "out-file", "copy-item", "move-item", "new-item")):
            effects.append("filesystem.write")
        if any(token in lowered for token in ("remove-item", "clear-content")):
            effects.append("filesystem.delete")
        if any(token in lowered for token in ("invoke-webrequest", "invoke-restmethod", "curl", "wget")):
            effects.append("network.access")
        effects.append("process.execute")
    elif language == "py":
        effects.append("python.execute")
        if re.search(r"\b(open|pathlib|os\.|shutil\.)", lowered):
            effects.append("filesystem.possible")
        if re.search(r"\b(requests|urllib|socket|httpx)\b", lowered):
            effects.append("network.possible")
    elif language == "sql":
        if re.search(r"\b(select|pragma)\b", lowered):
            effects.append("database.read")
        if re.search(r"\b(insert|update|delete|create|drop|alter|replace)\b", lowered):
            effects.append("database.write")
    return sorted(set(effects))


def enrich_and_validate(program: Program) -> Program:
    by_id = {}
    for node in program.nodes:
        input_type = "None"
        if node.input_ref:
            input_type = by_id[node.input_ref.node_id].output_type
        output_type = _OUTPUT_TYPES[node.language]
        if input_type not in _INPUT_TYPES[node.language]:
            raise TypeError(
                f"節點 {node.node_id}：{node.language} 不接受輸入型別 {input_type}。"
            )
        node.input_type = input_type
        node.output_type = output_type
        node.effects = _effects(node.language, node.code)
        if node.language == "ps":
            node.runtime = shutil.which("pwsh") or shutil.which("powershell") or "portable-powershell-subset"
        elif node.language == "py":
            node.runtime = "python-isolated-subprocess"
        elif node.language == "regex":
            node.runtime = "python-re"
        elif node.language == "sql":
            node.runtime = "sqlite3"
        by_id[node.node_id] = node
    return program


def plan_lines(program: Program) -> list[str]:
    lines = []
    for idx, node in enumerate(program.nodes, start=1):
        source = "無輸入" if not node.input_ref else node.input_ref.node_id + (f".{node.input_ref.field}" if node.input_ref.field else "")
        effects = ", ".join(node.effects) if node.effects else "純計算／未偵測到外部副作用"
        lines.append(
            f"{idx}. {node.node_id} [{node.language}] {node.input_type} → {node.output_type}; "
            f"來源={source}; Runtime={node.runtime}; 副作用={effects}"
        )
    return lines
