import logging
import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, Optional
import httpx

logger = logging.getLogger("pulse.rossby")

_atmo_anomaly_history: dict[tuple, deque[float]] = defaultdict(lambda: deque(maxlen=12))


def _classify_stability(entropy: float) -> str:
    if entropy < 15:
        return "stable"
    if entropy < 40:
        return "transitional"
    return "unstable"


def _compute_entropy(values: list[float]) -> float:
    if len(values) < 4:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)

class RossbyConfig:
    RIDGE_THRESHOLD  =  60   # warm ridge (+60m anomaly)
    TROUGH_THRESHOLD = -60   # cold trough (-60m anomaly)
    FORECAST_URL     = "https://api.open-meteo.com/v1/forecast"

# ── Climatological baseline table ─────────────────────────────────────────────
# 500hPa geopotential height seasonal means (meters) by |latitude| band and month.
_CLIMO_TABLE = {
    ( 0, 15): [5876, 5876, 5878, 5880, 5882, 5878, 5874, 5872, 5872, 5874, 5876, 5876],
    (15, 30): [5810, 5816, 5824, 5832, 5840, 5840, 5838, 5836, 5830, 5822, 5814, 5808],
    (30, 45): [5640, 5660, 5692, 5726, 5764, 5796, 5812, 5806, 5780, 5742, 5690, 5652],
    (45, 60): [5440, 5472, 5532, 5596, 5660, 5706, 5726, 5718, 5672, 5600, 5504, 5452],
    (60, 90): [5200, 5240, 5340, 5450, 5560, 5640, 5668, 5656, 5580, 5450, 5300, 5220],
}


def _seasonal_baseline(lat: float, month: int) -> float:
    """Return the climatological 500hPa geopotential height (m)."""
    abs_lat = abs(lat)
    m = ((month - 1 + 6) % 12) if lat < 0 else (month - 1) 

    for (lo, hi), monthly in _CLIMO_TABLE.items():
        if lo <= abs_lat < hi:
            return float(monthly[m])
    return float(_CLIMO_TABLE[(60, 90)][m])


async def get_phase(lat: float, lon: float, city_code: str = "", tz_offset_hours: int = 0) -> Dict:
    """
    Return the atmospheric phase for a location.
    Returns: {"phase": "RIDGE"|"TROUGH"|"NEUTRAL", "anomaly_m": float, ...}
    """
    try:
        now = datetime.now(timezone.utc)
        baseline = _seasonal_baseline(lat, now.month)
        current = await _fetch_current_height(lat, lon, tz_offset_hours)

        if current is None:
            return _unknown("Forecast unavailable")

        anomaly = current - baseline
        phase, confidence = _classify(anomaly)

        key = (round(lat, 2), round(lon, 2))
        _atmo_anomaly_history[key].append(anomaly)
        atmo_entropy = _compute_entropy(list(_atmo_anomaly_history[key]))
        stability = _classify_stability(atmo_entropy)

        return {
            "phase": phase,
            "anomaly_m": round(anomaly, 1),
            "height_m": round(current, 1),
            "baseline_m": round(baseline, 1),
            "confidence": confidence,
            "atmo_entropy": round(atmo_entropy, 4),
            "stability": stability,
            "source": "live",
        }
    except Exception as e:
        logger.warning(f"Rossby probe failed [{city_code}]: {e}")
        return _unknown(str(e))


async def _fetch_current_height(lat: float, lon: float, tz_offset_hours: int = 0) -> Optional[float]:
    """
    Pull current 500hPa geopotential height from Open-Meteo.
    Selects the midday UTC index closest to the local peak window (14:00-16:00 local).
    """
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "geopotential_height_500hPa",
        "forecast_days": 1,
        "timezone": "UTC",
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(RossbyConfig.FORECAST_URL, params=params)
            r.raise_for_status()
            data = r.json()
            heights = data.get("hourly", {}).get("geopotential_height_500hPa", [])
            valid = [h for h in heights if h is not None]
            if not valid:
                return None

            # Midday local = 14:00 local → UTC = 14 - tz_offset
            local_peak = 14
            utc_target = (local_peak - tz_offset_hours) % 24
            idx = max(0, min(utc_target, len(valid) - 1))
            return float(valid[idx])
    except Exception as e:
        logger.warning(f"Rossby fetch failed ({lat:.2f},{lon:.2f}): {e}")
        return None


def _classify(anomaly_m: float):
    abs_a = abs(anomaly_m)
    if anomaly_m >= RossbyConfig.RIDGE_THRESHOLD:
        phase = "RIDGE"
        confidence = "strong" if abs_a > 150 else "moderate" if abs_a > 80 else "weak"
    elif anomaly_m <= RossbyConfig.TROUGH_THRESHOLD:
        phase = "TROUGH"
        confidence = "strong" if abs_a > 150 else "moderate" if abs_a > 80 else "weak"
    else:
        phase = "NEUTRAL"
        confidence = "weak"
    return phase, confidence


def _unknown(reason: str) -> Dict:
    return {
        "phase": "NEUTRAL", "anomaly_m": 0.0,
        "height_m": None, "baseline_m": None,
        "confidence": "weak",
        "atmo_entropy": 0.0, "stability": "unstable",
        "source": f"error: {reason}",
    }


def get_system_atmo_entropy() -> float:
    """Return average atmospheric entropy across all tracked locations. 0 if no history."""
    if not _atmo_anomaly_history:
        return 0.0
    total = 0.0
    count = 0
    for values in _atmo_anomaly_history.values():
        vals = list(values)
        if len(vals) >= 4:
            mean = sum(vals) / len(vals)
            variance = sum((x - mean) ** 2 for x in vals) / len(vals)
            total += math.sqrt(variance)
            count += 1
    return total / count if count > 0 else 0.0
