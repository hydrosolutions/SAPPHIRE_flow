from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from sapphire_flow.types.observation_units import (
    DischargeSourceUnit,
    SourceDischargeValue,
    normalize_discharge,
)

if TYPE_CHECKING:
    import httpx

    from sapphire_flow.types.datetime import UtcDatetime

_BASE = "https://www.hydrodaten.admin.ch/de/seen-und-fluesse/stationen-und-daten/"
_MAX_BYTES = 2 * 1024 * 1024
_NUMBER = r"[0-9]+(?:[.,][0-9]+)?"


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuThresholdSnapshot:
    station_code: str
    url: str
    retrieved_at: UtcDatetime
    sha256: str
    bounds: tuple[float, float, float, float] | None
    html: str


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []
        self.headings: list[str] = []
        self._heading: list[str] | None = None
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored += 1
        if tag == "h1":
            self._heading = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._ignored = max(0, self._ignored - 1)
        if tag == "h1" and self._heading is not None:
            self.headings.append(" ".join(self._heading))
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        text = " ".join(data.split())
        if text:
            self.tokens.append(text)
            if self._heading is not None:
                self._heading.append(text)


def station_url(station_code: str) -> str:
    if re.fullmatch(r"[0-9]{4}", station_code) is None:
        raise ValueError("BAFU station code must contain four digits")
    return _BASE + station_code


def parse_threshold_page(
    html: str, *, station_code: str, retrieved_at: UtcDatetime
) -> BafuThresholdSnapshot:
    url = station_url(station_code)
    page = _Page()
    page.feed(html)
    station_headings = [h for h in page.headings if re.search(r"\b[0-9]{4}$", h)]
    if len(station_headings) != 1 or not re.search(
        rf"(?:^|\s){station_code}$", station_headings[0]
    ):
        raise ValueError("BAFU page station identity does not match requested station")
    sections = [i for i, token in enumerate(page.tokens) if token == "Gefahrenstufen"]
    bounds: tuple[float, float, float, float] | None = None
    if sections:
        if len(sections) != 1:
            raise ValueError("ambiguous BAFU danger-level sections")
        text = " ".join(page.tokens[sections[0] + 1 :])
        pattern = (
            rf"Abfluss (?P<unit>m³/s|l/s) 5 > ({_NUMBER}) 4 ({_NUMBER}) - ({_NUMBER}) "
            rf"3 ({_NUMBER}) - ({_NUMBER}) 2 ({_NUMBER}) - ({_NUMBER}) "
            rf"1 < ({_NUMBER}) Die 5 Gefahrenstufen"
        )
        match = re.match(pattern, text)
        if match is None:
            raise ValueError("unsupported or malformed BAFU discharge danger bands")
        unit = (
            DischargeSourceUnit.LITRES_PER_SECOND
            if match.group("unit") == "l/s"
            else DischargeSourceUnit.CUBIC_METRES_PER_SECOND
        )
        top, four, four_end, three, three_end, two, two_end, low = (
            normalize_discharge(
                SourceDischargeValue.from_literal(value.replace(",", ".")), unit
            ).value
            for value in match.groups()[1:]
        )
        if (four_end, three_end, two_end, low) != (top, four, three, two):
            raise ValueError("BAFU danger bands are not contiguous")
        if not (0 <= two < three < four < top and math.isfinite(top)):
            raise ValueError(
                "BAFU danger bounds must be finite and strictly increasing"
            )
        bounds = (two, three, four, top)
    return BafuThresholdSnapshot(
        station_code=station_code,
        url=url,
        retrieved_at=retrieved_at,
        sha256=hashlib.sha256(html.encode()).hexdigest(),
        bounds=bounds,
        html=html,
    )


def fetch_threshold_page(
    client: httpx.Client, *, station_code: str, retrieved_at: UtcDatetime
) -> BafuThresholdSnapshot:
    url = station_url(station_code)
    with client.stream("GET", url, timeout=20, follow_redirects=False) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > _MAX_BYTES:
                raise ValueError("BAFU threshold page exceeds size limit")
    return parse_threshold_page(
        body.decode("utf-8"), station_code=station_code, retrieved_at=retrieved_at
    )
