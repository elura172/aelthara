"""Oracle — Temperature oracle. Meteoblue primary, TAF for issued time, Open-Meteo fallback."""

import os
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx
from dotenv import load_dotenv

from gate import decide_signal, prob_above, prob_below
from signal import Confidence, OracleSignal, SignalType

load_dotenv()
logger = logging.getLogger(__name__)

# ICAO to coordinates mapping
ICAO_COORDS = {
    "KJFK": (40.6413, -73.7781), "KLAX": (33.9416, -118.4085), "KORD": (41.9742, -87.9073),
    "KMIA": (25.7959, -80.2870), "KATL": (33.6407, -84.4277), "KSEA": (47.4502, -122.3088),
    "KDEN": (39.7017, -104.7516), "EGLL": (51.4700, -0.4543), "LFPG": (49.0097, 2.5479),
    "EDDF": (50.0379, 8.5622), "EHAM": (52.3105, 4.7683), "RJTT": (35.7720, 140.3929),
    "RKSS": (37.4602, 126.4407), "WSSS": (1.3644, 103.9915), "VHHH": (22.3020, 114.1742),
    "ZBAA": (40.0801, 116.5970), "ZSPD": (31.1434, 121.8050), "YSSY": (-33.9461, 151.1772),
    "NZAA": (-37.0082, 174.7917), "SBGR": (-23.4356, -46.4731), "SAEZ": (-34.8222, -58.5358),
    "EGCC": (53.3537, -2.2749), "EGPH": (55.9533, -3.3727), "LIRF": (41.7994, 12.2462),
    "LEMD": (40.4839, -3.5680), "LOWW": (48.1103, 16.5697), "LKPR": (50.1008, 14.2600),
    "LSZH": (47.4581, 8.5555), "EBBR": (50.9010, 4.4844), "EPWA": (52.1657, 20.9671),
    "EFHK": (60.3172, 24.9633), "ENGM": (60.1939, 11.1004), "EKCH": (55.6181, 12.6560),
    "ESSA": (59.6519, 17.9186), "EIDW": (53.4213, -6.2701), "LPPT": (38.7742, -9.1342),
    "OMDB": (25.2532, 55.3657), "VTBS": (13.6900, 100.7501), "VIDP": (28.5562, 77.1000),
    "VABB": (19.0896, 72.8656), "RCTP": (25.0697, 121.5521), "RJBB": (34.4280, 135.2440),
    "RJFF": (33.5859, 130.4507), "WMKK": (2.7456, 101.7099), "WIII": (-6.2661, 106.8906),
    "ZGSZ": (22.6394, 113.8108), "PHNL": (21.3187, -157.9224), "KAUS": (30.1944, -97.6700),
    "KPHX": (33.4350, -112.0160), "KBOS": (42.3656, -71.0096), "KPHL": (39.8729, -75.2437),
    "KSAN": (32.7336, -117.1897), "KLAS": (36.0801, -115.1522), "KDFW": (32.8471, -96.8518),
    "KIAH": (29.6454, -95.2789), "MMMX": (19.4361, -99.0720), "CYYZ": (43.6777, -79.6248),
    "CYVR": (49.1967, -123.1815), "CYUL": (45.4706, -73.6406), "NZCH": (-43.4894, 172.5310),
    "NZWN": (-41.3272, 174.8076), "YPAD": (-34.9450, 138.5306), "YBBN": (-27.3942, 153.1219),
    "YMML": (-37.6690, 144.8410), "YPPH": (-31.9403, 115.9670),
}


@dataclass
class TAFReading:
    icao: str
    tx_celsius: float
    issued_at: str
    raw: str
    source: str = "meteoblue"  # "meteoblue", "taf", or "openmeteo"
    distribution: Dict[str, float] = field(default_factory=lambda: {"mean": 0.0, "std": 2.0})

    @property
    def tx_fahrenheit(self) -> float:
        return self.tx_celsius * 9 / 5 + 32

    def __str__(self) -> str:
        return (
            f"TAF {self.icao}  TX={self.tx_celsius:.0f}°C "
            f"({self.tx_fahrenheit:.0f}°F)  issued={self.issued_at} [{self.source}]"
        )


