"""Version is read from disk so a running process never lies about its code.

Two numbers exist and they are deliberately different:

* ``__version__`` is the Python package version, bumped by hand.
* ``OQ_VERSION`` (when set) is the *release* version stamped in at image build
  time by the release workflow (``1.0.0``, ``1.0.1``, ...). The container sets
  it so an operator can tell exactly which image is running.
"""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "1.0.0"


def app_version() -> str:
    """The version an operator sees: the build stamp wins over the package version."""
    stamped = os.getenv("OQ_VERSION", "").strip()
    return stamped or __version__


def code_version() -> str:
    """Best-effort build identifier recorded on every event."""
    base = app_version()
    try:
        head = Path(__file__).resolve().parents[2] / ".git" / "HEAD"
        if head.exists():
            ref = head.read_text(encoding="utf-8").strip()
            if ref.startswith("ref:"):
                ref_path = head.parent / ref.split(" ", 1)[1]
                if ref_path.exists():
                    return f"{base}+{ref_path.read_text(encoding='utf-8').strip()[:12]}"
            return f"{base}+{ref[:12]}"
    except OSError:
        pass
    return base
