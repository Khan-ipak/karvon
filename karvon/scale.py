"""Weighbridge integration: read the current weight from a scale indicator.

Most truck scale indicators send their display as a line of text over RS-232 or
a TCP socket, either continuously or when asked. Typical lines:

    ST,GS,+0031240kg      stable, gross (A&D style, common in many indicators)
    US,GS,+0031180kg      unstable (truck still moving)
    =0031240              no status at all
    S S     31240 kg      Mettler Toledo SICS answer to "SI"

`KARVON_SCALE_URL` tells Karvon where the indicator is:

    tcp://10.0.0.5:4001                 TCP (serial-to-Ethernet converter or the indicator itself)
    serial:///dev/ttyUSB0?baud=9600     RS-232 / USB, needs `pip install "karvon[scale]"`
    simulator                           a fake scale for demos and tests

Add `send=SI%0D%0A` (URL-encoded) for indicators that answer only when asked.
Nothing is kept open: every reading opens the connection, waits for a stable
weight and closes it again.

Try your indicator from the command line before setting it up:

    python -m karvon.scale tcp://10.0.0.5:4001 --raw
"""

from __future__ import annotations

import random
import re
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, unquote, urlsplit

from karvon.services import DomainError

# A signed number, an optional unit after it. Status words before it (ST, US, OL)
# decide whether the reading is stable.
DEFAULT_PATTERN = r"(?P<weight>[-+]?\s*\d+(?:[.,]\d+)?)\s*(?P<unit>kg|t|lb)?\b"
UNSTABLE = re.compile(r"\b(US|OL|MO)\b|\bS\s+D\b", re.IGNORECASE)
STABLE = re.compile(r"\b(ST|S\s+S)\b", re.IGNORECASE)
UNIT_KG = {"kg": Decimal(1), "t": Decimal(1000), "lb": Decimal("0.45359237")}
# Without a status word a reading counts as stable when this many lines in a row agree.
SAME_READINGS = 3


class ScaleError(DomainError):
    pass


@dataclass(frozen=True)
class Reading:
    weight_kg: Decimal
    stable: bool | None  # None: the line does not say
    raw: str


def parse_line(line: str, pattern: str = DEFAULT_PATTERN) -> Reading | None:
    """One indicator line as a reading, or None if it holds no weight.

    `stable` is True or False when the line says so, and None when it does not.
    """
    raw = line.strip()
    match = re.search(pattern, raw)
    if not match:
        return None
    try:
        value = Decimal(re.sub(r"\s", "", match.group("weight")).replace(",", "."))
    except InvalidOperation:
        return None
    unit = (match.groupdict().get("unit") or "kg").lower()
    weight = (value * UNIT_KG.get(unit, Decimal(1))).quantize(Decimal("1"))
    status = raw[: match.start()] + raw[match.end() :]
    if UNSTABLE.search(status):
        stable: bool | None = False
    elif STABLE.search(status):
        stable = True
    else:
        stable = None
    return Reading(weight_kg=weight, stable=stable, raw=raw)


def first_stable(lines: Iterator[str], pattern: str = DEFAULT_PATTERN) -> Reading:
    """The first stable reading in a stream of lines."""
    last: Reading | None = None
    same = 0
    for line in lines:
        reading = parse_line(line, pattern)
        if reading is None:
            continue
        if reading.stable is True:
            return reading
        if reading.stable is False:
            last, same = reading, 0
            continue
        same = same + 1 if last is not None and last.weight_kg == reading.weight_kg else 1
        last = reading
        if same >= SAME_READINGS:
            return Reading(reading.weight_kg, True, reading.raw)
    if last is None:
        raise ScaleError("scale_no_data", "No weight from the scale")
    raise ScaleError("scale_unstable", f"The weight is not stable yet ({last.weight_kg} kg)")


# --- sources -----------------------------------------------------------------


def _deadline_lines(chunks: Iterator[bytes], timeout: float) -> Iterator[str]:
    """Split a byte stream into text lines until `timeout` seconds have passed."""
    end = time.monotonic() + timeout
    buffer = b""
    for chunk in chunks:
        buffer += chunk
        *lines, buffer = re.split(rb"[\r\n\x02\x03]+", buffer)
        for line in lines:
            if line.strip():
                yield line.decode("ascii", "replace")
        if time.monotonic() > end:
            return


