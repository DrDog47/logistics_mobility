"""Shared form helpers."""

from __future__ import annotations

import pycountry


def country_choices() -> list[tuple[str, str]]:
    """ISO 3166-1 alpha-3 country choices, sorted by display label."""
    return sorted(
        ((c.alpha_3, f"{c.alpha_3} — {c.name}") for c in pycountry.countries),
        key=lambda x: x[1],
    )