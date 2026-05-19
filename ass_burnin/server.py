"""
ass_burnin — MCP server
Burns an .ass subtitle file into a video using ffmpeg.

Tool exposed:
  ass_burnin(video_path, ass_path, output_path?, crf?, preset?)
    video_path  : input video file
    ass_path    : .ass subtitle file to burn in
    output_path : optional; defaults to <video_stem>_subbed.<ext>
    crf         : H.264/H.265 quality (0–51, default 18; lower = better quality)
    preset      : ffmpeg preset (default "slow"); tradeoff encode speed vs file size
"""

import argparse
import asyncio
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import uvicorn


# ── Core burn-in logic ────────────────────────────────────

def run_ass_burnin(
    video_path:  str,
    ass_path:    str,
    output_path: str | None,
    crf:         int,
    preset:      str,
) -> dict:
    video_path = Path(video_path)
    ass_path   = Path(ass_path)

    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not ass_path.exists():
        raise FileNotFoundError(f"ASS file not found: {ass_path}")

    if output_path:
        out = Path(output_path)
    else:
        out = video_path.with_name(video_path.stem + "_subbed" + video_path.suffix)

    out.parent.mkdir(parents=True, exist_ok=True)

    import platform, tempfile, shutil as _shutil

    # ffmpeg's ass/subtitles filter has notorious issues with non-ASCII and
    # Windows paths. Safest fix: copy both input files to temp ASCII paths.
    tmp_dir   = Path(tempfile.gettempdir())
    tmp_video = tmp_dir / ("burnin_input" + video_path.suffix)
    tmp_ass   = tmp_dir / "burnin_sub.ass"
    tmp_out   = tmp_dir / ("burnin_output" + video_path.suffix)

    _shutil.copy2(video_path, tmp_video)
    _shutil.copy2(ass_path,   tmp_ass)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(tmp_video),
        "-vf", "ass=burnin_sub.ass",
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-c:a", "copy",
        str(tmp_out),
    ]

    print(f"[ass_burnin] Running: {' '.join(cmd)}", file=sys.stderr)

    proc = subprocess.run(
        cmd,
        capture_output=True,
        cwd=str(tmp_dir),   # run from temp dir so ass= path is just a filename
    )

    if proc.returncode != 0:
        stderr_text = proc.stderr.decode("utf-8", errors="replace")[-3000:]
        raise RuntimeError(
            f"ffmpeg exited with code {proc.returncode}.\n"
            f"stderr:\n{stderr_text}"
        )

    # Move result to final destination
    _shutil.move(str(tmp_out), str(out))

    size_mb = out.stat().st_size / (1024 * 1024)

    return {
        "output_path": str(out),
        "size_mb":     round(size_mb, 2),
        "crf":         crf,
        "preset":      preset,
    }

app = Server("ass_burnin")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="ass_burnin",
            description=(
                "Burn an .ass subtitle file into a video using ffmpeg (libx264). "
                "Audio is copied without re-encoding. Requires ffmpeg in PATH."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "video_path":  {"type": "string", "description": "Absolute path to input video."},
                    "ass_path":    {"type": "string", "description": "Absolute path to .ass subtitle file."},
                    "output_path": {"type": "string", "description": "Optional output video path."},
                    "crf":         {"type": "integer", "description": "H.264 CRF (default: 18).", "default": 18},
                    "preset":      {"type": "string",  "description": "ffmpeg preset (default: slow).", "default": "slow"},
                },
                "required": ["video_path", "ass_path"],
            },
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name != "ass_burnin":
        raise ValueError(f"Unknown tool: {name}")
    video_path  = arguments.get("video_path", "")
    ass_path    = arguments.get("ass_path", "")
    output_path = arguments.get("output_path")
    crf         = int(arguments.get("crf", 18))
    preset      = arguments.get("preset", "slow")
    if not video_path:
        return [TextContent(type="text", text="Error: video_path is required.")]
    if not ass_path:
        return [TextContent(type="text", text="Error: ass_path is required.")]
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_ass_burnin, video_path, ass_path, output_path, crf, preset)
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
    return JSONResponse({"status": "ok", "server": "ass_burnin"})

def make_app() -> Starlette:
    return Starlette(routes=[
        Route("/call",   endpoint=handle_call,   methods=["POST"]),
        Route("/health", endpoint=handle_health, methods=["GET"]),
    ])

async def run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

def main():
    parser = argparse.ArgumentParser(description="ass_burnin MCP server")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8774)
    args = parser.parse_args()
    if args.stdio:
        asyncio.run(run_stdio())
    else:
        print(f"[ass_burnin] listening on http://{args.host}:{args.port}", file=sys.stderr)
        uvicorn.run(make_app(), host=args.host, port=args.port)

if __name__ == "__main__":
    main()