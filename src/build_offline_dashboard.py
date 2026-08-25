from __future__ import annotations

from pathlib import Path
import base64
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
# When copied into the repository as src/build_offline_dashboard.py, ROOT should be repo root.
if not (ROOT / "index.html").exists():
    # Convenience fallback for direct local execution from a staging folder.
    ROOT = Path.cwd()

INDEX = ROOT / "index.html"
DASHBOARD = ROOT / "data" / "dashboard.json"
AUTO_MODE = ROOT / "data" / "typhoon_auto_mode.json"
SIMILARITY = ROOT / "data" / "typhoon_similarity.json"
MAP_IMAGE = ROOT / "assets" / "typhoon-map-clean.png"
OUTPUT = ROOT / "output" / "typhoon_dashboard_offline.html"


def read_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    required = [INDEX, DASHBOARD, MAP_IMAGE]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        print("Missing required files:")
        for p in missing:
            print(" -", p)
        return 1

    html = INDEX.read_text(encoding="utf-8")
    dashboard = read_json(DASHBOARD, {})
    auto_mode = read_json(AUTO_MODE, {"enabled": False, "last_run_at_utc": None})
    similarity = read_json(
        SIMILARITY,
        {
            "status": "NO_ACTIVE_TYPHOON",
            "message_ko": "현재 비교할 태풍 데이터가 없습니다.",
            "comparisons": [],
        },
    )

    # 1) Embed the static map image as a data URI.
    map_b64 = base64.b64encode(MAP_IMAGE.read_bytes()).decode("ascii")
    html = html.replace(
        "./assets/typhoon-map-clean.png",
        f"data:image/png;base64,{map_b64}",
    )

    # 2) Keep the existing dashboard JavaScript untouched by intercepting only
    #    its three local JSON reads and returning the snapshot embedded here.
    snapshot = json.dumps(
        {
            "dashboard": dashboard,
            "auto_mode": auto_mode,
            "similarity": similarity,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")

    shim = f'''\n// ==========================================\n// OFFLINE SNAPSHOT DATA - generated automatically\n// ==========================================\nconst __OFFLINE_SNAPSHOT = {snapshot};\n\nconst __offlineJsonResponse = (obj) =>\n  new Response(JSON.stringify(obj), {{\n    status: 200,\n    headers: {{"Content-Type":"application/json; charset=utf-8"}}\n  }});\n\nwindow.fetch = async function(input){{\n  const u = String(input && input.url ? input.url : input);\n  if(u.includes("data/dashboard.json")) return __offlineJsonResponse(__OFFLINE_SNAPSHOT.dashboard);\n  if(u.includes("data/typhoon_auto_mode.json")) return __offlineJsonResponse(__OFFLINE_SNAPSHOT.auto_mode);\n  if(u.includes("data/typhoon_similarity.json")) return __offlineJsonResponse(__OFFLINE_SNAPSHOT.similarity);\n  throw new Error("OFFLINE SNAPSHOT: external network request blocked");\n}};\n\n'''

    marker = "<script>\n\nconst DATA_URL ="
    if marker not in html:
        print("Could not find dashboard main script marker in index.html")
        return 2
    html = html.replace("<script>\n\nconst DATA_URL =", "<script>\n" + shim + "\nconst DATA_URL =", 1)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")

    print("Offline dashboard created:", OUTPUT)
    print("Size:", OUTPUT.stat().st_size, "bytes")
    print("Dashboard generated_at_utc:", dashboard.get("generated_at_utc"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
