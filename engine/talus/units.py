"""Unit conversions. The engine works in SI internally; everything shown is mi and ft."""

M_PER_MI = 1609.344
FT_PER_M = 3.280839895


def mi(meters: float) -> float:
    return meters / M_PER_MI


def ft(meters: float) -> float:
    return meters * FT_PER_M


def m_from_ft(feet: float) -> float:
    return feet / FT_PER_M
