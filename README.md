<div align="center">

# Minutes in Seconds

**Speaker-diarized transcription for meetings and lectures — English & Mandarin, built with Whisper + pyannote + FastAPI.**

![Python](https://img.shields.io/badge/Python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)
![Whisper](https://img.shields.io/badge/Whisper-large--v3-orange)
![Deploy](https://img.shields.io/badge/Deploy-Zeabur-purple)
![License](https://img.shields.io/badge/license-AGPLv3%20%2B%20Commercial-00D4C8.svg)

</div>

---

## What it does

Minutes in Seconds turns an audio or video recording into a clean, speaker-labelled transcript. Upload a file through the browser UI, and a background pipeline extracts audio, diarizes speakers, transcribes with Whisper (auto-detecting English/Mandarin per segment), optionally cleans it up and summarizes it with Claude Haiku, and exports the result as TXT, DOCX, or PDF.

## Features

| Feature | Details |
|---|---|
| Speaker diarization | pyannote/speaker-diarization-3.1 — detects who spoke and when |
| English + Mandarin | Whisper auto-detects language per segment; handles code-switching |
| 3 export formats | TXT (plain), DOCX (colour-coded), PDF (styled) |
| AI cleanup | Claude Haiku cleans punctuation and generates a bullet-point summary |
| Noise reduction | Optional spectral denoising for noisy recordings |
| Speaker renaming | Rename "Speaker 1" → a real name from the web UI after transcription |
| Any audio/video | MP4, MKV, MOV, MP3, WAV, M4A via ffmpeg |
| Chunking | Long recordings are chunked to avoid transcription timeouts |
| Real-time progress | Polling-based live progress bar with step-by-step console-style log |

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI (`app/main.py`) |
| Transcription | OpenAI Whisper (`large-v3` recommended) |
| Diarization | pyannote.audio (`speaker-diarization-3.1`) |
| AI cleanup/summary | Claude Haiku (Anthropic) |
| Audio | ffmpeg, optional `noisereduce` |
| Exports | TXT / DOCX / PDF (`app/exporters.py`) |
| Frontend | Single-file HTML (`static/index.html`), no build step |
| Hosting | Zeabur (Docker) |

## Screenshots

_Screenshots coming soon._ The app is a single-page browser UI (`static/index.html`) with an upload form and a live progress log.

## Architecture

```
Browser (static/index.html)
    │  POST /api/upload (FormData)
    ▼
FastAPI (app/main.py)
    │  Background thread
    ▼
pipeline.py
    ├── ffmpeg   → 16kHz mono WAV
    ├── noisereduce (optional)
    ├── pyannote → speaker segments
    └── Whisper  → per-segment text + language
    │
    ▼
exporters.py  →  TXT / DOCX / PDF
    │
    ▼
GET /api/download/{job_id}/{fmt}
```

## Quick Start

### Prerequisites

```bash
# System
sudo apt install ffmpeg       # Ubuntu/Debian
brew install ffmpeg           # macOS

# Python
pip install -r requirements.txt
```

### Environment variables

```bash
# Required for speaker diarization
# 1. Create account at https://huggingface.co
# 2. Get token at https://huggingface.co/settings/tokens
# 3. Accept terms at https://huggingface.co/pyannote/speaker-diarization-3.1
export HF_TOKEN=YOUR_HUGGINGFACE_TOKEN

# Optional: AI cleanup + summary
export ANTHROPIC_API_KEY=YOUR_ANTHROPIC_API_KEY
```

### Run locally

```bash
uvicorn app.main:app --reload --port 8080
```

Open http://localhost:8080

### Without API keys (degraded mode)

- **No `HF_TOKEN`**: all speech assigned to "Speaker 1" in 30-second chunks. Transcription still works.
- **No `ANTHROPIC_API_KEY`**: cleanup and summary steps are skipped. Transcription still works.

## Deploy

Production runs on one VM (Oracle Cloud Always Free ARM first, Hetzner as the fallback) with Docker Compose and a
Cloudflare Tunnel, no open ports: see [deploy/vm/README.md](deploy/vm/README.md). Whisper + pyannote are too heavy for
Workers or Vercel. The Zeabur service stays up until the VM is verified, then it is terminated.

> **First startup note:** on cold start, Whisper downloads model weights (~800MB for `medium`, ~3GB for `large-v3`). This takes 1–5 minutes; subsequent starts use the cached volume.

## Configuration

### Whisper model sizes

| Model | Size | EN Quality | ZH Quality | Speed |
|---|---|---|---|---|
| `tiny` | 75MB | Low | Very low | Very fast |
| `base` | 145MB | Fair | Low | Fast |
| `small` | 480MB | Good | Fair | Medium |
| **`medium`** ★ | 1.5GB | Good | Good | Medium |
| `large-v3` | 3GB | Best | Best | Slow |

`medium` is recommended for production — good accuracy, reasonable memory.

## Project Structure

```
minutes-in-seconds/
├── app/
│   ├── __init__.py
│   ├── main.py          ← FastAPI app, job management, routes
│   ├── pipeline.py      ← Audio extraction, diarization, Whisper
│   └── exporters.py     ← TXT, DOCX, PDF generation
├── static/
│   └── index.html       ← Full frontend (single file, no build step)
├── uploads/             ← Temp upload dir (gitignored)
├── outputs/             ← Generated transcript files (gitignored)
├── requirements.txt
├── Dockerfile
├── zbpack.json          ← Zeabur build config
├── .dockerignore
├── .gitignore
└── README.md
```

## Mandarin Notes

- Whisper outputs **Simplified Chinese** by default
- For **Traditional Chinese**, post-process with `opencc` (`pip install opencc-python-reimplemented`)
- **Code-switching** (Singlish + Mandarin) handled via `language=None` in Whisper — it auto-detects per segment

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | GET | Serve frontend |
| `/api/upload` | POST | Submit file + config, returns `job_id` |
| `/api/status/{job_id}` | GET | Poll job progress |
| `/api/cancel/{job_id}` | POST | Cancel a running job |
| `/api/rename-speaker/{job_id}` | POST | Rename speaker label |
| `/api/download/{job_id}/{fmt}` | GET | Download TXT/DOCX/PDF |
| `/api/jobs` | GET | List all jobs |

## Status / Roadmap

- [x] Speaker diarization + Whisper transcription with English/Mandarin auto-detection
- [x] TXT / DOCX / PDF export
- [x] Claude Haiku cleanup + bullet-point summary
- [x] Real-time polling progress with step-by-step log
- [x] Chunking for long recordings (avoids transcription timeouts)
- [x] Speaker renaming from the web UI
- [ ] Real-time WebSocket progress (replace polling)
- [ ] Persistent job history (SQLite)
- [ ] Timestamp editing in the browser
- [ ] Real-time transcription for live calls

## Changelog

- **2026-08-18** — Chunk long recordings and always translate to English
- **2026-06-19** — Chunk large audio files to avoid transcription timeouts
- **2026-06-18** — Console-style progress log replacing single changing status line
- **2026-05-15** — Template-based AI summary as a separate post-transcription step
- **2026-05-04** — Granular pipeline progress reporting; fixed progress badge stuck in PROCESSING; initial release

## License

This project is dual licensed.

- Community Edition — [GNU Affero General Public License v3 (AGPLv3)](LICENSE). Free to use, modify, and self-host. If you distribute a modified version or run it as a network service, you must make the corresponding source available.
- Commercial License — for organisations that want to embed, modify, or distribute this software without AGPLv3's obligations. See [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).

---

<div align="center">
<sub>Built by <a href="https://github.com/TheBooleanJulian">@TheBooleanJulian</a></sub>
</div>
