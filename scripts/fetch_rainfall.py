"""Fetch dekadal CHIRPS v3.0 rainfall for each pilot district's pixel, and
compare each dekad with its 1991-2020 normal. Writes data/rainfall.csv.

    uv run python scripts/fetch_rainfall.py

Reads one 0.05-degree pixel per file with HTTP range requests (the file's
header, its tag directory, and the one compressed row the pixel sits in), so
no raster is downloaded and no raster library is needed. CHC publishes final
CHIRPS around the third week of the following month; dekads not yet in the
final archive come from the preliminary archive and say so in `source`.
"""

import calendar
import csv
import statistics
import struct
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from math import floor
from pathlib import Path

from extension_desk.districts import DISTRICTS

BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0"
FINAL = BASE + "/dekads/africa/tifs/chirps-v3.0.{y}.{m:02d}.{dk}.tif"
PRELIM = BASE + "/prelim/dekads/global/tifs/chirps-v3.0.{y}.{m:02d}.{dk}.tif"
CLIMATOLOGY = (1991, 2020)
KEEP_FROM = date(2023, 7, 1)  # two full rainy seasons plus the current one
# Below this, a percentage anomaly is noise (dry-season dekads), so it is left blank.
MIN_NORMAL_MM = 5.0
WORKERS = 8
OUT = Path(__file__).resolve().parents[1] / "data" / "rainfall.csv"
SOURCE_FINAL = "CHIRPS v3.0 final (Climate Hazards Center, UCSB), 0.05-degree pixel"
SOURCE_PRELIM = "CHIRPS v3.0 preliminary (Climate Hazards Center, UCSB), 0.05-degree pixel; final not yet published"
SOURCE_NORMAL = "normal = 1991-2020 CHIRPS v3.0 final mean"

TIFF_TYPES = {3: ("H", 2), 4: ("I", 4), 12: ("d", 8)}


class NotPublished(Exception):
    pass


def dekad_bounds(year: int, month: int, dekad: int) -> tuple[date, date]:
    start = date(year, month, (dekad - 1) * 10 + 1)
    last = calendar.monthrange(year, month)[1] if dekad == 3 else dekad * 10
    return start, date(year, month, last)


def get_range(url: str, start: int, end: int | None = None) -> bytes:
    rng = f"bytes={start}-" if end is None else f"bytes={start}-{end}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"Range": rng})
            with urllib.request.urlopen(req, timeout=120) as resp:
                if resp.status != 206:
                    raise RuntimeError(f"{url}: server ignored the range request")
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotPublished(url) from None
            if attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def lzw_decode(data: bytes) -> bytes:
    """TIFF LZW: MSB-first variable-width codes with early change."""
    out = bytearray()
    table = [bytes([i]) for i in range(256)] + [b"", b""]
    width, bitbuf, nbits, prev = 9, 0, 0, None
    for byte in data:
        bitbuf = (bitbuf << 8) | byte
        nbits += 8
        while nbits >= width:
            nbits -= width
            code = (bitbuf >> nbits) & ((1 << width) - 1)
            if code == 256:  # clear
                table = table[:258]
                width, prev = 9, None
                continue
            if code == 257:  # end of information
                return bytes(out)
            if prev is None:
                entry = table[code]
            else:
                entry = table[code] if code < len(table) else prev + prev[:1]
                table.append(prev + entry[:1])
            out += entry
            prev = entry
            if len(table) + 1 >= (1 << width) and width < 12:
                width += 1
    return bytes(out)


def read_tags(url: str) -> dict[int, tuple]:
    head = get_range(url, 0, 7)
    if head[:4] != b"II*\x00":
        raise RuntimeError(f"{url}: not a little-endian classic TIFF")
    off = struct.unpack("<I", head[4:8])[0]
    tail = get_range(url, off)

    def at(pos: int, n: int) -> bytes:
        return tail[pos - off:pos - off + n]

    tags = {}
    for i in range(struct.unpack("<H", at(off, 2))[0]):
        tag, typ, count, value = struct.unpack("<HHI4s", at(off + 2 + 12 * i, 12))
        if typ not in TIFF_TYPES:
            continue
        fmt, size = TIFF_TYPES[typ]
        raw = value[:size * count] if size * count <= 4 else at(struct.unpack("<I", value)[0], size * count)
        tags[tag] = struct.unpack("<" + fmt * count, raw)
    return tags


