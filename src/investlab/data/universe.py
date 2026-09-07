"""A fixed, declared-up-front trading universe.

A survivorship-free universe is unobtainable at zero budget: building one
requires point-in-time index membership history, which is a paid data
product. Rather than pretend otherwise, this module fixes a single universe
today (2026-09-06) and labels it. `Universe.bias_warning()` states the
consequence in plain language; any writeup that uses this universe must
disclose it.

The default universe is S&P-100-style large caps (including `BRK.B`, which
exercises the `.`-to-`-` vendor symbol translation downstream in
`data/providers.py`), five bond ETFs, four mutual funds, and three
commodity/crypto trusts (IBIT, GLD, SLV) included specifically so the
competition modules can see them and reject them by rule, rather than the
universe silently omitting them.
"""

from __future__ import annotations

from dataclasses import dataclass

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
        """Instruments both competitions reject: commodity/crypto trusts."""
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
    ("IBIT", "iShares Bitcoin Trust"),
    ("GLD", "SPDR Gold Shares"),
    ("SLV", "iShares Silver Trust"),
)


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
                exchange="NYSE/NASDAQ",
            )
        )

    for symbol, name in _BOND_ETFS:
        instruments.append(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=AssetClass.ETF,
                exchange="NYSE Arca",
            )
        )

    for symbol, name in _MUTUAL_FUNDS:
        instruments.append(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=AssetClass.MUTUAL_FUND,
                exchange="MUTUAL FUND",
            )
        )

    for symbol, name in _TRUSTS:
        instruments.append(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=AssetClass.ETF,
                exchange="NYSE Arca",
                is_commodity_or_crypto_trust=True,
            )
        )

    return Universe(name="default-2026-09-06", instruments=tuple(instruments))
