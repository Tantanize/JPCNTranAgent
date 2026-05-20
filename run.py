"""
run.py — 单独调用任意一个 MCP server 的 tool

用法：
    python run.py transcribe    --input video.mp4 --template template.ass
    python run.py ass_bilingual --input video.ass
    python run.py ass_translate --input video_bilingual.ass --model gemini
    python run.py ass_burnin    --input video.mp4 --ass video_translated.ass

对应的 server 必须已经在运行（start_servers.bat）。
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

PORTS = {
    "transcribe":    8771,
    "ass_bilingual": 8772,
    "ass_translate": 8773,
    "ass_burnin":    8774,
}


def call_tool(server: str, tool: str, arguments: dict) -> dict:
    port = PORTS[server]
    url  = f"http://127.0.0.1:{port}/call"
    print(f"→ calling [{server}] ...", flush=True)
    with httpx.Client(timeout=3600) as client:
        resp = client.post(url, json={"tool": tool, "arguments": arguments})
        resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"[{server}] error: {data['error']}")
    text = data.get("result", "")
    if text.startswith("Error:"):
        raise RuntimeError(text)
    return json.loads(text)


def main():
    parser = argparse.ArgumentParser(
        description="Run a single subtitle_agent tool step",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python run.py transcribe    --input video.mp4 --template template.ass
  python run.py ass_bilingual --input video.ass
  python run.py ass_translate --input video_bilingual.ass --model gemini
  python run.py ass_translate --input video_bilingual.ass --model gemini --glossary glossary.txt
  python run.py ass_burnin    --input video.mp4 --ass video_translated.ass
  python run.py ass_burnin    --input video.mp4 --ass video_translated.ass --crf 18 --preset slow
        """,
    )
    parser.add_argument("server", choices=list(PORTS.keys()), help="Which server to call")
    parser.add_argument("--input",   required=True, help="Input file path")
    parser.add_argument("--output",  help="Output file path (optional)")
    parser.add_argument("--template", help="[transcribe] .ass template file")
    parser.add_argument("--backend", default="whisper", choices=["whisper", "funasr"],
                        help="[transcribe] ASR backend (default: whisper)")
    parser.add_argument("--model",    default="gemini",
                        choices=["claude", "gemini", "openai", "ollama"],
                        help="[ass_translate] LLM backend (default: gemini)")
    parser.add_argument("--ollama-model", default="qwen2.5:14b", dest="ollama_model",
                        help="[ass_translate] Ollama model name (default: qwen2.5:14b)")
    parser.add_argument("--glossary", help="[ass_translate] glossary.txt path")
    parser.add_argument("--ass",    help="[ass_burnin] .ass subtitle file")
    parser.add_argument("--crf",    type=int, default=18,   help="[ass_burnin] CRF quality (default: 18)")
    parser.add_argument("--preset", default="slow",         help="[ass_burnin] ffmpeg preset (default: slow)")
    args = parser.parse_args()

    input_path = str(Path(args.input).resolve())

    if args.server == "transcribe":
        if not args.template:
            parser.error("transcribe requires --template")
        tool_args = {
            "input_path":    input_path,
            "template_path": str(Path(args.template).resolve()),
            "backend":       args.backend,
        }
        if args.output:
            tool_args["output_path"] = str(Path(args.output).resolve())

    elif args.server == "ass_bilingual":
        tool_args = {"input_path": input_path}
        if args.output:
            tool_args["output_path"] = str(Path(args.output).resolve())

    elif args.server == "ass_translate":
        tool_args = {
            "input_path":   input_path,
            "model":        args.model,
            "ollama_model": args.ollama_model,
        }
        if args.output:
            tool_args["output_path"] = str(Path(args.output).resolve())
        if args.glossary:
            tool_args["glossary_path"] = str(Path(args.glossary).resolve())

    elif args.server == "ass_burnin":
        if not args.ass:
            parser.error("ass_burnin requires --ass")
        tool_args = {
            "video_path": input_path,
            "ass_path":   str(Path(args.ass).resolve()),
            "crf":        args.crf,
            "preset":     args.preset,
        }
        if args.output:
            tool_args["output_path"] = str(Path(args.output).resolve())

    try:
        result = call_tool(args.server, args.server, tool_args)
        print("\nDone!")
        for k, v in result.items():
            print(f"   {k}: {v}")
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()