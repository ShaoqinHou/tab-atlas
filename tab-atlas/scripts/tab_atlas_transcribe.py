#!/usr/bin/env python3
"""Transcribe one local audio file with a short-lived Whisper process."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_MODEL = "openai/whisper-base"
SAMPLE_RATE = 16_000
MAX_AUDIO_SECONDS = 10 * 60


def decode_audio(path: Path):
    import numpy as np

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg_not_installed")
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-t",
            str(MAX_AUDIO_SECONDS + 1),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=False,
        timeout=90,
    )
    if result.returncode or not result.stdout:
        raise RuntimeError("audio_decode_failed")
    waveform = np.frombuffer(result.stdout, dtype=np.float32).copy()
    if waveform.size > SAMPLE_RATE * MAX_AUDIO_SECONDS:
        raise RuntimeError("audio_duration_exceeds_limit")
    return waveform


def transcribe(path: Path, model_name: str) -> str:
    waveform = decode_audio(path)
    try:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
    except ImportError as error:
        raise RuntimeError("local_whisper_dependencies_missing") from error

    use_cuda = bool(torch.cuda.is_available())
    device = 0 if use_cuda else -1
    dtype = torch.float16 if use_cuda else torch.float32
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        model_name,
        torch_dtype=dtype,
        use_safetensors=True,
    )
    processor = AutoProcessor.from_pretrained(model_name)
    recognizer = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        torch_dtype=dtype,
        device=device,
    )
    result = recognizer(
        {"raw": waveform, "sampling_rate": SAMPLE_RATE},
        chunk_length_s=30,
        stride_length_s=5,
        generate_kwargs={"task": "transcribe"},
    )
    text = str(result.get("text") or "").strip()
    if not text:
        raise RuntimeError("empty_transcript")
    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a local TabAtlas voice note"
    )
    parser.add_argument("audio", type=Path)
    parser.add_argument(
        "--model",
        default=os.environ.get("TABATLAS_WHISPER_MODEL", DEFAULT_MODEL),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = args.audio.resolve()
    if not path.is_file():
        print(json.dumps({"error": "audio_file_not_found"}))
        return 2
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        text = transcribe(path, str(args.model))
    except Exception as error:
        code = str(error).strip() or error.__class__.__name__
        code = "".join(
            character if character.isalnum() or character in "_-" else "_"
            for character in code
        )
        print(json.dumps({"error": code[:120]}))
        return 1
    print(json.dumps({"text": text, "model": str(args.model)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
