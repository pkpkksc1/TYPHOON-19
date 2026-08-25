#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SBLC Typhoon Similarity Engine v1.1

Historical fixed data:
    data/typhoon_history.json

Live inputs:
    data/jma_typhoon.json
    data/jtwc_typhoon.json
    data/dashboard.json   (optional: preferred current target name/number)

Output:
    data/typhoon_similarity.json

Five metrics:
    1. Shanghai closest distance             30%
    2. Shanghai-direction 34kt wind radius   25%
    3. Shanghai strong-wind impact duration  20%
    4. Max wind at closest approach          15%
    5. Movement speed at closest approach    10%

Similarity for each metric:
    min(current, historical) / max(current, historical) * 100

Important:
- Historical benchmark values are fixed and do not depend on live APIs.
- Live typhoon selection never uses an unnamed tropical depression.
- If the dashboard target is still active in JMA, that target is preferred.
- The active typhoon must match the dashboard target exactly.
- Another active typhoon is never substituted automatically.
- JTWC must match the selected typhoon name exactly.
- If forecast ends while the typhoon is still approaching Shanghai,
  the result is marked PROVISIONAL.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]

HISTORY_PATH = BASE_DIR / "data" / "typhoon_history.json"
JMA_PATH = BASE_DIR / "data" / "jma_typhoon.json"
JTWC_PATH = BASE_DIR / "data" / "jtwc_typhoon.json"
DASHBOARD_PATH = BASE_DIR / "data" / "dashboard.json"
OUTPUT_PATH = BASE_DIR / "data" / "typhoon_similarity.json"

PARSER_VERSION = "1.1"
SHANGHAI_LAT = 31.2304
SHANGHAI_LON = 121.4737


