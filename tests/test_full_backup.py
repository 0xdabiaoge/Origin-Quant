"""Backup preserves a SQLite snapshot and checks market/config archive bytes."""

from __future__ import annotations

import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from oq.app.main import build_app
from oq.storage.backup import create_archive, verify_archive


@pytest.fixture()
def backup_config(config, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.yaml").write_text("app:\n  mode: paper\n", encoding="utf-8")
    market = tmp_path / "market"
    (market / "funding").mkdir(parents=True)
    (market / "funding" / "BTC-USDT-SWAP.parquet").write_bytes(b"market-data")
    (tmp_path / ".env").write_text("SECRET=never-back-up", encoding="utf-8")
    return config.model_copy(
        update={
            "root": tmp_path,
            "config_dir": config_dir,
            "app": config.app.model_copy(update={"db_path": tmp_path / "test.db", "mode": "paper"}),
            "data": config.data.model_copy(update={"parquet_dir": market}),
        }
    )


def test_full_backup_and_verification(db, backup_config):
    result = create_archive(db, backup_config)
    archive_path = backup_config.root / "backups" / result["file"]
    assert verify_archive(archive_path)["ok"]
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        assert {
            "database/origin.db",
            "market/funding/BTC-USDT-SWAP.parquet",
            "config/base.yaml",
        } <= names
        assert ".env" not in names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["files"]["market/funding/BTC-USDT-SWAP.parquet"]["bytes"] == 11


def test_modified_archive_member_fails_verification(db, backup_config, tmp_path):
    result = create_archive(db, backup_config)
    original = backup_config.root / "backups" / result["file"]
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(changed, "w") as target:
        for name in source.namelist():
            body = source.read(name)
            target.writestr(name, b"other-value" if name.startswith("market/") else body)
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_archive(changed)


def test_archive_uses_actual_custom_config_directory(db, backup_config, tmp_path):
    custom = tmp_path / "custom-configuration"
    custom.mkdir()
    (custom / "base.yaml").write_text("custom: true\n", encoding="utf-8")
    cfg = backup_config.model_copy(update={"config_dir": custom})
    result = create_archive(db, cfg)
    with zipfile.ZipFile(cfg.root / "backups" / result["file"]) as archive:
        assert archive.read("config/base.yaml") == (custom / "base.yaml").read_bytes()


def test_archive_refuses_missing_configuration(db, backup_config, tmp_path):
    cfg = backup_config.model_copy(update={"config_dir": tmp_path / "missing"})
    with pytest.raises(ValueError, match="base.yaml"):
        create_archive(db, cfg)
    assert list((cfg.root / "backups").glob("origin-full-*.zip")) == []


def test_full_backup_endpoints_require_owner(backup_config, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "secret")
    app = build_app(backup_config, start_scheduler=False)
    with TestClient(app) as client:
        assert client.post("/api/v1/admin/backups/full").status_code == 401
        headers = {"Authorization": "Bearer secret"}
        result = client.post("/api/v1/admin/backups/full", headers=headers)
        assert result.status_code == 200, result.text
        name = result.json()["file"]
        assert client.post(f"/api/v1/admin/backups/full/{name}/verify", headers=headers).json()[
            "ok"
        ]
        assert (
            client.get("/api/v1/admin/backups/full", headers=headers).json()["items"][0]["file"]
            == name
        )
