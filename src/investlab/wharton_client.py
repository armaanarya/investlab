"""Laura Gao's goals as numbers, and a projection engine for the team's strategy.

The 2026-27 case study (published 2026-09-15) fixes the cash flows:

- Start of 2027: invest $300,000. Start of 2028: add $150,000.
- No other flows before 2033.
- Start of each year 2033-2042: pay $50,000 (fixed, not inflation-adjusted),
  funded by the portfolio "with a high degree of certainty". At the start of
  2033, before the first payment, an operating reserve is set aside for all ten.
- What remains after the reserve may go partly to the residency facility; the
  rest is kept for flexibility.
- In 2031 Laura quotes co-sponsors a dollar range for the facility contribution,
  with a stated confidence.

Everything that is a judgment (return and volatility assumptions, the glide
path, reserve sizing, the flexibility buffer, the confidence level) lives in
`configs/wharton_strategy.json` and belongs to the team. This module computes;
it does not choose, and it writes no prose. Its numbers are evidence the team
evaluates and explains in its own words.

Floats are used on purpose: this is a statistical projection, not a ledger.
Nothing here touches money that is recorded anywhere.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from investlab.config import REPO_ROOT

YEAR_ZERO = 2026
CONTRIBUTIONS: tuple[tuple[int, float], ...] = ((2027, 300_000.0), (2028, 150_000.0))
PAYMENT = 50_000.0
FIRST_PAYMENT_YEAR = 2033
LAST_PAYMENT_YEAR = 2042
N_PAYMENTS = LAST_PAYMENT_YEAR - FIRST_PAYMENT_YEAR + 1
RESERVE_YEAR = 2033
COSPONSOR_YEAR = 2031

DEFAULT_STRATEGY_PATH = REPO_ROOT / "configs" / "wharton_strategy.json"


def year_number(calendar_year: int) -> int:
    """The case study's numbering: 2026 is Year 0."""
    return calendar_year - YEAR_ZERO


def reserve_pv(rate: float, payment: float = PAYMENT, n: int = N_PAYMENTS) -> float:
    """Value at the start of 2033 of ten beginning-of-year payments, discounted
    at `rate`. The first payment is due immediately, so it is not discounted."""
    if rate == 0:
        return payment * n
    return payment * (1 - (1 + rate) ** -n) / rate * (1 + rate)


class StrategyConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Sleeve:
    name: str
    expected_return: float
    volatility: float
    source: str


@dataclass(frozen=True)
class Phase:
    first_year: int
    last_year: int
    weights: dict[str, float]


@dataclass(frozen=True)
class ReservePolicy:
    """How the reserve is sized in 2033 and how it is invested after.

    method "pv": size = present value of the payments at `discount_rate`.
    method "confidence": the smallest reserve that funds all ten payments in
    at least `confidence` of simulated paths, given its return assumptions.
    """

    method: str
    expected_return: float
    volatility: float
    discount_rate: float = 0.0
    confidence: float = 0.95


@dataclass(frozen=True)
class Strategy:
    name: str
    phases: tuple[Phase, ...]
    reserve: ReservePolicy
    buffer_fraction: float
    buffer_dollars: float

    def weights_for(self, year: int) -> dict[str, float]:
        for p in self.phases:
            if p.first_year <= year <= p.last_year:
                return p.weights
        raise StrategyConfigError(f"{self.name}: no phase covers {year}")


