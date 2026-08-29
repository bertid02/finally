"""Tests for GBMSimulator."""

import math

import numpy as np
import pytest

from app.market.seed_prices import SEED_PRICES
from app.market.simulator import GBMSimulator


class TestGBMSimulator:
    """Unit tests for the GBM price simulator."""

    def test_step_returns_all_tickers(self):
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        assert set(sim.step().keys()) == {"AAPL", "GOOGL"}

    def test_prices_are_positive(self):
        """GBM prices can never go negative (exp() is always positive)."""
        sim = GBMSimulator(tickers=["AAPL"], seed=7)
        for _ in range(10_000):
            assert sim.step()["AAPL"] > 0

    def test_initial_prices_match_seeds(self):
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_add_ticker(self):
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("TSLA")
        assert "TSLA" in sim.step()

    def test_remove_ticker(self):
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("GOOGL")
        result = sim.step()
        assert "GOOGL" not in result
        assert "AAPL" in result

    def test_add_duplicate_is_noop(self):
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("AAPL")
        assert sim.get_tickers() == ["AAPL"]

    def test_constructor_deduplicates(self):
        """A watchlist with a repeated symbol must not double-track it."""
        sim = GBMSimulator(tickers=["AAPL", "aapl", "AAPL"])
        assert sim.get_tickers() == ["AAPL"]
        assert set(sim.step()) == {"AAPL"}

    def test_remove_nonexistent_is_noop(self):
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker("NOPE")  # Should not raise

    def test_unknown_ticker_gets_random_seed_price(self):
        sim = GBMSimulator(tickers=["ZZZZ"])
        price = sim.get_price("ZZZZ")
        assert price is not None
        assert 50.0 <= price <= 300.0

    def test_empty_step(self):
        assert GBMSimulator(tickers=[]).step() == {}

    def test_prices_change_over_time(self):
        sim = GBMSimulator(tickers=["AAPL"], seed=1)
        initial = sim.get_price("AAPL")
        for _ in range(1000):
            sim.step()
        assert sim.get_price("AAPL") != initial

    def test_get_price_returns_none_for_unknown(self):
        assert GBMSimulator(tickers=["AAPL"]).get_price("UNKNOWN") is None

    def test_get_tickers_returns_a_copy(self):
        sim = GBMSimulator(tickers=["AAPL"])
        sim.get_tickers().append("MSFT")
        assert sim.get_tickers() == ["AAPL"]

    def test_prices_rounded_to_two_decimals(self):
        sim = GBMSimulator(tickers=["AAPL"])
        price = sim.step()["AAPL"]
        assert price == round(price, 2)

    def test_internal_state_keeps_full_precision(self):
        """Rounding the state would let sub-cent moves get quantized away and, at
        low volatility, could freeze a price permanently."""
        sim = GBMSimulator(tickers=["AAPL"], seed=3)
        for _ in range(50):
            sim.step()
        assert sim.get_price("AAPL") != round(sim.get_price("AAPL"), 2)


