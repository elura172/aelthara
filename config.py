"""Aelthara configuration constants.

Single source of truth for all ICAO cities, coordinates, timezones,
and city name mappings. Consolidates what was previously scattered
across pulse.py, oracle.py, and executor.py.
"""

from zoneinfo import ZoneInfo

ICAO_LIST = [
    "KJFK", "KLAX", "KORD", "KMIA", "KATL", "KSEA", "KDEN",
    "EGLL", "LFPG", "EDDF", "EHAM", "RJTT", "RKSS", "WSSS",
    "VHHH", "ZBAA", "ZSPD", "YSSY", "NZAA", "SBGR", "SAEZ",
    "KDFW", "KIAH", "KPHX", "KLAS",
    "LEMD", "LIRF", "EDDM", "EIDW",
    "VTBS", "VABB", "VIDP", "RCTP",
    "FACT", "ZHHH", "CYYZ", "EPWA", "HECA", "UUEE",
    "EGCC", "EGPH", "LOWW", "LKPR", "LSZH", "EBBR",
    "EFHK", "ENGM", "EKCH", "ESSA", "LPPT", "OMDB",
    "ZGSZ", "PHNL", "KAUS", "KBOS", "KPHL", "KSAN",
    "CYVR", "CYUL", "NZCH", "NZWN", "YPAD", "YBBN",
    "YMML", "YPPH", "RJBB", "RJFF", "WMKK", "WIII",
]

CITY_LIST = [
    "NEW YORK", "LOS ANGELES", "CHICAGO", "MIAMI", "ATLANTA", "SEATTLE", "DENVER",
    "LONDON", "PARIS", "FRANKFURT", "AMSTERDAM", "TOKYO", "SEOUL", "SINGAPORE",
    "HONG KONG", "BEIJING", "SHANGHAI", "SYDNEY", "AUCKLAND", "SAO PAULO", "BUENOS AIRES",
    "DALLAS", "HOUSTON", "PHOENIX", "LAS VEGAS",
    "MADRID", "ROME", "MUNICH", "DUBLIN",
    "BANGKOK", "MUMBAI", "DELHI", "TAIPEI",
    "CAPE TOWN", "WUHAN", "TORONTO", "WARSAW", "CAIRO", "MOSCOW",
    "MANCHESTER", "EDINBURGH", "VIENNA", "PRAGUE", "ZURICH", "BRUSSELS",
    "HELSINKI", "OSLO", "COPENHAGEN", "STOCKHOLM", "LISBON", "DUBAI",
    "SHENZHEN",
    "HONOLULU", "AUSTIN", "BOSTON", "PHILADELPHIA", "SAN DIEGO", "VANCOUVER",
    "MONTREAL", "CHRISTCHURCH", "WELLINGTON", "ADELAIDE", "BRISBANE", "MELBOURNE",
    "PERTH", "OSAKA", "NAGOYA", "KUALA LUMPUR", "JAKARTA",
]

