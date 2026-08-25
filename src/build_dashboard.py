#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SBLC Typhoon Dashboard - Step 8.1
Build one dashboard summary JSON

Inputs:
    data/jma_typhoon.json
    data/typhoon_compare.json
    data/typhoon_risk.json

Output:
    data/dashboard.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]

JMA_PATH = BASE_DIR / "data" / "jma_typhoon.json"
COMPARE_PATH = BASE_DIR / "data" / "typhoon_compare.json"
RISK_PATH = BASE_DIR / "data" / "typhoon_risk.json"
OUTPUT_PATH = BASE_DIR / "data" / "dashboard.json"

PARSER_VERSION = "8.71.0-NARRA-NO-FLIGHT"
TARGET_TYPHOON_NUMBER = "2619"
TARGET_TYPHOON_NAME = "NARRA"
LOCATION_ORDER = ["SUZHOU", "PVG", "ICN", "MNL", "HAN", "CRK"]
LOCATION_NAME_OVERRIDES = {
    "PVG": "푸동 국제공항",
    "ICN": "인천 국제공항",
    "MNL": "마닐라 국제공항",
    "CRK": "클락 국제공항",
}


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def simplify_location(item: Dict[str, Any], code: str = "") -> Dict[str, Any]:
    weather = item.get("weather", {})
    typhoon = item.get("typhoon", {})
    risk = item.get("risk", {})

    return {
        "name_ko": LOCATION_NAME_OVERRIDES.get(code, item.get("name_ko")),
        "score": item.get("score"),
        "risk": {
            "emoji": risk.get("emoji"),
            "label_ko": risk.get("label_ko"),
        },
        "reason_ko": item.get("reason_ko"),
        "current_distance_km": typhoon.get("current_distance_km"),
        "closest_distance_km": typhoon.get("closest_distance_km"),
        "closest_time": typhoon.get("closest_time"),
        "closest_forecast_hour": typhoon.get("closest_forecast_hour"),
        "closest_source": typhoon.get("source"),
        "trend_ko": typhoon.get("trend_ko"),
        "current_weather": {
            "source": weather.get("source") or "WeatherAPI.com",
            "last_updated": weather.get("current_last_updated"),
            "rain_mm": weather.get("current_rain_mm"),
            "wind_mps": weather.get("current_wind_mps"),
        },
        "forecast_72h": {
            "max_rain_mm": weather.get("max_72h_rain_mm"),
            "max_rain_time": weather.get("max_72h_rain_time"),
            "max_wind_mps": weather.get("max_72h_wind_mps"),
            "max_wind_time": weather.get("max_72h_wind_time"),
        },
    }




LAST_KNOWN_AFTER_HOURS = 12.0


def _parse_iso(value: Any):
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_last_known_jma_item(item: Dict[str, Any]) -> bool:
    if str(item.get("retention_status") or "") == "LAST_KNOWN_JMA_BULLETIN":
        return True

    analysis = item.get("analysis") or {}
    dt = _parse_iso(analysis.get("time"))
    if dt is None:
        return False

    age_hours = (
        datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    ).total_seconds() / 3600.0
    return age_hours > LAST_KNOWN_AFTER_HOURS


def get_typhoon_track(jma: Dict[str, Any]) -> Dict[str, Any]:
    typhoons = jma.get("typhoons", [])

    if not isinstance(typhoons, list) or not typhoons:
        return {}

    # Dashboard is locked to 2619 NARRA.
    # Never display another named typhoon or tropical depression.
    item = next(
        (
            t for t in typhoons
            if isinstance(t, dict)
            and isinstance(t.get("typhoon"), dict)
            and str(t.get("typhoon", {}).get("number") or "").strip()
                == TARGET_TYPHOON_NUMBER
            and str(t.get("typhoon", {}).get("name") or "").strip().upper()
                == TARGET_TYPHOON_NAME
        ),
        None,
    )

    if item is None:
        return {}

    meta = item.get("typhoon", {})
    analysis = item.get("analysis", {}) or {}
    last_known = is_last_known_jma_item(item)

    forecast_points: List[Dict[str, Any]] = []

    # A final/retained bulletin is historical. Do not expose its old
    # future forecast as if it were still a live forecast.
    forecast_source = [] if last_known else item.get("forecast", [])

    for p in forecast_source:
        if not isinstance(p, dict):
            continue

        forecast_points.append({
            "forecast_hour": p.get("forecast_hour"),
            "time": p.get("time"),
            "lat": p.get("lat"),
            "lon": p.get("lon"),
            "pressure_hpa": p.get("pressure_hpa"),
            "max_wind_mps": p.get("max_wind_mps"),
            "movement_direction": p.get("movement_direction"),
        })

    return {
        "number": meta.get("number"),
        "name": meta.get("name"),
        "observation_status": (
            "LAST_KNOWN" if last_known else "ACTIVE"
        ),
        "last_known": last_known,
        "last_observation_time": analysis.get("time"),
        "retention_note_ko": item.get("retention_note_ko"),
        "current": {
            "time": analysis.get("time"),
            "lat": analysis.get("lat"),
            "lon": analysis.get("lon"),
            "pressure_hpa": analysis.get("pressure_hpa"),
            "max_wind_mps": analysis.get("max_wind_mps"),
            "movement_direction": analysis.get("movement_direction"),
            "movement_speed_kmh": analysis.get("movement_speed_kmh"),
        },
        "forecast_track": forecast_points,
    }