class TestNormalization:
    """A symbol must mean the same thing on both sides of the interface."""

    def test_add_ticker_normalizes_case(self):
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("aapl")
        assert sim.get_tickers() == ["AAPL"]

    def test_constructor_normalizes(self):
        assert GBMSimulator(tickers=[" aapl ", "googl"]).get_tickers() == ["AAPL", "GOOGL"]

    def test_lowercase_resolves_to_the_seeded_price(self):
        """Without normalization 'aapl' became a second ticker at a random price
        while AAPL sat at its $190 seed."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("aapl") == SEED_PRICES["AAPL"]

    def test_remove_normalizes(self):
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker(" aapl ")
        assert sim.get_tickers() == []


class TestSeedPrice:
    def test_seed_price_is_the_session_anchor(self):
        sim = GBMSimulator(tickers=["AAPL"], seed=5)
        for _ in range(100):
            sim.step()
        assert sim.get_seed_price("AAPL") == SEED_PRICES["AAPL"]
        assert sim.get_price("AAPL") != sim.get_seed_price("AAPL")

    def test_seed_price_none_for_unknown(self):
        assert GBMSimulator(tickers=["AAPL"]).get_seed_price("NOPE") is None

    def test_readding_reseeds(self):
        sim = GBMSimulator(tickers=["AAPL"], seed=5)
        for _ in range(500):
            sim.step()
        sim.remove_ticker("AAPL")
        sim.add_ticker("AAPL")
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]


class TestDeterminism:
    """Both RNGs are injectable so behaviour is reproducible."""

    def test_same_seed_produces_same_path(self):
        a = GBMSimulator(tickers=["AAPL", "GOOGL"], seed=42)
        b = GBMSimulator(tickers=["AAPL", "GOOGL"], seed=42)
        for _ in range(20):
            assert a.step() == b.step()

    def test_different_seeds_diverge(self):
        a = GBMSimulator(tickers=["AAPL"], seed=1)
        b = GBMSimulator(tickers=["AAPL"], seed=2)
        for _ in range(20):
            a.step()
            b.step()
        assert a.get_price("AAPL") != b.get_price("AAPL")

    def test_synthetic_seed_price_is_deterministic(self):
        a = GBMSimulator(tickers=["ZZZZ"], seed=9)
        b = GBMSimulator(tickers=["ZZZZ"], seed=9)
        assert a.get_price("ZZZZ") == b.get_price("ZZZZ")


class TestCorrelation:
    def test_pairwise_correlation_tech_stocks(self):
        assert GBMSimulator._pairwise_correlation("AAPL", "GOOGL") == 0.6

    def test_pairwise_correlation_finance_stocks(self):
        assert GBMSimulator._pairwise_correlation("JPM", "V") == 0.5

    def test_pairwise_correlation_tsla(self):
        assert GBMSimulator._pairwise_correlation("TSLA", "AAPL") == 0.3
        assert GBMSimulator._pairwise_correlation("TSLA", "JPM") == 0.3

    def test_pairwise_correlation_cross_sector(self):
        assert GBMSimulator._pairwise_correlation("AAPL", "JPM") == 0.3

    def test_cholesky_rebuilds_on_add(self):
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None  # Only 1 ticker, no correlation matrix
        sim.add_ticker("GOOGL")
        assert sim._cholesky is not None

    def test_cholesky_none_with_one_ticker(self):
        assert GBMSimulator(tickers=["AAPL"])._cholesky is None

    def test_cholesky_reconstructs_the_correlation_matrix(self):
        """L @ L.T must recover the matrix that was decomposed."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL", "JPM", "V", "TSLA"])
        recovered = sim._cholesky @ sim._cholesky.T
        assert recovered[0, 1] == pytest.approx(0.6)  # AAPL/GOOGL
        assert recovered[2, 3] == pytest.approx(0.5)  # JPM/V
        assert recovered[0, 2] == pytest.approx(0.3)  # AAPL/JPM
        assert np.allclose(np.diag(recovered), 1.0)

    def test_full_default_watchlist_is_positive_definite(self):
        sim = GBMSimulator(tickers=list(SEED_PRICES))
        assert sim._cholesky is not None

    def test_large_mixed_watchlist_is_positive_definite(self):
        tickers = list(SEED_PRICES) + [f"Z{i:03d}"[:5] for i in range(20)]
        assert GBMSimulator(tickers=tickers)._cholesky is not None

    def test_non_positive_definite_matrix_degrades_to_uncorrelated(self):
        """A degenerate matrix must not take down the whole feed."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                GBMSimulator,
                "_pairwise_correlation",
                staticmethod(lambda t1, t2: 1.5),  # impossible correlation
            )
            sim._rebuild_cholesky()
        assert sim._cholesky is None
        assert set(sim.step()) == {"AAPL", "GOOGL"}  # still ticking


class TestGBMStatistics:
    """The properties that make the simulator worth having.

    Tolerances are loose enough to be stable across platforms but tight enough to
    catch a wrong dt, a missing drift correction, or a Cholesky applied the wrong
    way round.
    """

    N_STEPS = 60_000

    def _log_returns(self, tickers, seed=1234):
        sim = GBMSimulator(tickers=tickers, event_probability=0.0, seed=seed)
        prev = {t: sim.get_price(t) for t in sim.get_tickers()}
        returns = {t: [] for t in prev}
        for _ in range(self.N_STEPS):
            sim.step()
            for ticker in prev:
                current = sim.get_price(ticker)
                returns[ticker].append(math.log(current / prev[ticker]))
                prev[ticker] = current
        return {t: np.array(v) for t, v in returns.items()}, sim

    def test_realized_volatility_matches_target_sigma(self):
        returns, sim = self._log_returns(["AAPL", "TSLA", "JPM"])
        for ticker in ("AAPL", "TSLA", "JPM"):
            target = sim._params[ticker]["sigma"]
            realized = returns[ticker].std() / math.sqrt(GBMSimulator.DEFAULT_DT)
            assert realized == pytest.approx(target, rel=0.05)

    def test_realized_correlation_matches_the_matrix(self):
        returns, _ = self._log_returns(["AAPL", "GOOGL", "JPM", "TSLA"])
        pairs = {("AAPL", "GOOGL"): 0.6, ("AAPL", "JPM"): 0.3, ("AAPL", "TSLA"): 0.3}
        for (a, b), target in pairs.items():
            realized = float(np.corrcoef(returns[a], returns[b])[0, 1])
            assert realized == pytest.approx(target, abs=0.05)

    def test_drift_includes_the_ito_correction(self):
        """The mean log-return is (mu - sigma^2/2)*dt, not mu*dt. Dropping the
        correction biases every price upward over a long session."""
        returns, sim = self._log_returns(["JPM"])  # lowest sigma = tightest test
        mu = sim._params["JPM"]["mu"]
        sigma = sim._params["JPM"]["sigma"]
        expected = (mu - 0.5 * sigma**2) * GBMSimulator.DEFAULT_DT
        # The mean is swamped by diffusion noise, so compare against the standard
        # error of the mean rather than a relative tolerance.
        stderr = returns["JPM"].std() / math.sqrt(self.N_STEPS)
        assert abs(returns["JPM"].mean() - expected) < 4 * stderr

    def test_dt_matches_a_500ms_tick_of_a_trading_year(self):
        assert GBMSimulator.TRADING_SECONDS_PER_YEAR == 252 * 6.5 * 3600
        assert GBMSimulator.DEFAULT_DT == pytest.approx(8.4791e-8, rel=1e-3)
        assert 0 < GBMSimulator.DEFAULT_DT < 0.0001


class TestShockEvents:
    def test_zero_probability_means_no_shocks(self):
        sim = GBMSimulator(tickers=["AAPL"], event_probability=0.0, seed=1)
        for _ in range(2000):
            sim.step()
        # Pure GBM over 2000 ticks moves well under 1%; a 2-5% shock would show.
        assert abs(sim.get_price("AAPL") / SEED_PRICES["AAPL"] - 1) < 0.01

    def test_certain_probability_shocks_every_tick(self):
        sim = GBMSimulator(tickers=["AAPL"], event_probability=1.0, seed=1)
        before = sim.get_price("AAPL")
        sim.step()
        assert abs(sim.get_price("AAPL") / before - 1) >= 0.02
