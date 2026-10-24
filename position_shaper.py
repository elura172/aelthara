"""Position Shaper - Structured position sizing for weather markets.

This module manages position building around a forecasted core band,
with systematic tail sizing and optional "short UI pin" patterns.
"""

import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class PositionBand:
    """A single position in a market."""
    threshold_celsius: float
    yes_price: float
    action: str  # "BUY_YES" or "SELL_NO"
    size_dollars: float
    is_core: bool = False
    is_tail: bool = False


@dataclass
class MarketTemplate:
    """Template for how to size positions in a market."""
    city: str
    icao: str
    date: str  # "YYYY-MM-DD"
    forecast_temp: float  # Celsius (TX or TN depending on type)
    temp_type: str  # "TX" or "TN"
    
    core_band_celsius: tuple[float, float] = None  # e.g., (24, 26)
    core_size_dollars: float = 5.0
    
    adjacent_fraction: float = 0.5  # adjacent bins get this fraction of core
    tail_fraction: float = 0.1  # tail bins get this fraction of core
    tail_budget_per_bin: float = 1.0  # fixed lottery size per tail bin
    
    skip_expired_bins: bool = True


# Top 30 cities by Polymarket weather market liquidity volume.
# US majors + highest-volume international hubs.
FOCUSED_CITIES: set[str] = {
    # US (15)
    "NEW YORK", "LOS ANGELES", "CHICAGO", "MIAMI", "ATLANTA",
    "SEATTLE", "DENVER", "DALLAS", "HOUSTON", "PHOENIX",
    "LAS VEGAS", "BOSTON", "SAN DIEGO", "AUSTIN", "PHILADELPHIA",
    # International (15)
    "LONDON", "PARIS", "FRANKFURT", "AMSTERDAM", "TOKYO",
    "SEOUL", "SINGAPORE", "HONG KONG", "SYDNEY", "TORONTO",
    "DUBAI", "MUMBAI", "BANGKOK", "BEIJING", "SHANGHAI",
}

# Short UI pin configs
UI_PIN_CITIES = ["LOS ANGELES", "NEW YORK", "CHICAGO"]


def get_market_template(city: str, date: str) -> Optional[MarketTemplate]:
    """Return None — templates are city-focused, not date-specific."""
    return None


def should_trade_market(city: str, date: str = "") -> bool:
    """Check if this city is in the focused liquidity set."""
    return city.upper() in FOCUSED_CITIES


def calculate_position_sizing(
    forecast_temp: float,
    market_thresholds: list[float],
    market_prices: list[float],
    core_band: tuple[float, float],
    core_size: float,
    adjacent_frac: float = 0.5,
    tail_fraction: float = 0.1,
    tail_budget: float = 1.0,
) -> list[PositionBand]:
    """
    Calculate position sizes for a market based on template.
    
    Args:
        forecast_temp: Model forecast (°C)
        market_thresholds: List of threshold temps from markets
        market_prices: Corresponding YES prices
        core_band: (min, max) °C for core band
        core_size: Dollars to allocate to core band
        adjacent_frac: Fraction of core for adjacent bins
        tail_fraction: Fraction of core for near tails
        tail_budget: Fixed $ per far tail bin
        
    Returns:
        List of PositionBand objects
    """
    positions = []
    
    for thresh, price in zip(market_thresholds, market_prices):
        # Determine which band this threshold falls into
        in_core = core_band[0] <= thresh <= core_band[1]
        
        # Calculate distance from forecast
        dist = abs(thresh - forecast_temp)
        
        # Determine sizing
        if in_core:
            # Core band - full size
            action = "BUY_YES" if price < 0.5 else "SELL_NO"
            positions.append(PositionBand(
                threshold_celsius=thresh,
                yes_price=price,
                action=action,
                size_dollars=core_size,
                is_core=True,
            ))
        elif dist <= 2.0:
            # Adjacent bin (within 2°C of core)
            action = "BUY_YES" if price < 0.5 else "SELL_NO"
            positions.append(PositionBand(
                threshold_celsius=thresh,
                yes_price=price,
                action=action,
                size_dollars=core_size * adjacent_frac,
                is_core=False,
            ))
        else:
            # Tail bin - fixed lottery size if price is low
            if price < 0.05:  # Only buy cheap tails
                positions.append(PositionBand(
                    threshold_celsius=thresh,
                    yes_price=price,
                    action="BUY_YES",
                    size_dollars=tail_budget,
                    is_core=False,
                    is_tail=True,
                ))
    
    return positions


def is_ui_pin_opportunity(city: str, market_threshold: float, market_price: float, 
                          model_prob: float) -> bool:
    """
    Detect if this is a "short the UI pin" opportunity.
    
    Returns True if:
    - Market price is very high (>95%) but model says much lower
    - This is a configured UI pin city
    - The threshold matches a common visual pin level
    """
    if city.upper() not in UI_PIN_CITIES:
        return False
    
    # Pin detection: market overpriced, model disagrees
    if market_price < 0.95:
        return False
    
    if model_prob < 0.30:  # Model says event is unlikely despite 95%+ market price
        return True
    
    return False


def calculate_pin_short_size(model_prob: float, market_price: float, 
                              max_size: float = 2.0) -> float:
    """
    Calculate position size for shorting a UI pin.
    
    Higher edge = larger position, capped at max_size.
    """
    edge = model_prob - market_price
    if edge < -0.20:  # Only if market is >20% overpriced
        size = min(abs(edge) * max_size, max_size)
        return round(size, 2)
    return 0.0