def main() -> int:
    jma = load_json(JMA_PATH)
    compare = load_json(COMPARE_PATH)
    risk = load_json(RISK_PATH)

    # HARD GUARD: dashboard must never render risk from another storm.
    risk_typhoon = risk.get("typhoon") or {}
    risk_number = str(risk_typhoon.get("number") or "").strip()
    risk_name = str(risk_typhoon.get("name") or "").strip().upper()

    if (
        risk_number != TARGET_TYPHOON_NUMBER
        or risk_name != TARGET_TYPHOON_NAME
    ):
        raise RuntimeError(
            "NARRA HARD LOCK: typhoon_risk.json is not "
            f"{TARGET_TYPHOON_NUMBER} {TARGET_TYPHOON_NAME}. "
            "Dashboard build stopped."
        )

    compare_summary = compare.get("summary", {})
    compare_overall = compare_summary.get("overall", {})

    locations: Dict[str, Dict[str, Any]] = {}

    for code in LOCATION_ORDER:
        item = risk.get("locations", {}).get(code)
        if isinstance(item, dict):
            locations[code] = simplify_location(item, code)

    routes: List[Dict[str, Any]] = []

    for route in risk.get("routes", []):
        if not isinstance(route, dict):
            continue

        rr = route.get("risk", {})

        route_name = route.get("name_ko")
        if route.get("code") == "ICN_PVG":
            route_name = "한국 → PVG"

        routes.append({
            "code": route.get("code"),
            "name_ko": route_name,
            "score": route.get("score"),
            "risk": {
                "emoji": rr.get("emoji"),
                "label_ko": rr.get("label_ko"),
            },
            "reason_ko": route.get("reason_ko"),
        })

    # Manila route: use the worst risk among SUZHOU / PVG / MNL.
    # This becomes active as soon as MNL exists in data/typhoon_risk.json.
    if "MNL" in locations and not any(r.get("code") == "SUZHOU_PVG_MNL" for r in routes):
        route_codes = ["SUZHOU", "PVG", "MNL"]
        route_items = [locations[c] for c in route_codes if c in locations]
        level_rank = {"낮음": 1, "주의": 2, "높음": 3}
        worst = max(
            route_items,
            key=lambda x: level_rank.get(x.get("risk", {}).get("label_ko"), 0),
        )
        routes.insert(3, {
            "code": "SUZHOU_PVG_MNL",
            "name_ko": "쑤저우 → PVG → 마닐라",
            "score": max((x.get("score") or 0) for x in route_items),
            "risk": worst.get("risk", {}),
            "reason_ko": f"{worst.get('name_ko')} {worst.get('risk', {}).get('label_ko')} ({worst.get('reason_ko') or '-'})",
        })

    typhoon_summary = get_typhoon_track(jma)

    output = {
        "source": "SBLC Typhoon Dashboard",
        "product": "Dashboard Summary",
        "parser_version": PARSER_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),

        # Source-specific refresh times.
        # These remain tied to the source JSON itself, so rebuilding the
        # rebuilding the dashboard does not falsely make JMA look newer.
        "source_updated_at_utc": {
            "jma": jma.get("generated_at_utc"),
            "weather": (
                risk.get("source_updated_at_utc", {}).get("weather")
                if isinstance(risk.get("source_updated_at_utc"), dict)
                else None
            ),
            "risk": risk.get("generated_at_utc"),
        },

        "risk_meta": {
            "typhoon_reference_time": (
                typhoon_summary.get("current", {}).get("time")
                if isinstance(typhoon_summary.get("current"), dict)
                else None
            ),
            "jma_updated_at_utc": jma.get("generated_at_utc"),
            "weather_updated_at_utc": (
                risk.get("source_updated_at_utc", {}).get("weather")
                if isinstance(risk.get("source_updated_at_utc"), dict)
                else None
            ),
            "impact_updated_at_utc": (
                risk.get("source_updated_at_utc", {}).get("impact")
                if isinstance(risk.get("source_updated_at_utc"), dict)
                else None
            ),
            "risk_updated_at_utc": risk.get("generated_at_utc"),
            "risk_version": risk.get("parser_version"),
        },

        "typhoon": typhoon_summary,
        "forecast_comparison": {
            "emoji": compare_overall.get("emoji", "⚪"),
            "label_ko": compare_overall.get("label_ko", "비교자료 없음"),
            "average_difference_km": compare_summary.get("average_difference_km"),
            "max_difference_km": compare_summary.get("max_difference_km"),
        },
        "locations": locations,
        "routes": routes,
        "attribution": [
            "Japan Meteorological Agency (JMA)",
            "Korea Meteorological Administration (KMA)",
            "Powered by WeatherAPI.com",
        ],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT_PATH.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(f"Dashboard summary version: {PARSER_VERSION}")
    print(f"Updated: {OUTPUT_PATH}")
    print(f"Locations: {len(locations)}")
    print(f"Routes: {len(routes)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
