"""UBX-CFG-GNSS command constants for the u-blox NEO-7M.

The NEO-7M (u-blox 7) has a **single RF down-converter**, so it can only tune
one band at a time: GPS L1 (1575.42 MHz) *or* GLONASS L1 (1602 MHz). It cannot
track both constellations simultaneously (that requires u-blox 8 / M8). These
pre-built ``UBX-CFG-GNSS`` (class ``0x06``, ID ``0x3E``) byte strings switch
the active constellation.

The byte strings were verified working over serial (not generated with
``pyubx2``), so they are hardcoded here as constants.

After a switch the receiver reports NMEA status ``V`` (void) while it
re-acquires; that is normal. Note the NEO-7M reports satellites under the
``GP`` talker even in GLONASS mode, so check the PRN (65-88 = GLONASS), not
the talker ID.
"""

# Enable GPS L1 only (disable GLONASS).
UBX_CFG_GNSS_GPS = bytes.fromhex(
    "B562063E2400001616040004FF0001000000010103000100000005000300010000000608FF0000000000B813"
)

# Enable GLONASS L1 only (disable GPS).
UBX_CFG_GNSS_GLO = bytes.fromhex(
    "B562063E2400000016040004FF0000000001010103000000000105000300000000010608FF0001000001A40D"
)

# Map of constellation name -> UBX-CFG-GNSS bytes.
CONSTELLATIONS = {
    "GPS": UBX_CFG_GNSS_GPS,
    "GLONASS": UBX_CFG_GNSS_GLO,
}

# The constellation used when no selection has been made yet.
DEFAULT_CONSTELLATION = "GPS"


def command_bytes(constellation: str) -> bytes:
    """Return the UBX-CFG-GNSS bytes for the given constellation name.

    :param constellation: ``"GPS"`` or ``"GLONASS"`` (case-insensitive).
    :raises ValueError: if the constellation is unknown.
    """
    try:
        return CONSTELLATIONS[constellation.upper()]
    except KeyError:
        raise ValueError(f"Unknown constellation: {constellation!r}")
