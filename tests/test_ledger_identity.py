from types import SimpleNamespace

import pytest

from oq.storage.identity import bind_ledger


def test_ledger_binding_is_stable_and_rejects_environment_change(db, store):
    bind_ledger(db, store, "paper", None)
    bind_ledger(db, store, "paper", None)
    with pytest.raises(RuntimeError, match="differs"):
        bind_ledger(
            db, store, "live", SimpleNamespace(has_credentials=True, demo=True, api_key="demo-key")
        )


def test_legacy_history_is_not_assumed_to_be_paper(db, store):
    with db.tx() as conn:
        store.save_equity(conn, ts_ms=1, snapshot={"equity": "100"})
    with pytest.raises(RuntimeError, match="legacy ledger"):
        bind_ledger(db, store, "paper", None)
    assert store.get_kv("ledger.identity.v1") is None


def test_live_binding_rejects_changed_credentials_and_environment(db, store):
    rest = SimpleNamespace(has_credentials=True, demo=True, api_key="demo-key")
    bind_ledger(db, store, "live", rest)
    rest.api_key = "other-key"
    with pytest.raises(RuntimeError, match="differs"):
        bind_ledger(db, store, "live", rest)


def test_binding_uses_actual_rest_credential_contract(db, store, config):
    from oq.data.okx_rest import OkxRest

    rest = OkxRest(
        config.venues["okx"],
        api_key="test-key",
        secret="test-secret",
        passphrase="test-pass",
        demo=True,
    )
    try:
        bind_ledger(db, store, "live", rest)
        assert store.get_kv("ledger.identity.v1")["environment"] == "okx-demo"
    finally:
        rest.close()
    rest.api_key, rest.demo = "demo-key", False
    with pytest.raises(RuntimeError, match="differs"):
        bind_ledger(db, store, "live", rest)
