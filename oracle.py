"""Oracle — Temperature oracle. Meteoblue primary, TAF for issued time, Open-Meteo fallback."""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv()

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

ICAO_TIMEZONES_FULL = {
    # US
    "KJFK": "America/New_York",
    "KLAX": "America/Los_Angeles",
    "KORD": "America/Chicago",
    "KMIA": "America/New_York",
    "KATL": "America/New_York",
    "KSEA": "America/Los_Angeles",
    "KDEN": "America/Denver",
    "KAUS": "America/Chicago",
    "KPHX": "America/Phoenix",
    "KBOS": "America/New_York",
    "KPHL": "America/New_York",
    "KSAN": "America/Los_Angeles",
    "KLAS": "America/Los_Angeles",
    "KDFW": "America/Chicago",
    "KIAH": "America/Chicago",
    # Europe
    "EGLL": "Europe/London",
    "LFPG": "Europe/Paris",
    "EDDF": "Europe/Berlin",
    "EHAM": "Europe/Amsterdam",
    # Asia
    "RJTT": "Asia/Tokyo",
    "RKSS": "Asia/Seoul",
    "WSSS": "Asia/Singapore",
    "VHHH": "Asia/Hong_Kong",
    "ZBAA": "Asia/Shanghai",
    "ZSPD": "Asia/Shanghai",
    # Oceania
    "YSSY": "Australia/Sydney",
    "NZAA": "Pacific/Auckland",
    # South America
    "SBGR": "America/Sao_Paulo",
    "SAEZ": "America/Argentina/Buenos_Aires",
}