def load_json(path: Path, optional: bool = False) -> Dict[str, Any]:
    if not path.exists():
        if optional:
            return {}
        raise FileNotFoundError(f"Missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def fnum(v: Any) -> Optional[float]:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    text = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                return dt
            return dt
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt
    except ValueError:
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = (
        math.cos(p1) * math.sin(p2)
        - math.sin(p1) * math.cos(p2) * math.cos(dl)
    )
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def quadrant(b: float) -> str:
    if 0 <= b < 90:
        return "NE"
    if 90 <= b < 180:
        return "SE"
    if 180 <= b < 270:
        return "SW"
    return "NW"


def ratio_similarity(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    a, b = abs(float(a)), abs(float(b))
    if a == 0 and b == 0:
        return 100.0
    hi = max(a, b)
    if hi == 0:
        return 100.0
    return round(min(a, b) / hi * 100.0, 1)


def jma_points(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    points: List[Dict[str, Any]] = []
    analysis = item.get("analysis")
    if isinstance(analysis, dict):
        points.append({"forecast_hour": 0, **analysis})
    for p in item.get("forecast", []) or []:
        if isinstance(p, dict):
            points.append(p)
    return [
        p for p in points
        if fnum(p.get("lat")) is not None and fnum(p.get("lon")) is not None
    ]


def min_shanghai_distance_jma(item: Dict[str, Any]) -> float:
    pts = jma_points(item)
    if not pts:
        return float("inf")
    return min(
        haversine_km(
            float(p["lat"]), float(p["lon"]),
            SHANGHAI_LAT, SHANGHAI_LON,
        )
        for p in pts
    )


def select_active_jma(jma: Dict[str, Any], dashboard: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    storms = []
    for item in jma.get("typhoons", []) or []:
        if not isinstance(item, dict):
            continue
        meta = item.get("typhoon") or {}
        number = str(meta.get("number") or "").strip()
        name = str(meta.get("name") or "").strip().upper()
        if number and name:
            storms.append(item)

    if not storms:
        return None

    # Prefer the current dashboard target only while it still exists in JMA.
    dmeta = dashboard.get("typhoon") or {}
    dnum = str(dmeta.get("number") or "").strip()
    dname = str(dmeta.get("name") or "").strip().upper()

    if dnum and dname:
        for item in storms:
            meta = item.get("typhoon") or {}
            if (
                str(meta.get("number") or "").strip() == dnum
                and str(meta.get("name") or "").strip().upper() == dname
            ):
                return item

    # Dashboard target exists but is not active in JMA: do not substitute another storm.
    # This prevents data from a different typhoon from being mixed into the dashboard.
    if dnum or dname:
        return None

    # No dashboard target: do not guess another storm for this reference module.
    return None


def infer_jtwc_time(raw: Any, issue_time: Any) -> Optional[datetime]:
    if not raw or not issue_time:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) != 6:
        return None

    issue = parse_dt(issue_time)
    if issue is None:
        return None
    if issue.tzinfo is None:
        issue = issue.replace(tzinfo=timezone.utc)
    issue = issue.astimezone(timezone.utc)

    day, hour, minute = int(digits[:2]), int(digits[2:4]), int(digits[4:])
    candidates = []
    for shift in (-1, 0, 1):
        y, m = issue.year, issue.month + shift
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
    return min(candidates, key=lambda d: abs((d - issue).total_seconds()))


def match_jtwc(jtwc: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    name = name.strip().upper()
    matches = [
        s for s in (jtwc.get("storms", []) or [])
        if isinstance(s, dict)
        and str(s.get("name") or "").strip().upper() == name
    ]
    if not matches:
        return None
    return max(matches, key=lambda s: str(s.get("issue_time_utc") or ""))


def jtwc_timeline(storm: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    issue = storm.get("issue_time_utc")

    cur = storm.get("current")
    if isinstance(cur, dict) and fnum(cur.get("lat")) is not None and fnum(cur.get("lon")) is not None:
        result.append({
            "forecast_hour": 0,
            "time": infer_jtwc_time(cur.get("valid_time_raw"), issue),
            "lat": float(cur["lat"]),
            "lon": float(cur["lon"]),
            "max_wind_mps": fnum(cur.get("max_wind_mps")),
            "wind_radii": cur.get("wind_radii") or {},
        })

    for p in storm.get("forecast", []) or []:
        if not isinstance(p, dict):
            continue
        if fnum(p.get("lat")) is None or fnum(p.get("lon")) is None:
            continue
        result.append({
            "forecast_hour": p.get("forecast_hour"),
            "time": infer_jtwc_time(p.get("valid_time_raw"), issue),
            "lat": float(p["lat"]),
            "lon": float(p["lon"]),
            "max_wind_mps": fnum(p.get("max_wind_mps")),
            "wind_radii": p.get("wind_radii") or {},
        })

    result.sort(key=lambda x: (x.get("forecast_hour") is None, x.get("forecast_hour") or 0))
    return result


def radius34_for_shanghai(point: Dict[str, Any]) -> Optional[float]:
    b = bearing_deg(point["lat"], point["lon"], SHANGHAI_LAT, SHANGHAI_LON)
    q = quadrant(b)
    wr = point.get("wind_radii") or {}
    qdata = ((wr.get("34kt") or {}).get("quadrants") or {}).get(q) or {}
    return fnum(qdata.get("km"))


def closest_from_jtwc(storm: Dict[str, Any]) -> Dict[str, Any]:
    pts = jtwc_timeline(storm)
    if not pts:
        return {}

    for p in pts:
        p["distance_km"] = haversine_km(
            p["lat"], p["lon"], SHANGHAI_LAT, SHANGHAI_LON
        )
        p["radius34_km"] = radius34_for_shanghai(p)

    idx = min(range(len(pts)), key=lambda i: pts[i]["distance_km"])
    cp = pts[idx]

    confirmed = True
    if idx == len(pts) - 1 and len(pts) >= 2:
        # Forecast boundary is still approaching Shanghai.
        if pts[-1]["distance_km"] < pts[-2]["distance_km"]:
            confirmed = False

    return {
        "closest_distance_km": round(cp["distance_km"], 1),
        "wind_radius_34kt_km": round(cp["radius34_km"], 1)
            if cp["radius34_km"] is not None else None,
        "max_wind_mps_at_closest": round(cp["max_wind_mps"], 1)
            if cp["max_wind_mps"] is not None else None,
        "closest_forecast_hour": cp.get("forecast_hour"),
        "closest_time_utc": cp["time"].isoformat().replace("+00:00", "Z")
            if isinstance(cp.get("time"), datetime) else None,
        "closest_confirmed": confirmed,
    }



def strong_wind_impact_hours(storm: Dict[str, Any]) -> Optional[float]:
    """Return forecast hours Shanghai is inside the storm's 34kt wind area.

    This is intentionally a simple comparison metric, not an observed-impact
    reconstruction. Between JTWC forecast points, the margin
    (distance_to_shanghai - shanghai_direction_34kt_radius) is linearly
    interpolated to estimate entry/exit times.
    """
    pts = jtwc_timeline(storm)
    usable: List[Dict[str, Any]] = []

    for p in pts:
        radius = radius34_for_shanghai(p)
        if radius is None:
            continue
        hour = fnum(p.get("forecast_hour"))
        if hour is None:
            continue
        distance = haversine_km(
            p["lat"], p["lon"], SHANGHAI_LAT, SHANGHAI_LON
        )
        usable.append({
            "hour": float(hour),
            "margin": distance - float(radius),
        })

    usable.sort(key=lambda x: x["hour"])
    if not usable:
        return None
    if len(usable) == 1:
        return 0.0

    total = 0.0
    for a, b in zip(usable, usable[1:]):
        h1, h2 = a["hour"], b["hour"]
        m1, m2 = a["margin"], b["margin"]
        dt = h2 - h1
        if dt <= 0:
            continue

        in1 = m1 <= 0
        in2 = m2 <= 0
        if in1 and in2:
            total += dt
        elif in1 != in2:
            denom = abs(m1) + abs(m2)
            frac = (abs(m1) / denom) if denom else 0.5
            cross = h1 + dt * frac
            total += (cross - h1) if in1 else (h2 - cross)

    return round(total, 1)

def nearest_jma_movement(item: Dict[str, Any], target_time: Optional[datetime]) -> Optional[float]:
    pts = jma_points(item)
    candidates: List[Tuple[float, float]] = []

    for p in pts:
        spd = fnum(p.get("movement_speed_kmh"))
        if spd is None:
            continue
        dt = parse_dt(p.get("time"))
        if target_time is None or dt is None:
            candidates.append((999999999.0, spd))
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if target_time.tzinfo is None:
            target_time = target_time.replace(tzinfo=timezone.utc)
        diff = abs((dt.astimezone(timezone.utc) - target_time.astimezone(timezone.utc)).total_seconds())
        candidates.append((diff, spd))

    if not candidates:
        return None
    return round(min(candidates, key=lambda x: x[0])[1], 1)


def jma_fallback_metrics(item: Dict[str, Any]) -> Dict[str, Any]:
    pts = jma_points(item)
    if not pts:
        return {}
    best = min(
        pts,
        key=lambda p: haversine_km(
            float(p["lat"]), float(p["lon"]), SHANGHAI_LAT, SHANGHAI_LON
        ),
    )
    dist = haversine_km(
        float(best["lat"]), float(best["lon"]), SHANGHAI_LAT, SHANGHAI_LON
    )
    return {
        "closest_distance_km": round(dist, 1),
        "wind_radius_34kt_km": None,
        "max_wind_mps_at_closest": fnum(best.get("max_wind_mps")),
        "closest_forecast_hour": best.get("forecast_hour"),
        "closest_time_utc": best.get("time"),
        "closest_confirmed": False,
    }


def build_similarity(current: Dict[str, Any], history: Dict[str, Any]) -> List[Dict[str, Any]]:
    weights = history["weights_pct"]
    results = []

    key_map = {
        "closest_distance_km": "closest_distance_km",
        "wind_radius_34kt_km": "wind_radius_34kt_km",
        "strong_wind_impact_hours": "strong_wind_impact_hours",
        "max_wind_mps_at_closest": "max_wind_mps_at_closest",
        "movement_speed_kmh_at_closest": "movement_speed_kmh_at_closest",
    }

    for benchmark in history.get("benchmarks", []):
        hist = benchmark["metrics"]
        details = {}
        weighted_sum = 0.0
        available_weight = 0.0

        for metric, current_key in key_map.items():
            cur = fnum(current.get(current_key))
            old = fnum(hist.get(metric))
            sim = ratio_similarity(cur, old)
            weight = float(weights[metric])

            details[metric] = {
                "weight_pct": weight,
                "current": cur,
                "historical": old,
                "similarity_pct": sim,
            }

            if sim is not None:
                weighted_sum += sim * weight
                available_weight += weight

        overall = (
            round(weighted_sum / available_weight, 1)
            if available_weight > 0 else None
        )

        results.append({
            "benchmark_id": benchmark["id"],
            "benchmark_name": benchmark["name_ko"],
            "similarity_pct": overall,
            "available_weight_pct": round(available_weight, 1),
            "details": details,
        })

    results.sort(
        key=lambda x: (
            x["similarity_pct"] is not None,
            x["similarity_pct"] or -1,
        ),
        reverse=True,
    )
    return results


def main() -> int:
    history = load_json(HISTORY_PATH)
    jma = load_json(JMA_PATH, optional=True)
    jtwc = load_json(JTWC_PATH, optional=True)
    dashboard = load_json(DASHBOARD_PATH, optional=True)

    target = select_active_jma(jma, dashboard)

    if target is None:
        output = {
            "product": "Typhoon Historical Similarity",
            "parser_version": PARSER_VERSION,
            "status": "NO_ACTIVE_TYPHOON",
            "message_ko": "현재 비교할 공식 번호/이름의 활성 태풍이 없습니다.",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "benchmarks": [
                {
                    "id": b["id"],
                    "name_ko": b["name_ko"],
                    "metrics": b["metrics"],
                }
                for b in history.get("benchmarks", [])
            ],
        }
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(output["message_ko"])
        return 0

    meta = target.get("typhoon") or {}
    number = str(meta.get("number") or "").strip()
    name = str(meta.get("name") or "").strip().upper()

    storm = match_jtwc(jtwc, name)
    if storm:
        metrics = closest_from_jtwc(storm)
        source_mode = "JTWC+JMA"
    else:
        metrics = jma_fallback_metrics(target)
        source_mode = "JMA (JTWC radius unavailable)"

    target_time = parse_dt(metrics.get("closest_time_utc"))
    metrics["movement_speed_kmh_at_closest"] = nearest_jma_movement(target, target_time)

    metrics["strong_wind_impact_hours"] = (
        strong_wind_impact_hours(storm) if storm else None
    )

    comparisons = build_similarity(metrics, history)

    provisional_reasons = []
    if not metrics.get("closest_confirmed", False):
        provisional_reasons.append("예보 마지막 지점까지 상하이에 접근 중이거나 JTWC 최접근 확정 전")
    if metrics.get("wind_radius_34kt_km") is None:
        provisional_reasons.append("JTWC 34kt 상하이 방향 풍권반경 없음")
    if metrics.get("strong_wind_impact_hours") is None:
        provisional_reasons.append("상하이 강풍 영향 지속시간 계산에 필요한 JTWC 34kt 풍권 자료 없음")

    output = {
        "product": "Typhoon Historical Similarity",
        "parser_version": PARSER_VERSION,
        "status": "PROVISIONAL" if provisional_reasons else "OK",
        "source_mode": source_mode,
        "target_location": history["target_location"],
        "current_typhoon": {
            "number": number,
            "name": name,
        },
        "current_metrics": metrics,
        "weights_pct": history["weights_pct"],
        "comparisons": comparisons,
        "most_similar": comparisons[0] if comparisons else None,
        "provisional_reasons": provisional_reasons,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Typhoon: {number} {name}")
    print(f"Status: {output['status']}")
    for c in comparisons:
        print(
            f"{c['benchmark_name']}: {c['similarity_pct']}% "
            f"(available weight {c['available_weight_pct']}%)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
