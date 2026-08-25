#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SBLC Typhoon Dashboard - Step 4.3
Logistics hub / route impact using JTWC wind radii.

Inputs:
    data/jma_typhoon.json
    data/typhoon_compare.json
    data/jtwc_typhoon.json

Output:
    data/typhoon_impact.json

Primary risk logic:
    50/64 kt wind field inside  -> RED    / 매우 높음
    34 kt wind field inside     -> RED    / 높음
    within 1.5 x 34 kt radius   -> YELLOW / 주의
    farther than 1.5 x radius   -> GREEN  / 낮음

The radius is selected by the hub's direction from the storm center:
NE / SE / SW / NW.

IMPORTANT:
- JTWC maximum sustained wind is a 1-minute average.
- JTWC wind radii are stated as valid over open water only.
- This is an internal logistics alert rule, not an airport closure rule.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[1]

JMA_PATH = BASE_DIR / "data" / "jma_typhoon.json"
COMPARE_PATH = BASE_DIR / "data" / "typhoon_compare.json"
JTWC_PATH = BASE_DIR / "data" / "jtwc_typhoon.json"
OUTPUT_PATH = BASE_DIR / "data" / "typhoon_impact.json"

PARSER_VERSION = "4.6-NARRA-HARDLOCK"
CAUTION_RADIUS_MULTIPLIER = 1.5

# This dashboard is intentionally locked to Typhoon 2619 NARRA.
# Other tropical cyclones may exist in source JSON files, but they
# must never be used for logistics distance / wind-radius calculations.
TARGET_TYPHOON_NUMBER = "2619"
TARGET_TYPHOON_NAME = "NARRA"

# Used only if JTWC radii are unavailable.
FALLBACK_RED_MAX_KM = 300
FALLBACK_YELLOW_MAX_KM = 700

LOCATION_ORDER = ["SUZHOU", "PVG", "ICN", "MNL", "HAN", "CRK"]

FALLBACK_LOCATIONS = {
    "SUZHOU": {
        "name_ko": "쑤저우",
        "lat": 31.2989,
        "lon": 120.5853,
    },
    "PVG": {
        "name_ko": "푸동 국제공항",
        "lat": 31.1443,
        "lon": 121.8083,
    },
    "ICN": {
        "name_ko": "인천 국제공항",
        "lat": 37.4602,
        "lon": 126.4407,
    },
    "MNL": {
        "name_ko": "마닐라 국제공항",
        "lat": 14.5086,
        "lon": 121.0198,
    },
    "HAN": {
        "name_ko": "하노이 노이바이 국제공항",
        "lat": 21.2211,
        "lon": 105.8070,
    },
    "CRK": {
        "name_ko": "클락 국제공항",
        "lat": 15.1859,
        "lon": 120.5603,
    },
}

ROUTES = [
    {
        "code": "SUZHOU_PVG_ICN",
        "name_ko": "쑤저우 → PVG → 한국",
        "locations": ["SUZHOU", "PVG", "ICN"],
    },
    {
        "code": "SUZHOU_PVG_MNL",
        "name_ko": "쑤저우 → PVG → 마닐라",
        "locations": ["SUZHOU", "PVG", "MNL"],
    },
    {
        "code": "SUZHOU_PVG_HAN",
        "name_ko": "쑤저우 → PVG → 하노이",
        "locations": ["SUZHOU", "PVG", "HAN"],
    },
    {
        "code": "SUZHOU_PVG_CRK",
        "name_ko": "쑤저우 → PVG → 클락",
        "locations": ["SUZHOU", "PVG", "CRK"],
    },
    {
        "code": "ICN_PVG",
        "name_ko": "한국 → PVG",
        "locations": ["ICN", "PVG"],
    },
]


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_optional(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:
        return {}


def to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def infer_jtwc_valid_time(raw: Any, issue_time_utc: Any) -> Optional[str]:
    """
    Convert JTWC DDHHMMZ (e.g. 250600Z) to ISO UTC using
    issue_time_utc to infer year/month.
    """
    if not raw:
        return None

    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) != 6:
        return None

    issue = parse_iso(issue_time_utc)
    if issue is None:
        return None

    day = int(digits[0:2])
    hour = int(digits[2:4])
    minute = int(digits[4:6])

    candidates: List[datetime] = []

    for month_shift in (-1, 0, 1):
        year = issue.year
        month = issue.month + month_shift

        if month < 1:
            year -= 1
            month += 12
        elif month > 12:
            year += 1
            month -= 12

        try:
            candidates.append(
                datetime(
                    year, month, day, hour, minute,
                    tzinfo=timezone.utc,
                )
            )
        except ValueError:
            pass

    if not candidates:
        return None

    best = min(
        candidates,
        key=lambda d: abs((d - issue).total_seconds()),
    )
    return best.isoformat().replace("+00:00", "Z")


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


