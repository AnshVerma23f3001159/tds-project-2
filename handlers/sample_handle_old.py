# handlers/sample_handler.py
from bs4 import BeautifulSoup
import re

def handle_quiz_page(html, page, email, secret, base_url):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text()

    # find submit URL (very basic)
    m = re.search(r"https?://[^\s\"']+/submit", text)
    submit_url = m.group(0) if m else None

    # default answer fallback
    answer = "unable to solve"

    if not submit_url:
        raise RuntimeError("submit_url not found on the quiz page")

    return {
        "email": email,
        "secret": secret,
        "url": base_url,
        "answer": answer,
        "submit_url": submit_url
    }
