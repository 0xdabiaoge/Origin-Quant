"""Optional AI confirmation gates paper entries without bypassing exits or risk."""

from __future__ import annotations

import time

import pytest

from oq.ai.proposals import Proposal
from oq.ai.settings import AISettings
from oq.app import ai_api
from oq.app.orchestrator import Orchestrator
from oq.data.models import Bar
from oq.data.store import ParquetStore
from oq.domain.intent import Intent
from oq.types import Book, D, InstType, Side


@pytest.fixture()
def confirmation(config, db, store, tmp_path, monkeypatch):
    symbol = "BTC-USDT-SWAP"
    cfg = config.model_copy(
        update={
            "universe": [entry for entry in config.universe if entry.symbol == symbol],
            "data": config.data.model_copy(update={"parquet_dir": tmp_path / "market"}),
            "app": config.app.model_copy(update={"mode": "paper"}),
        }
    )
    now = int(time.time() * 1000)
    step = 900_000
    last = (now // step) * step - step
    bars = [
        Bar(
            symbol=symbol,
            timeframe="15m",
            ts_ms=last - step * (29 - index),
            open=D("61000"),
            high=D("61100"),
            low=D("60900"),
            close=D("61000"),
            volume=D("1"),
            confirmed=True,
        )
        for index in range(30)
    ]
    ParquetStore(cfg.data.parquet_dir).write_bars(symbol, "15m", bars)
    settings = AISettings(enabled=True, paper_confirmation_enabled=True, model="fake")
    with db.tx() as conn:
        store.set_kv(conn, ai_api.CONFIG_KEY, settings.model_dump())

    class Strategy:
        id = "rule"
        book = Book.DIRECTIONAL

        def generate(self, ctx):
            return [
                Intent.open_long(
                    strategy_id=self.id,
                    symbol=symbol,
                    ts_ms=ctx.now_ms,
                    stop_px=D("60680"),
                    ref_px=D("61000"),
                    tp_ladder=((D("1.5"), D("0.33")), (D("3.0"), D("0.33"))),
                )
            ]

    orch = Orchestrator(cfg, db, store, None)
    orch.strategies = [Strategy()]
    return orch, now, symbol


def test_paper_confirmation_agreement_runs_risk_and_records_link(confirmation, monkeypatch):
    orch, now, symbol = confirmation
    monkeypatch.setattr(
        ai_api,
        "request_proposal",
        lambda settings, snapshot: (
            Proposal(action="long", symbol=symbol, stop_px="60680"),
            {"total_tokens": 12},
        ),
    )
    report = orch.run_once(now_ms=now)
    assert report.intents == 1 and report.approved == 1 and report.fills == 1, report.errors
    history = orch.store.get_kv(ai_api.HISTORY_KEY, [])
    assert history[0]["confirmed"] and history[0]["strategy_id"] == "rule"
    assert history[0]["execution"] == "paper_strategy_confirmation_only"
    decisions = orch.store.recent_decisions()
    assert decisions[0]["intent"]["tags"]["ai_confirmation_id"] == history[0]["id"]


@pytest.mark.parametrize("outcome", ["wait", "short", "wrong_symbol", "error"])
def test_paper_confirmation_missing_or_disagreement_rejects_entry(
    confirmation, monkeypatch, outcome
):
    orch, now, symbol = confirmation

    def model(settings, snapshot):
        if outcome == "error":
            raise TimeoutError("private exception")
        if outcome == "wait":
            return Proposal(action="wait", symbol=symbol), {}
        if outcome == "wrong_symbol":
            return Proposal(action="long", symbol="ETH-USDT-SWAP", stop_px="60680"), {}
        return Proposal(action="short", symbol=symbol, stop_px="61320"), {}

    monkeypatch.setattr(ai_api, "request_proposal", model)
    report = orch.run_once(now_ms=now)
    assert report.rejected == 1 and report.fills == 0
    assert orch.db.query_one("SELECT COUNT(*) AS n FROM orders")["n"] == 0
    assert orch.store.recent_decisions()[0]["gates"][0]["code"] == "ai_confirmation"
    entry = orch.store.get_kv(ai_api.HISTORY_KEY, [])[0]
    assert not entry["confirmed"] and "private exception" not in str(entry)


def test_disabled_confirmation_does_not_change_existing_strategy(confirmation, monkeypatch):
    orch, now, _ = confirmation
    with orch.db.tx() as conn:
        orch.store.set_kv(conn, ai_api.CONFIG_KEY, AISettings().model_dump())
    monkeypatch.setattr(ai_api, "request_proposal", lambda *a: pytest.fail("unexpected AI request"))
    report = orch.run_once(now_ms=now)
    assert report.approved == 1 and report.fills == 1


def test_corrupted_confirmation_config_never_silently_disables_gate(confirmation, monkeypatch):
    orch, now, _ = confirmation
    with orch.db.tx() as conn:
        conn.execute("UPDATE kv SET value=? WHERE key=?", ("{broken", ai_api.CONFIG_KEY))
    monkeypatch.setattr(ai_api, "request_proposal", lambda *a: pytest.fail("unexpected AI request"))
    report = orch.run_once(now_ms=now)
    assert report.rejected == 1 and report.fills == 0
    assert "settings unavailable" in orch.store.recent_decisions()[0]["gates"][0]["reason"]


def test_position_stop_executes_before_confirmation_and_prevents_reentry(confirmation, monkeypatch):
    orch, now, symbol = confirmation
    orch.portfolio.apply_fill(
        symbol=symbol,
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.01"),
        px=D("61000"),
        fee=D("0"),
        ts_ms=now - 1_000_000,
        stop_px=D("60950"),
    )
    monkeypatch.setattr(ai_api, "request_proposal", lambda *a: pytest.fail("unexpected AI request"))
    report = orch.run_once(now_ms=now)
    assert report.fills == 1 and report.rejected == 1
    assert not orch.portfolio.positions


def test_confirmation_and_independent_ai_entry_cannot_both_be_enabled():
    with pytest.raises(ValueError, match="cannot both be enabled"):
        AISettings(auto_paper_enabled=True, paper_confirmation_enabled=True)


def test_timeout_denies_and_records_gate(confirmation, monkeypatch):
    orch, now, _ = confirmation
    monkeypatch.setattr(
        orch, "_confirm_paper_ai", lambda *a: ai_api.PaperConfirmation(False, "timeout")
    )
    report = orch.run_once(now_ms=now)
    assert report.rejected == 1 and report.fills == 0
    gate = orch.store.recent_decisions()[0]["gates"][0]
    assert gate["code"] == "ai_confirmation" and "timeout" in gate["reason"]


def test_unrecorded_confirmation_never_authorizes_entry(confirmation, monkeypatch):
    orch, now, symbol = confirmation
    monkeypatch.setattr(
        ai_api,
        "request_proposal",
        lambda settings, snapshot: (Proposal(action="long", symbol=symbol, stop_px="60680"), {}),
    )

    def fail_history(*args):
        raise RuntimeError("simulated DB write failure")

    monkeypatch.setattr(ai_api, "_append_history", fail_history)
    report = orch.run_once(now_ms=now)
    assert report.rejected == 1 and report.fills == 0
    assert "audit_unavailable" in orch.store.recent_decisions()[0]["gates"][0]["reason"]


def test_settings_changed_during_review_never_authorizes_entry(confirmation, monkeypatch):
    orch, now, symbol = confirmation

    def model(settings, snapshot):
        with orch.db.tx() as conn:
            orch.store.set_kv(conn, ai_api.CONFIG_KEY, AISettings().model_dump())
        return Proposal(action="long", symbol=symbol, stop_px="60680"), {}

    monkeypatch.setattr(ai_api, "request_proposal", model)
    report = orch.run_once(now_ms=now)
    assert report.rejected == 1 and report.fills == 0
    assert "settings changed" in orch.store.recent_decisions()[0]["gates"][0]["reason"]


def test_model_agreement_cannot_override_risk_engine(confirmation, monkeypatch):
    orch, now, symbol = confirmation
    orch.risk.risk = orch.risk.risk.model_copy(update={"min_rr": D("2")})
    monkeypatch.setattr(
        ai_api,
        "request_proposal",
        lambda settings, snapshot: (Proposal(action="long", symbol=symbol, stop_px="60680"), {}),
    )
    report = orch.run_once(now_ms=now)
    assert report.rejected == 1 and report.fills == 0
    assert orch.store.get_kv(ai_api.HISTORY_KEY, [])[0]["confirmed"]
    gates = orch.store.recent_decisions()[0]["gates"]
    assert any(gate["code"] == "risk_reward" and not gate["passed"] for gate in gates)