def initial_bearing_degrees(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Initial great-circle bearing from storm center to hub."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)

    y = math.sin(dl) * math.cos(p2)
    x = (
        math.cos(p1) * math.sin(p2)
        - math.sin(p1) * math.cos(p2) * math.cos(dl)
    )

    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0


def quadrant_from_bearing(bearing: float) -> str:
    if 0 <= bearing < 90:
        return "NE"
    if 90 <= bearing < 180:
        return "SE"
    if 180 <= bearing < 270:
        return "SW"
    return "NW"


def radius_km(
    wind_radii: Dict[str, Any],
    threshold: str,
    quadrant: str,
) -> Optional[float]:
    item = wind_radii.get(threshold, {})
    quadrants = item.get("quadrants", {})
    q = quadrants.get(quadrant, {})

    value = to_float(q.get("km"))
    # JTWC can explicitly report 0 NM for a quadrant.
    # 0 means "no wind radius in this quadrant", not missing data.
    if value is None or value < 0:
        return None
    return value


def risk_object(
    level: str,
    label_ko: str,
    severity_rank: int,
    basis: str,
) -> Dict[str, Any]:
    emoji = {
        "GREEN": "🟢",
        "YELLOW": "🟡",
        "RED": "🔴",
        "NO_DATA": "⚪",
    }.get(level, "⚪")

    return {
        "level": level,
        "emoji": emoji,
        "label_ko": label_ko,
        "severity_rank": severity_rank,
        "basis": basis,
    }


def risk_from_wind_radii(
    distance_km: float,
    r34: Optional[float],
    r50: Optional[float],
    r64: Optional[float],
) -> Dict[str, Any]:
    # 50 kt or 64 kt zone: strongest logistics alert.
    if r64 is not None and distance_km <= r64:
        return risk_object(
            "RED", "매우 높음", 4, "64kt 폭풍 영향권 내부"
        )

    if r50 is not None and distance_km <= r50:
        return risk_object(
            "RED", "매우 높음", 4, "50kt 폭풍 영향권 내부"
        )

    if r34 is not None and distance_km <= r34:
        return risk_object(
            "RED", "높음", 3, "34kt 강풍 영향권 내부"
        )

    if (
        r34 is not None
        and distance_km <= r34 * CAUTION_RADIUS_MULTIPLIER
    ):
        return risk_object(
            "YELLOW", "주의", 2,
            f"34kt 강풍반경의 {CAUTION_RADIUS_MULTIPLIER:.1f}배 이내"
        )

    return risk_object(
        "GREEN", "낮음", 1, "34kt 강풍 영향권과 충분히 떨어짐"
    )


def fallback_risk_from_distance(
    distance_km: Optional[float],
) -> Dict[str, Any]:
    if distance_km is None:
        return risk_object(
            "NO_DATA", "자료 없음", 0, "거리 자료 없음"
        )

    if distance_km <= FALLBACK_RED_MAX_KM:
        return risk_object(
            "RED", "높음", 3, "JTWC 미확보: 중심거리 임시 기준"
        )

    if distance_km <= FALLBACK_YELLOW_MAX_KM:
        return risk_object(
            "YELLOW", "주의", 2, "JTWC 미확보: 중심거리 임시 기준"
        )

    return risk_object(
        "GREEN", "낮음", 1, "JTWC 미확보: 중심거리 임시 기준"
    )


def risk_rank(item: Dict[str, Any]) -> int:
    risk = item.get("risk", {})
    return int(risk.get("severity_rank", 0) or 0)


def get_primary_typhoon(jma: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Return ONLY JMA Typhoon 2619 NARRA.
    Never fall back to another tropical system.
    """
    typhoons = jma.get("typhoons", [])

    if not isinstance(typhoons, list):
        return None

    for item in typhoons:
        if not isinstance(item, dict):
            continue

        meta = item.get("typhoon") or {}
        number = str(meta.get("number") or "").strip()
        name = str(meta.get("name") or "").strip().upper()

        if (
            number == TARGET_TYPHOON_NUMBER
            and name == TARGET_TYPHOON_NAME
        ):
            return item

    return None


def get_locations(jma: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = jma.get("locations", {})
    result: Dict[str, Dict[str, Any]] = {}

    for code in LOCATION_ORDER:
        source = raw.get(code, {}) if isinstance(raw, dict) else {}
        fallback = FALLBACK_LOCATIONS[code]

        result[code] = {
            "name_ko": source.get(
                "name_ko", fallback["name_ko"]
            ),
            "lat": to_float(source.get("lat"))
            if to_float(source.get("lat")) is not None
            else fallback["lat"],
            "lon": to_float(source.get("lon"))
            if to_float(source.get("lon")) is not None
            else fallback["lon"],
        }

    return result


def find_matching_jtwc_storm(
    jtwc: Dict[str, Any],
    jma_typhoon: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Return ONLY JTWC NARRA.
    No 'single active storm' fallback is allowed.
    """
    meta = jma_typhoon.get("typhoon") or {}

    if (
        str(meta.get("number") or "").strip()
        != TARGET_TYPHOON_NUMBER
        or str(meta.get("name") or "").strip().upper()
        != TARGET_TYPHOON_NAME
    ):
        return None

    storms = jtwc.get("storms", [])
    if not isinstance(storms, list):
        return None

    candidates = [
        storm
        for storm in storms
        if isinstance(storm, dict)
        and str(storm.get("name") or "").strip().upper()
        == TARGET_TYPHOON_NAME
    ]

    if not candidates:
        return None

    # If duplicate NARRA bulletins exist, use the newest warning.
    return max(
        candidates,
        key=lambda storm: str(storm.get("issue_time_utc") or ""),
    )


def build_jtwc_timeline(storm: Dict[str, Any]) -> List[Dict[str, Any]]:
    issue_time = storm.get("issue_time_utc")
    timeline: List[Dict[str, Any]] = []

    current = storm.get("current")
    if isinstance(current, dict):
        lat = to_float(current.get("lat"))
        lon = to_float(current.get("lon"))

        if lat is not None and lon is not None:
            timeline.append({
                "forecast_hour": 0,
                "time": infer_jtwc_valid_time(
                    current.get("valid_time_raw"),
                    issue_time,
                ),
                "lat": lat,
                "lon": lon,
                "max_wind_mps": current.get("max_wind_mps"),
                "pressure_hpa": current.get("pressure_hpa"),
                "wind_radii": current.get("wind_radii", {}),
            })

    forecasts = storm.get("forecast", [])
    if isinstance(forecasts, list):
        for item in forecasts:
            if not isinstance(item, dict):
                continue

            lat = to_float(item.get("lat"))
            lon = to_float(item.get("lon"))

            if lat is None or lon is None:
                continue

            timeline.append({
                "forecast_hour": item.get("forecast_hour"),
                "time": infer_jtwc_valid_time(
                    item.get("valid_time_raw"),
                    issue_time,
                ),
                "lat": lat,
                "lon": lon,
                "max_wind_mps": item.get("max_wind_mps"),
                "pressure_hpa": item.get("pressure_hpa"),
                "wind_radii": item.get("wind_radii", {}),
            })

    timeline.sort(
        key=lambda x: (
            x.get("forecast_hour") is None,
            x.get("forecast_hour") or 0,
        )
    )
    return timeline


def make_wind_point(
    timeline_point: Dict[str, Any],
    location: Dict[str, Any],
) -> Dict[str, Any]:
    storm_lat = float(timeline_point["lat"])
    storm_lon = float(timeline_point["lon"])
    hub_lat = float(location["lat"])
    hub_lon = float(location["lon"])

    distance = haversine_km(
        storm_lat, storm_lon, hub_lat, hub_lon
    )
    bearing = initial_bearing_degrees(
        storm_lat, storm_lon, hub_lat, hub_lon
    )
    quadrant = quadrant_from_bearing(bearing)

    wind_radii = timeline_point.get("wind_radii", {})
    if not isinstance(wind_radii, dict):
        wind_radii = {}

    r34 = radius_km(wind_radii, "34kt", quadrant)
    r50 = radius_km(wind_radii, "50kt", quadrant)
    r64 = radius_km(wind_radii, "64kt", quadrant)

    risk = risk_from_wind_radii(
        distance, r34, r50, r64
    )

    clearance_34 = (
        distance - r34
        if r34 is not None
        else None
    )

    radius_ratio = (
        distance / r34
        if r34 is not None and r34 > 0
        else None
    )

    if clearance_34 is None:
        boundary_text = "해당 방향 34kt 강풍반경 자료 없음"
    elif r34 == 0:
        boundary_text = (
            f"해당 방향 34kt 강풍반경 0 km · "
            f"태풍 중심까지 {round(distance)} km"
        )
    elif clearance_34 < 0:
        boundary_text = (
            f"강풍 영향권 내부 {round(abs(clearance_34))} km"
        )
    else:
        boundary_text = (
            f"강풍 영향권까지 {round(clearance_34)} km"
        )

    return {
        "forecast_hour": timeline_point.get("forecast_hour"),
        "time": timeline_point.get("time"),
        "storm_lat": round(storm_lat, 3),
        "storm_lon": round(storm_lon, 3),
        "center_distance_km": round(distance),
        "bearing_deg": round(bearing),
        "quadrant": quadrant,
        "wind_radius_34_km": round(r34) if r34 is not None else None,
        "wind_radius_50_km": round(r50) if r50 is not None else None,
        "wind_radius_64_km": round(r64) if r64 is not None else None,
        "distance_to_34kt_boundary_km": (
            round(clearance_34)
            if clearance_34 is not None
            else None
        ),
        "distance_to_34kt_radius_ratio": (
            round(radius_ratio, 2)
            if radius_ratio is not None
            else None
        ),
        "boundary_status_ko": boundary_text,
        "max_wind_mps": timeline_point.get("max_wind_mps"),
        "pressure_hpa": timeline_point.get("pressure_hpa"),
        "risk": risk,
    }


def choose_worst_point(
    points: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not points:
        return None

    def tie_breaker(point: Dict[str, Any]) -> float:
        clearance = point.get(
            "distance_to_34kt_boundary_km"
        )
        if isinstance(clearance, (int, float)):
            # Smaller clearance = closer to / further inside the wind field.
            return -float(clearance)

        distance = point.get("center_distance_km")
        if isinstance(distance, (int, float)):
            return -float(distance)

        return float("-inf")

    return max(
        points,
        key=lambda p: (
            risk_rank(p),
            tie_breaker(p),
        ),
    )


def location_summary_jtwc(
    code: str,
    location: Dict[str, Any],
    timeline: List[Dict[str, Any]],
) -> Dict[str, Any]:
    points = [
        make_wind_point(point, location)
        for point in timeline
    ]

    if not points:
        return {
            "code": code,
            "name_ko": location["name_ko"],
            "risk": risk_object(
                "NO_DATA", "자료 없음", 0, "JTWC 자료 없음"
            ),
            "timeline": [],
        }

    current = points[0]
    closest_center = min(
        points,
        key=lambda x: x["center_distance_km"],
    )
    worst = choose_worst_point(points) or current

    last_distance = points[-1]["center_distance_km"]
    current_distance = current["center_distance_km"]

    if last_distance < current_distance:
        trend = "APPROACHING"
        trend_ko = "접근 중"
        trend_emoji = "↘"
    elif last_distance > current_distance:
        trend = "MOVING_AWAY"
        trend_ko = "멀어지는 중"
        trend_emoji = "↗"
    else:
        trend = "STABLE"
        trend_ko = "큰 변화 없음"
        trend_emoji = "→"

    return {
        "code": code,
        "name_ko": location["name_ko"],
        "risk_source": "JTWC_WIND_RADII",
        "current_distance_km": current["center_distance_km"],
        "closest_distance_km": closest_center["center_distance_km"],
        "closest_time": closest_center.get("time"),
        "closest_forecast_hour": closest_center.get("forecast_hour"),
        "trend": trend,
        "trend_ko": trend_ko,
        "trend_emoji": trend_emoji,
        "risk": worst["risk"],
        "risk_forecast_hour": worst.get("forecast_hour"),
        "risk_time": worst.get("time"),
        "risk_detail": {
            "center_distance_km": worst.get("center_distance_km"),
            "direction_from_typhoon": worst.get("quadrant"),
            "bearing_deg": worst.get("bearing_deg"),
            "wind_radius_34_km": worst.get("wind_radius_34_km"),
            "wind_radius_50_km": worst.get("wind_radius_50_km"),
            "wind_radius_64_km": worst.get("wind_radius_64_km"),
            "distance_to_34kt_boundary_km": worst.get(
                "distance_to_34kt_boundary_km"
            ),
            "distance_to_34kt_radius_ratio": worst.get(
                "distance_to_34kt_radius_ratio"
            ),
            "boundary_status_ko": worst.get("boundary_status_ko"),
            "max_wind_mps": worst.get("max_wind_mps"),
            "pressure_hpa": worst.get("pressure_hpa"),
        },
        "timeline": points,
    }


def build_jma_distance_timeline(
    typhoon: Dict[str, Any],
) -> List[Dict[str, Any]]:
    timeline: List[Dict[str, Any]] = []

    analysis = typhoon.get("analysis")
    if isinstance(analysis, dict):
        timeline.append({
            "forecast_hour": 0,
            "time": analysis.get("time"),
            "distances_km": analysis.get("distances_km", {}),
        })

    forecasts = typhoon.get("forecast", [])
    if isinstance(forecasts, list):
        for item in forecasts:
            if not isinstance(item, dict):
                continue
            timeline.append({
                "forecast_hour": item.get("forecast_hour"),
                "time": item.get("time"),
                "distances_km": item.get("distances_km", {}),
            })

    return timeline


def location_summary_fallback(
    code: str,
    location: Dict[str, Any],
    timeline: List[Dict[str, Any]],
) -> Dict[str, Any]:
    points = []

    for point in timeline:
        distances = point.get("distances_km", {})
        if not isinstance(distances, dict):
            continue

        value = to_float(distances.get(code))
        if value is None:
            continue

        points.append({
            "forecast_hour": point.get("forecast_hour"),
            "time": point.get("time"),
            "distance_km": round(value),
        })

    if not points:
        return {
            "code": code,
            "name_ko": location["name_ko"],
            "risk_source": "NO_DATA",
            "risk": fallback_risk_from_distance(None),
            "timeline": [],
        }

    current = points[0]
    closest = min(points, key=lambda x: x["distance_km"])
    risk = fallback_risk_from_distance(
        closest["distance_km"]
    )

    return {
        "code": code,
        "name_ko": location["name_ko"],
        "risk_source": "JMA_DISTANCE_FALLBACK",
        "current_distance_km": current["distance_km"],
        "closest_distance_km": closest["distance_km"],
        "closest_time": closest.get("time"),
        "closest_forecast_hour": closest.get("forecast_hour"),
        "trend": "UNKNOWN",
        "trend_ko": "JTWC 풍권 자료 없음",
        "trend_emoji": "⚪",
        "risk": risk,
        "risk_forecast_hour": closest.get("forecast_hour"),
        "risk_time": closest.get("time"),
        "risk_detail": {
            "center_distance_km": closest["distance_km"],
            "boundary_status_ko": "JTWC 풍권 자료 없음 - 임시 거리 기준",
        },
        "timeline": points,
    }


def route_summary(
    route: Dict[str, Any],
    locations: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    items = [
        locations[code]
        for code in route["locations"]
        if code in locations
    ]

    if not items:
        return {
            "code": route["code"],
            "name_ko": route["name_ko"],
            "risk": risk_object(
                "NO_DATA", "자료 없음", 0, "자료 없음"
            ),
            "reason_ko": "자료 없음",
            "locations": route["locations"],
        }

    worst = max(
        items,
        key=lambda x: (
            risk_rank(x),
            -float(
                x.get("risk_detail", {}).get(
                    "distance_to_34kt_boundary_km",
                    999999,
                )
                if isinstance(
                    x.get("risk_detail", {}).get(
                        "distance_to_34kt_boundary_km"
                    ),
                    (int, float),
                )
                else 999999
            ),
        ),
    )

    detail = worst.get("risk_detail", {})
    boundary = detail.get("boundary_status_ko")

    reason = f"{worst['name_ko']} {worst['risk']['label_ko']}"
    if boundary:
        reason += f" ({boundary})"

    return {
        "code": route["code"],
        "name_ko": route["name_ko"],
        "risk": worst["risk"],
        "reason_ko": reason,
        "worst_location": worst["code"],
        "locations": route["locations"],
    }


def semantic_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    clone = json.loads(json.dumps(data, ensure_ascii=False))
    clone.pop("generated_at_utc", None)
    return clone


def write_if_changed(data: Dict[str, Any]) -> bool:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    if OUTPUT_PATH.exists():
        try:
            old = load_json(OUTPUT_PATH)
            if semantic_payload(old) == semantic_payload(data):
                print("No impact data change.")
                return False
        except Exception:
            pass

    data["generated_at_utc"] = datetime.now(
        timezone.utc
    ).isoformat()

    OUTPUT_PATH.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(f"Updated: {OUTPUT_PATH}")
    return True


def main() -> int:
    print(f"Typhoon impact parser: {PARSER_VERSION}")

    jma = load_json(JMA_PATH)
    compare = load_json_optional(COMPARE_PATH)
    jtwc = load_json_optional(JTWC_PATH)

    jma_typhoon = get_primary_typhoon(jma)

    if not jma_typhoon:
        output = {
            "source": "JMA + KMA + JTWC",
            "product": "Logistics Typhoon Impact",
            "parser_version": PARSER_VERSION,
            "status": "NO_TYPHOON",
            "message_ko": "2619 NARRA 자료 없음 - 다른 태풍 사용 금지",
            "locations": {},
            "routes": [],
        }
        write_if_changed(output)
        return 0

    locations_meta = get_locations(jma)
    jtwc_storm = find_matching_jtwc_storm(
        jtwc, jma_typhoon
    )

    locations: Dict[str, Dict[str, Any]] = {}

    if jtwc_storm:
        mode = "JTWC_WIND_RADII"
        timeline = build_jtwc_timeline(jtwc_storm)

        for code in LOCATION_ORDER:
            locations[code] = location_summary_jtwc(
                code,
                locations_meta[code],
                timeline,
            )
    else:
        mode = "JMA_DISTANCE_FALLBACK"
        timeline = build_jma_distance_timeline(jma_typhoon)

        for code in LOCATION_ORDER:
            locations[code] = location_summary_fallback(
                code,
                locations_meta[code],
                timeline,
            )

    routes = [
        route_summary(route, locations)
        for route in ROUTES
    ]

    typhoon_meta = jma_typhoon.get("typhoon", {})
    compare_summary = compare.get("summary", {})
    compare_overall = compare_summary.get("overall", {})

    output = {
        "source": "JMA + KMA comparison + JTWC wind radii",
        "product": "Logistics Typhoon Impact",
        "parser_version": PARSER_VERSION,
        "status": "OK",
        "calculation_mode": mode,
        "note_ko": (
            "거점 위험도는 JTWC 34/50/64kt 방향별 풍권반경을 "
            "우선 사용합니다. JTWC 풍권은 해상 기준이며, "
            "공항 공식 결항/폐쇄 기준이 아닙니다."
        ),
        "typhoon": {
            "number": typhoon_meta.get("number"),
            "name": typhoon_meta.get("name"),
            "jtwc_id": (
                jtwc_storm.get("jtwc_id")
                if jtwc_storm
                else None
            ),
            "jtwc_warning_number": (
                jtwc_storm.get("warning_number")
                if jtwc_storm
                else None
            ),
            "jtwc_issue_time_utc": (
                jtwc_storm.get("issue_time_utc")
                if jtwc_storm
                else None
            ),
        },
        "forecast_confidence": {
            "emoji": compare_overall.get("emoji", "⚪"),
            "label_ko": compare_overall.get(
                "label_ko", "비교자료 없음"
            ),
            "average_difference_km": compare_summary.get(
                "average_difference_km"
            ),
        },
        "risk_rule": {
            "very_high": "50kt 또는 64kt 영향권 내부",
            "high": "34kt 강풍 영향권 내부",
            "caution": (
                f"해당 방향 34kt 강풍반경의 "
                f"{CAUTION_RADIUS_MULTIPLIER:.1f}배 이내"
            ),
            "low": (
                f"해당 방향 34kt 강풍반경의 "
                f"{CAUTION_RADIUS_MULTIPLIER:.1f}배보다 멀리"
            ),
            "fallback": (
                "JTWC 풍권 미확보 시 JMA 중심거리 "
                "300/700km 임시 기준"
            ),
        },
        "locations": locations,
        "routes": routes,
    }

    write_if_changed(output)

    print("")
    print("=== LOCATION IMPACT ===")
    for code in LOCATION_ORDER:
        item = locations[code]
        detail = item.get("risk_detail", {})
        print(
            f"{item['risk']['emoji']} "
            f"{item['name_ko']}: "
            f"{item['risk']['label_ko']} / "
            f"{detail.get('boundary_status_ko', '-')}"
        )

    print("")
    print("=== ROUTE IMPACT ===")
    for route in routes:
        print(
            f"{route['risk']['emoji']} "
            f"{route['name_ko']} - "
            f"{route['risk']['label_ko']} "
            f"({route['reason_ko']})"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