def read_pixels(url: str) -> dict[str, float]:
    """Rainfall (mm) at each district's pixel in one CHIRPS dekad file."""
    t = read_tags(url)
    width, compression = t[256][0], t[259][0]
    if (t[258][0] != 32 or t.get(339, (1,))[0] != 3  # 32-bit float samples
            or compression not in (1, 5) or t.get(317, (1,))[0] != 1
            or t[33922][:2] != (0.0, 0.0)):  # tiepoint anchored at the top-left pixel
        raise RuntimeError(f"{url}: unsupported TIFF layout")
    res = Decimal("0.05")
    if abs(t[33550][0] - float(res)) > 1e-6:
        raise RuntimeError(f"{url}: unexpected pixel size {t[33550][0]}")
    x0 = Decimal(str(round(t[33922][3], 6)))
    y0 = Decimal(str(round(t[33922][4], 6)))
    rows_per_strip = t.get(278, (t[257][0],))[0]
    values = {}
    for key, d in DISTRICTS.items():
        # Exact decimal arithmetic, so a point on a pixel edge always falls in
        # the pixel to its south/east rather than wherever float rounding puts it.
        row = floor((y0 - Decimal(str(d["lat"]))) / res)
        col = floor((Decimal(str(d["lon"])) - x0) / res)
        strip = row // rows_per_strip
        start, length = t[273][strip], t[279][strip]
        raw = get_range(url, start, start + length - 1)
        data = lzw_decode(raw) if compression == 5 else raw
        pos = ((row % rows_per_strip) * width + col) * 4
        value = struct.unpack("<f", data[pos:pos + 4])[0]
        if value < 0:
            raise RuntimeError(f"{url}: no data at {key} (row {row}, col {col})")
        values[key] = value
    return values


def fetch(dekad: tuple[int, int, int], allow_prelim: bool) -> tuple[dict[str, float], str] | None:
    y, m, dk = dekad
    try:
        return read_pixels(FINAL.format(y=y, m=m, dk=dk)), SOURCE_FINAL
    except NotPublished:
        if not allow_prelim:
            raise
    try:
        return read_pixels(PRELIM.format(y=y, m=m, dk=dk)), SOURCE_PRELIM
    except NotPublished:
        return None


def main() -> None:
    today = date.today()
    climatology = [(y, m, dk) for y in range(CLIMATOLOGY[0], CLIMATOLOGY[1] + 1)
                   for m in range(1, 13) for dk in (1, 2, 3)]
    recent = [(y, m, dk) for y in range(KEEP_FROM.year, today.year + 1)
              for m in range(1, 13) for dk in (1, 2, 3)
              if dekad_bounds(y, m, dk)[0] >= KEEP_FROM and dekad_bounds(y, m, dk)[1] < today]

    with ThreadPoolExecutor(WORKERS) as pool:
        # The climatology must come entirely from the final archive.
        normal_values = dict(zip(climatology, pool.map(lambda k: fetch(k, False), climatology)))
        recent_values = dict(zip(recent, pool.map(lambda k: fetch(k, True), recent)))

    years = CLIMATOLOGY[1] - CLIMATOLOGY[0] + 1
    rows = []
    for key in DISTRICTS:
        normals: dict[tuple[int, int], list[float]] = defaultdict(list)
        for (_, m, dk), (values, _) in normal_values.items():
            normals[(m, dk)].append(values[key])
        short = {k: len(v) for k, v in normals.items() if len(v) < years}
        if short:
            print(f"warning: {key} normals with fewer than {years} years: {short}")

        for (y, m, dk) in recent:
            fetched = recent_values[(y, m, dk)]
            if fetched is None:
                # Not published yet; everything after it is newer still.
                break
            values, source = fetched
            start, stop = dekad_bounds(y, m, dk)
            total = round(values[key], 1)
            normal = round(statistics.mean(normals[(m, dk)]), 1)
            anomaly = round((total - normal) / normal * 100) if normal >= MIN_NORMAL_MM else None
            rows.append({
                "record_id": f"rain:{key}:{y}-{m:02d}-d{dk}",
                "district": key,
                "dekad_start": start.isoformat(),
                "dekad_end": stop.isoformat(),
                "rain_mm": total,
                "normal_mm": normal,
                "departure_mm": round(total - normal, 1),
                "anomaly_pct": "" if anomaly is None else anomaly,
                "normal_years": len(normals[(m, dk)]),
                "source": f"{source}; {SOURCE_NORMAL}",
            })

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} dekads to {OUT}")


if __name__ == "__main__":
    main()
