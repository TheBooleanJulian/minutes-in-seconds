"""
Minutes in Seconds — Transcription Pipeline
────────────────────────────────────────────
Handles audio extraction, diarization, and Whisper transcription.
"""

import os
import sys
import subprocess
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Callable
from dataclasses import dataclass, field


# ─── Cancellation sentinel ────────────────────────────────────────────────────

class JOB_CANCELLED(Exception):
    pass


# ─── Data models ──────────────────────────────────────────────────────────────

@dataclass
class Utterance:
    speaker: str
    start: float
    end: float
    text: str
    language: str = ""


@dataclass
class Transcript:
    title: str
    source_file: str
    date: str
    duration: float
    utterances: list = field(default_factory=list)
    speakers: dict = field(default_factory=dict)
    summary: str = ""


# ─── Utilities ────────────────────────────────────────────────────────────────

def fmt_time(seconds: float) -> str:
    td = timedelta(seconds=round(seconds))
    total = int(td.total_seconds())
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError("ffmpeg not found. Install it: https://ffmpeg.org/download.html")


# ─── Step 1: Extract audio ────────────────────────────────────────────────────

def extract_audio(input_path: str, tmp_dir: str) -> str:
    check_ffmpeg()
    out_path = os.path.join(tmp_dir, "audio_16k.wav")
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-sample_fmt", "s16",
        "-af", "loudnorm",
        out_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")
    return out_path


def get_duration(wav_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", wav_path],
        capture_output=True, text=True
    )
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


# ─── Step 2: Optional denoising ───────────────────────────────────────────────

def denoise_audio(wav_path: str, tmp_dir: str) -> str:
    try:
        import noisereduce as nr
        import soundfile as sf
    except ImportError:
        print("noisereduce not installed — skipping denoising")
        return wav_path

    data, rate = sf.read(wav_path)
    reduced = nr.reduce_noise(y=data, sr=rate, stationary=False)
    out_path = os.path.join(tmp_dir, "audio_denoised.wav")
    sf.write(out_path, reduced, rate)
    return out_path


# ─── Step 3: Speaker diarization ─────────────────────────────────────────────

def diarize(wav_path: str, num_speakers: Optional[int] = None) -> Optional[list]:
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        return None  # fallback to single speaker chunked mode

    try:
        from pyannote.audio import Pipeline
    except ImportError:
        return None

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token
    )

    kwargs = {}
    if num_speakers:
        kwargs["num_speakers"] = num_speakers

    diarization = pipeline(wav_path, **kwargs)
    segments = [
        (speaker, turn.start, turn.end)
        for turn, _, speaker in diarization.itertracks(yield_label=True)
    ]
    return segments


def fallback_segments(duration: float, chunk_sec: float = 30.0) -> list:
    segments, t = [], 0.0
    while t < duration:
        end = min(t + chunk_sec, duration)
        segments.append(("SPEAKER_00", t, end))
        t = end
    return segments


def merge_short_segments(segments: list, min_duration: float = 1.5) -> list:
    if not segments:
        return segments
    merged = [list(segments[0])]
    for seg in segments[1:]:
        prev = merged[-1]
        if seg[0] == prev[0] and (seg[1] - prev[2]) < 0.5:
            prev[2] = seg[2]
        elif (prev[2] - prev[1]) < min_duration and seg[0] == prev[0]:
            prev[2] = seg[2]
        else:
            merged.append(list(seg))
    return [tuple(s) for s in merged]


# ─── Step 4: Transcription ────────────────────────────────────────────────────

_whisper_model_cache = {}

def load_whisper_model(model_size: str):
    if model_size in _whisper_model_cache:
        return _whisper_model_cache[model_size]
    import whisper
    model = whisper.load_model(model_size)
    _whisper_model_cache[model_size] = model
    return model


def transcribe_segment(model, wav_path: str, start: float, end: float) -> tuple:
    try:
        from pydub import AudioSegment
    except ImportError:
        raise RuntimeError("pydub not installed. Run: pip install pydub")

    audio = AudioSegment.from_wav(wav_path)
    chunk = audio[int(start * 1000):int(end * 1000)]
    if len(chunk) < 500:
        return "", ""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        chunk_path = f.name
    try:
        chunk.export(chunk_path, format="wav")
        result = model.transcribe(
            chunk_path,
            language=None,          # auto-detect EN/ZH/code-switch
            task="transcribe",
            temperature=0.0,
            word_timestamps=False,
            fp16=False,
            condition_on_previous_text=False,
        )
        return result["text"].strip(), result.get("language", "")
    finally:
        try:
            os.unlink(chunk_path)
        except Exception:
            pass


