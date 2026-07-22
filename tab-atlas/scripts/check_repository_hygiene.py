from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath


HIDDEN_INSTRUCTION_DIRECTORIES = {".codex", ".claude"}
PRIVATE_PREFIXES = (
    "legacy/",
    "tab-atlas/.local/",
    "tab-atlas/report/",
    "tab-atlas/state/",
)
AMBIENT_INSTRUCTION_FILES = {"agents.md", "claude.md", ".cursorrules"}
PRIVATE_SUFFIXES = {
    ".db",
    ".log",
    ".mp3",
    ".mp4",
    ".ogg",
    ".sqlite",
    ".sqlite3",
    ".wav",
    ".webm",
}


def tracked_paths(repository: Path) -> list[PurePosixPath]:
    result = subprocess.run(
        ["git", "-C", str(repository), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    paths = [
        PurePosixPath(value.decode("utf-8"))
        for value in result.stdout.split(b"\0")
        if value
    ]
    return [path for path in paths if (repository / Path(*path.parts)).is_file()]


def violations(paths: list[PurePosixPath]) -> list[str]:
    found: list[str] = []
    for path in paths:
        lowered = [part.casefold() for part in path.parts]
        normalized = path.as_posix().casefold()
        if normalized.startswith(
            PRIVATE_PREFIXES
        ) or HIDDEN_INSTRUCTION_DIRECTORIES.intersection(lowered):
            found.append(f"private or quarantined path is tracked: {path}")
        if path.name.casefold() in AMBIENT_INSTRUCTION_FILES:
            found.append(f"ambient instruction file is tracked: {path}")
        if path.suffix.casefold() in PRIVATE_SUFFIXES:
            found.append(f"private runtime artifact is tracked: {path}")
    return found


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    repository = Path(
        subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    found = violations(tracked_paths(repository))
    skill_policy = project / "agents" / "openai.yaml"
    policy_text = skill_policy.read_text(encoding="utf-8")
    if "allow_implicit_invocation: false" not in policy_text:
        found.append("TabAtlas skill must remain opt-in")
    if found:
        for item in found:
            print(item, file=sys.stderr)
        return 1
    print("repository hygiene: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
