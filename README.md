# JPCNTranAgent
A vibecoding project.

A local subtitle pipeline for Japanese video content. Transcribes video to ASS subtitles using Whisper large-v3, generates bilingual JP/CN subtitle files, translates empty CN lines via your choice of LLM backend (Gemini, Claude, OpenAI, or local Ollama), and burns the finished subtitles into the output video using ffmpeg.

Each step runs as an independent local MCP server, so you can run the full pipeline end-to-end or stop at any point to review and edit the subtitle file before continuing.

Includes a Chinese setup guide `SETUP.md`. Some steps may still have issues.

#### Components
`transcribe` · `ass_bilingual` · `ass_translate` · `ass_burnin`
#### Translation backends
`Gemini` · `Claude` · `OpenAI` · `Ollama`
#### Structure
```
subtitle_agent/
├── transcribe/
│   ├── server.py
│   └── requirements.txt
├── ass_bilingual/
│   ├── server.py
│   └── requirements.txt
├── ass_translate/
│   ├── server.py
│   └── requirements.txt
├── ass_burnin/
│   ├── server.py
│   └── requirements.txt
├── subtitle_agent.py
├── check_deps.py
├── run.py
├── start_servers.bat
└── stop_servers.bat
```
