# render_page_debug.py
from playwright.sync_api import sync_playwright
import sys

if len(sys.argv) < 2:
    print("Usage: python render_page_debug.py <url>")
    sys.exit(1)

url = sys.argv[1]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    print("Loading:", url)
    page.goto(url, timeout=60000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    html = page.content()
    with open("debug_rendered.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Saved to debug_rendered.html")
    print("---- first 2000 chars of rendered HTML ----")
    print(html[:2000])
    browser.close()
