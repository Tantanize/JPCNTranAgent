"""
transcribe — MCP server
Transcribes audio/video to a JP-style .ass subtitle file.

Backends:
  whisper   openai-whisper large-v2 (default)
  funasr    FunASR SenseVoiceSmall  (faster, good Japanese support)

Tool exposed:
  transcribe(input_path, template_path, output_path?, backend?)
    input_path    : path to audio or video file
    template_path : path to .ass template (must contain [Script Info] and [V4+ Styles]
                    with at least one style ending in 'JP')
    output_path   : optional; defaults to <input_stem>.ass next to input
    backend       : "whisper" (default) or "funasr"
"""

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

# ── lazy model cache ──────────────────────────────────────
_whisper_model = None
_funasr_vad    = None
_funasr_asr    = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        print("[transcribe] Loading Whisper large-v2 ...", file=sys.stderr)
        _whisper_model = whisper.load_model("large-v2")
        print("[transcribe] Whisper loaded.", file=sys.stderr)
    return _whisper_model


def get_funasr_models():
    global _funasr_vad, _funasr_asr
    if _funasr_vad is None:
        from funasr import AutoModel
        print("[transcribe] Loading fsmn-vad ...", file=sys.stderr)
        _funasr_vad = AutoModel(
            model="fsmn-vad",
            max_single_segment_time=8000,
            max_end_silence_time=500,
            device="cuda:0",
            disable_update=True,
        )
        print("[transcribe] Loading Fun-ASR-Nano-2512 ...", file=sys.stderr)
        _funasr_asr = AutoModel(
            model="FunAudioLLM/Fun-ASR-Nano-2512",
            trust_remote_code=True,
            remote_code=str(Path(__file__).parent / "model.py"),
            device="cuda:0",
            hub="hf",
            disable_update=True,
        )
        print("[transcribe] FunASR loaded.", file=sys.stderr)
    return _funasr_vad, _funasr_asr


# ── ASS helpers ───────────────────────────────────────────

def seconds_to_ass_time(seconds: float) -> str:
    cs = round(seconds * 100)
    h  = cs // 360000;  cs %= 360000
    m  = cs // 6000;    cs %= 6000
    s  = cs // 100;     cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ms_to_ass_time(ms: int) -> str:
    return seconds_to_ass_time(ms / 1000.0)


