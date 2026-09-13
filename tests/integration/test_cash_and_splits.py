from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from typer.testing import CliRunner

import investlab.cli as cli
from investlab.store import LedgerStore, TradeRecord

runner = CliRunner()


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(cli, "today_et", lambda: date(2026, 9, 14))
    monkeypatch.setattr(cli, "now_et", lambda: datetime(2026, 9, 14, 17, 0, tzinfo=cli.EASTERN))
    s = LedgerStore("deca")
    s.init(Decimal("100000"), date(2026, 9, 8))
    return s


def _buy(store, qty, price, session, stop=None):
    book = store.load_book()
    lot = {"quantity": qty, "price": price, "commission": "5", "opened": session.isoformat()}
    if stop:
        lot["stop"] = stop
    book["positions"] = [{"symbol": "AAA", "asset_class": "stock", "lots": [lot]}]
    cash = Decimal(book["cash"]) - Decimal(price) * qty - 5
    book["cash"] = str(cash)
    store.save_book(book)
    store.append_trade(
        TradeRecord(
            session,
            "t",
            "AAA",
            "buy",
            qty,
            Decimal(price),
            Decimal("5"),
            Decimal("0"),
            "stock",
            cash,
        )
    )


def test_cash_event_moves_cash_and_replays(store):
    after = store.add_cash_event(date(2026, 9, 12), "interest", Decimal("14.20"))
    assert after == Decimal("100014.20")
    assert store.cash_events()[0]["kind"] == "interest"
    assert store.holdings_at(date(2026, 9, 11))[0] == Decimal("100000")
    assert store.holdings_at(date(2026, 9, 14))[0] == Decimal("100014.20")
    with pytest.raises(ValueError):
        store.add_cash_event(date(2026, 9, 12), "gift", Decimal("1"))
    with pytest.raises(ValueError):
        store.add_cash_event(date(2026, 9, 12), "fee", Decimal("0"))


def test_split_restates_lots_stops_and_history(store):
    _buy(store, 10, "100", date(2026, 9, 9), stop="90")
    assert store.apply_split("AAA", Decimal("2"), date(2026, 9, 11)) == 1
    lot = store.load_book()["positions"][0]["lots"][0]
    assert lot["quantity"] == 20
    assert Decimal(lot["price"]) == Decimal("50")
    assert Decimal(lot["stop"]) == Decimal("45")
    assert store.adjusted_trades()[0]["quantity"] == "20"
    assert store.holdings_at(date(2026, 9, 14))[1] == {"AAA": 20}
    assert store.apply_split("NOPE", Decimal("2"), date(2026, 9, 11)) == 0


def test_round_trip_after_a_split_matches_in_new_shares(store):
    _buy(store, 10, "100", date(2026, 9, 9))
    store.apply_split("AAA", Decimal("2"), date(2026, 9, 11))
    store.append_trade(
        TradeRecord(
            date(2026, 9, 14),
            "t",
            "AAA",
            "sell",
            20,
            Decimal("55"),
            Decimal("5"),
            Decimal("0"),
            "stock",
            Decimal("0"),
        )
    )
    account = store.account()
    trips = store.performance(account).round_trips
    assert len(trips) == 1 and trips[0].quantity == 20
    assert trips[0].buy_price == Decimal("50")


def test_cli_cash_adjust_and_split(store):
    ok = runner.invoke(cli.app, ["cash-adjust", "--amount", "14.20", "--kind", "interest"])
    assert ok.exit_code == 0, ok.output
    assert Decimal(store.load_book()["cash"]) == Decimal("100014.20")
    bad = runner.invoke(cli.app, ["cash-adjust", "--amount", "1", "--kind", "gift"])
    assert bad.exit_code == cli.EXIT_BAD_CONFIG
    _buy(store, 10, "100", date(2026, 9, 9))
    done = runner.invoke(cli.app, ["split", "-s", "AAA", "--ratio", "3", "--when", "2026-09-11"])
    assert done.exit_code == 0, done.output
    assert store.load_book()["positions"][0]["lots"][0]["quantity"] == 30
    missing = runner.invoke(cli.app, ["split", "-s", "ZZZ", "--ratio", "2", "--when", "2026-09-11"])
    assert missing.exit_code == cli.EXIT_BAD_CONFIG
