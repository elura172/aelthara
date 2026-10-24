"""Oracle — Temperature oracle. TAF primary, NWS secondary, Open-Meteo fallback. METAR cross-check for same-day markets."""
from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

from config import ICAO_COORDS, ICAO_TIMEZONES, get_timezone, get_coords

def get_daily_stats_from_basic_1h(
    meteoblue_data: dict,
    market_resolution_date: str,
    airport_tz: str,
    hours_to_resolution: int,
    taf_max_remaining: float | None = None,
    taf_min_remaining: float | None = None,
) -> dict | None:
    """Extract max and min temp for a specific date from hourly data."""
    hourly = meteoblue_data.get("data_1h", {})
    hourly_times = hourly.get("time", [])
    hourly_temps = hourly.get("temperature", [])
    predictability = hourly.get("predictability", 80)

    if isinstance(predictability, list):
        numeric_vals = [v for v in predictability if isinstance(v, (int, float))]
        predictability = sum(numeric_vals) / len(numeric_vals) if numeric_vals else 80
    elif predictability is None or not isinstance(predictability, (int, float)):
        predictability = 80

    if not hourly_times or not hourly_temps:
        return None

    resolution_day = datetime.strptime(market_resolution_date, "%Y-%m-%d").date()
    tz = ZoneInfo(airport_tz)

    day_temps = []
    for t_str, temp in zip(hourly_times, hourly_temps):
        t_str_clean = t_str.replace("Z", "+00:00")
        if "+" not in t_str_clean and "-" not in t_str_clean[10:]:
            dt = datetime.fromisoformat(t_str_clean).replace(tzinfo=ZoneInfo("UTC"))
        else:
            dt = datetime.fromisoformat(t_str_clean)

        local_dt = dt.astimezone(tz)
        if local_dt.date() == resolution_day:
            day_temps.append(temp)

    if not day_temps:
        return None

    raw_max = max(day_temps)
    raw_min = min(day_temps)

    # Blending and sigma logic for TX
    tx_c = raw_max
    if hours_to_resolution <= 12 and taf_max_remaining is not None:
        tx_c = raw_max * 0.35 + taf_max_remaining * 0.65
        
    # Blending for TN
    tn_c = raw_min
    if hours_to_resolution <= 12 and taf_min_remaining is not None:
        tn_c = raw_min * 0.35 + taf_min_remaining * 0.65

    if predictability >= 70:
        sigma_adjust = 2.0
    elif predictability >= 50:
        sigma_adjust = 2.5
    else:
        sigma_adjust = 3.0

    return {
        "tx_c": round(tx_c, 1),
        "tn_c": round(tn_c, 1),
        "sigma_c": sigma_adjust,
        "predictability": predictability,
        "reason": f"hourly stats (predictability: {predictability}%)",
    }


def get_taf_max_remaining(taf_raw: str, current_local_hour: int) -> float | None:
    """Extract TAF max temp if still relevant for remaining day."""
    m = re.search(r"TX(M?)(\d+)/(\d{2})(\d{2})Z", taf_raw)
    if not m:
        return None
    temp = (-1.0 if m.group(1) == "M" else 1.0) * float(m.group(2))
    taf_hour = int(m.group(4))
    if taf_hour > current_local_hour:
        return temp
    return None


def get_taf_min_remaining(taf_raw: str, current_local_hour: int) -> float | None:
    """Extract TAF min temp if still relevant for remaining day."""
    m = re.search(r"TN(M?)(\d+)/(\d{2})(\d{2})Z", taf_raw)
    if not m:
        return None
    temp = (-1.0 if m.group(1) == "M" else 1.0) * float(m.group(2))
    taf_hour = int(m.group(4))
    if taf_hour > current_local_hour:
        return temp
    return None


def calculate_hours_to_resolution(resolution_time: datetime) -> int:
    """Calculate hours until market resolution."""
    delta = resolution_time - datetime.now(timezone.utc)
    return max(0, int(delta.total_seconds() / 3600))


