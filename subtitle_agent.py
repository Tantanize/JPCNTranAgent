"""
subtitle_agent.py — CLI agent
Orchestrates the four MCP servers to produce a subtitled video from raw audio/video.

Pipeline:
  1. transcribe      audio/video → JP-only .ass
  2. ass_bilingual   JP-only .ass → bilingual .ass (empty CN lines)
  3. ass_translate   bilingual .ass → translated .ass
  4. ass_burnin      video + translated .ass → final video

Usage:
  python subtitle_agent.py \\
    --input   /path/to/video.mp4 \\
    --template /path/to/template.ass \\
    [--glossary /path/to/glossary.txt] \\
    [--output  /path/to/output.mp4] \\
    [--workdir /path/to/workdir] \\
    [--crf 18] [--preset slow] \\
    [--skip-transcribe  /path/to/existing.ass] \\
    [--skip-bilingual   /path/to/existing_bilingual.ass] \\
    [--skip-translate   /path/to/existing_translated.ass] \\
    [--skip-burnin]

Each MCP server must already be running (HTTP mode) on its default port:
  transcribe   : 8771
  ass_bilingual: 8772
  ass_translate: 8773
  ass_burnin   : 8774
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

# ── MCP HTTP client ───────────────────────────────────────

PORTS = {
    "transcribe":    8771,
    "ass_bilingual": 8772,
    "ass_translate": 8773,
    "ass_burnin":    8774,
}

def call_tool(server: str, tool: str, arguments: dict) -> dict:
    port = PORTS[server]
    url  = f"http://127.0.0.1:{port}/call"
    print(f"\n▶ [{server}] calling tool '{tool}'", flush=True)
    print(f"  args: {json.dumps(arguments, ensure_ascii=False)}", flush=True)
    with httpx.Client(timeout=3600) as client:
        resp = client.post(url, json={"tool": tool, "arguments": arguments})
        resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"[{server}] error: {data['error']}")
    text = data.get("result", "")
    if text.startswith("Error:"):
        raise RuntimeError(f"[{server}] Tool error: {text}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


# ── Pipeline ──────────────────────────────────────────────

def run_pipeline(args):
    input_path = Path(args.input).resolve()
    workdir    = Path(args.workdir).resolve() if args.workdir else input_path.parent
    workdir.mkdir(parents=True, exist_ok=True)

    stem = input_path.stem

    # ── Step 1: transcribe ────────────────────────────────
    if args.skip_transcribe:
        jp_ass = Path(args.skip_transcribe).resolve()
        print(f"\n⏭  skip transcribe → using {jp_ass}")
    else:
        jp_ass = workdir / f"{stem}.ass"
        result = call_tool("transcribe", "transcribe", {
            "input_path":    str(input_path),
            "template_path": str(Path(args.template).resolve()),
            "output_path":   str(jp_ass),
        })
        jp_ass = Path(result["output_path"])
        print(f"  ✓ transcribed {result['segments']} segments → {jp_ass}")

    # ── Step 2: ass_bilingual ─────────────────────────────
    if args.skip_bilingual:
        bilingual_ass = Path(args.skip_bilingual).resolve()
        print(f"\n⏭  skip ass_bilingual → using {bilingual_ass}")
    else:
        bilingual_ass = workdir / f"{stem}_bilingual.ass"
        result = call_tool("ass_bilingual", "ass_bilingual", {
            "input_path":  str(jp_ass),
            "output_path": str(bilingual_ass),
        })
        bilingual_ass = Path(result["output_path"])
        print(f"  ✓ generated {result['generated_cn_lines']} empty CN lines → {bilingual_ass}")

    # ── Step 3: ass_translate ─────────────────────────────
    if args.skip_translate:
        translated_ass = Path(args.skip_translate).resolve()
        print(f"\n⏭  skip ass_translate → using {translated_ass}")
    else:
        translated_ass = workdir / f"{stem}_translated.ass"
        translate_args = {
            "input_path":  str(bilingual_ass),
            "output_path": str(translated_ass),
            "model":       args.model,
            "ollama_model": args.ollama_model,
        }
        if args.glossary:
            translate_args["glossary_path"] = str(Path(args.glossary).resolve())

        result = call_tool("ass_translate", "ass_translate", translate_args)
        translated_ass = Path(result["output_path"])
        print(f"  ✓ translated {result['translated']} lines, skipped {result['skipped']} (backend: {result.get('model','?')}) → {translated_ass}")

    # ── Step 4: ass_burnin ────────────────────────────────
    if args.skip_burnin:
        print(f"\n⏭  skip ass_burnin → translated .ass saved at {translated_ass}")
        print("\n✅ Done!")
        print(f"   Output: {translated_ass}")
        return

    output_video = Path(args.output).resolve() if args.output else \
                   workdir / f"{stem}_subbed{input_path.suffix}"

    result = call_tool("ass_burnin", "ass_burnin", {
        "video_path":  str(input_path),
        "ass_path":    str(translated_ass),
        "output_path": str(output_video),
        "crf":         args.crf,
        "preset":      args.preset,
    })
    output_video = Path(result["output_path"])
    print(f"  ✓ burned subtitles → {output_video}  ({result['size_mb']} MB)")

    print("\n✅ Done!")
    print(f"   Output: {output_video}")


# ── Entry point ───────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Subtitle agent: transcribe → bilingual → translate → burn-in"
    )
    parser.add_argument("--input",    required=True, help="Input audio/video file")
    parser.add_argument("--template", help="ASS template file (required unless --skip-transcribe)")
    parser.add_argument("--glossary", help="Optional glossary.txt for translation")
    parser.add_argument("--output",   help="Output video path")
    parser.add_argument("--workdir",  help="Working directory for intermediate files")
    parser.add_argument("--crf",      type=int, default=18,   help="H.264 CRF (default: 18)")
    parser.add_argument("--preset",   default="slow",         help="ffmpeg preset (default: slow)")
    parser.add_argument("--model",    default="gemini",
                        choices=["claude", "gemini", "openai", "ollama"],
                        help="LLM backend for translation (default: gemini)")
    parser.add_argument("--ollama-model", default="qwen2.5:14b",
                        dest="ollama_model",
                        help="Ollama model name, only used when --model=ollama (default: qwen2.5:14b)")

    # Skip flags for resuming a partial run
    parser.add_argument("--skip-transcribe",  metavar="PATH", help="Skip step 1, use existing JP .ass")
    parser.add_argument("--skip-bilingual",   metavar="PATH", help="Skip step 2, use existing bilingual .ass")
    parser.add_argument("--skip-translate",   metavar="PATH", help="Skip step 3, use existing translated .ass")
    parser.add_argument("--skip-burnin",      action="store_true",  help="Skip step 4, stop after translation")

    args = parser.parse_args()

    if not args.skip_transcribe and not args.template:
        parser.error("--template is required unless --skip-transcribe is set")

    try:
        run_pipeline(args)
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()