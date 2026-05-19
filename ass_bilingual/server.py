"""
ass_bilingual — MCP server
Takes a JP-only .ass file and generates a bilingual .ass:
  - every JP Dialogue line is kept as-is
  - a matching empty CN Dialogue line is inserted after each JP line
  - if a CN line for that (start, end) already exists it is left alone

Tool exposed:
  ass_bilingual(input_path, output_path?)
    input_path  : path to the JP .ass file
    output_path : optional; defaults to <stem>_bilingual.ass next to input
"""

import argparse
import asyncio
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import uvicorn


# ── Core logic (ported from ass_bilingual.py) ─────────────

def run_ass_bilingual(input_path: str, output_path: str | None) -> dict:
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if output_path:
        out = Path(output_path)
    else:
        out = input_path.with_name(input_path.stem + "_bilingual.ass")

    out.parent.mkdir(parents=True, exist_ok=True)

    with open(input_path, encoding="utf-8-sig") as f:
        lines = f.readlines()

    dialogue_pattern = re.compile(r"^Dialogue:")
    dialogue_lines   = []
    existing_cn      = set()

    # Collect all existing dialogue lines and note existing CN entries
    for line in lines:
        if dialogue_pattern.match(line):
            dialogue_lines.append(line)
            parts = line.rstrip("\n").split(",", 9)
            if len(parts) >= 10:
                start = parts[1]
                end   = parts[2]
                style = parts[3]
                if style.endswith("CN"):
                    existing_cn.add((start, end, style))

    new_dialogues   = []
    generated_count = 0

    for line in dialogue_lines:
        new_dialogues.append(line)

        parts = line.rstrip("\n").split(",", 9)
        if len(parts) < 10:
            continue

        start = parts[1]
        end   = parts[2]
        style = parts[3]

        if not style.endswith("JP"):
            continue

        cn_style = style[:-2] + "CN"

        if (start, end, cn_style) in existing_cn:
            continue

        # Insert empty CN line
        new_parts    = parts.copy()
        new_parts[3] = cn_style
        new_parts[9] = ""
        new_dialogues.append(",".join(new_parts) + "\n")
        generated_count += 1

    # Rebuild file: replace all original Dialogue blocks with new_dialogues
    output_lines = []
    inserted     = False
    for line in lines:
        if dialogue_pattern.match(line):
            if not inserted:
                output_lines.extend(new_dialogues)
                inserted = True
            # skip original dialogue lines (already in new_dialogues)
        else:
            output_lines.append(line)

    with open(out, "w", encoding="utf-8-sig") as f:
        f.writelines(output_lines)

    return {
        "output_path":        str(out),
        "generated_cn_lines": generated_count,
    }

app = Server("ass_bilingual")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="ass_bilingual",
            description=(
                "Convert a JP-only .ass subtitle file into a bilingual .ass file. "
                "For each JP Dialogue line an empty CN Dialogue line is inserted immediately after it. "
                "Existing CN lines are preserved as-is."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "input_path": {"type": "string", "description": "Absolute path to the JP-only .ass file."},
                    "output_path": {"type": "string", "description": "Optional output path."},
                },
                "required": ["input_path"],
            },
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name != "ass_bilingual":
        raise ValueError(f"Unknown tool: {name}")
    input_path  = arguments.get("input_path", "")
    output_path = arguments.get("output_path")
    if not input_path:
        return [TextContent(type="text", text="Error: input_path is required.")]
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_ass_bilingual, input_path, output_path)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}\n\n{traceback.format_exc()}")]

# ── Simple HTTP server ────────────────────────────────────
# Plain JSON endpoint: POST /call with {"tool": "...", "arguments": {...}}
# Returns {"result": ...} or {"error": "..."}

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

async def handle_call(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        tool_name = body.get("tool", "")
        arguments = body.get("arguments", {})
        # Call through MCP app
        result_contents = await call_tool(tool_name, arguments)
        text = " ".join(
            block.text for block in result_contents
            if hasattr(block, "text")
        )
        return JSONResponse({"result": text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

async def handle_health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "ass_bilingual"})

def make_app() -> Starlette:
    return Starlette(routes=[
        Route("/call",   endpoint=handle_call,   methods=["POST"]),
        Route("/health", endpoint=handle_health, methods=["GET"]),
    ])

async def run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

def main():
    parser = argparse.ArgumentParser(description="ass_bilingual MCP server")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8772)
    args = parser.parse_args()
    if args.stdio:
        asyncio.run(run_stdio())
    else:
        print(f"[ass_bilingual] listening on http://{args.host}:{args.port}", file=sys.stderr)
        uvicorn.run(make_app(), host=args.host, port=args.port)

if __name__ == "__main__":
    main()