def apply_late_day_cap(peak_c: float, current_local_hour: int, taf_max: float | None) -> float:
    """Cap upside after 4pm local when daily peak is likely set."""
    if current_local_hour >= 16 and taf_max is not None:
        return min(peak_c, taf_max + 1.5)
    return peak_c


def apply_rossby_bias(reading: TAFReading, phase_data: dict) -> tuple[TAFReading, str]:
    phase = phase_data.get("phase", "NEUTRAL")
    anomaly_m = phase_data.get("anomaly_m", 0.0)
    bias = round(anomaly_m / 60.0 * 0.8, 1)

    if phase == "RIDGE":
        rossby_tag = f" [RIDGE bias {bias:+.1f}°C]"
        new_dist = {"mean": reading.distribution["mean"] + bias, "std": reading.distribution["std"]}
    elif phase == "TROUGH":
        rossby_tag = f" [TROUGH bias {bias:+.1f}°C]"
        new_dist = {"mean": reading.distribution["mean"] + bias, "std": reading.distribution["std"]}
    else:
        rossby_tag = ""
        new_dist = reading.distribution

    reading.distribution = new_dist
    reading.tx_celsius = reading.distribution["mean"]
    return reading, rossby_tag


@dataclass
class TAFReading:
    icao: str
    tx_celsius: float
    issued_at: str
    raw: str
    tn_celsius: Optional[float] = None
    source: str = "meteoblue"  # "meteoblue", "taf", or "openmeteo"
    distribution: Dict[str, float] = field(default_factory=lambda: {"mean": 0.0, "std": 2.0})
    predictability: Optional[float] = None

    @property
    def tx_fahrenheit(self) -> float:
        return self.tx_celsius * 9 / 5 + 32

    def __str__(self) -> str:
        return (
            f"TAF {self.icao}  TX={self.tx_celsius:.0f}°C "
            f"({self.tx_fahrenheit:.0f}°F)  issued={self.issued_at} [{self.source}]"
        )