ICAO_COORDS = {
    "KJFK": (40.6413, -73.7781), "KLAX": (33.9416, -118.4085), "KORD": (41.9742, -87.9073),
    "KMIA": (25.7959, -80.2870), "KATL": (33.6407, -84.4277), "KSEA": (47.4502, -122.3088),
    "KDEN": (39.7017, -104.7516), "EGLL": (51.4700, -0.4543), "LFPG": (49.0097, 2.5479),
    "EDDF": (50.0379, 8.5622), "EHAM": (52.3105, 4.7683), "RJTT": (35.7720, 140.3929),
    "RKSS": (37.4602, 126.4407), "WSSS": (1.3644, 103.9915), "VHHH": (22.3020, 114.1742),
    "ZBAA": (40.0801, 116.5970), "ZSPD": (31.1434, 121.8050), "YSSY": (-33.9461, 151.1772),
    "NZAA": (-37.0082, 174.7917), "SBGR": (-23.4356, -46.4731), "SAEZ": (-34.8222, -58.5358),
    "KDFW": (32.8471, -96.8518), "KIAH": (29.9902, -95.3368), "KPHX": (33.4373, -112.0078),
    "KLAS": (36.0840, -115.1537), "LEMD": (40.4983, -3.5676), "LIRF": (41.8003, 12.2389),
    "EDDM": (48.3538, 11.7861), "EIDW": (53.4264, -6.2499), "VTBS": (13.6900, 100.7501),
    "VABB": (19.0896, 72.8656), "VIDP": (28.5562, 77.1000), "RCTP": (25.0797, 121.2342),
    "FACT": (-33.9715, 18.6021), "ZHHH": (30.7725, 114.2081), "CYYZ": (43.6777, -79.6248),
    "EPWA": (52.1657, 20.9671), "HECA": (30.1219, 31.4056), "UUEE": (55.5983, 37.2626),
    "EGCC": (53.3537, -2.2749), "EGPH": (55.9533, -3.3727), "LOWW": (48.1103, 16.5697),
    "LKPR": (50.1008, 14.2600), "LSZH": (47.4581, 8.5555), "EBBR": (50.9010, 4.4844),
    "EFHK": (60.3172, 24.9633), "ENGM": (60.1939, 11.1004), "EKCH": (55.6181, 12.6560),
    "ESSA": (59.6519, 17.9186), "LPPT": (38.7742, -9.1342), "OMDB": (25.2532, 55.3657),
    "ZGSZ": (22.6394, 113.8108), "PHNL": (21.3187, -157.9224), "KAUS": (30.1944, -97.6700),
    "KBOS": (42.3656, -71.0096), "KPHL": (39.8729, -75.2437), "KSAN": (32.7336, -117.1897),
    "CYVR": (49.1967, -123.1815), "CYUL": (45.4706, -73.6406), "NZCH": (-43.4894, 172.5310),
    "NZWN": (-41.3272, 174.8076), "YPAD": (-34.9450, 138.5306), "YBBN": (-27.3942, 153.1219),
    "YMML": (-37.6690, 144.8410), "YPPH": (-31.9403, 115.9670), "RJBB": (34.4280, 135.2440),
    "RJFF": (33.5859, 130.4507), "WMKK": (2.7456, 101.7099), "WIII": (-6.2661, 106.8906),
}

ICAO_TIMEZONES = {
    "KJFK": "America/New_York", "KLAX": "America/Los_Angeles", "KORD": "America/Chicago",
    "KMIA": "America/New_York", "KATL": "America/New_York", "KSEA": "America/Los_Angeles", "KDEN": "America/Denver",
    "EGLL": "Europe/London", "LFPG": "Europe/Paris", "EDDF": "Europe/Berlin", "EHAM": "Europe/Amsterdam",
    "RJTT": "Asia/Tokyo", "RKSS": "Asia/Seoul", "WSSS": "Asia/Singapore", "VHHH": "Asia/Hong_Kong",
    "ZBAA": "Asia/Shanghai", "ZSPD": "Asia/Shanghai", "YSSY": "Australia/Sydney", "NZAA": "Pacific/Auckland",
    "SBGR": "America/Sao_Paulo", "SAEZ": "America/Argentina/Buenos_Aires",
    "KDFW": "America/Chicago", "KIAH": "America/Chicago", "KPHX": "America/Phoenix", "KLAS": "America/Los_Angeles",
    "LEMD": "Europe/Madrid", "LIRF": "Europe/Rome", "EDDM": "Europe/Berlin", "EIDW": "Europe/Dublin",
    "VTBS": "Asia/Bangkok", "VABB": "Asia/Kolkata", "VIDP": "Asia/Kolkata", "RCTP": "Asia/Taipei",
    "FACT": "Africa/Johannesburg", "ZHHH": "Asia/Shanghai", "CYYZ": "America/Toronto", "EPWA": "Europe/Warsaw",
    "HECA": "Africa/Cairo", "UUEE": "Europe/Moscow",
    "EGCC": "Europe/London", "EGPH": "Europe/London", "LOWW": "Europe/Vienna", "LKPR": "Europe/Prague",
    "LSZH": "Europe/Zurich", "EBBR": "Europe/Brussels", "EFHK": "Europe/Helsinki", "ENGM": "Europe/Oslo",
    "EKCH": "Europe/Copenhagen", "ESSA": "Europe/Stockholm", "LPPT": "Europe/Lisbon", "OMDB": "Asia/Dubai",
    "ZGSZ": "Asia/Shanghai", "PHNL": "Pacific/Honolulu", "KAUS": "America/Chicago",
    "KBOS": "America/New_York", "KPHL": "America/New_York", "KSAN": "America/Los_Angeles",
    "CYVR": "America/Vancouver", "CYUL": "America/Montreal", "NZCH": "Pacific/Auckland",
    "NZWN": "Pacific/Auckland", "YPAD": "Australia/Adelaide", "YBBN": "Australia/Brisbane",
    "YMML": "Australia/Melbourne", "YPPH": "Australia/Perth", "RJBB": "Asia/Tokyo",
    "RJFF": "Asia/Tokyo", "WMKK": "Asia/Kuala_Lumpur", "WIII": "Asia/Jakarta",
}

