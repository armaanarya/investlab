"""The team ruling on spot bitcoin ETFs.

The published DECA text bans "bitcoin". The team has ruled that spot bitcoin
ETFs are allowed. The tool follows the ruling, keeps every other prohibition,
and marks the ruling CONFLICTING on every sheet until a written source exists.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from investlab.competitions.deca import DecaProfile
from investlab.config import DEFAULT_DECA_RULINGS, load_deca_rulings
from investlab.contracts import AccountState, AssetClass, Bar, BlockReason, Instrument, RuleStatus
from investlab.data.universe import default_universe


def _bar(sym, close="60.00"):
    c = Decimal(close)
    return Bar(sym, date(2026, 9, 11), c, c, c, c, c, 1_000_000, "test")


IBIT = Instrument(
    "IBIT",
    "iShares Bitcoin Trust ETF",
    AssetClass.ETF,
    "NASDAQ",
    market_cap=Decimal("60000000000"),
    is_commodity_or_crypto_trust=True,
    is_spot_bitcoin_etf=True,
    exposure_group="BTC",
)
GLD = Instrument(
    "GLD",
    "SPDR Gold Shares",
    AssetClass.ETF,
    "NYSE Arca",
    market_cap=Decimal("150000000000"),
    is_commodity_or_crypto_trust=True,
)


def _check(profile):
    checks = profile.check_rules(
        AccountState(date(2026, 9, 11), Decimal("100000"), ()), date(2026, 9, 11)
    )
    return next(c for c in checks if c.name == "bitcoin_etf_ruling")


def test_missing_rulings_file_keeps_the_published_ban(tmp_path):
    rulings = load_deca_rulings(tmp_path / "none.json")
    assert rulings.bitcoin_etfs_allowed is False
    profile = DecaProfile.from_rulings(rulings)
    ok, reason = profile.is_eligible(IBIT, _bar("IBIT"))
    assert not ok and "prohibit" in reason.lower()
    assert _check(profile).status is RuleStatus.VERIFIED


def test_lifting_a_ban_requires_who_when_and_why(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"bitcoin_etfs_allowed": {"value": True}}))
    with pytest.raises(ValueError, match="decided_by"):
        load_deca_rulings(path)


def test_the_repository_ruling_allows_bitcoin_etfs_but_stays_conflicting():
    rulings = load_deca_rulings(DEFAULT_DECA_RULINGS)
    assert rulings.bitcoin_etfs_allowed is True
    profile = DecaProfile.from_rulings(rulings)
    assert profile.is_eligible(IBIT, _bar("IBIT")) == (True, "")
    check = _check(profile)
    assert check.satisfied
    if rulings.bitcoin_etf_written_source is None:
        assert check.status is RuleStatus.CONFLICTING
        assert "no written confirmation" in check.detail


def test_commodity_trusts_stay_prohibited_under_the_ruling():
    profile = DecaProfile(bitcoin_etfs_allowed=True, bitcoin_etf_decided_by="team")
    block = profile.eligibility_block(GLD, _bar("GLD", "200.00"))
    assert block is not None and block.reason is BlockReason.PROHIBITED_SECURITY


def test_a_lifted_bitcoin_etf_still_faces_every_other_check():
    profile = DecaProfile(bitcoin_etfs_allowed=True, bitcoin_etf_decided_by="team")
    ok, reason = profile.is_eligible(IBIT, _bar("IBIT", "2.00"))
    assert not ok and "minimum" in reason


def test_written_source_marks_the_ruling_verified():
    profile = DecaProfile(
        bitcoin_etfs_allowed=True,
        bitcoin_etf_decided_by="team",
        bitcoin_etf_written_source="SIFMA email to advisor, 2026-09-20",
    )
    assert _check(profile).status is RuleStatus.VERIFIED


def test_universe_ibit_is_a_nasdaq_spot_bitcoin_etf_in_one_group():
    ibit = default_universe().get("IBIT")
    assert ibit.exchange == "NASDAQ"
    assert ibit.is_spot_bitcoin_etf and ibit.is_commodity_or_crypto_trust
    assert ibit.exposure_group == "BTC"


def test_every_stock_has_a_sector():
    u = default_universe()
    assert all(i.sector for i in u.by_asset_class(AssetClass.STOCK))
