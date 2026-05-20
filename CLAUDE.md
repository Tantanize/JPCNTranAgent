# subtitle_agent — Project Context for Claude Code

## What this project is

A local subtitle pipeline for Japanese video content, built as a vibe coding project.
Four independent MCP servers connected by a CLI orchestrator.

```
transcribe        audio/video  →  JP-only .ass          (Whisper or FunASR)
ass_bilingual     JP .ass      →  bilingual .ass         (empty CN lines added)
ass_translate     bilingual    →  translated .ass        (LLM fills CN lines)
ass_burnin        video + .ass →  final subtitled video  (ffmpeg)
```

---

## Architecture

Each server is a standalone Python process with:
- A simple HTTP server (uvicorn + Starlette) exposing two endpoints:
  - `POST /call` — `{"tool": "...", "arguments": {...}}` → `{"result": "..."}` or `{"error": "..."}`
  - `GET /health` — `{"status": "ok", "server": "..."}`
- A stdio MCP interface for Claude Desktop / Claude Code (`--stdio` flag)

`run.py` and `subtitle_agent.py` call servers via plain `httpx` POST to `/call`.
No MCP client libraries needed on the caller side.

### Ports
| Server        | Port |
|---------------|------|
| transcribe    | 8771 |
| ass_bilingual | 8772 |
| ass_translate | 8773 |
| ass_burnin    | 8774 |

---

## File structure

```
subtitle_agent/
├── transcribe/
│   ├── server.py          # Whisper large-v2 or Fun-ASR-Nano-2512
│   └── requirements.txt   # does NOT include torch — install manually
├── ass_bilingual/
│   ├── server.py          # pure Python, no ML deps
│   └── requirements.txt
├── ass_translate/
│   ├── server.py          # multi-backend LLM translation
│   └── requirements.txt   # backend libs (gemini/anthropic/openai) installed separately
├── ass_burnin/
│   ├── server.py          # wraps ffmpeg
│   └── requirements.txt
├── subtitle_agent.py      # full pipeline CLI orchestrator
├── run.py                 # single-step CLI runner
├── check_deps.py          # dependency checker
├── start_servers.bat      # Windows: start all 4 servers
├── stop_servers.bat       # Windows: stop all 4 servers
├── SETUP.md               # full setup guide (Windows)
└── CLAUDE.md              # this file
```

Each server has its own `.venv` inside its folder (e.g. `transcribe/.venv`).

---

## Key design decisions

**ASS file format:**
- Every subtitle has two `Dialogue` lines with identical timestamps
- JP line: style name ends in `JP` (e.g. `Default JP`)
- CN line: style name ends in `CN` (e.g. `Default CN`)
- Lines with styles not ending in JP or CN are ignored by all tools
- `ass_bilingual` matches JP↔CN by `(start, end)` timestamp key

**Translation:**
- Batch size: 6 lines per API call
- Context: 5 lines before + 2 lines after each batch
- Prompt uses `[BEFORE]` / `[TARGET n]` / `[AFTER]` tags
- Output format: `{"t": ["translation1", "translation2", ...]}`
- Existing CN content is always preserved, never overwritten
- Uses `pysubs2` for ASS parsing (not manual string splitting)

**Transcription backends (`--backend`):**
- `whisper` (default): openai-whisper large-v2, segment-level output, used directly
- `funasr`: two-model pipeline — fsmn-vad for segmentation, Fun-ASR-Nano-2512 for ASR per segment
  - **Why two models**: FunASR's `AutoModel` with `vad_model=` always merges all VAD-segment
    results into one single output in `inference_with_vad()` (auto_model.py lines ~579-610).
    There is no parameter to suppress this merge. The only way to get per-segment output is to
    run the two models independently.
  - **Flow**: `fsmn-vad.generate(audio_path)` → `[[start_ms, end_ms], ...]` windows →
    ffmpeg extracts 16 kHz mono wav → soundfile loads → slice per window as numpy →
    `torch.from_numpy(chunk)` → `Fun-ASR-Nano-2512.generate(chunk_tensor)` per window → collect segments
  - `transcribe/model.py` must exist (downloaded from FunAudioLLM/Fun-ASR on GitHub);
    `remote_code=` in AutoModel points to this file via absolute path from `__file__`
  - **VAD tuning params** (pass as constructor kwargs to `AutoModel(model="fsmn-vad", ...)`):
    - `max_single_segment_time` (ms, default 60000): hard cap per segment; currently set to 8000
    - `max_end_silence_time` (ms, default 800): silence duration before cutting; currently set to 500
    - These kwargs propagate to `VADXOptions.__init__` at model load time. Passing them to
      `generate()` at runtime does NOT work — `init_cache()` only handles `max_end_silence_time`
      and ignores everything else

