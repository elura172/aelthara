"""
Simple weather data from Meteoblue + TAF with Open-Meteo backup.
"""
import httpx
from datetime import datetime
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
                "https://api.meteoblue.com/v1/forecast",
                params={
                    'apikey': self.meteoblue_key,
                    'lat': lat, 'lon': lon,
                    'format': 'json',
                    'products': ['temperature_2m', 'precipitation', 'wind_speed_10m'],
                    'forecast_days': 1
                },
                timeout=10.0
            )
            r.raise_for_status()
            data = r.json().get('data', {})
            
            # Get current hour values
            now = int(datetime.now().timestamp())
            times = data.get('temperature_2m', {}).get('time', [])
            idx = min(range(len(times)), key=lambda i: abs(times[i] - now))
            
            return {
                'temperature': data.get('temperature_2m', {}).get('values', [None]*len(times))[idx],
                'precipitation': data.get('precipitation', {}).get('values', [0]*len(times))[idx],
                'wind_speed': data.get('wind_speed_10m', {}).get('values', [0]*len(times))[idx],
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
