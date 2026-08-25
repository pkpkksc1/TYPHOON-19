#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SBLC Typhoon Dashboard - Step 5
WeatherAPI weather collector

Required GitHub Secret:
    WEATHER_API_KEY

Output:
    data/weather.json

Locations:
    SUZHOU / PVG / ICN / MNL / HAN / CRK / NANNING

Collects:
    current rain and wind
    hourly forecast up to 72 hours

Uses Python standard library only.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = BASE_DIR / "data" / "weather.json"

API_URL = "https://api.weatherapi.com/v1/forecast.json"
PARSER_VERSION = "5.4-NANNING"
USER_AGENT = "sblc-typhoon-dashboard/5.4"

LOCATIONS = {
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
    "NANNING": {
        "name_ko": "난닝",
        "lat": 22.8170,
        "lon": 108.3665,
    },
}


def get_api_key() -> str:
    key = os.environ.get("WEATHER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("WEATHER_API_KEY secret is missing.")
    return key


def fetch_json(params: Dict[str, Any]) -> Dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{API_URL}?{query}"
    last_error = None

    for attempt in range(1, 4):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            print(f"WeatherAPI request attempt {attempt}/3")
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if 400 <= e.code < 500:
                raise RuntimeError(f"WeatherAPI HTTP {e.code}: {body[:500]}") from e
            last_error = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e

        if attempt < 3:
            time.sleep(attempt * 10)

    raise RuntimeError(f"WeatherAPI failed after 3 attempts: {last_error}")


def kmh_to_mps(value: Any) -> float | None:
    try:
        return round(float(value) / 3.6, 1)
    except (TypeError, ValueError):
        return None


def collect_location(api_key: str, code: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    data = fetch_json({
        "key": api_key,
        "q": f"{meta['lat']},{meta['lon']}",
        "days": 3,
        "aqi": "no",
        "alerts": "no",
    })

    current = data.get("current", {})
    forecast_days = data.get("forecast", {}).get("forecastday", [])

    hourly: List[Dict[str, Any]] = []
    for day in forecast_days:
        for hour in day.get("hour", []):
            hourly.append({
                "time": hour.get("time"),
                "rain_mm": hour.get("precip_mm"),
                "chance_of_rain_pct": hour.get("chance_of_rain"),
                "wind_mps": kmh_to_mps(hour.get("wind_kph")),
                "wind_dir": hour.get("wind_dir"),
                "condition": hour.get("condition", {}).get("text"),
            })

    return {
        "code": code,
        "name_ko": meta["name_ko"],
        "requested_coordinate": {
            "lat": meta["lat"],
            "lon": meta["lon"],
        },
        "resolved_location": {
            "name": data.get("location", {}).get("name"),
            "region": data.get("location", {}).get("region"),
            "country": data.get("location", {}).get("country"),
            "lat": data.get("location", {}).get("lat"),
            "lon": data.get("location", {}).get("lon"),
            "localtime": data.get("location", {}).get("localtime"),
        },
        "current": {
            "last_updated": current.get("last_updated"),
            "rain_mm": current.get("precip_mm"),
            "wind_mps": kmh_to_mps(current.get("wind_kph")),
            "wind_dir": current.get("wind_dir"),
            "condition": current.get("condition", {}).get("text"),
        },
        "hourly_forecast": hourly[:72],
    }


def semantic_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    clone = json.loads(json.dumps(data, ensure_ascii=False))
    clone.pop("generated_at_utc", None)
    clone.pop("data_changed", None)
    return clone


def write_snapshot(data: Dict[str, Any]) -> bool:
    """
    Always save a successful WeatherAPI fetch time.

    generated_at_utc = when our collector successfully finished.
    current.last_updated = WeatherAPI's own observation/update time.

    This makes it possible to distinguish:
      1) collector did not run
      2) collector ran, but WeatherAPI returned the same observation
    """
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    changed = True

    if OUTPUT_PATH.exists():
        try:
            old = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            changed = semantic_payload(old) != semantic_payload(data)
        except Exception:
            changed = True

    data["data_changed"] = changed
    data["generated_at_utc"] = datetime.now(timezone.utc).isoformat()

    OUTPUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Updated: {OUTPUT_PATH} "
        f"(weather values changed: {'YES' if changed else 'NO'})"
    )
    return True


def main() -> int:
    print(f"Weather collector version: {PARSER_VERSION}")
    api_key = get_api_key()

    locations = {}
    errors = []

    for code, meta in LOCATIONS.items():
        try:
            print(f"Collecting {code} - {meta['name_ko']}")
            locations[code] = collect_location(api_key, code, meta)
        except Exception as e:
            print(f"ERROR {code}: {e}")
            errors.append({"code": code, "error": str(e)})

    output = {
        "source": "WeatherAPI.com",
        "product": "Current + 72-hour Hourly Forecast",
        "parser_version": PARSER_VERSION,
        "attribution": "Powered by WeatherAPI.com",
        "location_count": len(locations),
        "locations": locations,
        "errors": errors,
    }

    write_snapshot(output)

    if errors:
        print(f"Completed with {len(errors)} location error(s).")
        return 1

    print(f"Completed: {len(locations)} locations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
