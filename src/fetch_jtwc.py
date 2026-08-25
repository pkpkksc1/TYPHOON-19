#!/usr/bin/env python3
# JTWC Typhoon Fetch v1.0
# Source: NOAA/NWS public relay of JTWC WTPN3x bulletins
# Output: data/jtwc_typhoon.json
#
# Notes:
# - JTWC maximum sustained wind = 1-minute average.
# - Wind radii in JTWC bulletins are valid over open water only.
# - 34/50/64 kt radii are parsed by NE/SE/SW/NW quadrant.
# - No API key required.

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

PARSER_VERSION = "1.0"
BASE_URL = "https://tgftp.nws.noaa.gov/data/raw/wt/wtpn{num}.pgtw..txt"
OUTPUT = Path("data/jtwc_typhoon.json")

# Scan a broad range. Stale bulletins are filtered out by issue time.
BULLETIN_NUMBERS = range(31, 40)
MAX_BULLETIN_AGE_HOURS = 72

KT_TO_MPS = 0.514444
KT_TO_KMH = 1.852
NM_TO_KM = 1.852


def round1(x: float) -> float:
    return round(x, 1)


def fetch_text(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (TyphoonLogisticsDashboard/1.0)",
            "Accept": "text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_coord(token: str) -> float:
    token = token.strip().upper()
    value = float(token[:-1])
    hemi = token[-1]
    if hemi in ("S", "W"):
        value = -value
    return value


def infer_issue_datetime(ddhhmm: str, now: datetime) -> Optional[datetime]:
    """Infer year/month for a WMO DDHHMM issue time."""
    if not re.fullmatch(r"\d{6}", ddhhmm):
        return None

    day = int(ddhhmm[0:2])
    hour = int(ddhhmm[2:4])
    minute = int(ddhhmm[4:6])

    candidates = []
    for month_shift in (-1, 0, 1):
        y = now.year
        m = now.month + month_shift
        if m < 1:
            y -= 1
            m += 12
        elif m > 12:
            y += 1
            m -= 12
        try:
            candidates.append(datetime(y, m, day, hour, minute, tzinfo=timezone.utc))
        except ValueError:
            pass

    if not candidates:
        return None

    return min(candidates, key=lambda d: abs((d - now).total_seconds()))


def parse_radii(text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    pattern = re.compile(
        r"RADIUS OF\s+(034|050|064)\s+KT WINDS\s*-\s*"
        r"(\d+)\s+NM NORTHEAST QUADRANT\s+"
        r"(\d+)\s+NM SOUTHEAST QUADRANT\s+"
        r"(\d+)\s+NM SOUTHWEST QUADRANT\s+"
        r"(\d+)\s+NM NORTHWEST QUADRANT",
        re.I | re.S,
    )

    for m in pattern.finditer(text):
        threshold = int(m.group(1))
        values = {
            "NE": int(m.group(2)),
            "SE": int(m.group(3)),
            "SW": int(m.group(4)),
            "NW": int(m.group(5)),
        }
        key = f"{threshold}kt"
        result[key] = {
            "threshold_kt": threshold,
            "threshold_mps": round1(threshold * KT_TO_MPS),
            "quadrants": {
                q: {"nm": nm, "km": round1(nm * NM_TO_KM)}
                for q, nm in values.items()
            },
            "max_radius_nm": max(values.values()),
            "max_radius_km": round1(max(values.values()) * NM_TO_KM),
        }

    return result


def parse_max_wind(text: str) -> Dict[str, Optional[float]]:
    m = re.search(
        r"MAX SUSTAINED WINDS\s*-\s*(\d+)\s*KT,\s*GUSTS\s*(\d+)\s*KT",
        text,
        re.I,
    )
    if not m:
        return {
            "max_wind_kt": None,
            "max_wind_mps": None,
            "gust_kt": None,
            "gust_mps": None,
        }

    wind = int(m.group(1))
    gust = int(m.group(2))
    return {
        "max_wind_kt": wind,
        "max_wind_mps": round1(wind * KT_TO_MPS),
        "gust_kt": gust,
        "gust_mps": round1(gust * KT_TO_MPS),
    }


def parse_forecasts(text: str) -> list[Dict[str, Any]]:
    forecasts = []

    header_pattern = re.compile(
        r"(?m)^\s*(\d+)\s+HRS,\s*VALID AT:\s*$"
    )
    matches = list(header_pattern.finditer(text))

    for i, m in enumerate(matches):
        hours = int(m.group(1))
        block_start = m.end()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[block_start:block_end]

        pos = re.search(
            r"(\d{6}Z)\s*---\s*([0-9.]+[NS])\s+([0-9.]+[EW])",
            block,
            re.I,
        )
        if not pos:
            continue

        item: Dict[str, Any] = {
            "forecast_hour": hours,
            "valid_time_raw": pos.group(1).upper(),
            "lat": parse_coord(pos.group(2)),
            "lon": parse_coord(pos.group(3)),
        }
        item.update(parse_max_wind(block))
        item["wind_radii"] = parse_radii(block)
        forecasts.append(item)

    return forecasts


def parse_bulletin(text: str, source_url: str, now: datetime) -> Optional[Dict[str, Any]]:
    # Bulletin header, e.g. WTPN31 PGTW 200900
    header = re.search(r"(?m)^(WTPN\d{2})\s+PGTW\s+(\d{6})\s*$", text)
    if not header:
        return None

    bulletin_code = header.group(1)
    issue_ddhhmm = header.group(2)
    issue_dt = infer_issue_datetime(issue_ddhhmm, now)
    if issue_dt is None:
        return None

    age_hours = (now - issue_dt).total_seconds() / 3600.0
    if age_hours < -6 or age_hours > MAX_BULLETIN_AGE_HOURS:
        return None

    subj = re.search(
        r"SUBJ/([A-Z ]+?)\s+(\d{1,2}W)\s+\(([^)]+)\)\s+WARNING NR\s+(\d+)//",
        text,
        re.I,
    )
    if not subj:
        # Ignore bulletins that are not an active named JTWC warning.
        return None

    storm_type = " ".join(subj.group(1).upper().split())
    jtwc_id = subj.group(2).upper()
    name = subj.group(3).upper().strip()
    warning_number = int(subj.group(4))

    pos = re.search(
        r"WARNING POSITION:\s*(\d{6}Z)\s*---\s*NEAR\s+([0-9.]+[NS])\s+([0-9.]+[EW])",
        text,
        re.I | re.S,
    )
    if not pos:
        return None

    present_start = text.find("PRESENT WIND DISTRIBUTION:")
    forecasts_start = text.find("FORECASTS:")
    if present_start >= 0:
        current_block = text[present_start:forecasts_start if forecasts_start > present_start else len(text)]
    else:
        current_block = text

    movement = re.search(
        r"MOVEMENT PAST SIX HOURS\s*-\s*(\d+)\s+DEGREES AT\s+(\d+)\s+KTS",
        text,
        re.I,
    )

    pressure = re.search(
        r"MINIMUM CENTRAL PRESSURE AT\s+\d{6}Z\s+IS\s+(\d+)\s+MB",
        text,
        re.I,
    )

    current: Dict[str, Any] = {
        "valid_time_raw": pos.group(1).upper(),
        "lat": parse_coord(pos.group(2)),
        "lon": parse_coord(pos.group(3)),
        "movement_degrees": int(movement.group(1)) if movement else None,
        "movement_speed_kt": int(movement.group(2)) if movement else None,
        "movement_speed_kmh": round1(int(movement.group(2)) * KT_TO_KMH) if movement else None,
        "pressure_hpa": int(pressure.group(1)) if pressure else None,
    }
    current.update(parse_max_wind(current_block))
    current["wind_radii"] = parse_radii(current_block)

    return {
        "bulletin": bulletin_code,
        "issue_time_utc": issue_dt.isoformat().replace("+00:00", "Z"),
        "age_hours": round1(age_hours),
        "jtwc_id": jtwc_id,
        "name": name,
        "storm_type": storm_type,
        "warning_number": warning_number,
        "source_url": source_url,
        "current": current,
        "forecast": parse_forecasts(text),
    }


def main() -> None:
    now = datetime.now(timezone.utc)
    storms = []
    errors = []

    for num in BULLETIN_NUMBERS:
        url = BASE_URL.format(num=num)
        try:
            text = fetch_text(url)
            storm = parse_bulletin(text, url, now)
            if storm:
                storms.append(storm)
                print(
                    f"[OK] {storm['bulletin']} {storm['jtwc_id']} "
                    f"{storm['name']} warning #{storm['warning_number']}"
                )
            else:
                print(f"[SKIP] WTPN{num}: stale / inactive / unsupported")
        except Exception as e:
            errors.append({"bulletin": f"WTPN{num}", "error": str(e)})
            print(f"[WARN] WTPN{num}: {e}")

    # Deduplicate same JTWC storm if it somehow appears in more than one bulletin.
    latest_by_id: Dict[str, Dict[str, Any]] = {}
    for s in storms:
        key = s["jtwc_id"]
        if key not in latest_by_id or s["issue_time_utc"] > latest_by_id[key]["issue_time_utc"]:
            latest_by_id[key] = s

    storms = sorted(latest_by_id.values(), key=lambda x: x["jtwc_id"])

    payload = {
        "parser_version": PARSER_VERSION,
        "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "source": "NOAA/NWS public relay of JTWC tropical cyclone warnings",
        "source_note": (
            "JTWC maximum sustained winds are one-minute averages. "
            "JTWC wind radii are valid over open water only."
        ),
        "active_count": len(storms),
        "storms": storms,
        "errors": errors,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Updated: {OUTPUT}")
    print(f"Active storms collected: {len(storms)}")

    # Test-friendly summary for current 34 kt radii.
    for s in storms:
        r34 = s.get("current", {}).get("wind_radii", {}).get("34kt")
        if r34:
            q = r34["quadrants"]
            print(
                f"{s['jtwc_id']} {s['name']} 34kt radius km "
                f"NE={q['NE']['km']} SE={q['SE']['km']} "
                f"SW={q['SW']['km']} NW={q['NW']['km']}"
            )


if __name__ == "__main__":
    main()
