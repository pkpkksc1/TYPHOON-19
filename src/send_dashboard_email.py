from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo
import html
import json
import os
import re
import smtplib

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "output").exists():
    ROOT = Path.cwd()

OFFLINE_HTML = ROOT / "output" / "typhoon_dashboard_offline.html"
CAPTURE_PNG = ROOT / "output" / "typhoon_dashboard_capture.png"
DASHBOARD_JSON = ROOT / "data" / "dashboard.json"
WEATHER_JSON = ROOT / "data" / "weather.json"
SUBJECT = "[물류] SBLC 태풍 물류대시보드 | {time}"
PLAIN_BODY = """안녕하세요.

SBLC 태풍 물류대시보드를 보내드립니다.
메일 본문에는 최신 대시보드 화면 캡처 이미지가 포함되어 있으며,
자세한 내용은 첨부된 오프라인 HTML 파일에서 확인해 주세요.

※ 첨부 HTML은 인터넷 연결 없이 확인할 수 있습니다.
"""


def env(name: str, required: bool = True) -> str:
    value = os.getenv(name, "").strip()
    if required and not value:
        print(f"ERROR: Missing environment variable: {name}")
        raise SystemExit(2)
    return value


def parse_recipients(raw: str) -> list[str]:
    values = [x.strip() for x in raw.replace(";", ",").split(",")]
    return [x for x in values if x]


def load_typhoon_identity() -> tuple[str, str]:
    """Read current typhoon number/name from data/dashboard.json.

    Returns:
      display_label: e.g. "19호 NARRA"
      file_label:    e.g. "19호_NARRA"
    """
    if not DASHBOARD_JSON.exists():
        return "태풍", "태풍"

    try:
        data = json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
        typhoon = data.get("typhoon") or {}

        number = str(typhoon.get("number") or "").strip()
        name = str(typhoon.get("name") or "").strip().upper()

        if len(number) == 4 and number.isdigit():
            storm_no = str(int(number[2:]))
        else:
            storm_no = number

        if storm_no and name:
            display_label = f"{storm_no}호 {name}"
            file_label = f"{storm_no}호_{name}"
        elif storm_no:
            display_label = f"{storm_no}호"
            file_label = f"{storm_no}호"
        elif name:
            display_label = name
            file_label = name
        else:
            display_label = "태풍"
            file_label = "태풍"

        file_label = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", file_label)
        return display_label, file_label

    except Exception as e:
        print(f"WARNING: Could not read typhoon identity from {DASHBOARD_JSON}: {e}")
        return "태풍", "태풍"




NANNING_LAT = 22.8170
NANNING_LON = 108.3665


def load_dashboard() -> dict:
    if not DASHBOARD_JSON.exists():
        return {}
    try:
        return json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARNING: Could not read dashboard summary: {e}")
        return {}


