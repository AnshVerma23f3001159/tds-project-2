# handlers/advanced_handler.py
"""
Advanced handler for TDS Project 2
- robust submit_url discovery (forms, scripts, JSON blobs, templated origin markers)
- parses HTML tables, CSV/XLSX, PDF tables
- simple heuristics for instructions: sum/mean/corr/plot
- returns payload: { email, secret, url, answer, submit_url, [attachment] }

Expect to be called as:
    handle_quiz_page(html=..., page=..., email=..., secret=..., base_url=...)

Note: this handler uses Playwright `page` to evaluate `window.location.origin` when needed.

"""

import re
import io
import json
import base64
import requests
import pandas as pd
import pdfplumber
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from urllib.parse import urljoin, urlparse

# optional: python-magic / python-magic-bin
try:
    import magic
except Exception:
    magic = None

# ---------- helpers ----------

def download_url_bytes(url, base_url=None, timeout=30):
    if base_url:
        url = urljoin(base_url, url)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    return resp.content, content_type, url


def detect_file_type(url, content_type, content_bytes):
    if content_type:
        lc = content_type.lower()
        if "csv" in lc or "text/plain" in lc:
            return "csv"
        if "excel" in lc or "spreadsheet" in lc or "xlsx" in lc or "xls" in lc:
            return "xlsx"
        if "pdf" in lc:
            return "pdf"
    try:
        if magic:
            m = magic.from_buffer(content_bytes, mime=True)
            if m:
                lm = m.lower()
                if "pdf" in lm:
                    return "pdf"
                if "excel" in lm or "vnd.ms-excel" in lm or "spreadsheet" in lm:
                    return "xlsx"
                if "csv" in lm or "text/plain" in lm:
                    return "csv"
    except Exception:
        pass
    if url.lower().endswith(".csv"):
        return "csv"
    if url.lower().endswith((".xlsx", ".xls")):
        return "xlsx"
    if url.lower().endswith(".pdf"):
        return "pdf"
    return None


def parse_table_from_bytes(content_bytes, ftype, url):
    if ftype == "csv":
        return pd.read_csv(io.BytesIO(content_bytes))
    if ftype == "xlsx":
        return pd.read_excel(io.BytesIO(content_bytes))
    return None


def parse_table_from_pdf_bytes(content_bytes):
    try:
        with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
            for page in pdf.pages:
                try:
                    tables = page.extract_tables()
                    if tables:
                        table = tables[0]
                        if len(table) < 2:
                            continue
                        df = pd.DataFrame(table[1:], columns=table[0])
                        return df
                except Exception:
                    continue
    except Exception:
        pass
    return None


def parse_tables_from_html(html):
    try:
        dfs = pd.read_html(html)
        return dfs if dfs else []
    except Exception:
        return []


def coerce_numeric(df):
    for c in df.columns:
        try:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(r'[^\d.\-]+', '', regex=True), errors='coerce')
        except Exception:
            pass
    return df


def df_plot_to_base64(df, xcol=None, ycol=None, kind="line", title=None):
    plt.close('all')
    fig, ax = plt.subplots(figsize=(6, 3))
    try:
        if xcol and ycol and xcol in df.columns and ycol in df.columns:
            df.plot(x=xcol, y=ycol, kind=kind, ax=ax)
        elif ycol and ycol in df.columns:
            df[ycol].plot(kind=kind, ax=ax)
        else:
            df.plot(ax=ax)
        if title:
            ax.set_title(title)
        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format='png', dpi=120)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode('ascii')
        return f"data:image/png;base64,{b64}"
    finally:
        plt.close(fig)


# ---------- instruction parsing (heuristic) ----------

