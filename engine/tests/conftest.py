from __future__ import annotations

import numpy as np
import pytest

from talus.models import Streams, Thresholds
from talus.units import M_PER_MI


@pytest.fixture
def athlete() -> Thresholds:
    """The spec's sample athlete."""
    return Thresholds(resting=45, aet=150, ant=172, max=190)


def make_streams(legs, hr=140.0, cadence=165.0, barometric=True, noise_m=0.0, seed=0) -> Streams:
    """1 Hz streams over legs of (miles, grade %, speed m/s)."""
    t, d, alt = [0.0], [0.0], [1500.0]
    for miles, grade, speed in legs:
        n = int(round(miles * M_PER_MI / speed))
        for _ in range(n):
            t.append(t[-1] + 1)
            d.append(d[-1] + speed)
            alt.append(alt[-1] + speed * grade / 100)
    alt = np.array(alt)
    if noise_m:
        alt = alt + np.random.default_rng(seed).normal(0, noise_m, alt.size)
    n = len(t)
    return Streams(
        time=np.array(t), distance=np.array(d), altitude=alt,
        heart_rate=None if hr is None else np.full(n, float(hr)),
        cadence=None if cadence is None else np.full(n, float(cadence)),
        barometric=barometric,
    )
