from __future__ import annotations

import re


def clean_display_title(title: str, source: str) -> str:
    value = title.strip()
    if source == "YouTube":
        value = re.sub(r"^\(\d+\)\s+", "", value)
        value = re.sub(r"\s+-\s+YouTube$", "", value, flags=re.IGNORECASE)
    return value or source


def format_label(host: str, kind: str, title: str, path: str, scheme: str) -> str:
    if kind == "youtube_short":
        return "Short video"
    if kind == "youtube":
        return "Video"
    if kind == "pdf" or path.casefold().endswith(".pdf"):
        return "PDF"
    if kind == "github":
        return "Repository"
    if kind == "docs":
        return "Documentation"
    if kind == "search":
        return "Search"
    if kind == "browser_internal":
        return "Browser page"
    if scheme == "file":
        return "Local file"
    if any(
        value in host
        for value in ("chatgpt.com", "chat.openai.com", "claude.ai", "kimi.com")
    ):
        return "Conversation"
    lower_title = title.casefold()
    if any(
        value in lower_title
        for value in ("pricing", "membership", "subscription", "upgrade")
    ):
        return "Pricing"
    if re.search(r"\.(?:png|jpe?g|gif|webp|avif)$", path, re.IGNORECASE):
        return "Image"
    return "Web page"


def intent_label(next_action: str, format_label: str) -> str:
    value = next_action.casefold().strip()
    rules = (
        (("needs context", "review context"), "Review"),
        (("watch", "listen"), "Watch"),
        (("read", "skim", "paper", "document"), "Read"),
        (("compare", "choose", "buy", "subscription", "plan"), "Decide"),
        (("continue", "resume", "revisit"), "Continue"),
        (("try", "test", "build", "install", "use", "run", "explore"), "Try"),
        (("close", "discard"), "Close"),
    )
    for needles, label in rules:
        if any(needle in value for needle in needles):
            return label
    return {
        "Video": "Watch",
        "Short video": "Watch",
        "PDF": "Read",
        "Documentation": "Read",
        "Repository": "Try",
        "Conversation": "Continue",
        "Pricing": "Decide",
        "Search": "Review",
    }.get(format_label, "Reference")


def sentence(value: str) -> str:
    text = value.strip()[:180]
    if text and text[-1] not in ".!?":
        text += "."
    return text


def initials(value: str) -> str:
    parts = [part for part in re.split(r"[.\-_\s]+", value) if part]
    return "".join(part[0] for part in parts[:2]).upper() or "?"