def detect_instruction(text):
    text = (text or "").lower()
    instr = {}
    m = re.search(r'sum of (?:the )?"?([a-z0-9 _\-\(\)]+)"? column', text)
    if m:
        instr['op'] = 'sum'; instr['col'] = m.group(1).strip(); return instr
    m = re.search(r'(?:mean|average) of (?:the )?"?([a-z0-9 _\-\(\)]+)"? column', text)
    if m:
        instr['op'] = 'mean'; instr['col'] = m.group(1).strip(); return instr
    m = re.search(r'correl(?:ation|ate)(?: between)? ([a-z0-9 _\-\(\)]+) (?:and|,) ([a-z0-9 _\-\(\)]+)', text)
    if m:
        instr['op'] = 'corr'; instr['x'] = m.group(1).strip(); instr['y'] = m.group(2).strip(); return instr
    if 'visual' in text or 'plot' in text or 'chart' in text:
        instr['op'] = 'plot'
        m = re.search(r'x[:=]\s*([a-z0-9 _\-\(\)]+)[,;]?\s*y[:=]\s*([a-z0-9 _\-\(\)]+)', text)
        if m:
            instr['x'] = m.group(1).strip(); instr['y'] = m.group(2).strip()
        return instr
    m = re.search(r'sum.*?([a-z0-9 _\-\(\)]+) column', text)
    if m:
        instr['op'] = 'sum'; instr['col'] = m.group(1).strip(); return instr
    return instr


# ---------- main handler ----------

