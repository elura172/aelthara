"""
Simple weather data from Meteoblue + TAF with Open-Meteo backup.
"""
import httpx
from datetime import datetime, timezone
from typing import Optional, Dict, Any


class WeatherData:
    """
    Fetch weather from Meteoblue (primary), TAF (wind/visibility), Open-Meteo (backup).
    """
    
    def __init__(self, meteoblue_key: str):
        self.meteoblue_key = meteoblue_key
    
    def get(
        self,
        lat: float,
        lon: float,
        station: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get weather data.
        
        Args:
            lat: Latitude
            lon: Longitude  
            station: Optional ICAO code for TAF
        
        Returns:
            Merged weather data
        """
        # Primary: Meteoblue
        mb = self._meteoblue(lat, lon)
        
        # Backup: Open-Meteo
        om = self._open_meteo(lat, lon) if not mb else None
        
        # TAF if station provided
        taf = self._taf(station) if station else None
        
        return self._merge(mb, om, taf)
    
    def _meteoblue(self, lat: float, lon: float) -> Optional[Dict]:
        """Fetch from Meteoblue."""
        try:
            r = httpx.get(
                "https://my.meteoblue.com/packages/basic-1h",
                params={
                    'apikey': self.meteoblue_key,
                    'lat': lat, 'lon': lon,
                    'format': 'json',
                },
                timeout=10.0
            )
            r.raise_for_status()
            data = r.json()
            data_1h = data.get('data_1h', {})
            
            # Get current hour values
            time_strs = data_1h.get('time', [])
            if not time_strs:
                return None

            now = datetime.now(timezone.utc)
            idx = 0
            try:
                timestamps = [datetime.fromisoformat(t.replace('Z', '+00:00')) for t in time_strs]
                idx = min(range(len(timestamps)), key=lambda i: abs((timestamps[i] - now).total_seconds()))
            except Exception:
                idx = 0

            temps = data_1h.get('temperature', [])
            precip = data_1h.get('precipitation', [])
            wind = data_1h.get('windspeed', [])
            
            return {
                'temperature': temps[idx] if idx < len(temps) else None,
                'precipitation': precip[idx] if idx < len(precip) else 0,
                'wind_speed': wind[idx] if idx < len(wind) else 0,
            }
        except Exception:
            return None
    
    def _open_meteo(self, lat: float, lon: float) -> Optional[Dict]:
        """Fetch from Open-Meteo (backup)."""
        try:
            r = httpx.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    'latitude': lat, 'longitude': lon,
                    'current': 'temperature_2m,precipitation,wind_speed_10m'
                },
                timeout=10.0
            )
            r.raise_for_status()
            c = r.json().get('current', {})
            return {
                'temperature': c.get('temperature_2m'),
                'precipitation': c.get('precipitation'),
                'wind_speed': c.get('wind_speed_10m'),
            }
        except Exception:
            return None
    
    def _taf(self, station: str) -> Optional[Dict]:
        """Fetch TAF data."""
        try:
            r = httpx.get(
                "https://aviationweather.gov/api/data/taf",
                params={'ids': station, 'format': 'json'},
                timeout=10.0
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                return None
            t = data[0]
            return {
                'wind_speed': t.get('spd'),
                'wind_dir': t.get('drct'),
                'visibility': t.get('vis'),
                'raw': t.get('rawOb'),
            }
        except Exception:
            return None
    
    def _merge(self, mb, om, taf) -> Dict:
        """Simple merge: Meteoblue primary, Open-Meteo backup."""
        result = {
            'timestamp': datetime.now().isoformat(),
            'sources': [],
            'temperature': None,
            'precipitation': None,
            'wind_speed': None,
            'wind_dir': None,
            'visibility': None,
        }
        
        # Temperature & precipitation: Meteoblue → Open-Meteo
        for key in ['temperature', 'precipitation']:
            if mb and mb.get(key) is not None:
                result[key] = mb[key]
                result['sources'].append('meteoblue')
            elif om and om.get(key) is not None:
                result[key] = om[key]
                result['sources'].append('open_meteo')
        
        # Wind: TAF → Meteoblue → Open-Meteo
        if taf and taf.get('wind_speed'):
            result['wind_speed'] = taf['wind_speed']
            result['wind_dir'] = taf.get('wind_dir')
            result['sources'].append('taf')
        elif mb and mb.get('wind_speed'):
            result['wind_speed'] = mb['wind_speed']
            result['sources'].append('meteoblue')
        elif om and om.get('wind_speed'):
            result['wind_speed'] = om['wind_speed']
            result['sources'].append('open_meteo')
        
        # Visibility from TAF only
        if taf and taf.get('visibility'):
            result['visibility'] = taf['visibility']
            result['sources'].append('taf')
        
        result['sources'] = list(set(result['sources']))
        return result