@dataclass(frozen=True)
class StrategyBook:
    sleeves: dict[str, Sleeve]
    correlations: dict[tuple[str, str], float]
    strategies: dict[str, Strategy]
    active: str
    inflation: float
    status: str
    wins: dict = field(default_factory=dict)

    def correlation(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        return self.correlations.get((a, b), self.correlations.get((b, a), 0.0))

    def portfolio_moments(self, weights: dict[str, float]) -> tuple[float, float]:
        """Arithmetic annual mean and volatility of a sleeve mix."""
        names = list(weights)
        w = np.array([weights[n] for n in names])
        mu = np.array([self.sleeves[n].expected_return for n in names])
        vol = np.array([self.sleeves[n].volatility for n in names])
        corr = np.array([[self.correlation(a, b) for b in names] for a in names])
        cov = np.outer(vol, vol) * corr
        return float(w @ mu), float(math.sqrt(max(0.0, w @ cov @ w)))

    def placeholder_sleeves(self) -> list[str]:
        return [s.name for s in self.sleeves.values() if "placeholder" in s.source.lower()]


def load_strategy_book(path: Path = DEFAULT_STRATEGY_PATH) -> StrategyBook:
    if not path.exists():
        raise StrategyConfigError(f"No strategy file at {path}.")
    raw = json.loads(path.read_text())
    try:
        sleeves = {
            name: Sleeve(
                name, float(v["expected_return"]), float(v["volatility"]), v.get("source", "")
            )
            for name, v in raw["sleeves"].items()
        }
        correlations: dict[tuple[str, str], float] = {}
        for key, value in raw.get("correlations", {}).items():
            a, _, b = key.partition("|")
            for n in (a, b):
                if n not in sleeves:
                    raise StrategyConfigError(f"correlation {key!r} names unknown sleeve {n!r}")
            correlations[(a, b)] = float(value)
        strategies = {}
        for name, s in raw["strategies"].items():
            phases = []
            for p in s["phases"]:
                weights = {k: float(v) for k, v in p["weights"].items()}
                unknown = set(weights) - set(sleeves)
                if unknown:
                    raise StrategyConfigError(f"{name}: unknown sleeve(s) {sorted(unknown)}")
                total = sum(weights.values())
                if abs(total - 1.0) > 1e-6:
                    raise StrategyConfigError(
                        f"{name} {p['from']}-{p['to']}: weights sum to {total:.4f}, not 1"
                    )
                phases.append(Phase(int(p["from"]), int(p["to"]), weights))
            covered = {y for p in phases for y in range(p.first_year, p.last_year + 1)}
            needed = set(range(CONTRIBUTIONS[0][0], RESERVE_YEAR))
            if not needed <= covered:
                raise StrategyConfigError(
                    f"{name}: phases must cover {min(needed)}-{max(needed)}; "
                    f"missing {sorted(needed - covered)}"
                )
            r = s["reserve"]
            if r["method"] not in ("pv", "confidence"):
                raise StrategyConfigError(f"{name}: reserve method must be 'pv' or 'confidence'")
            reserve = ReservePolicy(
                method=r["method"],
                expected_return=float(r["expected_return"]),
                volatility=float(r["volatility"]),
                discount_rate=float(r.get("discount_rate", r["expected_return"])),
                confidence=float(r.get("confidence", 0.95)),
            )
            buf = s.get("flexibility_buffer", {})
            strategies[name] = Strategy(
                name,
                tuple(phases),
                reserve,
                float(buf.get("fraction_of_remainder", 0.0)),
                float(buf.get("dollars", 0.0)),
            )
        active = raw["active"]
        if active not in strategies:
            raise StrategyConfigError(f"active strategy {active!r} is not defined")
    except KeyError as exc:
        raise StrategyConfigError(f"strategy file is missing {exc}") from exc
    return StrategyBook(
        sleeves=sleeves,
        correlations=correlations,
        strategies=strategies,
        active=active,
        inflation=float(raw.get("inflation", 0.0)),
        status=str(raw.get("status", "")),
        wins=raw.get("wins", {}),
    )


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------


def _lognormal_params(mean: float, vol: float) -> tuple[float, float]:
    """Log-space mean and sd for a gross return with arithmetic `mean`, `vol`."""
    gross = 1.0 + mean
    s2 = math.log(1.0 + (vol / gross) ** 2)
    return math.log(gross) - s2 / 2, math.sqrt(s2)


def _gross_returns(rng: np.random.Generator, mean: float, vol: float, size) -> np.ndarray:
    m, s = _lognormal_params(mean, vol)
    return np.exp(rng.normal(m, s, size))


def _reserve_survives(start: np.ndarray, growth: np.ndarray) -> np.ndarray:
    """Pay at the start of each year, then grow. `growth` is (paths, N-1): no
    growth is needed after the last payment."""
    bal = start.copy()
    ok = np.ones_like(bal, dtype=bool)
    for k in range(N_PAYMENTS):
        ok &= bal >= PAYMENT - 1e-6
        bal = bal - PAYMENT
        if k < N_PAYMENTS - 1:
            bal = bal * growth[:, k]
    return ok


def size_reserve_for_confidence(
    policy: ReservePolicy, confidence: float, paths: int = 20_000, seed: int = 7
) -> float:
    """Smallest reserve that funds all payments in `confidence` of paths."""
    rng = np.random.default_rng(seed)
    growth = _gross_returns(rng, policy.expected_return, policy.volatility, (paths, N_PAYMENTS - 1))
    lo, hi = 0.0, PAYMENT * N_PAYMENTS * 2
    for _ in range(60):
        mid = (lo + hi) / 2
        rate = _reserve_survives(np.full(paths, mid), growth).mean()
        if rate >= confidence:
            hi = mid
        else:
            lo = mid
    return hi


@dataclass(frozen=True)
class Projection:
    strategy: str
    paths: int
    seed: int
    reserve: float
    value_2031: np.ndarray
    value_2033: np.ndarray
    contribution: np.ndarray
    payments_funded: np.ndarray
    two_year_multiplier: np.ndarray
    deterministic_2033: float
    phase_moments: list[tuple[int, int, float, float]]
    inflation: float

    def pct(self, arr: np.ndarray, q: float) -> float:
        return float(np.percentile(arr, q * 100))

    @property
    def funding_probability(self) -> float:
        return float(self.payments_funded.mean())

    def real(self, nominal: float, year: int = RESERVE_YEAR) -> float:
        """In start-of-2027 dollars, deflated at the team's inflation rate."""
        return nominal / (1 + self.inflation) ** (year - CONTRIBUTIONS[0][0])

    def range_mass(self, low_q: float, high_q: float) -> float:
        lo, hi = self.pct(self.contribution, low_q), self.pct(self.contribution, high_q)
        return float(((self.contribution >= lo) & (self.contribution <= hi)).mean())

    def contribution_from_2031(
        self, value_2031: float, buffer_fraction: float, buffer_dollars: float, q: float
    ) -> float:
        """The contribution implied if the portfolio is `value_2031` at the
        start of 2031 and the next two years land at quantile `q`."""
        v33 = value_2031 * float(np.percentile(self.two_year_multiplier, q * 100))
        remainder = max(0.0, v33 - self.reserve)
        return max(0.0, remainder - buffer_fraction * remainder - buffer_dollars)


def project(
    book: StrategyBook, strategy_name: str, paths: int = 20_000, seed: int = 7
) -> Projection:
    strat = book.strategies[strategy_name]
    rng = np.random.default_rng(seed)
    flows = dict(CONTRIBUTIONS)

    value = np.zeros(paths)
    det = 0.0
    value_2031 = None
    multiplier = np.ones(paths)
    moments: list[tuple[int, int, float, float]] = []
    for p in strat.phases:
        mu, vol = book.portfolio_moments(p.weights)
        moments.append((p.first_year, p.last_year, mu, vol))

    for year in range(CONTRIBUTIONS[0][0], RESERVE_YEAR):
        if year == COSPONSOR_YEAR:
            value_2031 = value.copy()
        value = value + flows.get(year, 0.0)
        det += flows.get(year, 0.0)
        mu, vol = book.portfolio_moments(strat.weights_for(year))
        g = _gross_returns(rng, mu, vol, paths)
        value = value * g
        det *= 1 + mu
        if year >= COSPONSOR_YEAR:
            multiplier = multiplier * g
    assert value_2031 is not None

    pol = strat.reserve
    if pol.method == "pv":
        reserve = reserve_pv(pol.discount_rate)
    else:
        reserve = size_reserve_for_confidence(pol, pol.confidence, seed=seed + 1)

    reserve_start = np.minimum(value, reserve)
    growth = _gross_returns(rng, pol.expected_return, pol.volatility, (paths, N_PAYMENTS - 1))
    funded = _reserve_survives(reserve_start, growth)

    remainder = np.maximum(0.0, value - reserve)
    contribution = np.maximum(0.0, remainder * (1 - strat.buffer_fraction) - strat.buffer_dollars)

    return Projection(
        strategy=strategy_name,
        paths=paths,
        seed=seed,
        reserve=reserve,
        value_2031=value_2031,
        value_2033=value,
        contribution=contribution,
        payments_funded=funded,
        two_year_multiplier=multiplier,
        deterministic_2033=det,
        phase_moments=moments,
        inflation=book.inflation,
    )
