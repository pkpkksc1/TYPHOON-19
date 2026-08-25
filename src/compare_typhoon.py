#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SBLC Typhoon Dashboard - Step 3
JMA vs KMA forecast comparison

Inputs:
    data/jma_typhoon.json
    data/kma_typhoon.json

Output:
    data/typhoon_compare.json

Simple interpretation:
    <= 50 km   : GREEN  / 거의 일치
    <= 150 km  : YELLOW / 차이 있음
    > 150 km   : RED    / 차이 큼

Uses Python standard library only.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]

JMA_PATH = BASE_DIR / "data" / "jma_typhoon.json"
KMA_PATH = BASE_DIR / "data" / "kma_typhoon.json"
OUTPUT_PATH = BASE_DIR / "data" / "typhoon_compare.json"

TARGET_TYPHOON_NUMBER = "2619"
TARGET_TYPHOON_NAME = "NARRA"

PARSER_VERSION = "3.2-NARRA-HARDLOCK"

# Simple thresholds for the dashboard.
GREEN_MAX_KM = 50
YELLOW_MAX_KM = 150

# KMA forecast time does not always exactly match JMA forecast time.
# Match to the nearest KMA forecast within this window.
MAX_TIME_GAP_HOURS = 12

KST = timezone(timedelta(hours=9))


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None

    s = str(value).strip()

    # ISO 8601 used by JMA.
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    # Numeric KMA formats.
    digits = "".join(ch for ch in s if ch.isdigit())

    formats = {
        12: "%Y%m%d%H%M",
        10: "%Y%m%d%H",
        8: "%Y%m%d",
    }

    fmt = formats.get(len(digits))
    if fmt:
        try:
            dt = datetime.strptime(digits, fmt).replace(tzinfo=KST)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    return None


def to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    r = 6371.0088

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dl / 2) ** 2
    )

    return 2 * r * math.asin(math.sqrt(a))


def status_from_distance(distance_km: float) -> Dict[str, str]:
    if distance_km <= GREEN_MAX_KM:
        return {
            "level": "GREEN",
            "emoji": "🟢",
            "label_ko": "거의 일치",
        }

    if distance_km <= YELLOW_MAX_KM:
        return {
            "level": "YELLOW",
            "emoji": "🟡",
            "label_ko": "차이 있음",
        }

    return {
        "level": "RED",
        "emoji": "🔴",
        "label_ko": "차이 큼",
    }


