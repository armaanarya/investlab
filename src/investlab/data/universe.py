"""A fixed, declared-up-front trading universe.

A survivorship-free universe is unobtainable at zero budget: building one
requires point-in-time index membership history, which is a paid data
product. Rather than pretend otherwise, this module fixes a single universe
today (2026-09-06) and labels it. `Universe.bias_warning()` states the
consequence in plain language; any writeup that uses this universe must
disclose it.

The default universe is S&P-100-style large caps (including `BRK.B`, which
exercises the `.`-to-`-` vendor symbol translation downstream in
`data/providers.py`), five bond ETFs, four mutual funds, two commodity
trusts (GLD, SLV) kept so the rules engines can reject them by rule, and the
spot bitcoin ETF IBIT, whose DECA eligibility rests on a recorded team
ruling (see `configs/deca_rulings.json`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from investlab.contracts import AssetClass, Instrument

BIAS_WARNING = (
    "This universe is fixed and selection-biased: it was declared on "
    "2026-09-06 from today's index membership and today's list of surviving "
    "large caps, bond ETFs, and mutual funds. It is not survivorship-free — "
    "companies that were removed from the S&P 100 before this date, or that "
    "delisted, are absent by construction, which inflates any backtested "
    "performance measured against it. This selection bias must be disclosed "
    "in any writeup, report, or presentation that uses results derived from "
    "this universe."
)


class UnknownSymbolError(KeyError):
    """Raised by `Universe.get` for a symbol not in the universe."""


@dataclass(frozen=True, slots=True)
class Universe:
    """An immutable, named collection of `Instrument`."""

    name: str
    instruments: tuple[Instrument, ...]

    def __post_init__(self) -> None:
        symbols = [i.symbol for i in self.instruments]
        if len(symbols) != len(set(symbols)):
            dupes = sorted({s for s in symbols if symbols.count(s) > 1})
            raise ValueError(f"duplicate symbols in universe {self.name!r}: {dupes}")

    def symbols(self) -> list[str]:
        return sorted(i.symbol for i in self.instruments)

    def get(self, symbol: str) -> Instrument:
        for inst in self.instruments:
            if inst.symbol == symbol:
                return inst
        raise UnknownSymbolError(f"{symbol!r} is not in universe {self.name!r}")

    def by_asset_class(self, asset_class: AssetClass) -> list[Instrument]:
        return [i for i in self.instruments if i.asset_class is asset_class]

    def prohibited(self) -> list[Instrument]:
        """Commodity and crypto trusts: prohibited unless a profile's ruling
        lifts the ban for a narrower class (DECA, spot bitcoin ETFs)."""
        return [i for i in self.instruments if i.is_commodity_or_crypto_trust]

    def bias_warning(self) -> str:
        return BIAS_WARNING

    def __len__(self) -> int:
        return len(self.instruments)

    def __iter__(self):
        return iter(self.instruments)

    def __contains__(self, symbol: str) -> bool:
        return any(i.symbol == symbol for i in self.instruments)


# ---------------------------------------------------------------------------
# Default universe
# ---------------------------------------------------------------------------

# S&P-100-style large caps, roughly one per major sector, declared 2026-09-06.
# BRK.B is included deliberately: it exercises the `.` -> `-` yfinance symbol
# translation exercised in data/providers.py.
_LARGE_CAPS: tuple[tuple[str, str], ...] = (
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corp."),
    ("GOOGL", "Alphabet Inc. Class A"),
    ("AMZN", "Amazon.com Inc."),
    ("NVDA", "NVIDIA Corp."),
    ("META", "Meta Platforms Inc."),
    ("BRK.B", "Berkshire Hathaway Inc. Class B"),
    ("TSLA", "Tesla Inc."),
    ("AVGO", "Broadcom Inc."),
    ("JPM", "JPMorgan Chase & Co."),
    ("V", "Visa Inc."),
    ("MA", "Mastercard Inc."),
    ("UNH", "UnitedHealth Group Inc."),
    ("HD", "Home Depot Inc."),
    ("PG", "Procter & Gamble Co."),
    ("JNJ", "Johnson & Johnson"),
    ("XOM", "Exxon Mobil Corp."),
    ("CVX", "Chevron Corp."),
    ("MRK", "Merck & Co. Inc."),
    ("ABBV", "AbbVie Inc."),
    ("COST", "Costco Wholesale Corp."),
    ("PEP", "PepsiCo Inc."),
    ("KO", "Coca-Cola Co."),
    ("WMT", "Walmart Inc."),
    ("BAC", "Bank of America Corp."),
    ("ADBE", "Adobe Inc."),
    ("CRM", "Salesforce Inc."),
    ("NFLX", "Netflix Inc."),
    ("AMD", "Advanced Micro Devices Inc."),
    ("INTC", "Intel Corp."),
    ("CSCO", "Cisco Systems Inc."),
    ("ORCL", "Oracle Corp."),
    ("ACN", "Accenture plc"),
    ("TMO", "Thermo Fisher Scientific Inc."),
    ("ABT", "Abbott Laboratories"),
    ("LIN", "Linde plc"),
    ("MCD", "McDonald's Corp."),
    ("DIS", "Walt Disney Co."),
    ("WFC", "Wells Fargo & Co."),
    ("GS", "Goldman Sachs Group Inc."),
    ("MS", "Morgan Stanley"),
    ("IBM", "International Business Machines Corp."),
    ("TXN", "Texas Instruments Inc."),
    ("QCOM", "Qualcomm Inc."),
    ("HON", "Honeywell International Inc."),
    ("UPS", "United Parcel Service Inc."),
    ("CAT", "Caterpillar Inc."),
    ("BA", "Boeing Co."),
    ("GE", "General Electric Co."),
    ("NKE", "Nike Inc."),
    ("LOW", "Lowe's Companies Inc."),
    ("SBUX", "Starbucks Corp."),
    ("PM", "Philip Morris International Inc."),
    ("T", "AT&T Inc."),
    ("VZ", "Verizon Communications Inc."),
    ("PFE", "Pfizer Inc."),
    ("SPGI", "S&P Global Inc."),
    ("BLK", "BlackRock Inc."),
    ("AXP", "American Express Co."),
)

_BOND_ETFS: tuple[tuple[str, str], ...] = (
    ("AGG", "iShares Core U.S. Aggregate Bond ETF"),
    ("BND", "Vanguard Total Bond Market ETF"),
    ("TLT", "iShares 20+ Year Treasury Bond ETF"),
    ("LQD", "iShares iBoxx $ Investment Grade Corporate Bond ETF"),
    ("SHY", "iShares 1-3 Year Treasury Bond ETF"),
)

_MUTUAL_FUNDS: tuple[tuple[str, str], ...] = (
    ("VFIAX", "Vanguard 500 Index Fund Admiral Shares"),
    ("FXAIX", "Fidelity 500 Index Fund"),
    ("VTSAX", "Vanguard Total Stock Market Index Fund Admiral Shares"),
    ("VBTLX", "Vanguard Total Bond Market Index Fund Admiral Shares"),
)

_TRUSTS: tuple[tuple[str, str], ...] = (
    ("GLD", "SPDR Gold Shares"),
    ("SLV", "iShares Silver Trust"),
)

# Spot bitcoin ETFs. Flagged as crypto trusts (Wharton bans them outright), and
# additionally as spot bitcoin ETFs so the DECA profile can apply its team
# ruling to this class alone. Listing venues verified against vendor metadata
# on 2026-09-12: IBIT is Nasdaq-listed. FBTC and ARKB list on Cboe BZX, which
# DECA's NYSE/NASDAQ rule excludes, so they are deliberately absent.
_BITCOIN_ETFS: tuple[tuple[str, str, str], ...] = (("IBIT", "iShares Bitcoin Trust ETF", "NASDAQ"),)

BITCOIN_GROUP = "BTC"

# GICS sector per equity, used for the sector concentration cap. Visa and
# Mastercard sit in Financials since the 2023 GICS reclassification.
_SECTORS: dict[str, str] = {
    "AAPL": "Information Technology",
    "MSFT": "Information Technology",
    "NVDA": "Information Technology",
    "AVGO": "Information Technology",
    "ADBE": "Information Technology",
    "CRM": "Information Technology",
    "AMD": "Information Technology",
    "INTC": "Information Technology",
    "CSCO": "Information Technology",
    "ORCL": "Information Technology",
    "ACN": "Information Technology",
    "IBM": "Information Technology",
    "TXN": "Information Technology",
    "QCOM": "Information Technology",
    "GOOGL": "Communication Services",
    "META": "Communication Services",
    "NFLX": "Communication Services",
    "DIS": "Communication Services",
    "T": "Communication Services",
    "VZ": "Communication Services",
    "AMZN": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary",
    "MCD": "Consumer Discretionary",
    "NKE": "Consumer Discretionary",
    "LOW": "Consumer Discretionary",
    "SBUX": "Consumer Discretionary",
    "PG": "Consumer Staples",
    "COST": "Consumer Staples",
    "PEP": "Consumer Staples",
    "KO": "Consumer Staples",
    "WMT": "Consumer Staples",
    "PM": "Consumer Staples",
    "BRK.B": "Financials",
    "JPM": "Financials",
    "V": "Financials",
    "MA": "Financials",
    "BAC": "Financials",
    "WFC": "Financials",
    "GS": "Financials",
    "MS": "Financials",
    "SPGI": "Financials",
    "BLK": "Financials",
    "AXP": "Financials",
    "UNH": "Health Care",
    "JNJ": "Health Care",
    "MRK": "Health Care",
    "ABBV": "Health Care",
    "TMO": "Health Care",
    "ABT": "Health Care",
    "PFE": "Health Care",
    "XOM": "Energy",
    "CVX": "Energy",
    "LIN": "Materials",
    "HON": "Industrials",
    "UPS": "Industrials",
    "CAT": "Industrials",
    "BA": "Industrials",
    "GE": "Industrials",
}

FIXED_INCOME = "Fixed Income"


# Real listing venue per symbol, resolved from vendor metadata on 2026-09-07.
# This matters because DECA SMG restricts its universe to NYSE and NASDAQ, so a
# placeholder value here silently blocks every legitimate stock at the
# eligibility gate.
_EXCHANGES: dict[str, str] = {
    "AAPL": "NASDAQ",
    "ABBV": "NYSE",
    "ABT": "NYSE",
    "ACN": "NYSE",
    "ADBE": "NASDAQ",
    "AMD": "NASDAQ",
    "AMZN": "NASDAQ",
    "AVGO": "NASDAQ",
    "AXP": "NYSE",
    "BA": "NYSE",
    "BAC": "NYSE",
    "BLK": "NYSE",
    "BRK.B": "NYSE",
    "CAT": "NYSE",
    "COST": "NASDAQ",
    "CRM": "NYSE",
    "CSCO": "NASDAQ",
    "CVX": "NYSE",
    "DIS": "NYSE",
    "GE": "NYSE",
    "GOOGL": "NASDAQ",
    "GS": "NYSE",
    "HD": "NYSE",
    "HON": "NASDAQ",
    "IBM": "NYSE",
    "INTC": "NASDAQ",
    "JNJ": "NYSE",
    "JPM": "NYSE",
    "KO": "NYSE",
    "LIN": "NASDAQ",
    "LOW": "NYSE",
    "MA": "NYSE",
    "MCD": "NYSE",
    "META": "NASDAQ",
    "MRK": "NYSE",
    "MS": "NYSE",
    "MSFT": "NASDAQ",
    "NFLX": "NASDAQ",
    "NKE": "NYSE",
    "NVDA": "NASDAQ",
    "ORCL": "NYSE",
    "PEP": "NASDAQ",
    "PFE": "NYSE",
    "PG": "NYSE",
    "PM": "NYSE",
    "QCOM": "NASDAQ",
    "SBUX": "NASDAQ",
    "SPGI": "NYSE",
    "T": "NYSE",
    "TMO": "NYSE",
    "TSLA": "NASDAQ",
    "TXN": "NASDAQ",
    "UNH": "NYSE",
    "UPS": "NYSE",
    "V": "NYSE",
    "VZ": "NYSE",
    "WFC": "NYSE",
    "WMT": "NASDAQ",
    "XOM": "NYSE",
}

# Market capitalisation (or total net assets, for ETFs), resolved from vendor
# metadata on 2026-09-07. DECA SMG requires >= $25,000,000 and its rules
# engine rejects an unknown value rather than assuming eligibility, so leaving
# these unset silently blocks the entire universe. Refresh with
# `investlab data pull --refresh-metadata` when the universe changes.
_MARKET_CAPS: dict[str, Decimal] = {
    "AAPL": Decimal("4669700046848"),
    "ABBV": Decimal("453194874880"),
    "ABT": Decimal("188690612224"),
    "ACN": Decimal("114261827584"),
    "ADBE": Decimal("105937731584"),
    "AGG": Decimal("138317873152"),
    "AMD": Decimal("779621105664"),
    "AMZN": Decimal("2788369891328"),
    "AVGO": Decimal("1702714146816"),
    "AXP": Decimal("220259057664"),
    "BA": Decimal("167633453056"),
    "BAC": Decimal("438305488896"),
    "BLK": Decimal("182345416704"),
    "BND": Decimal("399067512832"),
    "BRK.B": Decimal("1100000000000"),
    "CAT": Decimal("374147776512"),
    "COST": Decimal("406111289344"),
    "CRM": Decimal("213346304000"),
    "CSCO": Decimal("430530461696"),
    "CVX": Decimal("409190465536"),
    "DIS": Decimal("181837381632"),
    "GE": Decimal("349783064576"),
    "GLD": Decimal("152861196288"),
    "GOOGL": Decimal("4139343675392"),
    "GS": Decimal("302413512704"),
    "HD": Decimal("320308248576"),
    "HON": Decimal("66433794048"),
    "IBIT": Decimal("61435101184"),
    "IBM": Decimal("221297950720"),
    "INTC": Decimal("506408894464"),
    "JNJ": Decimal("663276421120"),
    "JPM": Decimal("953331941376"),
    "KO": Decimal("378925481984"),
    "LIN": Decimal("220150300672"),
    "LOW": Decimal("114707488768"),
    "LQD": Decimal("32042811392"),
    "MA": Decimal("507393736704"),
    "MCD": Decimal("180936867840"),
    "META": Decimal("1571225468928"),
    "MRK": Decimal("370889916416"),
    "MS": Decimal("341943681024"),
    "MSFT": Decimal("3710545297408"),
    "NFLX": Decimal("325828280320"),
    "NKE": Decimal("56966352896"),
    "NVDA": Decimal("5562502742016"),
    "ORCL": Decimal("457361195008"),
    "PEP": Decimal("188002582528"),
    "PFE": Decimal("162155724800"),
    "PG": Decimal("340389986304"),
    "PM": Decimal("284493709312"),
    "QCOM": Decimal("180224524288"),
    "SBUX": Decimal("119095803904"),
    "SHY": Decimal("25913065472"),
    "SLV": Decimal("34683150336"),
    "SPGI": Decimal("130746753024"),
    "T": Decimal("175969271808"),
    "TLT": Decimal("47046328320"),
    "TMO": Decimal("226943500288"),
    "TSLA": Decimal("1398455795712"),
    "TXN": Decimal("236019728384"),
    "UNH": Decimal("356470816768"),
    "UPS": Decimal("87026450432"),
    "V": Decimal("700273459200"),
    "VZ": Decimal("208320430080"),
    "WFC": Decimal("272069214208"),
    "WMT": Decimal("850021580800"),
    "XOM": Decimal("655726608384"),
}


def default_universe() -> Universe:
    """The v1 fixed universe: large caps, bond ETFs, mutual funds, and the
    prohibited commodity/crypto trusts, all as of 2026-09-06."""
    instruments: list[Instrument] = []

    for symbol, name in _LARGE_CAPS:
        instruments.append(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=AssetClass.STOCK,
                exchange=_EXCHANGES[symbol],
                market_cap=_MARKET_CAPS.get(symbol),
                sector=_SECTORS[symbol],
            )
        )

    for symbol, name in _BOND_ETFS:
        instruments.append(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=AssetClass.ETF,
                exchange="NYSE Arca",
                market_cap=_MARKET_CAPS.get(symbol),
                sector=FIXED_INCOME,
            )
        )

    for symbol, name in _MUTUAL_FUNDS:
        instruments.append(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=AssetClass.MUTUAL_FUND,
                exchange="MUTUAL FUND",
                sector=FIXED_INCOME if symbol == "VBTLX" else "US Equity Fund",
            )
        )

    for symbol, name in _TRUSTS:
        instruments.append(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=AssetClass.ETF,
                exchange="NYSE Arca",
                market_cap=_MARKET_CAPS.get(symbol),
                is_commodity_or_crypto_trust=True,
                sector="Commodities",
            )
        )

    for symbol, name, exchange in _BITCOIN_ETFS:
        instruments.append(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=AssetClass.ETF,
                exchange=exchange,
                market_cap=_MARKET_CAPS.get(symbol),
                is_commodity_or_crypto_trust=True,
                is_spot_bitcoin_etf=True,
                sector="Digital Assets",
                exposure_group=BITCOIN_GROUP,
            )
        )

    return Universe(name="default-2026-09-06", instruments=tuple(instruments))
