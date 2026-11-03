"""An anonymous SPA request cannot escape the built frontend directory."""

from fastapi.testclient import TestClient

from oq.app.main import build_app


def test_spa_does_not_serve_parent_or_backslash_paths(config, tmp_path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("PUBLIC SHELL", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("PRIVATE SENTINEL", encoding="utf-8")
    monkeypatch.setattr("oq.app.main.WEB_DIR", web)
    monkeypatch.setenv("OQ_ALLOW_LOOPBACK_AUTH", "0")
    cfg = config.model_copy(
        update={
            "root": tmp_path,
            "app": config.app.model_copy(update={"db_path": tmp_path / "test.db"}),
        }
    )
    app = build_app(cfg, start_scheduler=False)
    with TestClient(app) as client:
        for path in ("/%2e%2e/secret.txt", "/..%2fsecret.txt", "/..%5csecret.txt"):
            response = client.get(path)
            assert response.status_code == 404, path
            assert "PRIVATE SENTINEL" not in response.text
        assert "PUBLIC SHELL" in client.get("/app").text