**Translation backends (`--model`):**
- `gemini` (default): google-generativeai, gemini-2.5-flash
- `claude`: anthropic, claude-sonnet-4-20250514
- `openai`: openai, gpt-4o-mini
- `ollama`: local Ollama via httpx, default model qwen2.5:14b

**ass_burnin Windows path issue:**
- ffmpeg's `ass=` filter breaks on paths with colons (drive letters) and non-ASCII
- Workaround: copy video + .ass to `%TEMP%` with ASCII filenames, run ffmpeg there,
  then move output to final destination

---

## Known issues / gotchas

- `transcribe/requirements.txt` does NOT include `torch` — must be installed manually
  with the correct `--index-url` for your CUDA version before running `start_servers.bat`
- `funasr` requires `Microsoft C++ Build Tools` on Windows (for `editdistance` dependency)
- Fun-ASR-Nano-2512 is an Encoder+LLM (Qwen3-0.6B) model; it always outputs one merged
  transcript for the entire input. Per-segment output requires the two-model approach above.
- `funasr_to_segments()` is commented out in transcribe/server.py — superseded by the
  inline VAD→slice→ASR loop in `run_transcribe()`
- `cam++` (spk_model) can be called separately after ASR to get per-segment speaker embeddings,
  then cluster with sklearn to assign speaker IDs — but this adds significant complexity
- `ct-punc` (punc_model) can be called separately after ASR: `punc_model.generate(input=text, text_language="ja")`
- Do NOT add `punc_model` or `spk_model` to the FunASR AutoModel init in the two-model flow —
  they are only triggered inside `inference_with_vad()` (the unified pipeline), not in direct
  per-chunk `model.inference()` calls
- torchaudio 2.9+ dropped legacy backends; `torchaudio.load()` now always requires TorchCodec.
  Workaround: use `ffmpeg` subprocess to extract audio to a temp wav, then load with `soundfile`
- Fun-ASR-Nano-2512 `generate_chatml()` only handles `str` (file path) and `torch.Tensor`;
  passing a numpy array returns `None` silently → must convert with `torch.from_numpy(chunk)`
- ass_burnin uses `shell=True` + temp file workaround for Windows path compatibility
- MCP SSE transport was tried and abandoned due to compatibility issues with mcp 1.27.1;
  current transport is plain HTTP (`/call` endpoint)

---

## Environment / runtime

- Windows (primary), Python 3.13
- CUDA 13.1, PyTorch installed with `--index-url https://download.pytorch.org/whl/cu130`
- Each server runs in its own venv (`<name>/.venv`)
- `subtitle_agent.py` and `run.py` run in system Python with `httpx` and `mcp` installed
- API keys set as environment variables: `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`

---

## How to run

```bat
:: Start all servers
start_servers.bat

:: Full pipeline
python subtitle_agent.py --input video.mp4 --template template.ass --model gemini

:: With FunASR backend
python subtitle_agent.py --input video.mp4 --template template.ass --backend funasr --model gemini

:: Single step
python run.py transcribe    --input video.mp4 --template template.ass
python run.py ass_bilingual --input video.ass
python run.py ass_translate --input video_bilingual.ass --model gemini
python run.py ass_burnin    --input video.mp4 --ass video_translated.ass
```

---

## Translation output requirements

When modifying translation prompts, preserve these rules:
- Natural spoken Chinese (口语)
- Half-width spaces for pauses — no commas or periods
- Quotation marks: 「」
- Concise subtitle length
- Must follow glossary if provided