def get_jma_primary_typhoon(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return ONLY JMA 2619 NARRA."""
    typhoons = data.get("typhoons", [])

    if not isinstance(typhoons, list):
        return None

    for item in typhoons:
        if not isinstance(item, dict):
            continue

        meta = item.get("typhoon") or {}

        if (
            str(meta.get("number") or "").strip()
            == TARGET_TYPHOON_NUMBER
            and str(meta.get("name") or "").strip().upper()
            == TARGET_TYPHOON_NAME
        ):
            return item

    return None


def normalize_jma_forecasts(
    typhoon: Dict[str, Any],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    forecasts = typhoon.get("forecast", [])

    if not isinstance(forecasts, list):
        return out

    for item in forecasts:
        if not isinstance(item, dict):
            continue

        lat = to_float(item.get("lat"))
        lon = to_float(item.get("lon"))
        dt = parse_time(item.get("time"))

        if lat is None or lon is None or dt is None:
            continue

        out.append(
            {
                "forecast_hour": item.get("forecast_hour"),
                "time": dt,
                "time_original": item.get("time"),
                "lat": lat,
                "lon": lon,
            }
        )

    return out


def normalize_kma_forecasts(
    data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    forecasts = data.get("forecast", [])

    if not isinstance(forecasts, list):
        return out

    for item in forecasts:
        if not isinstance(item, dict):
            continue

        # First try normalized fields.
        lat = to_float(item.get("lat"))
        lon = to_float(item.get("lon"))
        time_value = item.get("forecast_time")

        # If parser did not yet normalize the exact KMA field names,
        # inspect raw_item as a fallback.
        raw = item.get("raw_item")
        if isinstance(raw, dict):
            if lat is None:
                lat = to_float(
                    raw.get("lat")
                    or raw.get("latitude")
                    or raw.get("typLat")
                )

            if lon is None:
                lon = to_float(
                    raw.get("lon")
                    or raw.get("longitude")
                    or raw.get("typLon")
                )

            if time_value in (None, ""):
                time_value = (
                    raw.get("tmEf")
                    or raw.get("forecastTime")
                    or raw.get("tmFcst")
                )

        dt = parse_time(time_value)

        if lat is None or lon is None or dt is None:
            continue

        out.append(
            {
                "time": dt,
                "time_original": time_value,
                "lat": lat,
                "lon": lon,
            }
        )

    out.sort(key=lambda x: x["time"])

    return out


def nearest_kma_point(
    jma_time: datetime,
    kma_points: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    if not kma_points:
        return None, None

    best = None
    best_gap_hours = None

    for point in kma_points:
        gap_hours = abs(
            (point["time"] - jma_time).total_seconds()
        ) / 3600.0

        if best_gap_hours is None or gap_hours < best_gap_hours:
            best = point
            best_gap_hours = gap_hours

    if (
        best is None
        or best_gap_hours is None
        or best_gap_hours > MAX_TIME_GAP_HOURS
    ):
        return None, best_gap_hours

    return best, best_gap_hours


def make_comparison(
    jma_data: Dict[str, Any],
    kma_data: Dict[str, Any],
) -> Dict[str, Any]:
    jma_typhoon = get_jma_primary_typhoon(jma_data)

    if not jma_typhoon:
        return {
            "status": "NO_JMA_TYPHOON",
            "message_ko": "JMA 2619 NARRA 비교자료 없음",
            "comparisons": [],
        }

    jma_points = normalize_jma_forecasts(jma_typhoon)
    kma_points = normalize_kma_forecasts(kma_data)

    comparisons: List[Dict[str, Any]] = []

    for jma in jma_points:
        kma, gap_hours = nearest_kma_point(
            jma["time"],
            kma_points,
        )

        if not kma:
            comparisons.append(
                {
                    "forecast_hour": jma.get("forecast_hour"),
                    "jma_time": jma["time_original"],
                    "status": {
                        "level": "NO_DATA",
                        "emoji": "⚪",
                        "label_ko": "KMA 비교자료 없음",
                    },
                    "time_gap_hours": (
                        round(gap_hours, 1)
                        if gap_hours is not None
                        else None
                    ),
                }
            )
            continue

        distance = haversine_km(
            jma["lat"],
            jma["lon"],
            kma["lat"],
            kma["lon"],
        )

        status = status_from_distance(distance)

        comparisons.append(
            {
                "forecast_hour": jma.get("forecast_hour"),
                "jma_time": jma["time_original"],
                "kma_time": kma["time_original"],
                "time_gap_hours": round(gap_hours or 0, 1),
                "jma": {
                    "lat": jma["lat"],
                    "lon": jma["lon"],
                },
                "kma": {
                    "lat": kma["lat"],
                    "lon": kma["lon"],
                },
                "difference_km": round(distance),
                "status": status,
            }
        )

    valid = [
        x
        for x in comparisons
        if isinstance(x.get("difference_km"), (int, float))
    ]

    if valid:
        average_difference = round(
            sum(x["difference_km"] for x in valid)
            / len(valid)
        )
        max_difference = max(
            x["difference_km"] for x in valid
        )

        overall = status_from_distance(
            average_difference
        )
    else:
        average_difference = None
        max_difference = None
        overall = {
            "level": "NO_DATA",
            "emoji": "⚪",
            "label_ko": "비교자료 없음",
        }

    typhoon_meta = jma_typhoon.get("typhoon", {})

    return {
        "status": "OK",
        "typhoon": {
            "number": typhoon_meta.get("number"),
            "name": typhoon_meta.get("name"),
        },
        "summary": {
            "average_difference_km": average_difference,
            "max_difference_km": max_difference,
            "overall": overall,
        },
        "comparisons": comparisons,
    }


def semantic_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    clone = json.loads(
        json.dumps(data, ensure_ascii=False)
    )
    clone.pop("generated_at_utc", None)
    return clone


def write_if_changed(data: Dict[str, Any]) -> bool:
    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if OUTPUT_PATH.exists():
        try:
            old = load_json(OUTPUT_PATH)

            if semantic_payload(old) == semantic_payload(data):
                print("No comparison data change.")
                return False

        except Exception:
            pass

    data["generated_at_utc"] = (
        datetime.now(timezone.utc).isoformat()
    )

    OUTPUT_PATH.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Updated: {OUTPUT_PATH}")
    return True


def main() -> int:
    print(f"Typhoon comparison parser: {PARSER_VERSION}")

    jma_data = load_json(JMA_PATH)
    kma_data = load_json(KMA_PATH)

    result = make_comparison(
        jma_data,
        kma_data,
    )

    output = {
        "source": "JMA + KMA",
        "product": "Typhoon Forecast Comparison",
        "parser_version": PARSER_VERSION,
        **result,
    }

    write_if_changed(output)

    summary = output.get("summary", {})
    overall = summary.get("overall", {})

    if overall:
        print(
            "Overall: "
            f"{overall.get('emoji', '')} "
            f"{overall.get('label_ko', '')}"
        )

    print(
        "Comparison points: "
        f"{len(output.get('comparisons', []))}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
