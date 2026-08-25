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



LOCATION_ORDER = ["SUZHOU", "PVG", "ICN", "MNL", "HAN", "CRK"]

DIRECTION_KO = {
    "北": "북",
    "北北東": "북북동",
    "北東": "북동",
    "東北東": "동북동",
    "東": "동",
    "東南東": "동남동",
    "南東": "남동",
    "南南東": "남남동",
    "南": "남",
    "南南西": "남남서",
    "南西": "남서",
    "西南西": "서남서",
    "西": "서",
    "西北西": "서북서",
    "北西": "북서",
    "北北西": "북북서",
}


def load_dashboard() -> dict:
    if not DASHBOARD_JSON.exists():
        return {}
    try:
        return json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARNING: Could not read dashboard summary: {e}")
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


def china_time(value) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return str(value)


def risk_from_score(score) -> tuple[str, str]:
    try:
        value = int(score or 0)
    except Exception:
        value = 0

    if value >= 70:
        return "🔴", "위험"
    if value >= 35:
        return "🟡", "주의"
    return "🟢", "낮음"


def typhoon_strength(max_wind_mps) -> tuple[str, str]:
    """Simple mail-only display rule. Raw wind/pressure are always shown."""
    try:
        wind = float(max_wind_mps)
    except Exception:
        return "⚪", "자료 없음"

    if wind >= 54:
        return "🔴", "초강력"
    if wind >= 44:
        return "🔴", "매우 강함"
    if wind >= 33:
        return "🔴", "강함"
    if wind >= 25:
        return "🟡", "중간"
    if wind >= 17:
        return "🟢", "약함"
    return "⚪", "열대저압부 수준"


def route_summary_text(data: dict) -> str:
    routes = data.get("routes") or []
    if not routes:
        return "⚪ 노선 위험도 자료가 없습니다."

    ranked = sorted(
        routes,
        key=lambda r: int(r.get("score") or 0),
        reverse=True,
    )
    top_score = int(ranked[0].get("score") or 0)

    if top_score >= 70:
        names = [
            str(r.get("name_ko") or r.get("code") or "")
            for r in ranked
            if int(r.get("score") or 0) >= 70
        ][:2]
        return (
            f"🔴 {' / '.join(names)} 위험. "
            "해당 노선의 출고·입고 일정 조정과 우회 가능성을 우선 검토하세요."
        )

    if top_score >= 35:
        names = [
            str(r.get("name_ko") or r.get("code") or "")
            for r in ranked
            if int(r.get("score") or 0) >= 35
        ][:2]
        return (
            f"🟡 {' / '.join(names)} 주의. "
            "해당 노선의 강수·풍속 및 운송 변동을 집중 모니터링하세요."
        )

    locations = data.get("locations") or {}
    high_locations = []
    for code in LOCATION_ORDER:
        item = locations.get(code) or {}
        if int(item.get("score") or 0) >= 35:
            high_locations.append(
                str(item.get("name_ko") or code)
            )

    if high_locations:
        return (
            f"🟡 {' / '.join(high_locations[:2])} 거점 주의. "
            "해당 거점의 기상 및 연결 노선을 집중 확인하세요."
        )

    return (
        "🟢 주요 물류 노선과 6개 거점 모두 위험도 낮음. "
        "현재 기준 직접적인 태풍 물류 영향은 낮습니다."
    )


