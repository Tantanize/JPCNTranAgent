"""
transcribe — MCP server
Transcribes audio/video to a JP-style .ass subtitle file using local Whisper.

Supports two transport modes:
  --stdio   stdin/stdout (for Claude Desktop / Claude Code)
  --http    HTTP + SSE on localhost (default, for CLI / custom agents)

Tool exposed:
  transcribe(input_path, template_path, output_path?)
    input_path    : path to audio or video file
    template_path : path to .ass template (must contain [Script Info] and [V4+ Styles])
    output_path   : optional output path; defaults to <input_stem>.ass next to input
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

import whisper
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import uvicorn

# ── Whisper model (loaded once at startup) ────────────────
# large-v3: best accuracy, ~6 GB VRAM on 4070 Super
WHISPER_MODEL_NAME = "large-v3"
_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        print(f"[transcribe] Loading Whisper {WHISPER_MODEL_NAME}...", file=sys.stderr)
        _whisper_model = whisper.load_model(WHISPER_MODEL_NAME)
        print("[transcribe] Model loaded.", file=sys.stderr)
    return _whisper_model


# ── ASS helpers ───────────────────────────────────────────

def seconds_to_ass_time(seconds: float) -> str:
    """Convert float seconds to ASS timestamp h:mm:ss.cc"""
    cs  = round(seconds * 100)
    h   = cs // 360000;  cs %= 360000
    m   = cs // 6000;    cs %= 6000
    s   = cs // 100;     cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def read_template(template_path: str) -> tuple[list[str], str]:
    """
    Read a .ass template file.
    Returns (header_lines, first_jp_style_name).
    Raises ValueError if no JP-suffixed style is found.
    """
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    with open(path, encoding="utf-8-sig") as f:
        lines = f.readlines()

    # Find a style whose name ends with JP
    jp_style = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Style:"):
            # Format: Style: Name, ...
            name = stripped[len("Style:"):].split(",")[0].strip()
            if name.endswith("JP"):
                jp_style = name
                break

    if jp_style is None:
        raise ValueError(
            "Template contains no style whose name ends with 'JP'. "
            "Please add at least one JP style (e.g. 'Default JP')."
        )

    # Ensure [Events] section and Format line exist in header
    # We'll inject them ourselves, so strip any existing Dialogue lines
    # and keep everything up to (and including) the Format line.
    header = []
    events_seen = False
    format_seen = False

    for line in lines:
        s = line.strip()
        if s == "[Events]":
            events_seen = True
            header.append(line)
            continue
        if events_seen and s.startswith("Format:"):
            format_seen = True
            header.append(line)
            continue
        if events_seen and s.startswith("Dialogue:"):
            # Skip existing dialogue lines from template
            continue
        header.append(line)

    # If template had no [Events] section, add one
    if not events_seen:
        header.append("\n[Events]\n")
    if not format_seen:
        header.append(
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

    return header, jp_style


def build_ass(header_lines: list[str], jp_style: str, segments: list[dict]) -> str:
    """
    Combine template header with Dialogue lines generated from Whisper segments.
    Each segment becomes one JP Dialogue line.
    """
    dialogue_lines = []
    for seg in segments:
        start = seconds_to_ass_time(seg["start"])
        end   = seconds_to_ass_time(seg["end"])
        text  = seg["text"].strip().replace("\n", " ")
        line  = f"Dialogue: 0,{start},{end},{jp_style},,0,0,0,,{text}\n"
        dialogue_lines.append(line)

    return "".join(header_lines) + "".join(dialogue_lines)


# ── Core transcribe logic ─────────────────────────────────

def run_transcribe(input_path: str, template_path: str, output_path: str | None) -> dict:
    """
    Transcribe input_path with Whisper (Japanese, large-v3),
    merge with template, write .ass file.
    Returns a result dict with output_path and segment count.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Determine output path
    if output_path:
        out = Path(output_path)
    else:
        out = input_path.with_suffix(".ass")

    out.parent.mkdir(parents=True, exist_ok=True)

    # Read template
    header_lines, jp_style = read_template(template_path)

    # Transcribe
    model = get_whisper_model()
    print(f"[transcribe] Transcribing {input_path.name} ...", file=sys.stderr)
    result = model.transcribe(
        str(input_path),
        language="ja",
        task="transcribe",
        verbose=False,
    )

    segments = result.get("segments", [])
    print(f"[transcribe] Got {len(segments)} segments.", file=sys.stderr)

    # Build and write .ass
    ass_content = build_ass(header_lines, jp_style, segments)
    with open(out, "w", encoding="utf-8-sig") as f:
        f.write(ass_content)

    return {
        "output_path": str(out),
        "segments": len(segments),
        "style_used": jp_style,
    }

app = Server("transcribe")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="transcribe",
            description=(
                "Transcribe an audio or video file to a JP-style .ass subtitle file "
                "using local Whisper large-v3 (Japanese). "
                "Requires a .ass template file that contains [Script Info] and [V4+ Styles] "
                "with at least one style whose name ends with 'JP'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "input_path": {"type": "string", "description": "Absolute path to the audio or video file."},
                    "template_path": {"type": "string", "description": "Absolute path to the .ass template file."},
                    "output_path": {"type": "string", "description": "Optional output .ass path."},
                },
                "required": ["input_path", "template_path"],
            },
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name != "transcribe":
        raise ValueError(f"Unknown tool: {name}")
    input_path    = arguments.get("input_path", "")
    template_path = arguments.get("template_path", "")
    output_path   = arguments.get("output_path")
    if not input_path:
        return [TextContent(type="text", text="Error: input_path is required.")]
    if not template_path:
        return [TextContent(type="text", text="Error: template_path is required.")]
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_transcribe, input_path, template_path, output_path)
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
    return JSONResponse({"status": "ok", "server": "transcribe"})

def make_app() -> Starlette:
    return Starlette(routes=[
        Route("/call",   endpoint=handle_call,   methods=["POST"]),
        Route("/health", endpoint=handle_health, methods=["GET"]),
    ])

async def run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

def main():
    parser = argparse.ArgumentParser(description="transcribe MCP server")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    if args.stdio:
        asyncio.run(run_stdio())
    else:
        print(f"[transcribe] listening on http://{args.host}:{args.port}", file=sys.stderr)
        uvicorn.run(make_app(), host=args.host, port=args.port)

if __name__ == "__main__":
    main()