def calculate_hours_to_resolution(resolution_time: datetime) -> int:
    """Calculate hours from now until market resolution."""
    if resolution_time.tzinfo is None:
        resolution_time = resolution_time.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = resolution_time.astimezone(timezone.utc) - now
    return int(delta.total_seconds() // 3600)


def get_taf_max_remaining(taf_data: dict, airport_tz: str, current_local_hour: int) -> Optional[float]:
    """Get TAF-predicted max temperature for remaining hours of the day."""
    del airport_tz  # reserved for future richer hour conversion
    raw_taf = taf_data.get("rawTAF", "")
    tx_match = re.search(r"TX(M?)(\d+)/(\d{2})(\d{2})Z", raw_taf)
    if tx_match:
        sign = -1.0 if tx_match.group(1) == "M" else 1.0
        temp = sign * float(tx_match.group(2))
        if current_local_hour < 18:
            return temp
    return None


def get_daily_peak_from_basic_1h(
    meteoblue_data: dict,
    market_resolution_date: str,
    airport_tz: str,
    hours_to_resolution: int,
    taf_max_remaining: Optional[float] = None,
) -> Optional[dict]:
    """Returns expected daily max °C for the resolution day, with short-term nowcast weighting."""
    data_1h = meteoblue_data.get("data_1h", {})
    hourly_times = data_1h.get("time", [])
    hourly_temps = data_1h.get("temperature", [])
    predictability = data_1h.get("predictability", 80)

    if not hourly_times or not hourly_temps:
        return None

    resolution_day = datetime.strptime(market_resolution_date, "%Y-%m-%d").date()
    tz = ZoneInfo(airport_tz)
    day_temps = []
    for t_str, temp in zip(hourly_times, hourly_temps):
        try:
            t_str_clean = t_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(t_str_clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_dt = dt.astimezone(tz)
            if local_dt.date() == resolution_day:
                day_temps.append(temp)
        except (TypeError, ValueError):
            continue

    if not day_temps:
        return None

    raw_peak = max(day_temps)

    if hours_to_resolution <= 12:
        blended_peak = raw_peak * 0.35 + taf_max_remaining * 0.65 if taf_max_remaining is not None else raw_peak
        if predictability >= 80:
            sigma_adjust = 1.5
        elif predictability >= 60:
            sigma_adjust = 2.0
        else:
            sigma_adjust = 2.5
        return {
            "peak_c": round(blended_peak, 1),
            "sigma_c": sigma_adjust,
            "short_term_active": True,
            "predictability": predictability,
            "reason": f"basic-1h nowcast + TAF blend (hours left: {hours_to_resolution}, predictability: {predictability}%)",
        }

    if predictability >= 70:
        sigma_adjust = 2.0
    elif predictability >= 50:
        sigma_adjust = 2.5
    else:
        sigma_adjust = 3.0
    return {
        "peak_c": round(raw_peak, 1),
        "sigma_c": sigma_adjust,
        "short_term_active": False,
        "predictability": predictability,
        "reason": f"basic-1h hourly max (longer horizon, predictability: {predictability}%)",
    }


def apply_late_day_cap(peak_c: float, current_local_hour: int, taf_trend_c: Optional[float]) -> float:
    """Cap possible upside when peak has likely already occurred."""
    if current_local_hour >= 16 and taf_trend_c is not None:
        return min(peak_c, taf_trend_c + 1.5)
    return peak_c


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
            print(f"[Oracle] Meteoblue failed for {self.icao}: {e}")

        # Try TAF
        try:
            reading = self._pull_taf()
            if reading:
                return reading
        except Exception as e:
            print(f"[Oracle] TAF failed for {self.icao}: {e}")

        # Fallback to Open-Meteo
        try:
            return self._pull_openmeteo()
        except Exception as e:
            raise RuntimeError(f"All sources failed for {self.icao}: {e}")

    def _pull_meteoblue(self) -> Optional[TAFReading]:
        """Get daily max temp from Meteoblue."""
        data = self._pull_meteoblue_raw()
        if not data:
            return None

        airport_tz = ICAO_TIMEZONES_FULL.get(self.icao, "UTC")
        now_local = datetime.now(ZoneInfo(airport_tz))
        resolution_date = now_local.strftime("%Y-%m-%d")
        resolution_local = datetime(now_local.year, now_local.month, now_local.day, 23, 59, tzinfo=ZoneInfo(airport_tz))
        hours_to_resolution = calculate_hours_to_resolution(resolution_local.astimezone(timezone.utc))
        taf_data = self._pull_taf_raw()
        taf_max_remaining = get_taf_max_remaining(taf_data, airport_tz, now_local.hour) if taf_data else None

        peak_info = get_daily_peak_from_basic_1h(
            data,
            market_resolution_date=resolution_date,
            airport_tz=airport_tz,
            hours_to_resolution=hours_to_resolution,
            taf_max_remaining=taf_max_remaining,
        )

        if peak_info is None:
            temp_data = data.get("data", {}).get("temperature_2m", {})
            values = temp_data.get("values", [])
            if not values:
                return None
            peak_info = {
                "peak_c": round(max(values), 1),
                "sigma_c": self.STD_DEGREE_C,
                "short_term_active": False,
                "predictability": 80,
                "reason": "fallback hourly max",
            }

        model_peak = apply_late_day_cap(peak_info["peak_c"], now_local.hour, taf_max_remaining)
        dist = {"mean": float(model_peak), "std": float(peak_info["sigma_c"])}
        return TAFReading(
            icao=self.icao,
            tx_celsius=float(model_peak),
            issued_at=datetime.now(timezone.utc).isoformat(),
            raw=peak_info["reason"],
            source="meteoblue",
            distribution=dist,
        )

    def _pull_meteoblue_raw(self) -> Optional[Dict[str, Any]]:
        """Get raw Meteoblue response (basic-1h package)."""
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
                "package": "basic-1h",
                "forecast_days": 2,
            },
            timeout=12,
        )
        r.raise_for_status()
        return r.json()

    def _pull_taf(self) -> Optional[TAFReading]:
        """Get TX from TAF."""
        taf = self._pull_taf_raw()
        if not taf:
            return None

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

    def _pull_taf_raw(self) -> Optional[Dict[str, Any]]:
        """Get raw first TAF object for this airport."""
        r = httpx.get(
            self.AWC,
            params={"ids": self.icao, "format": "json"},
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return data[0]

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