def load_weather() -> dict:
    if not WEATHER_JSON.exists():
        return {}
    try:
        return json.loads(WEATHER_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARNING: Could not read weather data: {e}")
        return {}


def fmt_num(value, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "-"
    try:
        n = float(value)
        if digits == 0:
            body = f"{n:,.0f}"
        else:
            body = f"{n:,.{digits}f}"
        return f"{body}{suffix}"
    except Exception:
        return f"{value}{suffix}"


def haversine_km(lat1, lon1, lat2, lon2):
    try:
        lat1 = float(lat1)
        lon1 = float(lon1)
        lat2 = float(lat2)
        lon2 = float(lon2)
    except Exception:
        return None

    import math

    radius = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return round(2 * radius * math.asin(math.sqrt(a)))


def distance_score(distance_km) -> int:
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


def rain_score(rain_mm) -> int:
    try:
        rain = float(rain_mm or 0)
    except Exception:
        rain = 0.0

    if rain >= 10:
        return 25
    if rain >= 5:
        return 20
    if rain >= 2:
        return 15
    if rain >= 0.5:
        return 8
    if rain > 0:
        return 3
    return 0


def wind_score(wind_mps) -> int:
    try:
        wind = float(wind_mps or 0)
    except Exception:
        wind = 0.0

    if wind >= 20:
        return 20
    if wind >= 15:
        return 15
    if wind >= 10:
        return 10
    if wind >= 7:
        return 5
    return 0


def logistics_level(score: int) -> tuple[str, str]:
    if score >= 70:
        return "🔴", "위험"
    if score >= 35:
        return "🟡", "주의"
    return "🟢", "낮음"


def build_review_items(distance_km, rain_mm, wind_mps) -> list[str]:
    items = []

    try:
        rain = float(rain_mm or 0)
    except Exception:
        rain = 0.0

    try:
        wind = float(wind_mps or 0)
    except Exception:
        wind = 0.0

    if distance_km is not None:
        if distance_km <= 200:
            items.append(
                f"태풍 중심과 난닝 거리가 약 {distance_km:.0f} km로 매우 가까워 "
                "육상운송 일정과 도로 통제 가능성을 우선 확인하세요."
            )
        elif distance_km <= 400:
            items.append(
                f"태풍 중심과 난닝 거리가 약 {distance_km:.0f} km입니다. "
                "이동 방향 변화와 현지 도로 상황을 집중 확인하세요."
            )
        elif distance_km <= 700:
            items.append(
                f"태풍 중심과 난닝 거리가 약 {distance_km:.0f} km입니다. "
                "강수·풍속 변화 여부를 지속 확인하세요."
            )

    if rain >= 10:
        items.append(
            f"현재 난닝 시간당 강수량이 {rain:.1f} mm로 높습니다. "
            "도로 침수·저속 운행 및 도착 지연 가능성을 검토하세요."
        )
    elif rain >= 5:
        items.append(
            f"현재 난닝 시간당 강수량이 {rain:.1f} mm입니다. "
            "강한 비에 따른 차량 운행 지연 가능성을 확인하세요."
        )
    elif rain >= 2:
        items.append(
            f"현재 난닝 시간당 강수량이 {rain:.1f} mm입니다. "
            "우천에 따른 육상운송 속도 저하에 주의하세요."
        )
    elif rain >= 0.5:
        items.append(
            f"현재 난닝에 비가 내리고 있습니다({rain:.1f} mm/h). "
            "현지 도로 상태를 확인하세요."
        )

    if wind >= 20:
        items.append(
            f"현재 난닝 풍속이 {wind:.1f} m/s로 매우 강합니다. "
            "고속도로·차량 운행 제한 가능성을 우선 확인하세요."
        )
    elif wind >= 15:
        items.append(
            f"현재 난닝 풍속이 {wind:.1f} m/s로 강합니다. "
            "차량 운행 안전과 현지 통제 여부를 확인하세요."
        )
    elif wind >= 10:
        items.append(
            f"현재 난닝 풍속이 {wind:.1f} m/s입니다. "
            "대형차량 운행 시 강풍 영향을 확인하세요."
        )

    if not items:
        items.append(
            "현재 업데이트 시점 기준 난닝의 강수·풍속 영향은 낮습니다. "
            "쑤저우 → 난닝 육상운송의 특별 기상 검토사항은 없습니다."
        )

    return items


def build_logistics_review(data: dict, weather_data: dict) -> str:
    typhoon = data.get("typhoon") or {}
    current = typhoon.get("current") or {}

    nanning = (
        (weather_data.get("locations") or {}).get("NANNING")
        or {}
    )
    nanning_current = nanning.get("current") or {}

    rain_mm = nanning_current.get("rain_mm")
    wind_mps = nanning_current.get("wind_mps")
    wind_dir = nanning_current.get("wind_dir") or "-"
    weather_updated = nanning_current.get("last_updated") or "-"

    distance_km = haversine_km(
        current.get("lat"),
        current.get("lon"),
        NANNING_LAT,
        NANNING_LON,
    )

    score = min(
        100,
        distance_score(distance_km)
        + rain_score(rain_mm)
        + wind_score(wind_mps),
    )
    emoji, level = logistics_level(score)

    if not nanning:
        review_html = (
            "<div style='color:#ffcf66;font-weight:700;'>"
            "난닝 기상자료 갱신 대기 · WeatherAPI 업데이트 후 자동 표시"
            "</div>"
        )
    else:
        review_items = build_review_items(
            distance_km,
            rain_mm,
            wind_mps,
        )
        review_html = "".join(
            f"<div style='margin-top:4px;'>• {html.escape(item)}</div>"
            for item in review_items
        )

    return f'''
    <div style="padding:14px;background:#0e2034;border:1px solid #24415c;border-radius:10px;color:#b8c9da;font-size:12px;line-height:1.7;">
      <div style="font-size:14px;font-weight:900;color:#ffffff;margin-bottom:10px;">
        ■ 물류 검토사항 · 쑤저우 → 난닝 육상운송
      </div>

      <div style="display:block;padding:10px 12px;background:#0a1929;border:1px solid #294158;border-radius:8px;">
        <div style="color:#8198ad;font-size:11px;margin-bottom:7px;">
          WeatherAPI 업데이트 기준 · {html.escape(str(weather_updated))}
        </div>

        <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
               style="border-collapse:collapse;">
          <tr>
            <td style="padding:5px;color:#9fb4c8;">태풍 ↔ 난닝 현재거리</td>
            <td style="padding:5px;text-align:right;color:#ffffff;font-weight:800;">
              {html.escape(fmt_num(distance_km,0," km"))}
            </td>
          </tr>
          <tr>
            <td style="padding:5px;color:#9fb4c8;">난닝 현재 강수량</td>
            <td style="padding:5px;text-align:right;color:#ffffff;font-weight:800;">
              {html.escape(fmt_num(rain_mm,2," mm/h"))}
            </td>
          </tr>
          <tr>
            <td style="padding:5px;color:#9fb4c8;">난닝 현재 풍속</td>
            <td style="padding:5px;text-align:right;color:#ffffff;font-weight:800;">
              {html.escape(fmt_num(wind_mps,1," m/s"))} · {html.escape(str(wind_dir))}
            </td>
          </tr>
          <tr>
            <td style="padding:5px;color:#9fb4c8;">현재 영향도</td>
            <td style="padding:5px;text-align:right;color:#ffffff;font-weight:900;">
              {emoji} {level} · {score}점
            </td>
          </tr>
        </table>
      </div>

      <div style="margin-top:10px;padding:10px 12px;background:#102942;border:1px solid #315679;border-radius:8px;">
        <b style="color:#67b9ff;">자동 검토사항</b>
        {review_html}
      </div>

      <div style="margin-top:9px;color:#8198ad;font-size:11px;">
        ※ 72시간 예보값은 사용하지 않습니다. 난닝의 WeatherAPI 최신 업데이트 시점 강수량·풍속과 현재 태풍 중심거리를 기준으로 자동 판정합니다.
      </div>
    </div>
'''



def build_html_body() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f3f6fa;font-family:Arial,'Noto Sans KR','Malgun Gothic',sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f6fa;">
    <tr>
      <td align="center" style="padding:18px 10px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:1200px;background:#0a1726;border:1px solid #20344a;border-radius:14px;overflow:hidden;">
          <tr>
            <td style="padding:18px 20px 12px 20px;">
              <div style="font-size:11px;letter-spacing:1.2px;color:#67b9ff;font-weight:700;">SBLC · TYPHOON LOGISTICS CONTROL</div>
              <div style="margin-top:6px;font-size:24px;line-height:1.25;font-weight:900;color:#ffffff;">__TYPHOON_LABEL__ 태풍 물류대시보드</div>
              <div style="margin-top:6px;font-size:12px;line-height:1.6;color:#99afc5;">
                메일 본문에는 최신 대시보드 화면 캡처가 포함되어 있습니다.<br>
                자세한 확인은 첨부된 오프라인 HTML 파일을 열어 주세요.
              </div>
            </td>
          </tr>

          <tr>
            <td style="padding:0 20px 20px 20px;">
              __RULE_SUMMARY__
            </td>
          </tr>

          <tr>
            <td style="padding:0 20px 20px 20px;">
              <img src="cid:dashboard_capture" alt="SBLC 태풍 물류대시보드" style="display:block;width:100%;height:auto;border:1px solid #2a415a;border-radius:12px;background:#081321;">
            </td>
          </tr>
          <tr>
            <td style="padding:0 20px 20px 20px;">
              <div style="padding:12px 14px;background:#0e2034;border:1px solid #24415c;border-radius:10px;font-size:12px;line-height:1.7;color:#b8c9da;">
                📎 첨부파일: <b style="color:#ffffff;">SBLC_태풍_물류대시보드_YYYYMMDD_HHMM_CN.html</b><br>
                첨부 HTML은 발송 시점의 데이터가 포함된 오프라인 버전이며 인터넷 연결 없이 확인할 수 있습니다.
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def main() -> int:
    email_user = env("EMAIL_USER")
    app_password = env("EMAIL_APP_PASSWORD").replace(" ", "")
    recipients = parse_recipients(env("EMAIL_TO"))

    if not recipients:
        print("ERROR: EMAIL_TO has no valid recipients")
        return 2
    if not OFFLINE_HTML.exists():
        print(f"ERROR: Offline dashboard not found: {OFFLINE_HTML}")
        print("Run src/build_offline_dashboard.py first.")
        return 3
    if not CAPTURE_PNG.exists():
        print(f"ERROR: Dashboard capture not found: {CAPTURE_PNG}")
        print("Run src/capture_dashboard.py first.")
        return 4

    china_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    dashboard_data = load_dashboard()
    weather_data = load_weather()
    typhoon_label, typhoon_file_label = load_typhoon_identity()
    rule_summary_html = build_logistics_review(
        dashboard_data,
        weather_data,
    )

    subject = (
        f"[물류][{typhoon_label}] "
        f"SBLC 태풍 물류대시보드 | {china_now:%Y-%m-%d %H:%M}"
    )

    attachment_name = (
        f"SBLC_태풍{typhoon_file_label}_물류대시보드_"
        f"{china_now:%Y%m%d_%H%M}_CN.html"
    )

    msg = EmailMessage()
    msg["From"] = email_user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(PLAIN_BODY)

    html_body = (
        build_html_body()
        .replace("__TYPHOON_LABEL__", typhoon_label)
        .replace("__RULE_SUMMARY__", rule_summary_html)
        .replace(
            "SBLC_태풍_물류대시보드_YYYYMMDD_HHMM_CN.html",
            attachment_name,
        )
    )
    msg.add_alternative(html_body, subtype="html")
    html_part = msg.get_payload()[-1]
    html_part.add_related(
        CAPTURE_PNG.read_bytes(),
        maintype="image",
        subtype="png",
        cid="dashboard_capture",
        filename="dashboard_capture.png",
        disposition="inline",
    )

    html_data = OFFLINE_HTML.read_bytes()
    msg.add_attachment(
        html_data,
        maintype="text",
        subtype="html",
        filename=attachment_name,
    )

    print("Preparing image-body email")
    print(" From:", email_user)
    print(" To:", ", ".join(recipients))
    print(" Subject:", subject)
    print(" Inline image:", CAPTURE_PNG.name, CAPTURE_PNG.stat().st_size, "bytes")
    print(" Attachment:", attachment_name, len(html_data), "bytes")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(email_user, app_password)
        smtp.send_message(msg)

    print("EMAIL SENT SUCCESSFULLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