def build_speaker_map(segments: list) -> dict:
    unique = sorted(set(s[0] for s in segments))
    return {uid: f"Speaker {i+1}" for i, uid in enumerate(unique)}


# ─── Main pipeline ────────────────────────────────────────────────────────────

def run_pipeline(
    input_path: str,
    model_size: str = "medium",
    num_speakers: Optional[int] = None,
    title: str = "",
    denoise: bool = False,
    progress_cb: Optional[Callable] = None,
) -> Transcript:

    def cb(step: str, pct: int):
        if progress_cb:
            progress_cb(step, pct)

    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(f"File not found: {input_path}")

    with tempfile.TemporaryDirectory() as tmp_dir:

        # 1. Extract
        cb("Extracting audio...", 5)
        wav_path = extract_audio(str(source), tmp_dir)
        duration = get_duration(wav_path)

        # 2. Denoise
        if denoise:
            cb("Denoising audio...", 12)
            wav_path = denoise_audio(wav_path, tmp_dir)

        # 3. Diarize
        cb("Detecting speakers...", 18)
        raw_segments = diarize(wav_path, num_speakers)
        if raw_segments is None:
            raw_segments = fallback_segments(duration)

        raw_segments = merge_short_segments(raw_segments)
        speaker_map = build_speaker_map(raw_segments)

        # 4. Load Whisper
        cb(f"Loading Whisper ({model_size})...", 25)
        model = load_whisper_model(model_size)

        # 5. Transcribe
        utterances = []
        n = len(raw_segments)
        for i, (speaker, start, end) in enumerate(raw_segments):
            pct = 25 + int((i / n) * 60)
            cb(f"Transcribing segment {i+1}/{n}...", pct)
            text, lang = transcribe_segment(model, wav_path, start, end)
            if text:
                utterances.append(Utterance(
                    speaker=speaker,
                    start=start,
                    end=end,
                    text=text,
                    language=lang,
                ))

        return Transcript(
            title=title or source.stem,
            source_file=str(source),
            date=datetime.now().strftime("%d %B %Y, %H:%M"),
            duration=duration,
            utterances=utterances,
            speakers=speaker_map,
        )


# ─── AI cleanup ───────────────────────────────────────────────────────────────

def ai_cleanup_transcript(transcript: Transcript) -> Transcript:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return transcript

    try:
        import anthropic
        import re
        client = anthropic.Anthropic(api_key=api_key)

        raw_lines = [
            f"[{fmt_time(utt.start)}] {transcript.speakers.get(utt.speaker, utt.speaker)}: {utt.text}"
            for utt in transcript.utterances
        ]
        raw_text = "\n".join(raw_lines)

        cleanup = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": f"""You are a transcript editor. Clean up this transcript:
- Fix punctuation and capitalisation
- Remove filler words (uh, um) unless meaningful
- Fix obvious transcription errors using context
- Do NOT change meaning, speaker order, or timestamps
- Preserve Mandarin Chinese as-is
- Return ONLY the cleaned lines, no preamble

TRANSCRIPT:
{raw_text}"""}]
        )
        cleaned = cleanup.content[0].text.strip().split("\n")
        new_utts = []
        idx = 0
        for line in cleaned:
            if not line.strip() or idx >= len(transcript.utterances):
                continue
            m = re.match(r'^\[[\d:]+\]\s+.+?:\s+(.+)$', line)
            if m:
                orig = transcript.utterances[idx]
                new_utts.append(Utterance(
                    speaker=orig.speaker, start=orig.start, end=orig.end,
                    text=m.group(1), language=orig.language
                ))
                idx += 1
        if new_utts:
            transcript.utterances = new_utts

        # Summary
        summary = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            messages=[{"role": "user", "content": f"""Summarise this meeting/lecture transcript in 3-5 bullet points.
Cover: main topics, key decisions, action items.
Use plain text bullet points starting with •, no markdown.

TRANSCRIPT:
{raw_text[:6000]}"""}]
        )
        transcript.summary = summary.content[0].text.strip()

    except Exception as e:
        print(f"AI cleanup failed: {e}")

    return transcript
