"""Temperature bands (docs/05 section 9).

Temperature is not just randomness: it drives the familiar/discovery quota,
the exploration bonus and how far from a seed a candidate may sit.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemperatureBand:
    lower: int
    upper: int
    familiar_percent: int
    discovery_percent: int
    alpha_min: float
    alpha_max: float

    def alpha(self, temperature: int) -> float:
        span = max(1, self.upper - self.lower)
        position = (temperature - self.lower) / span
        return self.alpha_min + position * (self.alpha_max - self.alpha_min)


BANDS: tuple[TemperatureBand, ...] = (
    TemperatureBand(0, 25, 80, 20, 0.10, 0.25),
    TemperatureBand(26, 60, 55, 45, 0.25, 0.60),
    TemperatureBand(61, 85, 30, 70, 0.60, 1.00),
    TemperatureBand(86, 100, 15, 85, 1.00, 1.30),
)


def band_for(temperature: int) -> TemperatureBand:
    value = max(0, min(100, temperature))
    for band in BANDS:
        if band.lower <= value <= band.upper:
            return band
    return BANDS[-1]


def familiar_quota(temperature: int) -> int:
    return band_for(temperature).familiar_percent


def discovery_quota(temperature: int) -> int:
    return band_for(temperature).discovery_percent


def exploration_alpha(temperature: int) -> float:
    return band_for(temperature).alpha(max(0, min(100, temperature)))


def familiar_target_count(temperature: int, length: int) -> int:
    return round(length * familiar_quota(temperature) / 100)
