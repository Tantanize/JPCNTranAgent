"""
ass_translate — MCP server
Fills empty CN Dialogue lines in a bilingual .ass file by translating
the matching JP lines. Supports multiple LLM backends.

Backends:
  claude   claude-sonnet-4-20250514   env: ANTHROPIC_API_KEY
  gemini   gemini-2.5-flash           env: GEMINI_API_KEY
  openai   gpt-4o-mini                env: OPENAI_API_KEY
  ollama   qwen2.5:14b (default)      local Ollama, no key needed

Context window : 5 lines before, 2 lines after (per batch start)
Batch size     : 6 lines per API call

Tool exposed:
  ass_translate(input_path, output_path?, glossary_path?, model?, ollama_model?)
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import pysubs2
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import uvicorn

# ── constants ─────────────────────────────────────────────
CTX_BEFORE    = 5
CTX_AFTER     = 2
BATCH_SIZE    = 6
REQUEST_DELAY = 0.4

DEFAULT_OLLAMA_MODEL = "qwen2.5:14b"


# ── helpers ───────────────────────────────────────────────

def is_jp_style(s: str) -> bool: return s.endswith("JP")
def is_cn_style(s: str) -> bool: return s.endswith("CN")
def is_blank(s: str)    -> bool: return s.strip() in ("", "　")


# ── pair builder ──────────────────────────────────────────

def build_pairs(subs: pysubs2.SSAFile) -> list[dict]:
    bucket: dict[tuple, dict] = {}
    for line in subs:
        if line.type != "Dialogue":
            continue
        key = (line.start, line.end)
        if key not in bucket:
            bucket[key] = {"jp": None, "cn": None}
        if is_jp_style(line.style):
            bucket[key]["jp"] = line
        elif is_cn_style(line.style):
            bucket[key]["cn"] = line
    return [
        pair for _, pair in sorted(bucket.items())
        if pair["jp"] is not None and pair["cn"] is not None
    ]


# ── context builder ───────────────────────────────────────

def build_context(pairs: list[dict], first_idx: int, batch_count: int) -> str:
    lines = []
    for i in range(max(0, first_idx - CTX_BEFORE), first_idx):
        jp = pairs[i]["jp"].plaintext.strip()
        if jp: lines.append(f"[BEFORE] {jp}")
    for i in range(first_idx, min(len(pairs), first_idx + batch_count)):
        jp = pairs[i]["jp"].plaintext.strip()
        if jp: lines.append(f"[TARGET {i - first_idx + 1}] {jp}")
    for i in range(first_idx + batch_count, min(len(pairs), first_idx + batch_count + CTX_AFTER)):
        jp = pairs[i]["jp"].plaintext.strip()
        if jp: lines.append(f"[AFTER] {jp}")
    return "\n".join(lines)


# ── prompt builder (shared across all backends) ───────────

def build_prompt(context_text: str, targets: list[str], glossary: str) -> str:
    return f"""You are a professional Japanese subtitle translator.

Translate the TARGET lines into natural Simplified Chinese.
There are {len(targets)} TARGET line(s). You must output exactly {len(targets)} translation(s).

Context lines (BEFORE / AFTER) are for reference only — do not translate them.

Translation rules:
- Natural spoken Chinese, preserve tone and personality
- Use half-width spaces for pauses instead of commas or periods
- Use 「」for quotation marks
- Keep each subtitle concise

Terminology glossary (must follow):
{glossary if glossary else '（无词汇表）'}

--- CONTEXT ---
{context_text}

