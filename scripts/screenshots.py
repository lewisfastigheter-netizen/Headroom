"""Capture full-page screenshots of every page for the README.

Usage: uv run python scripts/screenshots.py [base_url] [out_dir] [demo|live]
Requires a running app (make app) and Playwright's Chromium.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ISSUER = {"demo": "DEMO-009", "live": "559286-6809"}  # live: Holmström Fastigheter Holding


def pages(mode: str) -> dict[str, str]:
    q = f"data={mode}"
    return {
        "market": f"?{q}",
        "companies": f"companies?{q}",
        "locations": f"locations?{q}",
        "location": f"locations?level=kommun&code=0380&{q}",
        "issuer": f"issuer?org={ISSUER[mode]}&{q}",
        "method": f"method?{q}",
    }


async def main(base: str, out: Path, mode: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": 1440, "height": 900}, device_scale_factor=1
        )
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        for name, path in pages(mode).items():
            await page.goto(f"{base.rstrip('/')}/{path}", wait_until="networkidle")
            await page.wait_for_selector('[data-testid="stAppViewContainer"]')
            await page.wait_for_timeout(3500)
            exc = await page.locator('[data-testid="stException"]').count()
            # Streamlit scrolls inside its own container, so grow the viewport to the content.
            h = await page.evaluate(
                "() => Math.max(...[...document.querySelectorAll('[data-testid=stMain], "
                "[data-testid=stAppViewContainer], section.main')].map(e => e.scrollHeight), 900)"
            )
            await page.set_viewport_size({"width": 1440, "height": min(int(h) + 40, 12000)})
            await page.wait_for_timeout(1500)
            await page.screenshot(path=out / f"{mode}_{name}.png", full_page=False)
            await page.set_viewport_size({"width": 1440, "height": 900})
            print(f"{name}: {'EXCEPTION' if exc else 'ok'}")
        await browser.close()
        for e in errors[:10]:
            print("console:", e[:200])


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8501"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/screenshots")
    mode = sys.argv[3] if len(sys.argv) > 3 else "demo"
    asyncio.run(main(base, out, mode))
