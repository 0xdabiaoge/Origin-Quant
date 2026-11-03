"""Create and inspect portable, private snapshots without touching live data.

SQLite is snapshotted using its backup API. Market files are copied separately,
so the archive is not a cross-file point-in-time transaction. Environment files
and credentials are deliberately excluded; the archive itself remains sensitive.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_archive(db: Any, config: Any) -> dict[str, Any]:
    """Write an archive to backups/; preserve all existing files and secrets."""
    backup_dir = Path(config.root) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = f"origin-full-{stamp}-{uuid.uuid4().hex[:8]}.zip"
    final = backup_dir / name
    with tempfile.TemporaryDirectory(prefix=".oq-backup-", dir=backup_dir) as temp:
        staging = Path(temp)
        snapshot = staging / "origin.db"
        with closing(db.connect()) as source, closing(sqlite3.connect(snapshot)) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("database snapshot failed integrity check")

        files: list[tuple[Path, str]] = [(snapshot, "database/origin.db")]
        parquet = Path(config.data.parquet_dir)
        if parquet.exists():
            for source_path in sorted(parquet.rglob("*.parquet")):
                rel = source_path.relative_to(parquet)
                if any(part.startswith(".") for part in rel.parts):
                    continue
                if source_path.is_symlink() or any(
                    p.is_symlink() for p in source_path.parents if p != parquet.parent
                ):
                    raise ValueError("market-data symlinks cannot be archived")
                if source_path.is_file():
                    files.append((source_path, "market/" + rel.as_posix()))

        config_dir = Path(config.config_dir) if config.config_dir else Path(config.root) / "config"
        if not (config_dir / "base.yaml").is_file():
            raise ValueError("configuration directory does not contain base.yaml")
        for relative in ("base.yaml", "risk.yaml"):
            source_path = config_dir / relative
            if source_path.is_file() and not source_path.is_symlink():
                files.append((source_path, "config/" + relative))
        strategies = config_dir / "strategies"
        if strategies.is_dir() and not strategies.is_symlink():
            for source_path in sorted(strategies.glob("*.yaml")):
                if source_path.is_file() and not source_path.is_symlink():
                    files.append((source_path, "config/strategies/" + source_path.name))

        temporary_archive = staging / "archive.zip"
        manifest: dict[str, Any] = {
            "format": 1,
            "created_utc": datetime.now(UTC).isoformat(),
            "config_hash": config.hash,
            "consistency": "SQLite snapshot; market and config files copied separately",
            "excluded": ["environment", "credentials", "logs"],
            "files": {},
        }
        with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source_path, member in files:
                # Hash precisely the bytes stored, even if an input is replaced concurrently.
                digest = hashlib.sha256()
                size = 0
                with source_path.open("rb") as source, archive.open(member, "w") as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        target.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                manifest["files"][member] = {"sha256": digest.hexdigest(), "bytes": size}
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        os.chmod(temporary_archive, 0o600)
        os.replace(temporary_archive, final)
    return {
        "file": name,
        "bytes": final.stat().st_size,
        "sha256": _digest(final),
        "files": len(files),
    }


def verify_archive(path: Path) -> dict[str, Any]:
    """Check every member against its manifest without extracting or restoring it."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or len(names) > 100_000:
            raise ValueError("duplicate or too many archive members")
        for info in infos:
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts or "\\" in info.filename:
                raise ValueError("invalid archive member")
        manifest_info = archive.getinfo("manifest.json")
        if manifest_info.file_size > 4 * 1024 * 1024:
            raise ValueError("manifest too large")
        manifest = json.loads(archive.read(manifest_info))
        files = manifest.get("files")
        if manifest.get("format") != 1 or not isinstance(files, dict):
            raise ValueError("invalid manifest")
        if set(names) != set(files) | {"manifest.json"}:
            raise ValueError("archive member list does not match manifest")
        for name, expected in files.items():
            info = archive.getinfo(name)
            if not isinstance(expected, dict) or info.file_size != expected.get("bytes"):
                raise ValueError(f"size mismatch: {name}")
            digest = hashlib.sha256()
            with archive.open(info) as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected.get("sha256"):
                raise ValueError(f"checksum mismatch: {name}")
        if "database/origin.db" not in files:
            raise ValueError("database snapshot missing")
        # Exercise a restore into a private temporary directory, never the live database.
        with tempfile.TemporaryDirectory(prefix=".oq-restore-check-", dir=path.parent) as temp:
            restored = Path(temp) / "origin.db"
            with archive.open("database/origin.db") as source, restored.open("wb") as dest:
                shutil.copyfileobj(source, dest, length=1024 * 1024)
            with closing(sqlite3.connect(restored)) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("restored database integrity check failed")
    return {
        "ok": True,
        "file": path.name,
        "sha256": _digest(path),
        "files": len(files),
        "sqlite_integrity": "ok",
    }
