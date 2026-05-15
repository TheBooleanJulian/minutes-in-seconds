"""
Minutes in Seconds — FastAPI Backend
─────────────────────────────────────
Handles file uploads, transcription jobs, and download serving.
"""

import os
import uuid
import threading
import traceback
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.pipeline import run_pipeline, ai_cleanup_transcript, ai_summarize_with_template, JOB_CANCELLED
from app.exporters import export_txt, export_docx, export_pdf

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
STATIC_DIR = BASE_DIR / "static"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ─── Job store (in-memory) ────────────────────────────────────────────────────

jobs: dict[str, dict] = {}
# Structure: {
#   "status": "queued" | "processing" | "done" | "error" | "cancelled",
#   "step": str,
#   "progress": int (0-100),
#   "transcript": Transcript | None,
#   "outputs": {format: filepath},
#   "error": str,
#   "created_at": str,
#   "config": {...},
# }

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Minutes in Seconds", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    title: str = Form(""),
    model: str = Form("medium"),
    speakers: Optional[int] = Form(None),
    output_formats: str = Form("txt"),   # comma-separated: "txt,docx,pdf"
    denoise: bool = Form(False),
    cleanup: bool = Form(True),
):
    """Accept a media file and start a transcription job."""

    # Validate file type
    allowed_exts = {".mp3", ".mp4", ".wav", ".m4a", ".mkv", ".mov", ".ogg", ".flac", ".webm"}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_exts:
        raise HTTPException(400, f"Unsupported file type: {file_ext}. Allowed: {', '.join(allowed_exts)}")

    # Save upload
    job_id = str(uuid.uuid4())
    upload_path = UPLOAD_DIR / f"{job_id}{file_ext}"
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Build job
    formats = [f.strip() for f in output_formats.split(",") if f.strip() in ("txt", "docx", "pdf")]
    if not formats:
        formats = ["txt"]

    job = {
        "status": "queued",
        "step": "Queued...",
        "progress": 0,
        "transcript": None,
        "outputs": {},
        "error": None,
        "created_at": datetime.now().isoformat(),
        "filename": file.filename,
        "config": {
            "title": title or Path(file.filename).stem,
            "model": model,
            "speakers": speakers,
            "output_formats": formats,
            "denoise": denoise,
            "cleanup": cleanup,
            "upload_path": str(upload_path),
            "job_id": job_id,
        },
    }
    jobs[job_id] = job

    # Launch background thread
    t = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    t.start()

    return {"job_id": job_id, "filename": file.filename}


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "status": job["status"],
        "step": job["step"],
        "progress": job["progress"],
        "error": job["error"],
        "outputs": list(job["outputs"].keys()),
        "speakers": job.get("speakers", {}),
        "filename": job.get("filename", ""),
    }


@app.post("/api/cancel/{job_id}")
async def cancel_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] in ("done", "error", "cancelled"):
        return {"ok": True}
    job["status"] = "cancelled"
    job["step"] = "Cancelled."
    return {"ok": True}


@app.post("/api/rename-speaker/{job_id}")
async def rename_speaker(job_id: str, speaker_id: str = Form(...), name: str = Form(...)):
    """Rename a speaker label after transcription."""
    job = jobs.get(job_id)
    if not job or not job.get("transcript"):
        raise HTTPException(404, "Job or transcript not found")
    job["transcript"].speakers[speaker_id] = name
    # Re-export all outputs with new names
    _export_outputs(job_id)
    return {"ok": True}


@app.get("/api/download/{job_id}/{fmt}")
async def download(job_id: str, fmt: str):
    """Download a completed transcript file."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if fmt not in job["outputs"]:
        raise HTTPException(404, f"Format '{fmt}' not available")

    filepath = job["outputs"][fmt]
    if not Path(filepath).exists():
        raise HTTPException(404, "Output file missing")

    stem = Path(job["config"]["title"]).stem or "transcript"
    media_types = {
        "txt": "text/plain",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
    }
    return FileResponse(
        filepath,
        media_type=media_types.get(fmt, "application/octet-stream"),
        filename=f"{stem}_transcript.{fmt}",
    )


class SummarizeRequest(BaseModel):
    keypoints: list[str]


@app.post("/api/summarize/{job_id}")
async def summarize(job_id: str, req: SummarizeRequest):
    """Generate a structured AI summary from a chosen keypoints template."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.get("transcript"):
        raise HTTPException(400, "Transcript not ready yet")
    if not req.keypoints:
        raise HTTPException(400, "No keypoints provided")

    structured = ai_summarize_with_template(job["transcript"], req.keypoints)
    job["structured_summary"] = structured
    return {"summary": structured}


@app.get("/api/jobs")
async def list_jobs():
    """List all jobs (latest first)."""
    return [
        {
            "job_id": jid,
            "filename": j.get("filename", ""),
            "status": j["status"],
            "progress": j["progress"],
            "created_at": j["created_at"],
        }
        for jid, j in sorted(jobs.items(), key=lambda x: x[1]["created_at"], reverse=True)
    ]


# ─── Background job runner ────────────────────────────────────────────────────

def _progress_callback(job_id: str, step: str, progress: int):
    job = jobs.get(job_id)
    if job:
        job["step"] = step
        job["progress"] = progress


def _run_job(job_id: str):
    job = jobs[job_id]
    cfg = job["config"]

    try:
        job["status"] = "processing"

        def cb(step, pct):
            _progress_callback(job_id, step, pct)
            # Check for cancellation
            if jobs.get(job_id, {}).get("status") == "cancelled":
                raise JOB_CANCELLED()

        transcript = run_pipeline(
            input_path=cfg["upload_path"],
            model_size=cfg["model"],
            num_speakers=cfg["speakers"],
            title=cfg["title"],
            denoise=cfg["denoise"],
            progress_cb=cb,
        )

        if job["status"] == "cancelled":
            return

        job["transcript"] = transcript
        job["speakers"] = transcript.speakers

        if cfg["cleanup"]:
            transcript = ai_cleanup_transcript(transcript, progress_cb=cb)
            job["transcript"] = transcript

        _export_outputs(job_id, cb)

        job["status"] = "done"
        job["step"] = "Complete!"
        job["progress"] = 100

    except JOB_CANCELLED:
        job["status"] = "cancelled"
        job["step"] = "Cancelled."
    except Exception as e:
        job["status"] = "error"
        job["step"] = "Error"
        job["error"] = str(e)
        traceback.print_exc()
    finally:
        # Clean up upload file
        try:
            Path(cfg["upload_path"]).unlink(missing_ok=True)
        except Exception:
            pass


def _export_outputs(job_id: str, cb=None):
    job = jobs[job_id]
    transcript = job["transcript"]
    cfg = job["config"]
    stem = cfg["title"].replace(" ", "_").replace("/", "-")
    fmts = cfg["output_formats"]
    out = {}

    base_pct = 95
    for i, fmt in enumerate(fmts):
        pct = base_pct + int((i / max(len(fmts), 1)) * 4)
        if cb:
            cb(f"Exporting {fmt.upper()}...", pct)
        path = str(OUTPUT_DIR / f"{job_id}_{stem}.{fmt}")
        if fmt == "txt":
            export_txt(transcript, path)
        elif fmt == "docx":
            export_docx(transcript, path)
        elif fmt == "pdf":
            export_pdf(transcript, path)
        out[fmt] = path

    job["outputs"] = out
