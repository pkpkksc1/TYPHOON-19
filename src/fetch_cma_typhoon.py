#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import json, re, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = BASE_DIR / "data" / "cma_typhoon.json"

LIST_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default"
VIEW_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{id}"
PARSER_VERSION = "1.1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 SBLC-Typhoon-Dashboard/1.1",
    "Referer": "http://typhoon.nmc.cn/mobile.html",
    "Accept": "*/*",
}

GRADE_KO = {
    "TD": "열대저압부",
    "TS": "열대폭풍",
    "STS": "강한 열대폭풍",
    "TY": "태풍",
    "STY": "강한 태풍",
    "SuperTY": "초강력 태풍",
}

DIR_KO = {
    "N":"북","NNE":"북북동","NE":"북동","ENE":"동북동","E":"동","ESE":"동남동",
    "SE":"남동","SSE":"남남동","S":"남","SSW":"남남서","SW":"남서","WSW":"서남서",
    "W":"서","WNW":"서북서","NW":"북서","NNW":"북북서",
}

def fetch_text(url):
    sep = "&" if "?" in url else "?"
    final_url = f"{url}{sep}t={int(time.time()*1000)}"
    req = urllib.request.Request(final_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

def parse_jsonp(text):
    m = re.search(r"(\{.*\})", text.strip(), re.S)
    if not m:
        raise ValueError("JSONP parse failed")
    return json.loads(m.group(1))

def active_list():
    data = parse_jsonp(fetch_text(LIST_URL))
    out = []
    for row in data.get("typhoonList", []):
        if isinstance(row, list) and len(row) >= 8 and row[7] == "start":
            out.append({
                "id": row[0],
                "name_en": row[1],
                "name_cn": row[2],
                "number": row[4] if len(row) > 4 else None,
                "state": row[7],
            })
    return out

def parse_radii(raw):
    """
    CMA/NMC directional wind radii.

    Official display order:
        NE -> SE -> SW -> NW

    Example:
        ["30KTS", 280, 280, 180, 150, point_id]
    """
    out = []

    if not isinstance(raw, list):
        return out

    for item in raw:
        if not isinstance(item, list) or len(item) < 5:
            continue

        label = str(item[0]).upper()

        entry = {
            "label": label,
            "ne_km": item[1],
            "se_km": item[2],
            "sw_km": item[3],
            "nw_km": item[4],
            "raw": item,
        }

        if label == "30KTS":
            entry["meaning_ko"] = "30kt 풍권"
        elif label == "34KTS":
            entry["meaning_ko"] = "34kt 강풍권"
        elif label == "50KTS":
            entry["meaning_ko"] = "50kt 폭풍권"
        elif label == "64KTS":
            entry["meaning_ko"] = "64kt 매우 강한 폭풍권"
        else:
            entry["meaning_ko"] = f"{label} 풍권"

        out.append(entry)

    return out

def parse_forecast(raw):
    out = []
    if not isinstance(raw, dict):
        return out
    for row in raw.get("BABJ", []):
        if isinstance(row, list) and len(row) >= 8:
            out.append({
                "forecast_hour": row[0],
                "base_time_utc": row[1],
                "lon": row[2],
                "lat": row[3],
                "pressure_hpa": row[4],
                "max_wind_mps": row[5],
                "agency": row[6],
                "grade": row[7],
                "grade_ko": GRADE_KO.get(row[7], row[7]),
            })
    return out

def detail(storm):
    data = parse_jsonp(fetch_text(VIEW_URL.format(id=storm["id"])))
    ty = data["typhoon"]
    history = ty[8]
    latest = history[-1]

    current = {
        "point_id": latest[0],
        "time_utc": latest[1],
        "grade": latest[3],
        "grade_ko": GRADE_KO.get(latest[3], latest[3]),
        "lon": latest[4],
        "lat": latest[5],
        "pressure_hpa": latest[6],
        "max_wind_mps": latest[7],
        "movement_direction": latest[8],
        "movement_direction_ko": DIR_KO.get(latest[8], latest[8]),
        "movement_speed_kmh": latest[9],
        "wind_radii": parse_radii(latest[10]),
    }

    return {
        "id": storm["id"],
        "number": ty[4] if len(ty) > 4 else storm.get("number"),
        "name_en": ty[1],
        "name_cn": ty[2],
        "state": ty[7],
        "current": current,
        "forecast": parse_forecast(latest[11]),
        "raw_point_count": len(history),
    }

def main():
    print(f"CMA/NMC fetch version: {PARSER_VERSION}")
    storms, errors = [], []
    active = active_list()
    print(f"Active CMA systems: {len(active)}")

    for s in active:
        try:
            print(f"Fetching: {s['id']} {s.get('name_en')}")
            storms.append(detail(s))
        except Exception as e:
            errors.append({"id": s.get("id"), "name_en": s.get("name_en"), "error": str(e)})

    output = {
        "source": "CMA / NMC",
        "product": "China Typhoon Track and Forecast",
        "parser_version": PARSER_VERSION,
        "active_count": len(storms),
        "typhoons": storms,
        "errors": errors,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated: {OUTPUT_PATH}")
    return 1 if errors and not storms else 0

if __name__ == "__main__":
    sys.exit(main())