class Oracle:
    """Pulls temperature — TAF primary, NWS secondary, Open-Meteo fallback. METAR cross-check for same-day markets."""

    AWC = "https://aviationweather.gov/api/data/taf"
    METAR_URL = "https://aviationweather.gov/api/data/metar"
    OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
    STD_DEGREE_C: float = 2.0

    def __init__(self, icao: str):
        self.icao = icao.upper()
        self._last_issued: str = ""
        self._cache = {}  # "YYYY-MM-DD_HH" -> {"time": float, "reading": TAFReading}

    async def pull(self, resolution_time: Optional[datetime] = None) -> TAFReading:
        import time
        
        if not resolution_time:
            resolution_time = datetime.now(timezone.utc)

        day_str = resolution_time.strftime("%Y-%m-%d_%H")
        now = time.time()
        
        # Cache valid for 1 hour; key includes hour so morning/afternoon forecasts are distinct
        if day_str in self._cache and now - self._cache[day_str]["time"] < 3600:
            return self._cache[day_str]["reading"]

        airport_tz = ICAO_TIMEZONES.get(self.icao, "UTC")
        local_tz = ZoneInfo(airport_tz)
        local_now = datetime.now(local_tz)
        current_local_hour = local_now.hour

        # STRICT TAF-ONLY "DORMANT" MODE
        # The user has requested to completely lobotomize OM/Meteoblue fallbacks
        # and only lock onto cities with explicitly broadcast aviation TX temperatures.
        taf_reading = None
        try:
            taf_reading = await self._pull_taf(resolution_time)
        except Exception as e:
            logger.debug("TAF failed for %s: %s", self.icao, e)

        # 2. National Weather Service (NWS) Secondary Human Fallback (US Only)
        if not taf_reading and (self.icao.startswith("K") or self.icao.startswith("P")):
            try:
                taf_reading = await self._pull_nws(resolution_time)
            except Exception as e:
                logger.debug("NWS failed for %s: %s", self.icao, e)

        # 3. Open-Meteo Tertiary "Model" Fallback (Global)
        taf_tx = None
        taf_tn = None
        if taf_reading:
            taf_tx = taf_reading.tx_celsius
            taf_tn = taf_reading.tn_celsius
        
        if not taf_reading:
            try:
                taf_reading = await self._pull_openmeteo(
                    resolution_time, airport_tz, current_local_hour, taf_tx, taf_tn
                )
                if taf_reading:
                    taf_reading.source = "open-meteo [MODEL]"
                    taf_reading.distribution = self.to_distribution(taf_reading.tx_celsius, std=2.5)
            except Exception as e:
                logger.debug("Open-Meteo fallback failed for %s: %s", self.icao, e)

        if not taf_reading:
            raise RuntimeError(f"DORMANT: No explicit TAF TX/TN, NWS stats, or Model fallback available for {self.icao}")

        # METAR cross-check: for same-day markets, current observation is ground truth.
        # If the observed temp already exceeds the model TX, update upward with tight sigma.
        hours_to_res = calculate_hours_to_resolution(resolution_time)
        if hours_to_res <= 12:
            try:
                metar_temp = await self._get_metar_temp()
                if metar_temp is not None and metar_temp > taf_reading.tx_celsius:
                    logger.info(f"METAR override {self.icao}: observed {metar_temp:.1f}°C > model {taf_reading.tx_celsius:.1f}°C")
                    taf_reading.tx_celsius = metar_temp
                    taf_reading.distribution = self.to_distribution(metar_temp, std=0.5)
                    taf_reading.source += "+METAR"
            except Exception as e:
                logger.debug("METAR cross-check failed for %s: %s", self.icao, e)

        self._cache[day_str] = {"time": now, "reading": taf_reading}
        return taf_reading

    async def _pull_nws(self, resolution_time: datetime) -> Optional[TAFReading]:
        """Fetch human-curated max daily temperature from the National Weather Service API."""
        from config import ICAO_COORDS
        coords = ICAO_COORDS.get(self.icao)
        if not coords:
            return None
        lat, lon = coords
        
        headers = {"User-Agent": "VaelTar/1.0 (aelthara@polymarket.engine)"}
        
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            # 1. Map Coordinates to Grid Forecast
            points_res = await client.get(f"https://api.weather.gov/points/{lat},{lon}")
            points_res.raise_for_status()
            points_data = points_res.json()
            
            forecast_url = points_data.get("properties", {}).get("forecast")
            if not forecast_url:
                raise ValueError("NWS points API missing forecast URL")

            # 2. Fetch Human Forecast
            forecast_res = await client.get(forecast_url)
            forecast_res.raise_for_status()
            forecast_data = forecast_res.json()
            
            periods = forecast_data.get("properties", {}).get("periods", [])
            target_date_str = resolution_time.strftime("%Y-%m-%d")
            
            nws_high_c = None
            nws_low_c = None
            nws_reason = "No daytime NWS forecast found"
            
            for p in periods:
                if target_date_str in p.get("startTime", ""):
                    temp = float(p.get("temperature", 0))
                    c_temp = (temp - 32) * 5.0 / 9.0 if p.get("temperatureUnit", "F") == "F" else temp
                    
                    if p.get("isDaytime"):
                        nws_high_c = c_temp
                        nws_reason = f"NWS Official: {p.get('shortForecast')}"
                    else:
                        nws_low_c = c_temp
            
            if nws_high_c is None:
                return None
                
            dist = self.to_distribution(nws_high_c)
            return TAFReading(
                icao=self.icao,
                tx_celsius=nws_high_c,
                tn_celsius=nws_low_c,
                issued_at=datetime.now(timezone.utc).isoformat(),
                raw=nws_reason,
                source="nws",
                distribution=dist,
            )

    async def _get_metar_temp(self) -> Optional[float]:
        """Fetch current observed temperature from METAR."""
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(self.METAR_URL, params={"ids": self.icao, "format": "json"})
            r.raise_for_status()
            data = r.json()
        if data and isinstance(data, list):
            temp = data[0].get("temp")
            if temp is not None:
                return float(temp)
        return None

    async def _pull_taf(self, resolution_time: datetime) -> Optional[TAFReading]:
        """Get TX from TAF matching the resolution day."""
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(
                self.AWC,
                params={"ids": self.icao, "format": "json"},
            )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None

        taf = data[0]
        raw = taf.get("rawTAF", "")
        issued = taf.get("issueTime", raw[4:16] if len(raw) > 16 else "unknown")

        try:
            target_day = resolution_time.day
            tx, tn = self._parse_tx_tn(raw, target_day)
            dist = self.to_distribution(tx)
            return TAFReading(
                icao=self.icao,
                tx_celsius=tx,
                tn_celsius=tn,
                issued_at=str(issued),
                raw=raw,
                source="taf",
                distribution=dist,
            )
        except (ValueError, TypeError):
            return None

    async def _pull_openmeteo(
        self,
        resolution_time: datetime,
        airport_tz: str,
        current_local_hour: int,
        taf_max: Optional[float],
        taf_min: Optional[float],
    ) -> TAFReading:
        """Get max temp from Open-Meteo, plugged into the nowcasting pipeline."""
        coords = ICAO_COORDS.get(self.icao)
        if not coords:
            raise ValueError(f"No coordinates for ICAO {self.icao}")
        lat, lon = coords

        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": "temperature_2m",
                    "timezone": "UTC",
                    "forecast_days": 3,
                },
            )
        r.raise_for_status()
        data = r.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", []) 
        temps = hourly.get("temperature_2m", [])

        converted_times = [t.replace("T", " ") + ":00" for t in times]

        mock_meteoblue = {
            "data_1h": {
                "time": converted_times,
                "temperature": temps,
                "predictability": 80
            }
        }

        market_resolution_date = resolution_time.strftime("%Y-%m-%d")
        hours_to_resolution = calculate_hours_to_resolution(resolution_time)

        stats = get_daily_stats_from_basic_1h(
            meteoblue_data=mock_meteoblue,
            market_resolution_date=market_resolution_date,
            airport_tz=airport_tz,
            hours_to_resolution=hours_to_resolution,
            taf_max_remaining=taf_max,
            taf_min_remaining=taf_min,
        )

        if not stats:
            logger.warning(f"Open-Meteo stats extraction returned no data for {self.icao} — refusing 0°C fallback")
            return None

        tx_c = stats["tx_c"]
        tn_c = stats["tn_c"]
        sigma_c = stats["sigma_c"]
        reason = stats["reason"].replace("basic-1h", "open-meteo")
        predictability = stats["predictability"]

        if hours_to_resolution <= 24:
            tx_c = apply_late_day_cap(tx_c, current_local_hour, taf_max)

        dist = {"mean": float(tx_c), "std": sigma_c}
        return TAFReading(
            icao=self.icao,
            tx_celsius=tx_c,
            tn_celsius=tn_c,
            issued_at=datetime.now(timezone.utc).isoformat(),
            raw=reason,
            source="open-meteo",
            distribution=dist,
            predictability=predictability
        )

    def is_new(self, reading: TAFReading) -> bool:
        changed = reading.issued_at != self._last_issued
        if changed:
            self._last_issued = reading.issued_at
        return changed

    def _calculate_sigma(self, predictability: float | None) -> float:
        """Calculate Gaussian σ based on predictability."""
        if predictability is None:
            return 2.0
        if predictability >= 80:
            return 1.5
        if predictability >= 70:
            return 2.0
        if predictability >= 60:
            return 2.5
        return 3.0

    async def generate_signal(
        self,
        market_question: str,
        market_price_cents: int,
        resolution_time: datetime,
        threshold_celsius: float,
        outcome_type: str = "above",
        market_id: Optional[str] = None,
    ) -> "OracleSignal":
        """Generate a full structured trading signal."""
        from market_signal import OracleSignal, SignalType
        from gate import prob_above, prob_below

        reading = await self.pull(resolution_time)
        hours_to_resolution = calculate_hours_to_resolution(resolution_time)

        model_adjusted_c = reading.distribution["mean"]
        predictability = getattr(reading, "predictability", None)
        sigma = self._calculate_sigma(predictability)

        rossby_tag = ""
        if hours_to_resolution > 48:
            try:
                import rossby
                coords = ICAO_COORDS.get(self.icao)
                if coords:
                    lat, lon = coords
                    airport_tz = ICAO_TIMEZONES.get(self.icao, "UTC")
                    local_now = datetime.now(ZoneInfo(airport_tz))
                    tz_offset = int(local_now.utcoffset().total_seconds() / 3600)
                    phase_data = await rossby.get_phase(lat, lon, self.icao, tz_offset)
                    phase = phase_data.get("phase", "NEUTRAL")
                    stability = phase_data.get("stability", "unstable")
                    bias = phase_data.get("bias_degC", 0.0)

                    if phase in ("RIDGE", "TROUGH") and stability != "unstable":
                        reading, rossby_tag = apply_rossby_bias(reading, phase_data)
                        model_adjusted_c = reading.distribution["mean"]
                    else:
                        rossby_tag = "" if stability != "unstable" else " [atmo unstable - bias suppressed]"
            except Exception as e:
                logger.warning(f"Failed to apply Rossby bias for {self.icao}: {e}")
                rossby_tag = ""

        if outcome_type == "below":
            model_prob = prob_below(model_adjusted_c, sigma, threshold_celsius)
        else:
            model_prob = prob_above(model_adjusted_c, sigma, threshold_celsius)

        market_prob = market_price_cents / 100.0
        edge = (model_prob - market_prob) * 100

        confidence = self._calculate_confidence(predictability, hours_to_resolution)
        kelly = self._calculate_kelly(model_prob, market_prob)

        if model_prob > market_prob + 0.10:
            signal_type = SignalType.BUY_YES
        elif model_prob < market_prob - 0.10:
            signal_type = SignalType.SELL_NO
        elif edge > 20.0:
            signal_type = SignalType.LEAN_YES
        elif edge < -20.0:
            signal_type = SignalType.LEAN_NO
        else:
            signal_type = SignalType.PASS

        return OracleSignal(
            market=market_question,
            market_id=market_id,
            model_mean_c=model_adjusted_c,
            model_prob=model_prob,
            market_price=market_prob,
            edge=edge,
            confidence=confidence,
            signal=signal_type,
            reason=f"{reading.source} forecast: {model_adjusted_c:.1f}°C{rossby_tag}",
            kelly_fraction=kelly,
        )

    def _calculate_confidence(self, predictability: float | None, hours_to_resolution: int) -> "Confidence":
        from market_signal import Confidence

        if hours_to_resolution <= 12:
            return Confidence.HIGH
        if predictability is None:
            return Confidence.MEDIUM
        if predictability >= 70:
            return Confidence.HIGH
        if predictability >= 50:
            return Confidence.MEDIUM
        return Confidence.LOW

    def _calculate_kelly(self, model_prob: float, market_prob: float) -> float:
        """Calculate Kelly fraction, capped at 5%."""
        if model_prob <= market_prob:
            return 0.0
        edge = model_prob - market_prob
        kelly = edge / (1 - market_prob) if market_prob < 1 else 0
        return min(kelly, 0.05)

    def to_distribution(self, tx_celsius: float, std: Optional[float] = None) -> Dict[str, float]:
        return {"mean": float(tx_celsius), "std": std if std is not None else self.STD_DEGREE_C}

    def _parse_tx_tn(self, raw: str, target_day: int) -> tuple[float, Optional[float]]:
        """Extract TX and TN for a specific day from the TAF string."""
        # TAF TX pattern: TX[M]nn/ddhhZ 
        tx_pattern = rf"TX(M?)(\d+)/{target_day:02d}"
        tx_m = re.search(tx_pattern, raw)
        tx = None
        if tx_m:
            tx = (-1.0 if tx_m.group(1) == "M" else 1.0) * float(tx_m.group(2))
            
        # TAF TN pattern: TN[M]nn/ddhhZ
        tn_pattern = rf"TN(M?)(\d+)/{target_day:02d}"
        tn_m = re.search(tn_pattern, raw)
        tn = None
        if tn_m:
            tn = (-1.0 if tn_m.group(1) == "M" else 1.0) * float(tn_m.group(2))
            
        if tx is None:
            raise ValueError(f"No TX group found in TAF for day {target_day} at {self.icao}")
            
        return tx, tn
