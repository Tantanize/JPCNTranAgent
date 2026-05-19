# subtitle_agent 安装运行指南（Windows）

## 文件结构

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

---

## 前置条件

- Python 3.11 或以上（确认：`python --version`）
- NVIDIA 显卡驱动已安装（确认：`nvidia-smi`，记下右上角的 CUDA Version）
- ffmpeg 已安装并在 PATH 中（确认：`ffmpeg -version`）
  - 没有的话：`winget install ffmpeg` 或去 https://ffmpeg.org/download.html 下载

---

## 第一步：安装 subtitle_agent.py 自身的依赖

`subtitle_agent.py` 是总控脚本，运行在系统 Python 里（不在 venv），需要单独安装 httpx：

```bat
pip install httpx mcp
```

---

## 第二步：创建所有 venv 并安装基础依赖

在项目根目录运行：

```bat
start_servers.bat
```

这会为 `ass_bilingual`、`ass_translate`、`ass_burnin` 自动创建 venv 并安装依赖。
`transcribe` 的 venv 也会创建，但 PyTorch 需要手动装（下一步）。

运行完之后**关掉弹出的所有命令行窗口**，现在还不是正式启动，只是借用脚本来建 venv。

---

## 第三步：手动安装 PyTorch（transcribe 用）

先确认你的 CUDA 版本（`nvidia-smi` 右上角），然后运行：

```bat
:: CUDA 13.x（13.0 / 13.1）：
transcribe\.venv\Scripts\pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

:: CUDA 12.8：
transcribe\.venv\Scripts\pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

:: CUDA 12.6：
transcribe\.venv\Scripts\pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
```

然后装 transcribe 剩余的依赖：

```bat
transcribe\.venv\Scripts\pip install -r transcribe\requirements.txt
```

---

## 第四步：安装翻译后端（ass_translate 用）

选择你要用的翻译后端，至少装一个：

```bat
:: Gemini（需要 Gemini API key）：
ass_translate\.venv\Scripts\pip install google-generativeai

:: Claude（需要 Anthropic API key）：
ass_translate\.venv\Scripts\pip install anthropic

:: OpenAI（需要 OpenAI API key）：
ass_translate\.venv\Scripts\pip install openai

:: Ollama（本地模型，无需 key，需要先安装 Ollama）：
:: 不需要额外安装，httpx 已经包含在 requirements.txt 里
```

---

## 第五步：设置 API Key

在命令行里设置环境变量（每次开新终端都要设，或者加进系统环境变量里）：

```bat
:: Gemini：
set GEMINI_API_KEY=你的key

:: Claude：
set ANTHROPIC_API_KEY=你的key

:: OpenAI：
set OPENAI_API_KEY=你的key
```

永久设置（推荐）：
- 开始菜单搜索「环境变量」→「编辑系统环境变量」→「环境变量」→ 新建用户变量

---

## 第六步：验证依赖

```bat
python check_deps.py
```

全部显示绿色 ✓ 才能继续。如果有红色 ✗ 按提示安装缺失的包。
翻译后端显示黄色 ○ 是正常的，只要你要用的那个是绿色就行。

---

## 第七步：准备 .ass 模板文件

模板文件只需包含 `[Script Info]` 和 `[V4+ Styles]` 两段，
样式名必须有以 `JP` 结尾的（`ass_bilingual` 会自动生成对应的 `CN` 样式）。

最简示例（保存为 `template.ass`）：

```
[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default JP,Source Han Sans,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,10,10,40,1
Style: Default CN,Source Han Sans,42,&H0000FFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,80,1
```

---

## 第八步：正式启动 servers

```bat
start_servers.bat
```

四个命令行窗口会弹出，各自对应一个 server，保持这些窗口开着。

确认启动成功（新开一个终端）：

```bat
curl http://localhost:8771/sse
curl http://localhost:8773/sse
```

有响应就说明正常。

---

## 第九步：运行

```bat
:: 完整流程（音视频 → 双语字幕视频）：
python subtitle_agent.py --input D:\videos\video.mp4 --template D:\tools\template.ass --model gemini

:: 带词汇表：
python subtitle_agent.py --input video.mp4 --template template.ass --glossary glossary.txt --model gemini

:: 跳过转录，直接翻译已有的 .ass：
python subtitle_agent.py --input video.mp4 --skip-transcribe video.ass --model gemini

:: 跳过转录和翻译，只压制字幕：
python subtitle_agent.py --input video.mp4 --skip-translate translated.ass

:: 只跑到翻译，不压制（之后用 run.py 单独压制）：
python subtitle_agent.py --input video.mp4 --template template.ass --model gemini --skip-burnin
```

### 完整参数列表

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--input` | 输入音视频文件（必填） | — |
| `--template` | .ass 模板文件 | — |
| `--glossary` | 词汇表 txt | — |
| `--output` | 输出视频路径 | `<输入文件名>_subbed.mp4` |
| `--workdir` | 中间文件目录 | 与输入文件同目录 |
| `--model` | 翻译后端：`gemini` / `claude` / `openai` / `ollama` | `gemini` |
| `--ollama-model` | Ollama 模型名（仅 `--model=ollama` 时有效） | `qwen2.5:14b` |
| `--crf` | 视频质量 0–51，越小越好 | `18` |
| `--preset` | 编码速度：`ultrafast` / `fast` / `medium` / `slow` / `veryslow` | `slow` |
| `--skip-transcribe` | 跳过步骤1，使用已有的 JP .ass | — |
| `--skip-bilingual` | 跳过步骤2，使用已有的双语 .ass | — |
| `--skip-translate` | 跳过步骤3，使用已有的翻译完成的 .ass | — |
| `--skip-burnin` | 跳过步骤4，翻译完成后不压制视频 | — |

---

## 停止 servers

```bat
stop_servers.bat
```

或者直接关掉那四个命令行窗口。

---

## 端口一览

| server | 端口 |
|--------|------|
| transcribe | 8771 |
| ass_bilingual | 8772 |
| ass_translate | 8773 |
| ass_burnin | 8774 |