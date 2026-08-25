#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SBLC Typhoon Dashboard - Step 2
KMA TyphoonInfoService -> data/kma_typhoon.json

Required GitHub Secret:
    KMA_API_KEY

API:
    https://apis.data.go.kr/1360000/TyphoonInfoService

Version:
    KMA parser v2.2

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = BASE_DIR / "data" / "kma_typhoon.json"

BASE_URL = "https://apis.data.go.kr/1360000/TyphoonInfoService"
LIST_ENDPOINT = f"{BASE_URL}/getTyphoonInfoList"
FCST_ENDPOINT = f"{BASE_URL}/getTyphoonFcst"

PARSER_VERSION = "2.2"
USER_AGENT = "sblc-typhoon-dashboard/2.2 (KMA OpenAPI client)"

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def get_api_key() -> str:
    raw = os.environ.get("KMA_API_KEY", "").strip()
    if not raw:
        raise RuntimeError("KMA_API_KEY secret is missing.")

    # 공공데이터포털 인증키가 URL 인코딩된 상태여도 정상 처리.
    return urllib.parse.unquote(raw)


def fetch_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"

    last_error: Optional[BaseException] = None

    # KMA 서버가 간헐적으로 느릴 때를 대비해 최대 3회 재시도.
    for attempt in range(1, 4):
        req = urllib.request.Request(
            full_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )

        try:
            print(f"KMA request attempt {attempt}/3: {url}")

            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", errors="replace")

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"KMA returned non-JSON data: {raw[:500]}"
                ) from e

            check_api_result(data)
            return data

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")

            # 인증키/요청값 문제는 재시도해도 해결되지 않으므로 즉시 종료.
            if 400 <= e.code < 500:
                raise RuntimeError(
                    f"KMA HTTP {e.code}: {body[:500]}"
                ) from e

            last_error = e

        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e

        if attempt < 3:
            wait_sec = attempt * 10
            print(
                f"KMA request failed: {last_error}. "
                f"Retrying in {wait_sec}s..."
            )
            time.sleep(wait_sec)

    raise RuntimeError(
        f"KMA connection failed after 3 attempts: {last_error}"
    )


def check_api_result(data: Dict[str, Any]) -> None:
    response = data.get("response")

    if not isinstance(response, dict):
        return

    header = response.get("header")

    if not isinstance(header, dict):
        return

    code = str(header.get("resultCode", "")).strip()
    message = str(header.get("resultMsg", "")).strip()

    if code and code != "00":
        raise RuntimeError(f"KMA API error {code}: {message}")


def extract_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    response = data.get("response", data)

    if not isinstance(response, dict):
        return []

    body = response.get("body", response)

    if not isinstance(body, dict):
        return []

    items = body.get("items", [])

    if isinstance(items, dict):
        items = items.get("item", [])

    if isinstance(items, dict):
        return [items]

    if isinstance(items, list):
        return [x for x in items if isinstance(x, dict)]

    return []


