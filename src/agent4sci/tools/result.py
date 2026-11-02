"""``submit_result``: structured output through a tool call (works on every provider)."""

from __future__ import annotations

from typing import Any

import jsonschema

from .base import Tool, ToolContext, ToolInputError, ToolResult


class ResultSink:
    def __init__(self, schema: dict[str, Any] | None = None) -> None:
        self.schema = schema
        self.value: Any = None
        self.summary: str = ""
        self.submitted = False


def result_from_text(text: str, schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Recover a result a model wrote into its reply instead of calling ``submit_result``.

    Small / local models often print the JSON (in a code fence or inline) rather than making the tool call. The
    last JSON object in the text that validates against the schema — directly, or as ``{"result": ...}`` — wins.
    """
    import json
    import re

    spans: set[tuple[int, int]] = set()
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            _, length = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        spans.add((match.start(), match.start() + length))
    validator = jsonschema.Draft202012Validator(schema or {"type": "object"})
    # latest object first; for objects ending at the same place, the outermost (a {"result": ..., "summary": ...}
    # wrapper) before what it contains
    for start, end in sorted(spans, key=lambda span: (-span[1], span[0])):
        raw = text[start:end]
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        for candidate in (value.get("result") if isinstance(value, dict) else None, value):
            if isinstance(candidate, dict) and not any(validator.iter_errors(candidate)):
                summary = value.get("summary", "") if isinstance(value, dict) else ""
                return {"result": candidate, "summary": str(summary)}
    return None


class SubmitResultTool(Tool):
    name = "submit_result"
    category = "control"
    parallel_safe = False

    def __init__(self, schema: dict[str, Any] | None = None, description: str | None = None) -> None:
        self.schema = schema or {"type": "object"}
        self.description = description or (
            "Submit the final structured result of this task. Call it exactly once, when the work is done; "
            "`summary` is a short human-readable account of what you did and found."
        )

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "result": self.schema,
                "summary": {"type": "string", "description": "Short summary of what was done and found."},
            },
            "required": ["result", "summary"],
        }

    def parse_args(self, data: dict[str, Any]) -> dict[str, Any]:
        if "result" not in data:
            raise ToolInputError("缺少 result 字段")
        errors = sorted(jsonschema.Draft202012Validator(self.schema).iter_errors(data["result"]), key=str)
        if errors:
            detail = "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errors[:5])
            raise ToolInputError(f"result 不符合要求的结构: {detail}")
        return data

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        sink: ResultSink = ctx.service("result_sink")
        sink.value = args["result"]
        sink.summary = str(args.get("summary", ""))
        sink.submitted = True
        return ToolResult.text("结果已提交。")
