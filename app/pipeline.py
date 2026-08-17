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


# ─── Chunking for long files ──────────────────────────────────────────────────

DEFAULT_CHUNK_SECONDS = 15 * 60  # split files longer than this into chunks


def split_into_chunks(wav_path: str, tmp_dir: str, duration: float,
                       chunk_sec: float = DEFAULT_CHUNK_SECONDS) -> list:
    """Split a long wav into fixed-length chunks via ffmpeg stream-copy (no re-encode).

    Keeps diarization/transcription working on bounded-size audio instead of
    loading and processing a multi-hour file in one shot, which is what was
    blowing past the job timeout on very large files.
    """
    if duration <= chunk_sec:
        return [(wav_path, 0.0, duration)]

    chunks_dir = os.path.join(tmp_dir, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)
    pattern = os.path.join(chunks_dir, "chunk_%04d.wav")
    cmd = [
        "ffmpeg", "-y",
        "-i", wav_path,
        "-f", "segment",
        "-segment_time", str(chunk_sec),
        "-c", "copy",
        "-reset_timestamps", "1",
        pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg segmenting failed: {result.stderr[-500:]}")

    chunk_files = sorted(Path(chunks_dir).glob("chunk_*.wav"))
    chunks, offset = [], 0.0
    for cf in chunk_files:
        cdur = get_duration(str(cf))
        chunks.append((str(cf), offset, cdur))
        offset += cdur
    return chunks


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


def load_audio_segment(wav_path: str):
    try:
        from pydub import AudioSegment
    except ImportError:
        raise RuntimeError("pydub not installed. Run: pip install pydub")
    return AudioSegment.from_wav(wav_path)


def transcribe_segment(model, audio, start: float, end: float) -> tuple:
    chunk = audio[int(start * 1000):int(end * 1000)]
    if len(chunk) < 500:
        return "", ""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        chunk_path = f.name
    try:
        chunk.export(chunk_path, format="wav")
        result = model.transcribe(
            chunk_path,
            language=None,          # auto-detect source language
            task="translate",       # always output English, regardless of spoken language
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


def merge_consecutive_utterances(utterances: list, max_gap: float = 3.0) -> list:
    """Collapse consecutive same-speaker utterances into one turn so the
    transcript flows as continuous dialogue instead of one block per
    diarization segment."""
    if not utterances:
        return utterances

    merged = [utterances[0]]
    for utt in utterances[1:]:
        prev = merged[-1]
        if utt.speaker == prev.speaker and (utt.start - prev.end) <= max_gap:
            prev.text = f"{prev.text} {utt.text}".strip()
            prev.end = utt.end
        else:
            merged.append(utt)
    return merged


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
        cb("Remuxing to 16 kHz mono WAV...", 3)
        wav_path = extract_audio(str(source), tmp_dir)
        duration = get_duration(wav_path)
        cb(f"Audio ready — {fmt_time(duration)} total", 10)

        # 2. Denoise
        if denoise:
            cb("Reading audio samples for noise profile...", 12)
            cb("Applying spectral noise reduction...", 13)
            wav_path = denoise_audio(wav_path, tmp_dir)
            cb("Noise reduction complete", 17)

        # 3. Split into manageable chunks for long files
        chunks = split_into_chunks(wav_path, tmp_dir, duration)
        if len(chunks) > 1:
            cb(f"Split into {len(chunks)} chunks of ~{fmt_time(DEFAULT_CHUNK_SECONDS)} for processing", 20)

        hf_token = os.environ.get("HF_TOKEN")

        # 4. Load Whisper (once, reused across all chunks)
        if model_size in _whisper_model_cache:
            cb(f"Whisper '{model_size}' already in memory", 24)
        else:
            cb(f"Loading Whisper '{model_size}' weights...", 22)
        model = load_whisper_model(model_size)
        cb(f"Whisper '{model_size}' ready", 26)

        # 5. Diarize + transcribe, chunk by chunk
        utterances = []
        all_raw_segments = []
        n_chunks = len(chunks)
        progress_span = 70.0  # 26 → 96

        for ci, (chunk_path, offset, cdur) in enumerate(chunks):
            chunk_base = 26 + (ci / n_chunks) * progress_span
            chunk_span = progress_span / n_chunks

            if hf_token:
                cb(f"Chunk {ci+1}/{n_chunks} — diarizing...", int(chunk_base))
                raw_segments = diarize(chunk_path, num_speakers)
            else:
                raw_segments = None

            if raw_segments is None:
                raw_segments = fallback_segments(cdur)
            else:
                # Speaker clusters are local to each chunk — namespace them so
                # they don't collide with same-named clusters from other chunks.
                raw_segments = [(f"chunk{ci}_{spk}", s, e) for spk, s, e in raw_segments]

            raw_segments = merge_short_segments(raw_segments)
            all_raw_segments.extend(raw_segments)

            chunk_audio = load_audio_segment(chunk_path)
            m = len(raw_segments)
            for i, (speaker, start, end) in enumerate(raw_segments):
                pct = int(chunk_base + (i / max(m, 1)) * chunk_span)
                cb(f"[{fmt_time(offset + start)} → {fmt_time(offset + end)}] "
                   f"chunk {ci+1}/{n_chunks} · seg {i+1}/{m}", pct)
                text, lang = transcribe_segment(model, chunk_audio, start, end)
                if text:
                    utterances.append(Utterance(
                        speaker=speaker,
                        start=offset + start,
                        end=offset + end,
                        text=text,
                        language=lang,
                    ))

        utterances = merge_consecutive_utterances(utterances)
        speaker_map = build_speaker_map(all_raw_segments)
        cb(f"{len(speaker_map)} speaker(s) · {len(utterances)} utterance(s) transcribed", 96)

        return Transcript(
            title=title or source.stem,
            source_file=str(source),
            date=datetime.now().strftime("%d %B %Y, %H:%M"),
            duration=duration,
            utterances=utterances,
            speakers=speaker_map,
        )


# ─── AI cleanup ───────────────────────────────────────────────────────────────

def ai_cleanup_transcript(transcript: Transcript, progress_cb: Optional[Callable] = None) -> Transcript:
    def cb(step, pct):
        if progress_cb:
            progress_cb(step, pct)

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

        cb("AI cleanup — fixing punctuation & removing filler words...", 88)
        cleanup = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": f"""You are a transcript editor. Clean up this transcript:
- Fix punctuation and capitalisation
- Remove filler words (uh, um) unless meaningful
- Fix obvious transcription errors using context
- Do NOT change meaning, speaker order, or timestamps
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

    except Exception as e:
        print(f"AI cleanup failed: {e}")

    return transcript


# ─── Structured AI summary ────────────────────────────────────────────────────

def ai_summarize_with_template(transcript: Transcript, keypoints: list) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY not set"}

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        raw_text = "\n".join(
            f"[{fmt_time(utt.start)}] {transcript.speakers.get(utt.speaker, utt.speaker)}: {utt.text}"
            for utt in transcript.utterances
        )
        kp_str = "\n".join(f"- {kp}" for kp in keypoints)

        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2048,
            messages=[{"role": "user", "content": f"""Extract and summarize these keypoints from the transcript. For each keypoint provide 2-5 concise bullet points.

Keypoints:
{kp_str}

Format exactly like this (one block per keypoint):
### [Keypoint Name]
• point 1
• point 2

TRANSCRIPT:
{raw_text[:8000]}"""}]
        )

        text = response.content[0].text.strip()
        result = {}
        current_key = None
        current_bullets: list = []

        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("### "):
                if current_key is not None:
                    result[current_key] = "\n".join(current_bullets)
                current_key = line[4:].strip()
                current_bullets = []
            elif line.startswith(("•", "-", "*")) and current_key is not None:
                current_bullets.append(line)
            elif line and current_key is not None and not line.startswith("#"):
                current_bullets.append("• " + line)

        if current_key is not None:
            result[current_key] = "\n".join(current_bullets)

        return result

    except Exception as e:
        return {"error": str(e)}