def first_value(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def digits_only(value: Any) -> str:
    if value is None:
        return ""

    return "".join(ch for ch in str(value) if ch.isdigit())


def parse_kma_time(value: Any) -> Optional[datetime]:
    s = digits_only(value)

    formats = {
        12: "%Y%m%d%H%M",
        10: "%Y%m%d%H",
        8: "%Y%m%d",
    }

    fmt = formats.get(len(s))
    if not fmt:
        return None

    try:
        return datetime.strptime(s, fmt).replace(tzinfo=KST)
    except ValueError:
        return None


def bulletin_sort_key(item: Dict[str, Any]) -> tuple:
    # 현재 KMA 실제 응답은 announceTime / typhoonSeq 사용.
    tm_fc = first_value(
        item,
        "announceTime",
        "tmFc",
        "tmfc",
        "TM_FC",
    )

    typ_seq = first_value(
        item,
        "typhoonSeq",
        "typSeq",
        "typseq",
        "typNo",
    )

    dt = parse_kma_time(tm_fc)
    seq = to_int(typ_seq) or -1

    return (
        dt or datetime.min.replace(tzinfo=KST),
        seq,
    )


def get_latest_bulletin(service_key: str) -> Dict[str, Any]:
    """
    최근 3일 범위에서 태풍정보 목록을 조회하고
    가장 최신 발표자료 1건을 선택한다.
    """
    found: List[Dict[str, Any]] = []
    today = now_kst().date()

    for offset in range(0, 3):
        day = (today - timedelta(days=offset)).strftime("%Y%m%d")

        data = fetch_json(
            LIST_ENDPOINT,
            {
                "serviceKey": service_key,
                "pageNo": 1,
                "numOfRows": 100,
                "dataType": "JSON",
                "tmFc": day,
            },
        )

        found.extend(extract_items(data))

    if not found:
        return {}

    found.sort(
        key=bulletin_sort_key,
        reverse=True,
    )

    return found[0]


def normalize_forecast_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    KMA 예보 응답을 대시보드용으로 단순화.
    원본은 raw_item에 그대로 남겨 실제 필드가 달라도 확인 가능.
    """
    return {
        "forecast_time": first_value(
            item,
            "tmEf",
            "tmFcst",
            "tmFcstDate",
            "tm",
        ),
        "lat": to_float(
            first_value(
                item,
                "lat",
                "latitude",
            )
        ),
        "lon": to_float(
            first_value(
                item,
                "lon",
                "longitude",
            )
        ),
        "pressure_hpa": to_float(
            first_value(
                item,
                "ps",
                "pres",
                "pressure",
            )
        ),
        "max_wind_mps": to_float(
            first_value(
                item,
                "ws",
                "windSpeed",
            )
        ),
        "movement_direction": first_value(
            item,
            "dir",
            "direction",
        ),
        "movement_speed_kmh": to_float(
            first_value(
                item,
                "sp",
                "speed",
            )
        ),
        "probability_radius_km": to_float(
            first_value(
                item,
                "radPr",
                "radpr",
                "probabilityRadius",
            )
        ),
        "gale_radius_km": to_float(
            first_value(
                item,
                "rad15",
                "galeRadius",
            )
        ),
        "storm_radius_km": to_float(
            first_value(
                item,
                "rad25",
                "stormRadius",
            )
        ),
        "forecast_text_ko": first_value(
            item,
            "fclocko",
            "fcstKo",
            "remark",
        ),
        "raw_item": item,
    }


def get_forecast(
    service_key: str,
    bulletin_time: str,
    typhoon_seq: int,
) -> List[Dict[str, Any]]:

    data = fetch_json(
        FCST_ENDPOINT,
        {
            "serviceKey": service_key,
            "pageNo": 1,
            "numOfRows": 100,
            "dataType": "JSON",
            "tmFc": bulletin_time,
            "typSeq": typhoon_seq,
        },
    )

    normalized = [
        normalize_forecast_item(x)
        for x in extract_items(data)
    ]

    normalized.sort(
        key=lambda x: (
            parse_kma_time(x.get("forecast_time"))
            or datetime.max.replace(tzinfo=KST)
        )
    )

    return normalized


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
            old = json.loads(
                OUTPUT_PATH.read_text(
                    encoding="utf-8"
                )
            )

            if semantic_payload(old) == semantic_payload(data):
                print("No meaningful KMA data change.")
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
    service_key = get_api_key()

    print(f"KMA parser version: {PARSER_VERSION}")

    latest = get_latest_bulletin(service_key)

    if not latest:
        data = {
            "source": "Korea Meteorological Administration (KMA)",
            "product": "TyphoonInfoService",
            "parser_version": PARSER_VERSION,
            "active_count": 0,
            "bulletin": None,
            "forecast": [],
            "message": "No recent KMA typhoon bulletin found.",
        }

        write_if_changed(data)

        print("No recent KMA typhoon bulletin found.")
        return 0

    # 실제 응답에서 확인된 키:
    # announceTime = 202608191030
    # typhoonSeq   = 35
    tm_fc_raw = first_value(
        latest,
        "announceTime",
        "tmFc",
        "tmfc",
        "TM_FC",
    )

    typ_seq_raw = first_value(
        latest,
        "typhoonSeq",
        "typSeq",
        "typseq",
        "typNo",
    )

    tm_fc = digits_only(tm_fc_raw)
    typ_seq = to_int(typ_seq_raw)

    if not tm_fc or typ_seq is None:
        raise RuntimeError(
            "KMA list data was returned, but "
            "announceTime/typhoonSeq could not be found. "
            f"Latest raw item: {latest}"
        )

    print(
        f"Latest KMA bulletin: "
        f"announceTime={tm_fc}, "
        f"typhoonSeq={typ_seq}"
    )

    forecasts = get_forecast(
        service_key,
        tm_fc,
        typ_seq,
    )

    data = {
        "source": "Korea Meteorological Administration (KMA)",
        "product": "TyphoonInfoService",
        "parser_version": PARSER_VERSION,
        "active_count": 1,
        "bulletin": {
            "announceTime": tm_fc,
            "typhoonSeq": typ_seq,
            "announceSeq": first_value(
                latest,
                "announceSeq",
            ),
            "title": first_value(
                latest,
                "title",
                "tit",
            ),
            "raw_item": latest,
        },
        "forecast": forecasts,
    }

    write_if_changed(data)

    print(
        f"KMA bulletin: "
        f"announceTime={tm_fc}, "
        f"typhoonSeq={typ_seq}"
    )

    print(
        f"Forecast points: {len(forecasts)}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