--- OUTPUT FORMAT ---
Return ONLY a JSON object: {{"t":["translation 1","translation 2",...]}}
No explanations. No markdown fences. JSON only."""


def parse_translation_response(raw: str) -> list[str]:
    cleaned = re.sub(r"^```[\s\S]*?```$|^```|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(cleaned).get("t", [])


# ── backend: Claude ───────────────────────────────────────

def translate_claude(context_text: str, targets: list[str], glossary: str) -> list[str]:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")
    client  = anthropic.Anthropic(api_key=api_key)
    prompt  = build_prompt(context_text, targets, glossary)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return parse_translation_response(message.content[0].text.strip())


# ── backend: Gemini ───────────────────────────────────────

def translate_gemini(context_text: str, targets: list[str], glossary: str) -> list[str]:
    import google.generativeai as genai
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not set.")
    genai.configure(api_key=api_key)
    model    = genai.GenerativeModel("gemini-2.5-flash")
    prompt   = build_prompt(context_text, targets, glossary)
    response = model.generate_content(prompt)
    return parse_translation_response(response.text.strip())


# ── backend: OpenAI ───────────────────────────────────────

def translate_openai(context_text: str, targets: list[str], glossary: str) -> list[str]:
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set.")
    client   = OpenAI(api_key=api_key)
    prompt   = build_prompt(context_text, targets, glossary)
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return parse_translation_response(response.choices[0].message.content.strip())


# ── backend: Ollama ───────────────────────────────────────

def translate_ollama(context_text: str, targets: list[str], glossary: str,
                     ollama_model: str) -> list[str]:
    import httpx
    prompt   = build_prompt(context_text, targets, glossary)
    payload  = {
        "model":  ollama_model,
        "prompt": prompt,
        "stream": False,
    }
    resp = httpx.post("http://localhost:11434/api/generate", json=payload, timeout=120)
    resp.raise_for_status()
    raw = resp.json().get("response", "").strip()
    return parse_translation_response(raw)


# ── dispatch ──────────────────────────────────────────────

def translate_batch(context_text: str, targets: list[str], glossary: str,
                    model: str, ollama_model: str) -> list[str]:
    if model == "claude":
        return translate_claude(context_text, targets, glossary)
    elif model == "gemini":
        return translate_gemini(context_text, targets, glossary)
    elif model == "openai":
        return translate_openai(context_text, targets, glossary)
    elif model == "ollama":
        return translate_ollama(context_text, targets, glossary, ollama_model)
    else:
        raise ValueError(f"Unknown model: {model!r}. Choose from: claude, gemini, openai, ollama")


# ── core logic ────────────────────────────────────────────

def run_ass_translate(input_path: str, output_path: str | None,
                      glossary_path: str | None, model: str,
                      ollama_model: str) -> dict:
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    out = Path(output_path) if output_path else \
          input_path.with_name(input_path.stem + "_translated.ass")
    out.parent.mkdir(parents=True, exist_ok=True)

    glossary = ""
    if glossary_path:
        gp = Path(glossary_path)
        if gp.exists():
            glossary = gp.read_text(encoding="utf-8").strip()
        else:
            print(f"[ass_translate] Warning: glossary not found at {glossary_path}", file=sys.stderr)

    subs  = pysubs2.load(str(input_path))
    pairs = build_pairs(subs)

    to_translate  = [(i, p) for i, p in enumerate(pairs) if is_blank(p["cn"].plaintext)]
    total         = len(to_translate)
    skipped_count = len(pairs) - total

    print(f"[ass_translate] backend={model}  pairs={len(pairs)}  to_translate={total}  skipped={skipped_count}", file=sys.stderr)

    if total == 0:
        subs.save(str(out))
        return {"output_path": str(out), "translated": 0, "skipped": skipped_count, "model": model}

    translated_count = 0

    for b in range(0, len(to_translate), BATCH_SIZE):
        batch     = to_translate[b: b + BATCH_SIZE]
        first_idx = batch[0][0]
        targets   = [pair["jp"].plaintext.strip() for _, pair in batch]
        ctx       = build_context(pairs, first_idx, len(batch))

        print(f"[ass_translate] Batch {b // BATCH_SIZE + 1}: lines {b + 1}–{b + len(batch)}", file=sys.stderr)

        try:
            translations = translate_batch(ctx, targets, glossary, model, ollama_model)
            for j, (_, pair) in enumerate(batch):
                tr = translations[j].strip() if j < len(translations) else ""
                if tr:
                    pair["cn"].text = tr
                    translated_count += 1
                    print(f"  ✓ {targets[j][:20]}… → {tr[:20]}…", file=sys.stderr)
                else:
                    print(f"  ⚠ empty translation for line {b + j + 1}, skipped", file=sys.stderr)
        except Exception as e:
            print(f"  ✗ batch failed: {e}", file=sys.stderr)

        if b + BATCH_SIZE < len(to_translate):
            time.sleep(REQUEST_DELAY)

    subs.save(str(out))

    return {
        "output_path": str(out),
        "translated":  translated_count,
        "skipped":     skipped_count,
        "model":       model,
    }

app = Server("ass_translate")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="ass_translate",
            description=(
                "Fill empty CN subtitle lines in a bilingual .ass file by translating "
                "the corresponding JP lines. Supports multiple LLM backends: "
                "claude, gemini, openai, ollama. Existing CN content is always preserved."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "input_path":    {"type": "string", "description": "Absolute path to the bilingual .ass file."},
                    "output_path":   {"type": "string", "description": "Optional output path."},
                    "glossary_path": {"type": "string", "description": "Optional path to glossary.txt."},
                    "model": {
                        "type": "string",
                        "enum": ["claude", "gemini", "openai", "ollama"],
                        "description": "LLM backend. Default: gemini.",
                        "default": "gemini",
                    },
                    "ollama_model": {
                        "type": "string",
                        "description": f"Ollama model name. Default: {DEFAULT_OLLAMA_MODEL}.",
                        "default": DEFAULT_OLLAMA_MODEL,
                    },
                },
                "required": ["input_path"],
            },
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name != "ass_translate":
        raise ValueError(f"Unknown tool: {name}")
    input_path    = arguments.get("input_path", "")
    output_path   = arguments.get("output_path")
    glossary_path = arguments.get("glossary_path")
    model         = arguments.get("model", "gemini")
    ollama_model  = arguments.get("ollama_model", DEFAULT_OLLAMA_MODEL)
    if not input_path:
        return [TextContent(type="text", text="Error: input_path is required.")]
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, run_ass_translate, input_path, output_path, glossary_path, model, ollama_model
        )
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
    return JSONResponse({"status": "ok", "server": "ass_translate"})

def make_app() -> Starlette:
    return Starlette(routes=[
        Route("/call",   endpoint=handle_call,   methods=["POST"]),
        Route("/health", endpoint=handle_health, methods=["GET"]),
    ])

async def run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

def main():
    parser = argparse.ArgumentParser(description="ass_translate MCP server")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8773)
    args = parser.parse_args()
    if args.stdio:
        asyncio.run(run_stdio())
    else:
        print(f"[ass_translate] listening on http://{args.host}:{args.port}", file=sys.stderr)
        uvicorn.run(make_app(), host=args.host, port=args.port)

if __name__ == "__main__":
    main()