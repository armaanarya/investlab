"""End-to-end guards on the daily loop.

These cover the three integration defects found while wiring the CLI, each of
which was invisible to the unit suites because each lived in the seam between
two modules that were built concurrently.
"""

from datetime import date
from decimal import Decimal

import pytest

from investlab.competitions.deca import DecaProfile
from investlab.contracts import AccountState, AssetClass
from investlab.data.universe import default_universe
from investlab.portfolio.sizing import Candidate, size_batch


@pytest.fixture
def universe():
    return default_universe()


def test_every_equity_has_a_real_exchange(universe):
    """A placeholder exchange silently blocked the entire universe.

    The universe stored the literal "NYSE/NASDAQ" for all 59 equities while
    the DECA gate matches "NYSE" or "NASDAQ" exactly, so every legitimate
    stock was rejected with a self-contradicting message.
    """
    placeholders = [
        s for s in universe.symbols() if universe.get(s).exchange in {"NYSE/NASDAQ", "", None}
    ]
    assert placeholders == [], f"placeholder exchange still set for: {placeholders}"


def test_every_tradeable_instrument_has_a_market_cap(universe):
    """DECA rejects an unknown market cap rather than assuming eligibility,
    which is correct, and which means an unpopulated field blocks everything."""
    missing = [
        s
        for s in universe.symbols()
        if universe.get(s).asset_class in (AssetClass.STOCK, AssetClass.ETF)
        and not universe.get(s).market_cap
    ]
    assert missing == [], f"no market cap for: {missing}"


def test_etfs_are_eligible_for_deca(universe):
    """DECA states 'all ETFs (including bond ETFs) are classified as stocks'.

    Nearly every US ETF lists on NYSE Arca. Reading rule 3's "NYSE" to exclude
    Arca would make that explicit permission a dead letter.
    """
    profile = DecaProfile()
    etfs = [
        s
        for s in universe.symbols()
        if universe.get(s).asset_class is AssetClass.ETF
        and not universe.get(s).is_commodity_or_crypto_trust
    ]
    assert etfs, "fixture universe has no ordinary ETFs to check"
    for symbol in etfs:
        instrument = universe.get(symbol)
        bar = _bar(symbol, Decimal("100"))
        ok, reason = profile.is_eligible(instrument, bar)
        assert ok, f"{symbol} on {instrument.exchange} was blocked: {reason}"


def test_prohibited_trusts_are_still_blocked(universe):
    """Widening the exchange set must not have widened the prohibition."""
    profile = DecaProfile()
    for symbol in ("IBIT", "GLD", "SLV"):
        if symbol not in set(universe.symbols()):
            continue
        ok, reason = profile.is_eligible(universe.get(symbol), _bar(symbol, Decimal("100")))
        assert not ok, f"{symbol} must be blocked"
        assert "prohibit" in reason.lower()


def test_batch_sizing_cannot_spend_the_same_dollar_twice():
    """Sizing each candidate independently produced eight orders totalling
    $216,000 against a $100,000 account. The batch reserves sequentially."""
    profile = DecaProfile()
    account = AccountState(date(2026, 9, 4), Decimal("100000"), ())
    constraints = profile.sizing_constraints(account)

    candidates = tuple(
        Candidate(
            symbol=f"S{i}",
            price_bound=Decimal("50"),
            protective_reference=Decimal("46"),
            rank=i,
        )
        for i in range(8)
    )
    results = size_batch(candidates, constraints)
    committed = sum(
        (r.estimated_notional + r.estimated_commission for r in results if hasattr(r, "quantity")),
        Decimal("0"),
    )
    assert committed <= constraints.spendable_cash, (
        f"batch committed ${committed} against ${constraints.spendable_cash} available"
    )


def _bar(symbol: str, close: Decimal):
    from investlab.contracts import Bar

    return Bar(
        symbol=symbol,
        session=date(2026, 9, 4),
        open=close,
        high=close,
        low=close,
        close=close,
        adj_close=close,
        volume=5_000_000,
        source="test",
    )