def _tcp(host: str, port: int, send: bytes, timeout: float) -> Iterator[str]:
    try:
        conn = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise ScaleError(
            "scale_offline", f"Cannot reach the scale at {host}:{port}: {exc}"
        ) from exc

    def chunks() -> Iterator[bytes]:
        with conn:
            if send:
                conn.sendall(send)
            while True:
                try:
                    data = conn.recv(256)
                except TimeoutError:
                    return
                if not data:
                    return
                yield data

    return _deadline_lines(chunks(), timeout)


def _serial(port: str, baud: int, send: bytes, timeout: float) -> Iterator[str]:
    try:
        import serial  # pyserial, optional
    except ImportError as exc:
        raise ScaleError(
            "scale_offline", 'Serial scales need pyserial: pip install "karvon[scale]"'
        ) from exc
    try:
        conn = serial.Serial(port, baud, timeout=0.5)
    except (OSError, serial.SerialException) as exc:
        raise ScaleError("scale_offline", f"Cannot open {port}: {exc}") from exc

    def chunks() -> Iterator[bytes]:
        with conn:
            if send:
                conn.write(send)
            while True:
                yield conn.read(256) or b""

    return _deadline_lines(chunks(), timeout)


def _simulator(hint: str) -> Iterator[str]:
    """A truck settling on the scale: a few moving readings, then a stable one."""
    low, high = (13500, 16500) if hint == "tare" else (30000, 44000)
    weight = random.randrange(low, high, 20)
    for offset in (-240, 120, -40):
        yield f"US,GS,+{weight + offset:07d}kg"
    while True:
        yield f"ST,GS,+{weight:07d}kg"


def lines_from(url: str, *, timeout: float = 5.0, hint: str = "gross") -> Iterator[str]:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    send = unquote(query.get("send", [""])[0]).encode("ascii")
    match parts.scheme or parts.path:
        case "simulator" | "sim":
            return _simulator(hint)
        case "tcp":
            if not parts.hostname or not parts.port:
                raise ScaleError("scale_config", f"Need tcp://host:port, got {url!r}")
            return _tcp(parts.hostname, parts.port, send, timeout)
        case "serial":
            baud = int(query.get("baud", ["9600"])[0])
            return _serial(parts.path, baud, send, timeout)
    raise ScaleError("scale_config", f"Unknown scale URL {url!r}")


def read(
    url: str, *, pattern: str | None = None, timeout: float = 5.0, hint: str = "gross"
) -> Reading:
    """Wait up to `timeout` seconds for a stable weight from the scale at `url`."""
    return first_stable(lines_from(url, timeout=timeout, hint=hint), pattern or DEFAULT_PATTERN)


def read_configured(settings, hint: str = "gross") -> Reading:
    """A reading from the scale in the app's Settings (KARVON_SCALE_*)."""
    if not settings.scale_url:
        raise ScaleError("scale_not_configured", "No scale: set KARVON_SCALE_URL")
    return read(
        settings.scale_url,
        pattern=settings.scale_pattern,
        timeout=settings.scale_timeout,
        hint=hint,
    )


def main(argv: list[str] | None = None) -> None:
    """Print what an indicator sends, to check the URL and the parsing."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m karvon.scale", description=main.__doc__)
    parser.add_argument("url", help="tcp://host:port, serial:///dev/ttyUSB0?baud=9600 or simulator")
    parser.add_argument("--raw", action="store_true", help="print every line as it arrives")
    parser.add_argument("--pattern", help="regex with a (?P<weight>...) group")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        if args.raw:
            for line in lines_from(args.url, timeout=args.timeout):
                reading = parse_line(line, args.pattern or DEFAULT_PATTERN)
                print(
                    f"{line!r:40} -> {reading.weight_kg if reading else '-'} kg, "
                    f"stable={reading.stable if reading else '-'}"
                )
            return
        reading = read(args.url, pattern=args.pattern, timeout=args.timeout)
    except ScaleError as exc:
        raise SystemExit(f"error: {exc.message}") from exc
    print(f"{reading.weight_kg} kg  (from {reading.raw!r})")


if __name__ == "__main__":
    main()