class Oracle:
    """Pulls temperature — Meteoblue primary, TAF for issued time, Open-Meteo fallback."""

    AWC = "https://aviationweather.gov/api/data/taf"
    METEOBLUE = "https://api.meteoblue.com/v1/forecast"
    OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
    STD_DEGREE_C: float = 2.0
    KILL_THRESHOLD = 1.5
    MAX_POSITION = 0.05
    TAF_WEIGHT_SHORT = 0.15
    TAF_WEIGHT_LONG = 0.30
    NOWCAST_SIGMA_FACTOR = 0.85

    def __init__(self, icao: str):
        self.icao = icao.upper()
        self._last_issued: str = ""
        self._meteoblue_key = os.getenv("METEO_BLUE_API", "")

    def pull(self) -> TAFReading:
        """Pull temperature — Meteoblue primary, then TAF, then Open-Meteo."""
        # Try Meteoblue first
        try:
            reading = self._pull_meteoblue()
            if reading:
                return reading
        except Exception as e:
            logger.warning("Meteoblue failed for %s: %s", self.icao, e)

        # Try TAF
        try:
            reading = self._pull_taf()
            if reading:
                return reading
        except Exception as e:
            logger.warning("TAF failed for %s: %s", self.icao, e)

        # Fallback to Open-Meteo
        try:
            return self._pull_openmeteo()
        except Exception as e:
            raise RuntimeError(f"All sources failed for {self.icao}: {e}")

    def _pull_meteoblue(self) -> Optional[TAFReading]:
        """Get daily max temp from Meteoblue."""
        if not self._meteoblue_key:
            return None

        coords = ICAO_COORDS.get(self.icao)
        if not coords:
            return None

        lat, lon = coords

        r = httpx.get(
            self.METEOBLUE,
            params={
                "apikey": self._meteoblue_key,
                "lat": lat,
                "lon": lon,
                "format": "json",
                "products": ["temperature_2m"],
                "forecast_days": 1,
            },
            timeout=12,
        )
        r.raise_for_status()
        data = r.json().get("data", {})

        temp_data = data.get("temperature_2m", {})
        values = temp_data.get("values", [])

        if not values:
            return None

        # Get max from hourly values
        tx = max(values)
        dist = self.to_distribution(tx)

        return TAFReading(
            icao=self.icao,
            tx_celsius=tx,
            issued_at=datetime.now(timezone.utc).isoformat(),
            raw="Meteoblue daily max",
            source="meteoblue",
            distribution=dist,
        )

    def _pull_taf(self) -> Optional[TAFReading]:
        """Get TX from TAF."""
        r = httpx.get(
            self.AWC,
            params={"ids": self.icao, "format": "json"},
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None

        taf = data[0]
        raw = taf.get("rawTAF", "")
        issued = taf.get("issueTime", raw[4:16] if len(raw) > 16 else "unknown")

        try:
            tx = self._parse_tx(raw)
            dist = self.to_distribution(tx)
            return TAFReading(
                icao=self.icao,
                tx_celsius=tx,
                issued_at=str(issued),
                raw=raw,
                source="taf",
                distribution=dist,
            )
        except ValueError:
            return None

    def _pull_openmeteo(self) -> TAFReading:
        """Get daily max temp from Open-Meteo."""
        coords = ICAO_COORDS.get(self.icao)
        if not coords:
            raise ValueError(f"No coordinates for {self.icao}")

        lat, lon = coords
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        r = httpx.get(
            self.OPEN_METEO,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max",
                "start_date": today,
                "end_date": today,
                "timezone": "auto",
            },
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()

        daily = data.get("daily", {})
        temps = daily.get("temperature_2m_max", [])

        if not temps:
            raise ValueError(f"No daily temp in Open-Meteo response for {self.icao}")

        tx = float(temps[0])
        dist = self.to_distribution(tx)

        return TAFReading(
            icao=self.icao,
            tx_celsius=tx,
            issued_at=datetime.now(timezone.utc).isoformat(),
            raw=f"Open-Meteo daily max {today}",
            source="openmeteo",
            distribution=dist,
        )

    def is_new(self, reading: TAFReading) -> bool:
        changed = reading.issued_at != self._last_issued
        if changed:
            self._last_issued = reading.issued_at
        return changed

    def to_distribution(self, tx_celsius: float) -> Dict[str, float]:
        return {"mean": float(tx_celsius), "std": self.STD_DEGREE_C}

    def _parse_tx(self, raw: str) -> float:
        m = re.search(r"TX(M?)(\d+)/", raw)
        if not m:
            raise ValueError(f"No TX group found in TAF for {self.icao}")
        return (-1.0 if m.group(1) == "M" else 1.0) * float(m.group(2))

    def _calculate_daily_max(self, hourly_temps: list[float], hourly_times: list[str], target_date: str) -> float:
        day_temps = []
        for temp, time_str in zip(hourly_temps, hourly_times):
            if time_str.startswith(target_date):
                day_temps.append(float(temp))
        if not day_temps:
            raise ValueError(f"No temperatures found for {target_date}")
        return max(day_temps)

    def _calculate_confidence(self, predictability: float | None, hours_to_resolution: int) -> Confidence:
        if hours_to_resolution <= 12:
            return Confidence.HIGH
        if predictability is None:
            return Confidence.MEDIUM
        if predictability >= 70:
            return Confidence.HIGH
        if predictability >= 50:
            return Confidence.MEDIUM
        return Confidence.LOW

    def _calculate_sigma(self, predictability: float | None) -> float:
        base_sigma = 2.0
        if predictability is None:
            return base_sigma
        if predictability >= 70:
            return base_sigma
        if predictability >= 50:
            return base_sigma * 1.25
        return base_sigma * 1.5

    def _calculate_kelly(self, edge: float, model_prob: float, market_prob: float) -> float:
        if edge <= 0:
            return 0.0
        if 0 < market_prob < 1:
            odds = (1 - market_prob) / market_prob
            q = 1 - model_prob
            raw_kelly = ((model_prob * odds) - q) / odds if odds > 0 else 0.0
            kelly = max(0.0, raw_kelly) * 0.5
        else:
            kelly = edge * 0.5
        return min(kelly, self.MAX_POSITION)

    def _extract_predictability(self, data: dict) -> float | None:
        for key in ("predictability", "predictability_score"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        meta = data.get("metadata", {})
        for key in ("predictability", "predictability_score"):
            value = meta.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    def _pull_meteoblue_hourly(self) -> tuple[list[float], list[str], float | None]:
        if not self._meteoblue_key:
            raise ValueError("Meteoblue key not configured")
        coords = ICAO_COORDS.get(self.icao)
        if not coords:
            raise ValueError(f"No coordinates for {self.icao}")
        lat, lon = coords
        r = httpx.get(
            self.METEOBLUE,
            params={
                "apikey": self._meteoblue_key,
                "lat": lat,
                "lon": lon,
                "format": "json",
                "models": "basic-1h",
                "forecast_days": 3,
            },
            timeout=12,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data", {})
        temp_data = data.get("temperature_2m", {})
        hourly_temps = temp_data.get("values", [])
        hourly_times = temp_data.get("time", [])
        if not hourly_temps or not hourly_times:
            raise ValueError("Missing hourly Meteoblue temperature data")
        return hourly_temps, hourly_times, self._extract_predictability(payload)

    def _pull_openmeteo_daily_max(self, target_date: str) -> float:
        coords = ICAO_COORDS.get(self.icao)
        if not coords:
            raise ValueError(f"No coordinates for {self.icao}")
        lat, lon = coords
        r = httpx.get(
            self.OPEN_METEO,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max",
                "start_date": target_date,
                "end_date": target_date,
                "timezone": "auto",
            },
            timeout=12,
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        temps = daily.get("temperature_2m_max", [])
        if not temps:
            raise ValueError(f"No daily temp in Open-Meteo response for {self.icao} on {target_date}")
        return float(temps[0])

    def generate_signal(
        self,
        market_question: str,
        market_price_cents: int,
        resolution_time: datetime,
        threshold_celsius: float,
        outcome_type: str = "above",
    ) -> OracleSignal:
        now = datetime.now(timezone.utc)
        if resolution_time.tzinfo is None:
            resolution_time = resolution_time.replace(tzinfo=timezone.utc)
        hours_to_resolution = max(0, int((resolution_time - now).total_seconds() / 3600))
        target_date = resolution_time.strftime("%Y-%m-%d")

        source = "meteoblue"
        predictability = None
        taf = None
        try:
            hourly_temps, hourly_times, predictability = self._pull_meteoblue_hourly()
            daily_max = self._calculate_daily_max(hourly_temps, hourly_times, target_date)
        except Exception as err:
            logger.warning("Meteoblue hourly unavailable for %s: %s", self.icao, err)
            source = "openmeteo"
            daily_max = self._pull_openmeteo_daily_max(target_date)

        try:
            taf = self._pull_taf()
        except Exception as err:
            logger.warning("TAF unavailable for %s: %s", self.icao, err)

        model_mean = daily_max
        taf_note = "TAF unavailable"
        if taf:
            taf_weight = self.TAF_WEIGHT_SHORT if hours_to_resolution <= 12 else self.TAF_WEIGHT_LONG
            model_mean = (1.0 - taf_weight) * daily_max + taf_weight * taf.tx_celsius
            taf_note = "TAF confirms" if abs(taf.tx_celsius - daily_max) < 1.5 else "TAF adjusts"

        sigma = self._calculate_sigma(predictability)
        if source == "meteoblue" and hours_to_resolution <= 12:
            sigma *= self.NOWCAST_SIGMA_FACTOR

        if outcome_type == "below":
            model_prob = prob_below(model_mean, sigma, threshold_celsius)
        else:
            model_prob = prob_above(model_mean, sigma, threshold_celsius)

        market_prob = max(0.0, min(1.0, market_price_cents / 100.0))
        edge_decimal = model_prob - market_prob
        edge_points = int(round(model_prob * 100 - market_price_cents))
        confidence = self._calculate_confidence(predictability, hours_to_resolution)
        signal = decide_signal(model_prob, market_prob)
        kelly_fraction = self._calculate_kelly(edge_decimal, model_prob, market_prob)
        if abs(model_mean - threshold_celsius) < self.KILL_THRESHOLD:
            signal = SignalType.PASS
            edge_points = 0
            kelly_fraction = 0.0

        if taf:
            taf_detail = f" (TAF={taf.tx_celsius:.1f}°C)"
            reason = f"{source} basic-1h daily max {daily_max:.1f}°C, {taf_note.lower()}{taf_detail}"
        else:
            reason = f"{source} daily max {daily_max:.1f}°C, {taf_note.lower()}"
        if signal == SignalType.PASS and abs(model_mean - threshold_celsius) < self.KILL_THRESHOLD:
            reason = f"{reason}; within {self.KILL_THRESHOLD:.1f}°C kill threshold"

        return OracleSignal(
            market=market_question,
            model_mean_c=model_mean,
            model_prob=model_prob,
            market_price=market_price_cents,
            edge=edge_points,
            confidence=confidence,
            signal=signal,
            reason=reason,
            kelly_fraction=kelly_fraction,
        )
