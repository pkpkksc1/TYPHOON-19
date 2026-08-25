from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo
import os
import smtplib

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "output").exists():
    ROOT = Path.cwd()

OFFLINE_HTML = ROOT / "output" / "typhoon_dashboard_offline.html"
CAPTURE_PNG = ROOT / "output" / "typhoon_dashboard_capture.png"
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
              <div style="margin-top:6px;font-size:24px;line-height:1.25;font-weight:900;color:#ffffff;">태풍 물류대시보드</div>
              <div style="margin-top:6px;font-size:12px;line-height:1.6;color:#99afc5;">
                메일 본문에는 최신 대시보드 화면 캡처가 포함되어 있습니다.<br>
                자세한 확인은 첨부된 오프라인 HTML 파일을 열어 주세요.
              </div>
            </td>
          </tr>

          <tr>
            <td style="padding:0 20px 20px 20px;">
              <div style="padding:12px 14px;background:#0e2034;border:1px solid #24415c;border-radius:10px;font-size:12px;line-height:1.8;color:#b8c9da;">
                <b style="color:#ffffff;">■ 항공 현황</b><br>
                🟡 운항 중 / 🔵 출발 예정 / 🟢 도착 완료 / 🔴 문제<br><br>
                <b style="color:#ffffff;">■ 업데이트</b><br>
                중국시간 기준 최신 대시보드
              </div>
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
    global SUBJECT
    SUBJECT = SUBJECT.format(time=china_now.strftime("%Y-%m-%d %H:%M"))
    attachment_name = f"SBLC_태풍_물류대시보드_{china_now:%Y%m%d_%H%M}_CN.html"

    msg = EmailMessage()
    msg["From"] = email_user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = SUBJECT
    msg.set_content(PLAIN_BODY)

    html_body = build_html_body().replace(
        "SBLC_태풍_물류대시보드_YYYYMMDD_HHMM_CN.html",
        attachment_name,
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
    print(" Subject:", SUBJECT)
    print(" Inline image:", CAPTURE_PNG.name, CAPTURE_PNG.stat().st_size, "bytes")
    print(" Attachment:", attachment_name, len(html_data), "bytes")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(email_user, app_password)
        smtp.send_message(msg)

    print("EMAIL SENT SUCCESSFULLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
