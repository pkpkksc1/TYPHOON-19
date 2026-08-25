#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SBLC Typhoon Dashboard - Step 6
Combined logistics risk

Inputs:
    data/typhoon_impact.json
    data/weather.json

Output:
    data/typhoon_risk.json

This is an internal operational risk score, NOT an official airport
cancellation/delay rule.

Risk inputs:
1) Typhoon closest distance
2) Hourly rain
3) Wind

Uses Python standard library only.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[1]

IMPACT_PATH = BASE_DIR / "data" / "typhoon_impact.json"
WEATHER_PATH = BASE_DIR / "data" / "weather.json"
OUTPUT_PATH = BASE_DIR / "data" / "typhoon_risk.json"

PARSER_VERSION = "6.7-CURRENT-WEATHER"

TARGET_TYPHOON_NUMBER = "2619"
TARGET_TYPHOON_NAME = "NARRA"

LOCATION_ORDER = ["SUZHOU", "PVG", "ICN", "MNL", "HAN", "CRK"]

ROUTES = [
    {
        "code": "SUZHOU_PVG_ICN",
        "name_ko": "쑤저우 → PVG → 한국",
        "locations": ["SUZHOU", "PVG", "ICN"],
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
        "code": "SUZHOU_PVG_MNL",
        "name_ko": "쑤저우 → PVG → 마닐라",
        "locations": ["SUZHOU", "PVG", "MNL"],
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


def to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def risk_label(score: int) -> Dict[str, str]:
    if score >= 70:
        return {
            "level": "RED",
            "emoji": "🔴",
            "label_ko": "높음",
        }
    if score >= 35:
        return {
            "level": "YELLOW",
            "emoji": "🟡",
            "label_ko": "주의",
        }
    return {
        "level": "GREEN",
        "emoji": "🟢",
        "label_ko": "낮음",
    }


def distance_score(distance_km: Optional[float]) -> int:
    if distance_km is None:
        return 0
    if distance_km <= 200:
        return 40
    if distance_km <= 400:
        return 30
    if distance_km <= 700:
        return 20
    if distance_km <= 1000:
        return 10
    return 0


def rain_score(rain_mm: float) -> int:
    # hourly precipitation
    if rain_mm >= 10:
        return 25
    if rain_mm >= 5:
        return 20
    if rain_mm >= 2:
        return 15
    if rain_mm >= 0.5:
        return 8
    if rain_mm > 0:
        return 3
    return 0


def wind_score(wind_mps: float) -> int:
    if wind_mps >= 20:
        return 20
    if wind_mps >= 15:
        return 15
    if wind_mps >= 10:
        return 10
    if wind_mps >= 7:
        return 5
    return 0



def summarize_weather(location: Dict[str, Any]) -> Dict[str, Any]:
    current = location.get("current", {})
    hourly = location.get("hourly_forecast", [])

    max_rain = to_float(current.get("rain_mm")) or 0.0
    max_wind = to_float(current.get("wind_mps")) or 0.0
    max_rain_time = current.get("last_updated")
    max_wind_time = current.get("last_updated")

    for item in hourly:
        if not isinstance(item, dict):
            continue

        rain = to_float(item.get("rain_mm")) or 0.0
        wind = to_float(item.get("wind_mps")) or 0.0
        tm = item.get("time")

        if rain > max_rain:
            max_rain = rain
            max_rain_time = tm

        if wind > max_wind:
            max_wind = wind
            max_wind_time = tm

    return {
        "source": "WeatherAPI.com",
        "current_last_updated": current.get("last_updated"),
        "current_rain_mm": to_float(current.get("rain_mm")),
        "current_wind_mps": to_float(current.get("wind_mps")),
        "max_72h_rain_mm": round(max_rain, 2),
        "max_72h_rain_time": max_rain_time,
        "max_72h_wind_mps": round(max_wind, 1),
        "max_72h_wind_time": max_wind_time,
    }


def make_location_risk(
    code: str,
    impact_item: Dict[str, Any],
    weather_item: Dict[str, Any],
) -> Dict[str, Any]:
    weather = summarize_weather(weather_item)

    current_distance = to_float(
        impact_item.get("current_distance_km")
    )

    closest_distance = to_float(
        impact_item.get("closest_distance_km")
    )

    d_score = distance_score(closest_distance)

    # WeatherAPI is a SUPPORTING risk factor.
    # Use the latest/current observation for the risk score.
    # The 72h maximum values remain in the output for the
    # separate "거점 영향 전망" display only.
    current_rain = weather["current_rain_mm"] or 0.0
    current_wind = weather["current_wind_mps"] or 0.0

    r_score = rain_score(current_rain)
    w_score = wind_score(current_wind)

    total = min(100, d_score + r_score + w_score)
    risk = risk_label(total)

    reasons: List[str] = []

    if d_score:
        reasons.append(
            f"태풍 최접근 {round(closest_distance)} km"
        )

    if r_score >= 15:
        reasons.append(
            f"현재 시간당 강수 {round(current_rain, 2)} mm"
        )

    if w_score >= 10:
        reasons.append(
            f"현재 풍속 {round(current_wind, 1)} m/s"
        )

    if not reasons:
        reasons.append("특이 위험요소 낮음")

    return {
        "code": code,
        "name_ko": impact_item.get(
            "name_ko",
            weather_item.get("name_ko", code),
        ),
        "score": total,
        "risk": risk,
        "reason_ko": " / ".join(reasons),
        "typhoon": {
            "source": impact_item.get("risk_source") or "JMA_FORECAST",
            "current_distance_km": (
                round(current_distance)
                if current_distance is not None
                else None
            ),
            "closest_distance_km": (
                round(closest_distance)
                if closest_distance is not None
                else None
            ),
            "closest_time": impact_item.get("closest_time"),
            "closest_forecast_hour": impact_item.get("closest_forecast_hour"),
            "trend_ko": impact_item.get("trend_ko"),
        },
        "weather": weather,
        "score_detail": {
            "distance": d_score,
            "rain": r_score,
            "wind": w_score,
        },
    }


def risk_rank(level: str) -> int:
    return {
        "GREEN": 1,
        "YELLOW": 2,
        "RED": 3,
    }.get(level, 0)


def make_route_risk(
    route: Dict[str, Any],
    locations: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    route_locations = [
        locations[code]
        for code in route["locations"]
        if code in locations
    ]

    if not route_locations:
        return {
            "code": route["code"],
            "name_ko": route["name_ko"],
            "score": 0,
            "risk": {
                "level": "NO_DATA",
                "emoji": "⚪",
                "label_ko": "자료 없음",
            },
            "reason_ko": "자료 없음",
        }

    valid_route_locations = [
        x for x in route_locations
        if x.get("risk", {}).get("level") != "NO_DATA"
    ]

    if not valid_route_locations:
        return {
            "code": route["code"],
            "name_ko": route["name_ko"],
            "score": None,
            "risk": {
                "level": "NO_DATA",
                "emoji": "⚪",
                "label_ko": "자료 없음",
            },
            "reason_ko": "노선 거점 데이터 없음",
            "locations": route["locations"],
        }

    worst = max(
        valid_route_locations,
        key=lambda x: (
            risk_rank(x["risk"]["level"]),
            x.get("score") or 0,
        ),
    )

    # Route score uses worst hub score.
    return {
        "code": route["code"],
        "name_ko": route["name_ko"],
        "score": worst["score"],
        "risk": worst["risk"],
        "reason_ko": (
            f"{worst['name_ko']} "
            f"{worst['risk']['label_ko']} "
            f"({worst['reason_ko']})"
        ),
        "locations": route["locations"],
    }


def semantic_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    clone = json.loads(json.dumps(data, ensure_ascii=False))
    clone.pop("generated_at_utc", None)
    clone.pop("data_changed", None)
    return clone


def write_snapshot(data: Dict[str, Any]) -> bool:
    """
    Always record the time the risk calculation actually ran.
    The risk score may remain unchanged even though it was recalculated.
    """
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    changed = True

    if OUTPUT_PATH.exists():
        try:
            old = load_json(OUTPUT_PATH)
            changed = semantic_payload(old) != semantic_payload(data)
        except Exception:
            changed = True

    data["data_changed"] = changed
    data["generated_at_utc"] = datetime.now(
        timezone.utc
    ).isoformat()

    OUTPUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Updated: {OUTPUT_PATH} "
        f"(risk values changed: {'YES' if changed else 'NO'})"
    )
    return True


def main() -> int:
    print(f"Combined risk parser: {PARSER_VERSION}")

    impact = load_json(IMPACT_PATH)
    weather = load_json(WEATHER_PATH)

    # HARD GUARD:
    # Never calculate logistics risk from another tropical system.
    impact_typhoon = impact.get("typhoon") or {}
    impact_number = str(impact_typhoon.get("number") or "").strip()
    impact_name = str(impact_typhoon.get("name") or "").strip().upper()

    if (
        impact_number != TARGET_TYPHOON_NUMBER
        or impact_name != TARGET_TYPHOON_NAME
    ):
        raise RuntimeError(
            "NARRA HARD LOCK: typhoon_impact.json is not "
            f"{TARGET_TYPHOON_NUMBER} {TARGET_TYPHOON_NAME}. "
            f"Received number={impact_number!r}, name={impact_name!r}. "
            "Risk calculation stopped."
        )

    impact_locations = impact.get("locations", {})
    weather_locations = weather.get("locations", {})

    locations: Dict[str, Dict[str, Any]] = {}

    for code in LOCATION_ORDER:
        impact_item = impact_locations.get(code)
        weather_item = weather_locations.get(code)

        if isinstance(impact_item, dict) and isinstance(weather_item, dict):
            locations[code] = make_location_risk(
                code,
                impact_item,
                weather_item,
            )
            continue

        # Always expose all configured logistics hubs to the dashboard.
        # If an upstream source is missing, show NO_DATA instead of
        # silently dropping the location card.
        fallback_names = {
            "SUZHOU": "쑤저우",
            "PVG": "푸동 국제공항",
            "ICN": "인천 국제공항",
            "MNL": "마닐라 국제공항",
            "HAN": "하노이 노이바이 국제공항",
            "CRK": "클락 국제공항",
        }

        missing = []
        if not isinstance(impact_item, dict):
            missing.append("태풍 영향")
        if not isinstance(weather_item, dict):
            missing.append("날씨")

        locations[code] = {
            "code": code,
            "name_ko": fallback_names.get(code, code),
            "score": None,
            "risk": {
                "level": "NO_DATA",
                "emoji": "⚪",
                "label_ko": "자료 없음",
            },
            "reason_ko": f"{' / '.join(missing)} 데이터 없음",
            "typhoon": {
                "source": (
                    impact_item.get("risk_source")
                    if isinstance(impact_item, dict)
                    else None
                ),
                "closest_distance_km": (
                    impact_item.get("closest_distance_km")
                    if isinstance(impact_item, dict)
                    else None
                ),
                "closest_time": (
                    impact_item.get("closest_time")
                    if isinstance(impact_item, dict)
                    else None
                ),
                "closest_forecast_hour": (
                    impact_item.get("closest_forecast_hour")
                    if isinstance(impact_item, dict)
                    else None
                ),
                "trend_ko": (
                    impact_item.get("trend_ko")
                    if isinstance(impact_item, dict)
                    else None
                ),
            },
            "weather": summarize_weather(weather_item)
            if isinstance(weather_item, dict)
            else {
                "source": "WeatherAPI.com",
                "current_last_updated": None,
                "current_rain_mm": None,
                "current_wind_mps": None,
                "max_72h_rain_mm": 0.0,
                "max_72h_rain_time": None,
                "max_72h_wind_mps": 0.0,
                "max_72h_wind_time": None,
            },
            "score_detail": {
                "distance": 0,
                "rain": 0,
                "wind": 0,
            },
        }

    routes = [
        make_route_risk(route, locations)
        for route in ROUTES
    ]

    output = {
        "source": "JMA/KMA + WeatherAPI.com",
        "product": "Combined Logistics Typhoon Risk",
        "parser_version": PARSER_VERSION,
        "status": "OK",
        "note_ko": (
            "내부 물류 판단용 위험도입니다. "
            "태풍 최접근 거리 + WeatherAPI 현재 강수/현재 풍속을 사용합니다. "
            "72시간 최대 강수/풍속은 전망 표시용이며 위험점수에는 사용하지 않습니다. "
            "공항 공식 결항/지연 기준이 아닙니다."
        ),
        "attribution": "Powered by WeatherAPI.com",
        "source_updated_at_utc": {
            "impact": impact.get("generated_at_utc"),
            "weather": weather.get("generated_at_utc"),
        },
        "typhoon": impact.get("typhoon"),
        "forecast_confidence": impact.get(
            "forecast_confidence"
        ),
        "locations": locations,
        "routes": routes,
    }

    write_snapshot(output)

    print("")
    print("=== COMBINED LOCATION RISK ===")
    for code in LOCATION_ORDER:
        if code not in locations:
            continue
        item = locations[code]
        print(
            f"{item['risk']['emoji']} "
            f"{item['name_ko']} "
            f"{item['score']}점 - "
            f"{item['risk']['label_ko']}"
        )

    print("")
    print("=== ROUTE RISK ===")
    for route in routes:
        print(
            f"{route['risk']['emoji']} "
            f"{route['name_ko']} "
            f"{route['score']}점 - "
            f"{route['risk']['label_ko']}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
