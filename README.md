# 🎙 Minutes in Seconds

> Speaker-diarized transcription for meetings and lectures — English & Mandarin.

Built with Whisper + pyannote + FastAPI. Deployable to Zeabur in minutes.

![Stack](https://img.shields.io/badge/Python-3.11-blue) ![Framework](https://img.shields.io/badge/FastAPI-0.115-green) ![Model](https://img.shields.io/badge/Whisper-large--v3-orange) ![Deploy](https://img.shields.io/badge/Deploy-Zeabur-purple)

---

## ✨ Features

| Feature | Details |
|---|---|
| 🗣 Speaker diarization | pyannote/speaker-diarization-3.1 — detects who spoke and when |
| 🌐 English + Mandarin | Whisper auto-detects language per segment; handles code-switching |
| 📄 3 export formats | TXT (plain), DOCX (colour-coded), PDF (styled) |
| 🤖 AI cleanup | Claude Haiku cleans punctuation and generates a bullet-point summary |
| 🔇 Noise reduction | Optional spectral denoising for noisy recordings |
| ✏️ Speaker renaming | Rename "Speaker 1" → "Julian" from the web UI after transcription |
| 🎥 Any audio/video | MP4, MKV, MOV, MP3, WAV, M4A via ffmpeg |
| ⚡ Real-time progress | Polling-based live progress bar with step description |

---

## 🏗 Architecture

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

---

## 🚀 Running Locally

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
export HF_TOKEN=hf_xxxxxxxxxxxx

# Optional: AI cleanup + summary
export ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx
```

### Start server

```bash
uvicorn app.main:app --reload --port 8080
```

Open http://localhost:8080

---

## ☁️ Deploy to Zeabur

### Step 1 — Push to GitHub

```bash
git init
git add .
git commit -m "feat: initial commit — Minutes in Seconds"
git remote add origin https://github.com/YOUR_USERNAME/minutes-in-seconds.git
git push -u origin main
```

### Step 2 — Create Zeabur project

1. Go to https://zeabur.com → **New Project**
2. **Add Service** → **Git** → select your `minutes-in-seconds` repo
3. Zeabur detects the `Dockerfile` automatically

### Step 3 — Set environment variables

In the Zeabur service dashboard → **Variables** tab, add:

| Variable | Value | Required? |
|---|---|---|
| `HF_TOKEN` | `hf_xxxx` | For speaker diarization |
| `ANTHROPIC_API_KEY` | `sk-ant-xxxx` | For AI cleanup & summary |

### Step 4 — Add persistent volume (recommended)

To cache Whisper model weights between deployments (saves re-downloading ~1.5GB on restart):

1. Zeabur service → **Volumes** tab
2. Add volume: mount path `/app/.cache`

### Step 5 — Deploy

Click **Deploy**. Zeabur builds the Docker image and provisions a public URL.

> **First startup note:** On cold start, Whisper downloads the model weights (~800MB for `medium`, ~3GB for `large-v3`). This takes 1–5 minutes. Subsequent starts use the cached volume.

---

## ⚙️ Configuration

### Whisper model sizes

| Model | Size | EN Quality | ZH Quality | Speed |
|---|---|---|---|---|
| `tiny` | 75MB | ⭐⭐ | ⭐ | Very fast |
| `base` | 145MB | ⭐⭐⭐ | ⭐⭐ | Fast |
| `small` | 480MB | ⭐⭐⭐⭐ | ⭐⭐⭐ | Medium |
| **`medium`** ★ | 1.5GB | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | Medium |
| `large-v3` | 3GB | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Slow |

`medium` is recommended for production — good accuracy, reasonable memory.

### Without API keys (degraded mode)

- **No `HF_TOKEN`**: All speech assigned to "Speaker 1" in 30-second chunks. Transcription still works.
- **No `ANTHROPIC_API_KEY`**: Cleanup and summary steps are skipped. Transcription still works.

---

## 📁 Project Structure

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

---

## 🌐 Mandarin Notes

- Whisper outputs **Simplified Chinese** by default
- For **Traditional Chinese**, post-process with `opencc`:
  ```bash
  pip install opencc-python-reimplemented
  ```
- **Code-switching** (Singlish + Mandarin) handled via `language=None` in Whisper — it auto-detects per segment

---

## 📄 API Reference

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | GET | Serve frontend |
| `/api/upload` | POST | Submit file + config, returns `job_id` |
| `/api/status/{job_id}` | GET | Poll job progress |
| `/api/cancel/{job_id}` | POST | Cancel a running job |
| `/api/rename-speaker/{job_id}` | POST | Rename speaker label |
| `/api/download/{job_id}/{fmt}` | GET | Download TXT/DOCX/PDF |
| `/api/jobs` | GET | List all jobs |

---

## 🛣 Roadmap

- [ ] V2: Real-time WebSocket progress (replace polling)
- [ ] V2: Persistent job history (SQLite)
- [ ] V2: Timestamp editing in the browser
- [ ] V3: Real-time transcription for live calls
- [ ] V3: Zoom / Google Meet integration

---

## 📄 License

MIT — built by [TheBooleanJulian](https://github.com/TheBooleanJulian)
