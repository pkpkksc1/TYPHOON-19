from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "output").exists():
    ROOT = Path.cwd()

INPUT_HTML = ROOT / "output" / "typhoon_dashboard_offline.html"
OUTPUT_PNG = ROOT / "output" / "typhoon_dashboard_capture.png"


async def capture() -> int:
    if not INPUT_HTML.exists():
        print(f"ERROR: Offline dashboard not found: {INPUT_HTML}")
        print("Run src/build_offline_dashboard.py first.")
        return 2

    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        print("ERROR: playwright is not installed.")
        print(e)
        return 3

    file_url = INPUT_HTML.resolve().as_uri()
    print("Opening:", file_url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1440, "height": 2600},
            device_scale_factor=1,
        )

        await page.goto(file_url, wait_until="load")
        await page.wait_for_timeout(2500)

        # Best-effort wait for the dashboard shell to render.
        for selector in [".shell", "#locGrid", "body"]:
            try:
                await page.wait_for_selector(selector, timeout=5000)
                break
            except Exception:
                continue

        await page.screenshot(path=str(OUTPUT_PNG), full_page=True)
        await browser.close()

    print("Dashboard screenshot created:", OUTPUT_PNG)
    print("Size:", OUTPUT_PNG.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(capture()))
