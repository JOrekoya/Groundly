"""Resolve references to a concrete property.

Turns "this property", "the last one", or a plain address into coordinates the
comp model can use. Deterministic and offline: geocoding is a tool the caller
supplies, so the resolution rules are testable without a network.

Deictic references ("this", "that one") resolve against session state rather
than being sent to a model. "The last one" has exactly one meaning given a
history, and asking a model to work it out would be slower and less reliable
than a dictionary lookup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

#: "this property", "it", "the last one" — anything pointing at prior context.
DEICTIC_RE = re.compile(
    r"\b(this|that|it|the\s+(last|previous|first)\s+one|the\s+property|"
    r"the\s+same\s+one)\b",
    re.IGNORECASE,
)

#: A US street address: number, street words, optional suffix. Deliberately
#: loose — the geocoder is the real judge of whether something is an address.
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,4}\s+"
    r"(st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|"
    r"pl|place|way|ter|terrace|pkwy|parkway|cir|circle)\b\.?",
    re.IGNORECASE,
)

#: A bare coordinate pair: "41.9484, -87.6553".
COORDS_RE = re.compile(r"(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")


@dataclass(frozen=True)
class ResolvedLocation:
    """Where a reference pointed, and how it was worked out."""

    latitude: float
    longitude: float
    label: str
    source: str


@dataclass(frozen=True)
class Unresolved:
    """The reference could not be pinned to a place."""

    reason: str


class Geocoder(Protocol):
    """Turns an address into coordinates. Supplied by the caller."""

    def geocode(self, address: str) -> tuple[float, float] | None:
        """Return coordinates, or None if the address is not found."""
        ...


class FakeGeocoder:
    """Lookup table standing in for a geocoding service."""

    def __init__(self, known: dict[str, tuple[float, float]] | None = None) -> None:
        self.known = {k.lower(): v for k, v in (known or {}).items()}
        self.calls: list[str] = []

    def geocode(self, address: str) -> tuple[float, float] | None:
        self.calls.append(address)
        return self.known.get(address.strip().lower())


def find_address(text: str) -> str | None:
    """The first thing in a message that looks like a street address."""
    match = ADDRESS_RE.search(text)
    return match.group(0).strip().rstrip(".") if match else None


def find_coordinates(text: str) -> tuple[float, float] | None:
    """An explicit coordinate pair, if one is present and in range."""
    match = COORDS_RE.search(text)
    if not match:
        return None
    latitude, longitude = float(match.group(1)), float(match.group(2))
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def mentions_current_property(text: str) -> bool:
    """Whether the message points at something already in the conversation."""
    return bool(DEICTIC_RE.search(text))


def resolve(
    message: str,
    *,
    current: tuple[float, float] | None = None,
    geocoder: Geocoder | None = None,
) -> ResolvedLocation | Unresolved:
    """Work out which property a message is about.

    Order matters and reflects how specific each signal is: explicit
    coordinates beat an address, an address beats a pronoun, and a pronoun
    beats nothing. A message with no reference at all falls back to whatever
    the session already had, which is what makes a conversation feel continuous.
    """
    coords = find_coordinates(message)
    if coords is not None:
        return ResolvedLocation(coords[0], coords[1], "given coordinates", "coordinates")

    address = find_address(message)
    if address is not None:
        if geocoder is None:
            return Unresolved(f"found the address {address!r} but have no geocoder")
        located = geocoder.geocode(address)
        if located is None:
            return Unresolved(f"could not find {address!r}")
        return ResolvedLocation(located[0], located[1], address, "address")

    if current is not None:
        label = (
            "the property under discussion"
            if mentions_current_property(message)
            else "the current property"
        )
        return ResolvedLocation(current[0], current[1], label, "session")

    if mentions_current_property(message):
        return Unresolved(
            "no property has been set yet, so there is nothing for that to refer to"
        )
    return Unresolved("no property was mentioned")
