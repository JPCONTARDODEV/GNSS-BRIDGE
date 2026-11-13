"""NMEA 0183 parser for GGA + RMC sentences (using pynmea2).

Wraps pynmea2 to parse the u-blox NEO-7M's NMEA output into a single
GpsFix dataclass. pynmea2 handles checksums, validation, and coordinate
conversion; we aggregate GGA + RMC into one fix.

Pure Python (pynmea2 is pure Python), testable on a PC.
"""

from dataclasses import dataclass
from typing import Optional

import pynmea2


@dataclass
class GpsFix:
    """A single GPS fix, aggregated from GGA + RMC sentences."""

    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude: Optional[float] = None
    speed_kmh: Optional[float] = None
    course: Optional[float] = None
    sats: Optional[int] = None
    hdop: Optional[float] = None
    fix: bool = False
    fix_quality: int = 0
    timestamp: Optional[str] = None  # "YYYY-MM-DD HH:MM:SS"
    date: Optional[str] = None  # "YYYY-MM-DD"
    time: Optional[str] = None  # "HH:MM:SS"

    def is_valid(self) -> bool:
        return self.fix and self.lat is not None and self.lon is not None

    def to_dict(self) -> dict:
        return {
            "lat": self.lat,
            "lon": self.lon,
            "altitude": self.altitude,
            "speed_kmh": self.speed_kmh,
            "course": self.course,
            "sats": self.sats,
            "hdop": self.hdop,
            "fix": self.fix,
            "fix_quality": self.fix_quality,
            "timestamp": self.timestamp,
            "date": self.date,
            "time": self.time,
        }


class NmeaParser:
    """Incremental NMEA parser (wraps pynmea2).

    Feed raw lines (with or without the trailing newline) via .feed().
    The aggregated fix is available in .fix.
    """

    def __init__(self):
        self.fix = GpsFix()

    def feed(self, line: str) -> Optional[GpsFix]:
        """Feed a single NMEA sentence. Returns the updated fix if the
        sentence was GGA or RMC and parsed successfully, else None."""
        line = line.strip()
        if not line.startswith("$"):
            return None
        try:
            sentence = pynmea2.parse(line)
        except pynmea2.ChecksumError:
            return None
        except Exception:
            return None

        if sentence.sentence_type == "GGA":
            self._parse_gga(sentence)
            return self.fix
        if sentence.sentence_type == "RMC":
            self._parse_rmc(sentence)
            return self.fix
        return None

    def _parse_gga(self, s):
        try:
            quality = int(s.gps_qual) if s.gps_qual else 0
        except (ValueError, TypeError):
            quality = 0
        self.fix.fix_quality = quality

        if quality > 0:
            self.fix.fix = True
            self.fix.lat = float(s.latitude)
            self.fix.lon = float(s.longitude)
            if s.num_sats:
                try:
                    self.fix.sats = int(s.num_sats)
                except (ValueError, TypeError):
                    pass
            if s.horizontal_dil:
                try:
                    self.fix.hdop = float(s.horizontal_dil)
                except (ValueError, TypeError):
                    pass
            if s.altitude:
                try:
                    self.fix.altitude = float(s.altitude)
                except (ValueError, TypeError):
                    pass
            if s.timestamp:
                self.fix.time = s.timestamp.strftime("%H:%M:%S")
        else:
            self.fix.fix = False

    def _parse_rmc(self, s):
        if not s.is_valid:
            self.fix.fix = False
            return
        self.fix.fix = True
        self.fix.lat = float(s.latitude)
        self.fix.lon = float(s.longitude)
        if s.spd_over_grnd:
            try:
                self.fix.speed_kmh = round(float(s.spd_over_grnd) * 1.852, 1)
            except (ValueError, TypeError):
                pass
        if s.true_course:
            try:
                self.fix.course = float(s.true_course)
            except (ValueError, TypeError):
                pass
        if s.datetime:
            self.fix.date = s.datetime.strftime("%Y-%m-%d")
            self.fix.time = s.datetime.strftime("%H:%M:%S")
            self.fix.timestamp = s.datetime.strftime("%Y-%m-%d %H:%M:%S")


def parse_stream(text: str) -> GpsFix:
    """Parse a block of NMEA text (multiple lines) into a single GpsFix."""
    parser = NmeaParser()
    for line in text.splitlines():
        parser.feed(line)
    return parser.fix
