#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SBLC Typhoon Dashboard - Step 1
JMA 5-day typhoon analysis/forecast XML -> normalized JSON.

Uses only Python standard library.
"""

from __future__ import annotations

import json
import math
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / "config" / "locations.json"
OUTPUT_PATH = BASE_DIR / "data" / "jma_typhoon.json"

# JMA PULL feeds
HIGH_FEED = "https://www.data.jma.go.jp/developer/xml/feed/extra.xml"
LONG_FEED = "https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml"

TARGET_TITLE_WORDS = (
    "台風解析・予報情報（５日予報）",
    "台風解析・予報情報（延長予報）",
)
MAX_ENTRY_AGE_HOURS = 12
MAX_XML_DOWNLOADS = 18

# JMA may publish hourly short updates containing only the current position
# and a very short (for example +1h) estimate. Keep that update for the
# current position, but merge it with the latest available multi-day forecast.
MIN_LONG_FORECAST_HOURS = 48
SHORT_UPDATE_MAX_HOURS = 6
MAX_PRESERVED_FORECAST_AGE_HOURS = 12

USER_AGENT = "sblc-typhoon-dashboard/1.2 (JMA XML PULL client)"


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def get_attr(el: ET.Element, name: str, default: str = "") -> str:
    for key, value in el.attrib.items():
        if local_name(key) == name:
            return value
    return default


def iter_local(root: ET.Element, name: str) -> Iterable[ET.Element]:
    for el in root.iter():
        if local_name(el.tag) == name:
            yield el


def first_text(root: ET.Element, name: str) -> Optional[str]:
    for el in iter_local(root, name):
        if el.text and el.text.strip():
            return el.text.strip()
    return None


def child_text(root: ET.Element, name: str) -> Optional[str]:
    for el in list(root):
        if local_name(el.tag) == name and el.text and el.text.strip():
            return el.text.strip()
    return None


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/xml,text/xml,application/atom+xml,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_iso(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    s = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_atom(feed_xml: bytes) -> List[Dict[str, str]]:
    root = ET.fromstring(feed_xml)
    out: List[Dict[str, str]] = []
    for entry in [e for e in root.iter() if local_name(e.tag) == "entry"]:
        title = ""
        updated = ""
        link = ""
        entry_id = ""

        for c in list(entry):
            ln = local_name(c.tag)
            if ln == "title" and c.text:
                title = c.text.strip()
            elif ln == "updated" and c.text:
                updated = c.text.strip()
            elif ln == "id" and c.text:
                entry_id = c.text.strip()
            elif ln == "link":
                href = c.attrib.get("href", "")
                if href:
                    link = href

        if link and any(word in title for word in TARGET_TITLE_WORDS):
            out.append(
                {
                    "title": title,
                    "updated": updated,
                    "url": link,
                    "id": entry_id,
                }
            )
    return out


def merge_feed_entries(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    # Deduplicate by XML URL and sort newest first.
    by_url: Dict[str, Dict[str, str]] = {}
    for e in entries:
        by_url[e["url"]] = e

    merged = list(by_url.values())
    merged.sort(
        key=lambda e: parse_iso(e.get("updated")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    now = datetime.now(timezone.utc)
    recent = []
    for e in merged:
        dt = parse_iso(e.get("updated"))
        if dt is None:
            recent.append(e)
            continue
        age_h = (now - dt).total_seconds() / 3600
        if age_h <= MAX_ENTRY_AGE_HOURS:
            recent.append(e)

    return recent[:MAX_XML_DOWNLOADS]


_COORD_RE = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:/|$)")


def parse_coordinate(text: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    if not text:
        return None, None
    s = text.strip().replace(" ", "")
    m = _COORD_RE.search(s)
    if not m:
        return None, None
    try:
        lat = float(m.group(1))
        lon = float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except ValueError:
        pass
    return None, None


def find_center_coordinate(info: ET.Element) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    # JMA analysis positions are usually Coordinate.
    # Forecast-circle centers can be stored as BasePoint inside ProbabilityCircle.
    candidates: List[ET.Element] = []
    for tag_name in ("Coordinate", "BasePoint"):
        candidates.extend(list(iter_local(info, tag_name)))

    # Prefer center-position / forecast-circle center coordinates expressed in degrees.
    preferred_words = ("中心位置", "予報円中心", "予報円")
    for el in candidates:
        typ = get_attr(el, "type")
        if any(word in typ for word in preferred_words) and ("度" in typ or not typ):
            lat, lon = parse_coordinate(el.text)
            if lat is not None:
                return lat, lon, el.text.strip() if el.text else None

    # Fallback: any coordinate-like value that parses as decimal lat/lon.
    for el in candidates:
        lat, lon = parse_coordinate(el.text)
        if lat is not None:
            return lat, lon, el.text.strip() if el.text else None

    return None, None, None


def numeric_text(el: Optional[ET.Element]) -> Optional[float]:
    if el is None or not el.text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", el.text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def find_typed_numeric(info: ET.Element, tag: str, include: str, exclude: str = "") -> Tuple[Optional[float], Optional[str]]:
    fallback = None
    for el in iter_local(info, tag):
        typ = get_attr(el, "type")
        unit = get_attr(el, "unit")
        val = numeric_text(el)
        if val is None:
            continue
        if fallback is None:
            fallback = (val, unit or None)
        if include in typ and (not exclude or exclude not in typ):
            return val, unit or None
    return fallback if fallback else (None, None)


def find_typed_text(info: ET.Element, tag: str, include: str) -> Optional[str]:
    fallback = None
    for el in iter_local(info, tag):
        if el.text and el.text.strip():
            val = el.text.strip()
            if fallback is None:
                fallback = val
            if include in get_attr(el, "type"):
                return val
    return fallback


def parse_typhoon_name(root: ET.Element) -> Dict[str, Optional[str]]:
    for part in iter_local(root, "TyphoonNamePart"):
        name = child_text(part, "Name")
        kana = child_text(part, "NameKana")
        number = child_text(part, "Number")
        if name or kana or number:
            return {
                "number": number,
                "name": name,
                "kana": kana,
            }
    return {"number": None, "name": None, "kana": None}


def find_area_name(info: ET.Element) -> Optional[str]:
    for area in iter_local(info, "Area"):
        name = child_text(area, "Name")
        if name:
            return name
    return None


def normalize_speed_kmh(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    u = (unit or "").strip().lower()
    if "km/h" in u or "km毎時" in u or "キロメートル毎時" in u:
        return round(value, 1)
    if "m/s" in u or "メートル毎秒" in u:
        return round(value * 3.6, 1)
    if "ノット" in u or "kt" in u or "knot" in u:
        return round(value * 1.852, 1)
    return round(value, 1)


def normalize_speed_mps(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    u = (unit or "").strip().lower()
    if "m/s" in u or "メートル毎秒" in u:
        return round(value, 1)
    if "ノット" in u or "kt" in u or "knot" in u:
        return round(value * 0.514444, 1)
    if "km/h" in u or "km毎時" in u or "キロメートル毎時" in u:
        return round(value / 3.6, 1)
    return round(value, 1)


def normalize_distance_km(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    u = (unit or "").strip().lower()
    if "海里" in u or "nautical" in u or u in ("nm", "nmi"):
        return round(value * 1.852, 1)
    if "km" in u or "キロメートル" in u:
        return round(value, 1)
    if "m" == u or "メートル" == u:
        return round(value / 1000.0, 1)
    return round(value, 1)



def collect_radius_details(info: ET.Element) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    for el in iter_local(info, "Radius"):
        val = numeric_text(el)
        if val is None:
            continue
        unit = get_attr(el, "unit") or None
        typ = get_attr(el, "type") or None
        details.append({
            "type": typ,
            "value": val,
            "unit": unit,
            "km": normalize_distance_km(val, unit),
        })
    return details


def classify_radius_details(radius_details: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups = {
        "forecast_circle": [],
        "storm_wind_area": [],
        "gale_wind_area": [],
        "storm_warning_area": [],
        "other": [],
    }

    for item in radius_details:
        typ = item.get("type") or ""
        if "予報円" in typ:
            groups["forecast_circle"].append(item)
        elif "暴風警戒域" in typ:
            groups["storm_warning_area"].append(item)
        elif "暴風域" in typ:
            groups["storm_wind_area"].append(item)
        elif "強風域" in typ:
            groups["gale_wind_area"].append(item)
        else:
            groups["other"].append(item)

    return groups


def find_forecast_circle_radius(
    radius_details: List[Dict[str, Any]]
) -> Tuple[Optional[float], Optional[str], Optional[float]]:
    for item in radius_details:
        typ = item.get("type") or ""
        if "予報円" in typ:
            return item["value"], item["unit"], item["km"]
    return None, None, None


def validate_point(point: Dict[str, Any], is_forecast: bool) -> List[str]:
    issues: List[str] = []
    if point.get("lat") is None or point.get("lon") is None:
        issues.append("missing_center_coordinate")
    if is_forecast and point.get("forecast_circle_radius") is None:
        issues.append("forecast_circle_radius_not_found")
    return issues


def parse_meteo_info(info: ET.Element) -> Dict[str, Any]:
    dt_el = next(iter(iter_local(info, "DateTime")), None)
    dt_text = dt_el.text.strip() if dt_el is not None and dt_el.text else None
    dt_type = get_attr(dt_el, "type") if dt_el is not None else ""

    lat, lon, raw_coord = find_center_coordinate(info)

    pressure, pressure_unit = find_typed_numeric(info, "Pressure", "中心気圧")
    max_wind, wind_unit = find_typed_numeric(info, "WindSpeed", "最大風速", "最大瞬間")
    gust, gust_unit = find_typed_numeric(info, "WindSpeed", "最大瞬間風速")

    move_dir = find_typed_text(info, "Direction", "移動方向")
    move_speed, move_speed_unit = find_typed_numeric(info, "Speed", "移動速度")

    radius_details = collect_radius_details(info)
    radius_groups = classify_radius_details(radius_details)

    is_forecast = "予報" in (dt_type or "")
    if is_forecast:
        radius, radius_unit, radius_km = find_forecast_circle_radius(radius_details)
    else:
        radius, radius_unit, radius_km = None, None, None

    result = {
        "time": dt_text,
        "time_type": dt_type or None,
        "lat": lat,
        "lon": lon,
        "raw_coordinate": raw_coord,
        "area": find_area_name(info),
        "pressure_hpa": pressure if (pressure_unit in (None, "", "hPa")) else pressure,
        "max_wind_source_value": max_wind,
        "max_wind_source_unit": wind_unit,
        "max_wind_mps": normalize_speed_mps(max_wind, wind_unit),
        "gust_source_value": gust,
        "gust_source_unit": gust_unit,
        "gust_mps": normalize_speed_mps(gust, gust_unit),
        "movement_direction": move_dir,
        "movement_speed": move_speed,
        "movement_speed_unit": move_speed_unit,
        "movement_speed_kmh": normalize_speed_kmh(move_speed, move_speed_unit),
        "forecast_circle_radius": radius,
        "forecast_circle_radius_unit": radius_unit,
        "forecast_circle_radius_km": radius_km,
        "radius_details": radius_details,
        "radius_groups": radius_groups,
    }

    result["data_quality"] = {
        "status": "OK",
        "issues": validate_point(result, is_forecast),
    }
    if result["data_quality"]["issues"]:
        result["data_quality"]["status"] = "WARN"

    return result


def hours_between(base: Optional[str], target: Optional[str]) -> Optional[int]:
    b = parse_iso(base)
    t = parse_iso(target)
    if not b or not t:
        return None
    return int(round((t - b).total_seconds() / 3600))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_locations() -> Dict[str, Dict[str, Any]]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data["locations"]


def add_distances(point: Dict[str, Any], locations: Dict[str, Dict[str, Any]]) -> None:
    lat, lon = point.get("lat"), point.get("lon")
    if lat is None or lon is None:
        point["distances_km"] = {}
        return
    point["distances_km"] = {
        code: round(haversine_km(lat, lon, loc["lat"], loc["lon"]))
        for code, loc in locations.items()
        if loc.get("enabled", True)
    }


def parse_typhoon_xml(xml_bytes: bytes, entry: Dict[str, str], locations: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    root = ET.fromstring(xml_bytes)
    name = parse_typhoon_name(root)

    infos = [e for e in root.iter() if local_name(e.tag) == "MeteorologicalInfo"]
    parsed = [parse_meteo_info(info) for info in infos]

    analysis = None
    for p in parsed:
        if p.get("time_type") == "実況":
            analysis = p
            break
    if analysis is None:
        # Fallback: first point with a valid center coordinate.
        analysis = next((p for p in parsed if p.get("lat") is not None), parsed[0] if parsed else None)

    forecasts: List[Dict[str, Any]] = []
    if analysis:
        add_distances(analysis, locations)
        base_time = analysis.get("time")
        for p in parsed:
            if p is analysis:
                continue
            h = hours_between(base_time, p.get("time"))
            # Keep future points; also tolerate JMA labels even if time parsing fails.
            if h is not None and h <= 0:
                continue
            p["forecast_hour"] = h
            add_distances(p, locations)
            forecasts.append(p)

    forecasts.sort(key=lambda p: (p.get("forecast_hour") is None, p.get("forecast_hour") or 9999))

    title = first_text(root, "Title")
    report_time = first_text(root, "ReportDateTime")
    target_time = first_text(root, "TargetDateTime")
    event_id = first_text(root, "EventID")

    slot_match = re.search(r"(VPTW(?:6[0-5]|5[0-5]))", entry["url"], re.I)
    slot = slot_match.group(1).upper() if slot_match else None

    return {
        "slot": slot,
        "event_id": event_id,
        "information_title": title or entry.get("title"),
        "feed_updated": entry.get("updated"),
        "report_time": report_time,
        "target_time": target_time,
        "source_url": entry["url"],
        "typhoon": name,
        "analysis": analysis,
        "forecast": forecasts,
    }


def identity(item: Dict[str, Any]) -> str:
    t = item.get("typhoon") or {}
    return (
        t.get("number")
        or t.get("name")
        or item.get("event_id")
        or item.get("slot")
        or item.get("source_url")
    )



def item_issue_time(item: Dict[str, Any]) -> Optional[datetime]:
    return parse_iso(item.get("report_time") or item.get("feed_updated"))


def forecast_horizon_hours(item: Dict[str, Any]) -> int:
    values: List[int] = []
    for point in item.get("forecast", []):
        if not isinstance(point, dict):
            continue
        value = point.get("forecast_hour")
        if isinstance(value, (int, float)):
            values.append(int(round(value)))
    return max(values) if values else 0


def find_previous_typhoon(
    previous_data: Dict[str, Any],
    key: str,
) -> Optional[Dict[str, Any]]:
    for item in previous_data.get("typhoons", []):
        if isinstance(item, dict) and identity(item) == key:
            return item
    return None


def previous_forecast_is_fresh(
    previous_item: Dict[str, Any],
    analysis_time: Optional[str],
) -> bool:
    analysis_dt = parse_iso(analysis_time)
    source_dt = parse_iso(
        previous_item.get("forecast_source_report_time")
        or previous_item.get("report_time")
        or previous_item.get("feed_updated")
    )
    if analysis_dt is None or source_dt is None:
        return False

    age_h = (analysis_dt - source_dt).total_seconds() / 3600
    return 0 <= age_h <= MAX_PRESERVED_FORECAST_AGE_HOURS


def merge_forecast_lists(
    analysis: Optional[Dict[str, Any]],
    current_item: Dict[str, Any],
    long_item: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not analysis:
        return []

    base_time = analysis.get("time")
    base_dt = parse_iso(base_time)
    if base_dt is None:
        return list(current_item.get("forecast", []))

    # Use the long-range bulletin as the base, then let the newest short
    # bulletin replace any point with the exact same valid time.
    by_time: Dict[str, Dict[str, Any]] = {}

    sources: List[Dict[str, Any]] = []
    if long_item is not None:
        sources.append(long_item)
    if current_item is not long_item:
        sources.append(current_item)

    for source in sources:
        for raw_point in source.get("forecast", []):
            if not isinstance(raw_point, dict):
                continue

            point = json.loads(json.dumps(raw_point, ensure_ascii=False))
            valid_time = point.get("time")
            valid_dt = parse_iso(valid_time)
            if valid_dt is None or valid_dt <= base_dt:
                continue

            rebased_hour = int(
                round((valid_dt - base_dt).total_seconds() / 3600)
            )
            if rebased_hour <= 0:
                continue

            point["forecast_hour"] = rebased_hour
            by_time[str(valid_time)] = point

    forecasts = list(by_time.values())
    forecasts.sort(
        key=lambda p: (
            p.get("forecast_hour") is None,
            p.get("forecast_hour") or 9999,
        )
    )
    return forecasts


def merge_typhoon_items(
    items: List[Dict[str, Any]],
    previous_item: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    JMA can publish a newer hourly update with only +1h data after a regular
    multi-day bulletin. Use the newest valid analysis for the current position
    and merge it with the newest available long-range forecast.

    If the current feed window no longer contains a long-range bulletin,
    preserve the previous output's future forecast only for a fresh hourly
    short-update situation. This prevents a 5-day track from collapsing to
    one point while avoiding indefinite use of stale forecasts.
    """
    ordered = sorted(
        items,
        key=lambda x: item_issue_time(x)
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    current_item = next(
        (
            item
            for item in ordered
            if isinstance(item.get("analysis"), dict)
            and item.get("analysis", {}).get("lat") is not None
            and item.get("analysis", {}).get("lon") is not None
        ),
        ordered[0],
    )

    current_horizon = forecast_horizon_hours(current_item)

    long_candidates = [
        item
        for item in ordered
        if forecast_horizon_hours(item) >= MIN_LONG_FORECAST_HOURS
    ]
    long_item: Optional[Dict[str, Any]] = (
        long_candidates[0] if long_candidates else None
    )
    long_source = "CURRENT_FETCH" if long_item is not None else None

    # Safety net for the exact failure mode seen on hourly JMA updates:
    # the latest bulletin is very short, and the older long bulletin can be
    # pushed outside MAX_XML_DOWNLOADS. Reuse only a still-fresh previous
    # long forecast and only while the latest bulletin is truly short.
    if (
        long_item is None
        and previous_item is not None
        and current_horizon <= SHORT_UPDATE_MAX_HOURS
        and forecast_horizon_hours(previous_item) >= MIN_LONG_FORECAST_HOURS
        and previous_forecast_is_fresh(
            previous_item,
            current_item.get("analysis", {}).get("time"),
        )
    ):
        long_item = previous_item
        long_source = "PREVIOUS_OUTPUT_FALLBACK"

    # If no multi-day source exists, still use the best forecast available
    # from this fetch rather than discarding forecasts completely.
    if long_item is None:
        long_item = max(
            ordered,
            key=lambda x: (
                forecast_horizon_hours(x),
                item_issue_time(x)
                or datetime.min.replace(tzinfo=timezone.utc),
            ),
        )
        long_source = "BEST_AVAILABLE_SHORT"

    merged = json.loads(json.dumps(current_item, ensure_ascii=False))
    merged["analysis"] = current_item.get("analysis")
    merged["forecast"] = merge_forecast_lists(
        merged.get("analysis"),
        current_item,
        long_item,
    )

    max_hour = 0
    for point in merged["forecast"]:
        value = point.get("forecast_hour")
        if isinstance(value, (int, float)):
            max_hour = max(max_hour, int(round(value)))

    merged["forecast_source_report_time"] = (
        long_item.get("forecast_source_report_time")
        or long_item.get("report_time")
        or long_item.get("feed_updated")
    )
    merged["forecast_source_url"] = (
        long_item.get("forecast_source_url")
        or long_item.get("source_url")
    )
    merged["forecast_merge_mode"] = long_source
    merged["forecast_max_hour"] = max_hour
    merged["forecast_quality"] = {
        "status": (
            "OK"
            if max_hour >= MIN_LONG_FORECAST_HOURS
            else "SHORT_ONLY"
        ),
        "current_bulletin_horizon_hour": current_horizon,
        "merged_horizon_hour": max_hour,
        "used_previous_output": (
            long_source == "PREVIOUS_OUTPUT_FALLBACK"
        ),
    }

    return merged


def semantic_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    clone = json.loads(json.dumps(data, ensure_ascii=False))
    clone.pop("generated_at_utc", None)
    return clone


def write_if_changed(data: Dict[str, Any]) -> bool:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT_PATH.exists():
        try:
            old = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            if semantic_payload(old) == semantic_payload(data):
                print("No meaningful JMA data change.")
                return False
        except Exception:
            pass

    data["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    OUTPUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Updated: {OUTPUT_PATH}")
    return True


def main() -> int:
    locations = load_locations()

    previous_data: Dict[str, Any] = {}
    if OUTPUT_PATH.exists():
        try:
            previous_data = json.loads(
                OUTPUT_PATH.read_text(encoding="utf-8")
            )
        except Exception:
            previous_data = {}

    all_entries: List[Dict[str, str]] = []
    feed_errors: List[str] = []

    for feed_url in (HIGH_FEED, LONG_FEED):
        try:
            all_entries.extend(parse_atom(fetch_bytes(feed_url)))
        except Exception as e:
            feed_errors.append(f"{feed_url}: {type(e).__name__}: {e}")

    entries = merge_feed_entries(all_entries)

    parsed_items: List[Dict[str, Any]] = []
    xml_errors: List[str] = []

    for entry in entries:
        try:
            parsed_items.append(parse_typhoon_xml(fetch_bytes(entry["url"]), entry, locations))
        except Exception as e:
            xml_errors.append(f"{entry['url']}: {type(e).__name__}: {e}")

    # Group all bulletins for the same typhoon. JMA can publish a newer
    # hourly short update (+1h) after a regular multi-day forecast, so do not
    # discard the older long-range bulletin simply because its report time is
    # slightly older.
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in parsed_items:
        grouped.setdefault(identity(item), []).append(item)

    typhoons: List[Dict[str, Any]] = []
    for key, items in grouped.items():
        previous_item = find_previous_typhoon(previous_data, key)
        typhoons.append(
            merge_typhoon_items(items, previous_item)
        )
    typhoons.sort(
        key=lambda x: parse_iso(x.get("report_time") or x.get("feed_updated"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    data = {
        "source": "Japan Meteorological Agency (JMA)",
        "product": "Typhoon Analysis and Forecast Information (5-day)",
        "parser_version": "1.3-JMA-CURRENT-LONG-MERGE",
        "feed_high": HIGH_FEED,
        "feed_long": LONG_FEED,
        "active_count": len(typhoons),
        "locations": locations,
        "typhoons": typhoons,
        "warnings": {
            "feed_errors": feed_errors,
            "xml_errors": xml_errors,
        },
    }

    write_if_changed(data)

    print(f"Active/recent typhoons: {len(typhoons)}")
    if feed_errors:
        print("Feed warnings:")
        for x in feed_errors:
            print(" -", x)
    if xml_errors:
        print("XML warnings:")
        for x in xml_errors[:5]:
            print(" -", x)

    # Fail only if both feeds failed entirely.
    if len(feed_errors) == 2 and not all_entries:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())