def handle_quiz_page(html, page, email, secret, base_url):
    soup = BeautifulSoup(html or "", "html.parser")
    body_text = soup.get_text("\n") if soup else (html or "")

    # ------- submit_url discovery (robust + templated JSON support) -------
    submit_url = None

    # 1) explicit absolute urls with submit-like patterns
    m = re.search(r"https?://[^\s'\"<>]*(?:/submit|/scrape|/api/submit|/submit-answer|/submit_result)[^\s'\"<>]*", body_text, flags=re.IGNORECASE)
    if m:
        submit_url = m.group(0)

    # 2) JSON/script keys like "submit_url": "https://..." or any quoted absolute with submit/scrape
    if not submit_url:
        scripts = [s.string for s in soup.find_all('script') if s.string]
        for st in scripts:
            if not st:
                continue
            m2 = re.search(r'["\']submit[_-]?url["\']\s*:\s*["\'](https?://[^"\']+)["\']', st, flags=re.IGNORECASE)
            if m2:
                submit_url = m2.group(1); break
            m3 = re.search(r'["\'](https?://[^"\']*(?:submit|scrape)[^"\']*)["\']', st, flags=re.IGNORECASE)
            if m3:
                submit_url = m3.group(1); break

    # 3) form action
    if not submit_url:
        form = soup.find("form", action=True)
        if form:
            action = form.get('action')
            if action:
                if action.startswith("http"):
                    submit_url = action
                elif base_url:
                    submit_url = urljoin(base_url, action)

    # 4) hrefs with submit/scrape/api
    if not submit_url:
        for a in soup.find_all('a', href=True):
            href = a.get('href')
            if href and re.search(r'(submit|scrape|api)', href, flags=re.IGNORECASE):
                submit_url = urljoin(base_url or "", href)
                break

    # 5) check <pre> or <code> blocks for JSON-like payloads (templated origin)
    if not submit_url:
        for pre in soup.find_all(['pre', 'code']):
            try:
                text = pre.get_text()
                jm = re.search(r'\{[\s\S]*?\}', text)
                if jm:
                    js = jm.group(0)
                    try:
                        j = json.loads(js)
                        if isinstance(j, dict) and 'url' in j and isinstance(j['url'], str):
                            candidate = j['url']
                            if '<span' in candidate or 'window.location.origin' in candidate or '{{origin}}' in candidate:
                                origin = None
                                try:
                                    if page:
                                        origin = page.evaluate('() => window.location.origin')
                                except Exception:
                                    origin = None
                                if not origin and base_url:
                                    p = urlparse(base_url)
                                    origin = f"{p.scheme}://{p.netloc}"
                                if origin:
                                    candidate_fixed = re.sub(r'<span[^>]*class=[\"\']origin[\"\'][^>]*>.*?</span>', origin, candidate, flags=re.IGNORECASE)
                                    candidate_fixed = candidate_fixed.replace('window.location.origin', origin).replace('{{origin}}', origin)
                                    submit_url = urljoin(origin, candidate_fixed)
                                    break
                            else:
                                submit_url = urljoin(base_url or "", candidate)
                                break
                    except Exception:
                        continue
            except Exception:
                continue

    # 6) final fallback absolute url with submit/scrape
    if not submit_url:
        m4 = re.search(r'https?://[^\s\'\"<>]*(?:submit|scrape)[^\s\'\"<>]*', body_text, flags=re.IGNORECASE)
        if m4:
            submit_url = m4.group(0)

    print("DEBUG: submit_url discovered:", submit_url)

    if not submit_url:
        raise RuntimeError("submit_url not found on page")

    # ---------- find candidate datasets ----------
    links = [a.get('href') for a in soup.find_all('a', href=True)]
    candidates = []
    for link in links:
        try:
            ll = link.lower()
            if any(ext in ll for ext in ['.csv', '.xls', '.xlsx', '.pdf']):
                candidates.append(link)
        except Exception:
            continue

    df = None
    source_url = None

    # try inline HTML tables first
    tables = parse_tables_from_html(html) if html else []
    if tables:
        df = tables[0]
    else:
        for link in candidates:
            try:
                content_bytes, content_type, real_url = download_url_bytes(link, base_url=base_url)
                ftype = detect_file_type(real_url, content_type, content_bytes)
                if ftype in ('csv', 'xlsx'):
                    df = parse_table_from_bytes(content_bytes, ftype, real_url)
                    source_url = real_url
                    break
                if ftype == 'pdf':
                    df = parse_table_from_pdf_bytes(content_bytes)
                    source_url = real_url
                    if df is not None:
                        break
            except Exception:
                continue

    if df is None:
        scripts = [s.string for s in soup.find_all('script') if s.string]
        for st in scripts:
            try:
                jm = re.search(r'\{[\s\S]*"answer"[\s\S]*\}', st)
                if jm:
                    j = json.loads(jm.group(0))
                    if 'answer' in j:
                        return {"email": email, "secret": secret, "url": base_url, "answer": j['answer'], "submit_url": submit_url}
            except Exception:
                continue
        return {"email": email, "secret": secret, "url": base_url, "answer": "unable to locate dataset", "submit_url": submit_url}

    # ---------- clean and coerce ----------
    df = df.rename(columns=lambda c: str(c).strip())
    df = coerce_numeric(df)
    instr = detect_instruction(body_text)

    answer = None
    attachment = None

    try:
        if instr.get('op') == 'sum' and instr.get('col'):
            col = instr['col']
            matched = [c for c in df.columns if c.strip().lower() == col.lower()]
            if matched:
                answer = float(df[matched[0]].dropna().sum())
            else:
                matched = [c for c in df.columns if col.lower() in str(c).lower()]
                if matched:
                    answer = float(df[matched[0]].dropna().sum())

        elif instr.get('op') == 'mean' and instr.get('col'):
            col = instr['col']
            matched = [c for c in df.columns if c.strip().lower() == col.lower()]
            if matched:
                answer = float(df[matched[0]].dropna().mean())

        elif instr.get('op') == 'corr' and instr.get('x') and instr.get('y'):
            x = instr['x']; y = instr['y']
            mx = [c for c in df.columns if c.strip().lower() == x.lower()]
            my = [c for c in df.columns if c.strip().lower() == y.lower()]
            if mx and my:
                answer = float(df[mx[0]].corr(df[my[0]]))

        elif instr.get('op') == 'plot':
            x = instr.get('x'); y = instr.get('y')
            if y and y in df.columns:
                attachment = df_plot_to_base64(df, xcol=x, ycol=y, kind="line", title="Plot")
                answer = "attached_plot"
            else:
                numcols = df.select_dtypes(include=['number']).columns
                if len(numcols) > 0:
                    attachment = df_plot_to_base64(df, ycol=numcols[0], kind="line", title="Plot")
                    answer = "attached_plot"

        else:
            if 'value' in [c.strip().lower() for c in df.columns]:
                answer = float(df[[c for c in df.columns if c.strip().lower()=='value'][0]].dropna().sum())

    except Exception as e:
        return {"email": email, "secret": secret, "url": base_url, "answer": "error computing instruction: " + str(e), "submit_url": submit_url}

    if attachment:
        return {"email": email, "secret": secret, "url": base_url, "answer": answer, "attachment": attachment, "submit_url": submit_url}

    if answer is None:
        return {"email": email, "secret": secret, "url": base_url, "answer": "no_answer_generated", "submit_url": submit_url}

    if isinstance(answer, float) and abs(round(answer) - answer) < 1e-8:
        answer = int(round(answer))

    return {"email": email, "secret": secret, "url": base_url, "answer": answer, "submit_url": submit_url}
