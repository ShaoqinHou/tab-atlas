from __future__ import annotations

MAX_NOTE_BYTES = 32 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_AUDIO_DURATION_MS = 10 * 60 * 1000
PROMPT_VERSION = "note-interpretation-v1"
PRIMARY_COLLECTION_KINDS = {"space", "topic", "focus"}
OVERLAY_COLLECTION_KINDS = {"project", "action_list"}
COLLECTION_KINDS = PRIMARY_COLLECTION_KINDS | OVERLAY_COLLECTION_KINDS
AUDIO_SIGNATURES = {
    "audio/wav": lambda data: len(data) >= 12
    and data[:4] == b"RIFF"
    and data[8:12] == b"WAVE",
    "audio/webm": lambda data: data.startswith(b"\x1aE\xdf\xa3"),
    "audio/ogg": lambda data: data.startswith(b"OggS"),
    "audio/mp4": lambda data: len(data) >= 12 and data[4:8] == b"ftyp",
}
AUDIO_SUFFIXES = {
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
}
