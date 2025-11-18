from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import requests
import re
from handlers.advanced_handler import handle_quiz_page

def solve_quiz_task(email, secret, url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(url, timeout=60000)
        page.wait_for_load_state("networkidle")

        html = page.content()

        payload = handle_quiz_page(
            html=html,
            page=page,
            email=email,
            secret=secret,
            base_url=url
        )

        submit_url = payload.pop("submit_url")

        response = requests.post(submit_url, json=payload)
        try:
            return response.json()
        except:
            return {"raw": response.text}