def build_rule_summary(data: dict) -> str:
    typhoon = data.get("typhoon") or {}
    current = typhoon.get("current") or {}
    forecast = typhoon.get("forecast_track") or []
    locations = data.get("locations") or {}

    lat = current.get("lat")
    lon = current.get("lon")
    position = (
        f"{fmt_num(lat, 1)}N / {fmt_num(lon, 1)}E"
        if lat is not None and lon is not None
        else "자료 없음"
    )

    direction_raw = str(current.get("movement_direction") or "").strip()
    direction_ko = DIRECTION_KO.get(direction_raw, direction_raw or "-")
    speed = current.get("movement_speed_kmh")
    movement = direction_ko
    if speed is not None:
        movement += f" · {fmt_num(speed, 1, ' km/h')}"

    strength_emoji, strength_label = typhoon_strength(
        current.get("max_wind_mps")
    )

    # Forecast: data-driven only. No place-name guessing.
    forecast_parts = []
    for point in forecast[:3]:
        hour = point.get("forecast_hour")
        plat = point.get("lat")
        plon = point.get("lon")
        if plat is None or plon is None:
            continue
        label = f"+{hour}h" if hour is not None else china_time(point.get("time"))
        forecast_parts.append(
            f"{label} {fmt_num(plat,1)}N/{fmt_num(plon,1)}E"
        )

    if forecast_parts:
        forecast_text = " → ".join(forecast_parts)
    elif typhoon.get("last_known") or typhoon.get("observation_status") == "LAST_KNOWN":
        forecast_text = "예보 종료 · 마지막 공식 위치 유지"
    else:
        forecast_text = "예상경로 자료 없음"

    # Six logistics hubs.
    rows = []
    for code in LOCATION_ORDER:
        item = locations.get(code) or {}
        name = str(item.get("name_ko") or code)
        emoji, label = risk_from_score(item.get("score"))

        current_distance = item.get("current_distance_km")
        closest_distance = item.get("closest_distance_km")

        if typhoon.get("last_known") or typhoon.get("observation_status") == "LAST_KNOWN":
            distance_text = f"마지막 위치 {fmt_num(current_distance,0,' km')}"
        else:
            distance_text = (
                f"현재 {fmt_num(current_distance,0,' km')} · "
                f"최접근 {fmt_num(closest_distance,0,' km')}"
            )

        rows.append(
            "<tr>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #294158;color:#ffffff;font-weight:700;'>{html.escape(name)}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #294158;color:#d6e4f2;'>{emoji} {label}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #294158;color:#9fb4c8;text-align:right;'>{html.escape(distance_text)}</td>"
            "</tr>"
        )

    conclusion = route_summary_text(data)

    return f"""
    <div style="padding:14px;background:#0e2034;border:1px solid #24415c;border-radius:10px;color:#b8c9da;font-size:12px;line-height:1.7;">
      <div style="font-size:14px;font-weight:900;color:#ffffff;margin-bottom:10px;">■ 자동 핵심 요약 · 위험도 규칙형</div>

      <div style="margin-bottom:9px;">
        <b style="color:#67b9ff;">① 현재 위치</b><br>
        <span style="color:#ffffff;font-weight:700;">{html.escape(position)}</span>
        · 중국시간 {html.escape(china_time(current.get("time")))}
        · {html.escape(movement)}
      </div>

      <div style="margin-bottom:9px;">
        <b style="color:#67b9ff;">② 태풍 세기</b><br>
        {strength_emoji} <span style="color:#ffffff;font-weight:700;">{html.escape(strength_label)}</span>
        · 최대풍속 {html.escape(fmt_num(current.get("max_wind_mps"),1," m/s"))}
        · 중심기압 {html.escape(fmt_num(current.get("pressure_hpa"),0," hPa"))}
      </div>

      <div style="margin-bottom:9px;">
        <b style="color:#67b9ff;">③ 예상 경로</b><br>
        <span style="color:#d6e4f2;">{html.escape(forecast_text)}</span>
      </div>

      <div style="margin-bottom:9px;">
        <b style="color:#67b9ff;">④ 6개 물류거점 영향</b>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
               style="margin-top:5px;border-collapse:collapse;background:#0a1929;border:1px solid #294158;border-radius:8px;">
          {''.join(rows)}
        </table>
      </div>

      <div style="margin-top:10px;padding:10px 12px;background:#102942;border:1px solid #315679;border-radius:8px;">
        <b style="color:#67b9ff;">⑤ 물류 한줄 결론</b><br>
        <span style="color:#ffffff;font-weight:700;">{html.escape(conclusion)}</span>
      </div>

      <div style="margin-top:9px;color:#8198ad;font-size:11px;">
        ※ 위 문구는 AI 생성이 아니라 dashboard.json의 위험점수(0~34 낮음 / 35~69 주의 / 70~100 위험)와 현재 태풍·기상 자료를 기준으로 자동 생성됩니다.
      </div>
    </div>
"""



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
    typhoon_label, typhoon_file_label = load_typhoon_identity()
    rule_summary_html = build_rule_summary(dashboard_data)

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