CITY_TO_ICAO = {
    "NEW YORK": "KJFK", "LOS ANGELES": "KLAX", "CHICAGO": "KORD", "MIAMI": "KMIA", "ATLANTA": "KATL",
    "SEATTLE": "KSEA", "DENVER": "KDEN", "LONDON": "EGLL", "PARIS": "LFPG", "FRANKFURT": "EDDF",
    "AMSTERDAM": "EHAM", "TOKYO": "RJTT", "SEOUL": "RKSS", "SINGAPORE": "WSSS", "HONG KONG": "VHHH",
    "BEIJING": "ZBAA", "SHANGHAI": "ZSPD", "SYDNEY": "YSSY", "AUCKLAND": "NZAA", "SAO PAULO": "SBGR",
    "BUENOS AIRES": "SAEZ", "DALLAS": "KDFW", "HOUSTON": "KIAH", "PHOENIX": "KPHX", "LAS VEGAS": "KLAS",
    "MUNICH": "EDDM", "DUBLIN": "EIDW", "ROME": "LIRF", "MADRID": "LEMD",
    "BANGKOK": "VTBS", "MUMBAI": "VABB", "DELHI": "VIDP", "TAIPEI": "RCTP",
    "CAPE TOWN": "FACT", "WUHAN": "ZHHH", "TORONTO": "CYYZ", "WARSAW": "EPWA", "CAIRO": "HECA", "MOSCOW": "UUEE",
    "NYC": "KJFK", "VANCOUVER": "CYVR", "MONTREAL": "CYUL", "DUBAI": "OMDB", "SHENZHEN": "ZGSZ",
    "HONOLULU": "PHNL", "AUSTIN": "KAUS", "BOSTON": "KBOS", "PHILADELPHIA": "KPHL", "SAN DIEGO": "KSAN",
    "MANCHESTER": "EGCC", "EDINBURGH": "EGPH", "VIENNA": "LOWW", "PRAGUE": "LKPR", "ZURICH": "LSZH", "BRUSSELS": "EBBR",
    "HELSINKI": "EFHK", "OSLO": "ENGM", "COPENHAGEN": "EKCH", "LISBON": "LPPT",
    "OSAKA": "RJBB", "NAGOYA": "RJGG", "KUALA LUMPUR": "WMKK", "JAKARTA": "WIII",
    "CHRISTCHURCH": "NZCH", "WELLINGTON": "NZWN", "ADELAIDE": "YPAD", "BRISBANE": "YBBN",
    "MELBOURNE": "YMML", "PERTH": "YPPH",
    "VEGAS": "KLAS", "ROMA": "LIRF", "MUNCHEN": "EDDM", "BOMBAY": "VABB", "NEW DELHI": "VIDP",
    "FORT WORTH": "KDFW", "DFW": "KDFW",
}

NAME_TO_CODE = dict(CITY_TO_ICAO)


def get_timezone(icao: str) -> ZoneInfo:
    """Get ZoneInfo for an ICAO code."""
    tz_name = ICAO_TIMEZONES.get(icao, "UTC")
    return ZoneInfo(tz_name)


def get_coords(icao: str) -> tuple[float, float] | None:
    """Get (lat, lon) for an ICAO code."""
    return ICAO_COORDS.get(icao)


def city_to_icao(city: str) -> str | None:
    """Convert city name to ICAO code."""
    return CITY_TO_ICAO.get(city.upper())