def read_template(template_path: str) -> tuple[list[str], str]:
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    with open(path, encoding="utf-8-sig") as f:
        lines = f.readlines()

    jp_style = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Style:"):
            name = stripped[len("Style:"):].split(",")[0].strip()
            if name.endswith("JP"):
                jp_style = name
                break

    if jp_style is None:
        raise ValueError("Template has no style ending in 'JP'.")

    header       = []
    events_seen  = False
    format_seen  = False
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
            continue
        header.append(line)

    if not events_seen:
        header.append("\n[Events]\n")
    if not format_seen:
        header.append(
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

    return header, jp_style


def build_ass(header_lines: list[str], jp_style: str, segments: list[dict]) -> str:
    """segments: list of {"start": float_sec, "end": float_sec, "text": str}"""
    lines = []
    for seg in segments:
        start = seconds_to_ass_time(seg["start"])
        end   = seconds_to_ass_time(seg["end"])
        text  = seg["text"].strip().replace("\n", " ")
        lines.append(f"Dialogue: 0,{start},{end},{jp_style},,0,0,0,,{text}\n")
    return "".join(header_lines) + "".join(lines)


# def funasr_to_segments(res: list[dict]) -> list[dict]:
#     """Convert FunASR output to subtitle segments."""
#     if not res:
#         return []
#     sentence_info = res[0].get("sentence_info")
#     if sentence_info:
#         return [
#             {"start": s["start"] / 1000.0, "end": s["end"] / 1000.0, "text": s["text"].strip()}
#             for s in sentence_info if s.get("text", "").strip()
#         ]
#     import re
#     segments = []
#     for item in res:
#         text = item.get("text", "").strip()
#         if not text:
#             continue
#         key = item.get("key", "")
#         m = re.search(r"_(\d+)_(\d+)$", key)
#         if m:
#             start_ms, end_ms = int(m.group(1)), int(m.group(2))
#         else:
#             ts = item.get("timestamp", [])
#             if not ts:
#                 continue
#             start_ms, end_ms = int(ts[0][0]), int(ts[-1][1])
#         segments.append({"start": start_ms/1000.0, "end": end_ms/1000.0, "text": text})
#     return segments

    # ── Case 2 (disabled): single merged item with character-level timestamps ─
    # item = res[0]
    # text = item.get("text", "").strip()
    # timestamps = item.get("timestamp", [])
    # if not text or not timestamps:
    #     return []
    # MIN_GAP_MS = 400
    # MAX_SEG_MS = 9000
    # split_ts_indices: list[int] = []
    # seg_start_ts = 0
    # for i in range(1, len(timestamps)):
    #     gap      = timestamps[i][0] - timestamps[i - 1][1]
    #     duration = timestamps[i][1] - timestamps[seg_start_ts][0]
    #     if gap >= MIN_GAP_MS or duration >= MAX_SEG_MS:
    #         split_ts_indices.append(i)
    #         seg_start_ts = i
    # split_ts_indices.append(len(timestamps))
    # ts_len  = len(timestamps)
    # txt_len = len(text)
    # segments: list[dict] = []
    # prev_ts = 0
    # for split_ts in split_ts_indices:
    #     txt_start = min(round(prev_ts  * txt_len / ts_len), txt_len)
    #     txt_end   = min(round(split_ts * txt_len / ts_len), txt_len)
    #     seg_text  = text[txt_start:txt_end].strip()
    #     if seg_text:
    #         start_ms = timestamps[prev_ts][0]
    #         end_ms   = timestamps[min(split_ts - 1, ts_len - 1)][1]
    #         segments.append({"start": start_ms/1000.0, "end": end_ms/1000.0, "text": seg_text})
    #     prev_ts = split_ts
    # return segments


# ── Core transcribe logic ─────────────────────────────────

def run_transcribe(input_path: str, template_path: str,
                   output_path: str | None, backend: str) -> dict:
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    out = Path(output_path) if output_path else input_path.with_suffix(".ass")
    out.parent.mkdir(parents=True, exist_ok=True)

    header_lines, jp_style = read_template(template_path)

    print(f"[transcribe] Backend: {backend}", file=sys.stderr)
    print(f"[transcribe] Transcribing {input_path.name} ...", file=sys.stderr)

    if backend == "funasr":
        import subprocess
        import tempfile
        import torch
        import soundfile as sf

        vad_model, asr_model = get_funasr_models()

        # Step 1: VAD → [[start_ms, end_ms], ...]
        vad_res = vad_model.generate(input=[str(input_path)])
        windows = vad_res[0].get("value", [])
        print(f"[transcribe] VAD: {len(windows)} windows", file=sys.stderr)

        # Step 2: extract 16 kHz mono wav via ffmpeg, then load with soundfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_wav = tmp.name
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path),
             "-ar", "16000", "-ac", "1", "-vn", tmp_wav],
            check=True, capture_output=True,
        )
        audio_np, sr = sf.read(tmp_wav, dtype="float32")
        Path(tmp_wav).unlink(missing_ok=True)
        print(f"[transcribe] Audio loaded: {len(audio_np)/sr:.1f}s @ {sr}Hz", file=sys.stderr)

        # Step 3: ASR per window
        segments = []
        for win in windows:
            start_ms, end_ms = int(win[0]), int(win[1])
            s = int(start_ms * sr / 1000)
            e = int(end_ms   * sr / 1000)
            chunk = audio_np[s:e]
            if len(chunk) < sr * 0.1:   # skip < 100 ms
                continue
            chunk_t = torch.from_numpy(chunk)
            asr_out = asr_model.generate(input=chunk_t, language="日文", itn=True)
            text = asr_out[0].get("text", "").strip() if asr_out else ""
            if text:
                segments.append({
                    "start": start_ms / 1000.0,
                    "end":   end_ms   / 1000.0,
                    "text":  text,
                })

    else:  # whisper (default)
        model  = get_whisper_model()
        result = model.transcribe(
            str(input_path),
            language="ja",
            task="transcribe",
            verbose=False,
        )
        segments = [
            {"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in result.get("segments", [])
        ]

    print(f"[transcribe] Got {len(segments)} segments.", file=sys.stderr)

    ass_content = build_ass(header_lines, jp_style, segments)
    with open(out, "w", encoding="utf-8-sig") as f:
        f.write(ass_content)

    return {
        "output_path": str(out),
        "segments":    len(segments),
        "style_used":  jp_style,
        "backend":     backend,
    }


# ── MCP + HTTP server ─────────────────────────────────────

app = Server("transcribe")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="transcribe",
            description=(
                "Transcribe an audio or video file to a JP-style .ass subtitle file. "
                "backend='whisper' uses Whisper large-v2 (default). "
                "backend='funasr' uses FunASR SenseVoiceSmall (faster). "
                "Requires a .ass template with at least one style ending in 'JP'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "input_path":    {"type": "string", "description": "Absolute path to the audio or video file."},
                    "template_path": {"type": "string", "description": "Absolute path to the .ass template file."},
                    "output_path":   {"type": "string", "description": "Optional output .ass path."},
                    "backend": {
                        "type": "string",
                        "enum": ["whisper", "funasr"],
                        "description": "ASR backend. Default: whisper.",
                        "default": "whisper",
                    },
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
    backend       = arguments.get("backend", "whisper")
    if not input_path:
        return [TextContent(type="text", text="Error: input_path is required.")]
    if not template_path:
        return [TextContent(type="text", text="Error: template_path is required.")]
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, run_transcribe, input_path, template_path, output_path, backend
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}\n\n{traceback.format_exc()}")]


# ── Simple HTTP server ────────────────────────────────────

async def handle_call(request: Request) -> JSONResponse:
    try:
        body      = await request.json()
        tool_name = body.get("tool", "")
        arguments = body.get("arguments", {})
        result_contents = await call_tool(tool_name, arguments)
        text = " ".join(block.text for block in result_contents if hasattr(block, "text"))
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