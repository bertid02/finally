"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of one ticker's price at a point in time.

    Two distinct notions of "change" live here and must not be confused:

      change / change_percent    - versus the PREVIOUS TICK (~500ms ago).
                                   Drives the flash animation. Meaningless as a
                                   displayed number (typically +/-0.02%).

      change_session /           - versus SESSION_OPEN. This is the "daily change %"
      change_percent_session       the watchlist and positions table display.
    """

    ticker: str
    price: float
    previous_price: float
    session_open: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds

    # Set when the provider computes the session change itself (Massive's
    # todaysChangePerc, which is split- and dividend-adjusted). None means
    # "derive it from session_open".
    provider_change_percent: float | None = None

    # --- tick-over-tick: flash animation only ---

    @property
    def change(self) -> float:
        """Absolute price change from the previous update."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from the previous update."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up' | 'down' | 'flat' — the CSS class the frontend applies."""
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    # --- versus session open: the displayed daily change ---

    @property
    def change_session(self) -> float:
        """Absolute price change since the session anchor."""
        return round(self.price - self.session_open, 4)

    @property
    def change_percent_session(self) -> float:
        """Prefer the provider's adjusted figure; derive only as a fallback."""
        if self.provider_change_percent is not None:
            return round(self.provider_change_percent, 4)
        if self.session_open == 0:
            return 0.0
        return round((self.price - self.session_open) / self.session_open * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE. This shape is the frontend contract."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "session_open": self.session_open,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "change_session": self.change_session,
            "change_percent_session": self.change_percent_session,
            "direction": self.direction,
        }
