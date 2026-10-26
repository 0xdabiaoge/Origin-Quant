"""Version is read from disk so a running process never lies about its code."""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"


def code_version() -> str:
    """Best-effort build identifier recorded on every event."""
    try:
        head = Path(__file__).resolve().parents[2] / ".git" / "HEAD"
        if head.exists():
            ref = head.read_text(encoding="utf-8").strip()
            if ref.startswith("ref:"):
                ref_path = head.parent / ref.split(" ", 1)[1]
                if ref_path.exists():
                    return f"{__version__}+{ref_path.read_text(encoding='utf-8').strip()[:12]}"
            return f"{__version__}+{ref[:12]}"
    except OSError:
        pass
    return __version__
