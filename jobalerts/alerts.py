"""Read job-alert emails (LinkedIn, Naukri, Indeed) from a dedicated Gmail inbox.

The platforms send these emails themselves, so nothing here logs into or scrapes them. Only links that point at a
job page on those three sites are used; everything else in the email is ignored."""
import email
import imaplib
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from . import rules
from .sources import mk
from .util import strip_html

_BLOCK = {"p", "div", "td", "tr", "li", "br", "table", "h1", "h2", "h3", "h4", "span"}
_GENERIC_LINK = re.compile(r"^(view|apply|see|easy apply|view job|view details|apply now|see job|view all|"
                           r"see all|unsubscribe|manage|edit|more|learn more)", re.I)
_LOC_WORD = re.compile(r"remote|hybrid|work from home|bengal|bangal|mumbai|thane|navi|pune|delhi|gurg|noida|"
                       r"hyderab|chennai|kolkata|india|karnataka|maharashtra|anywhere", re.I)
_SAL_LIKE = re.compile(r"₹|\blpa\b|\blacs?\b|\blakhs?\b|\bsalary\b|\bctc\b|not disclosed", re.I)
_EXP = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})\s*(?:yrs?|years?)", re.I)
SENDER_NAME = {"LinkedIn": "LinkedIn alert", "Naukri": "Naukri alert", "Indeed": "Indeed alert"}


def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


class _Tokens(HTMLParser):
    """Turns an HTML email into an ordered list of ('link', href, text) and ('text', text) tokens."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items, self._buf, self._abuf, self._href, self._skip = [], [], [], None, 0

    def _flush(self):
        t = _clean(" ".join(self._buf))
        self._buf = []
        if t:
            self.items.append(("text", t))

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            self._flush()
            self._href, self._abuf = dict(attrs).get("href") or "", []
        elif tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            self.items.append(("link", self._href, _clean(" ".join(self._abuf))))
            self._href = None
        elif tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        (self._abuf if self._href is not None else self._buf).append(data)

    def close(self):
        super().close()
        self._flush()


def normalize_link(href):
    """Returns (platform, clean_url) for a job-page link, else None."""
    try:
        u = urlparse(href)
    except ValueError:
        return None
    host, path = u.netloc.lower(), u.path
    if host.endswith("linkedin.com"):
        m = re.search(r"/jobs/view/(?:[^/?]*-)?(\d{6,})", path)
        if m:
            return "LinkedIn", f"https://www.linkedin.com/jobs/view/{m.group(1)}"
    elif host.endswith("naukri.com") and "job-listings" in path:
        return "Naukri", f"https://{u.netloc}{path}"
    elif host.endswith("indeed.com"):
        q = parse_qs(u.query)
        jk = (q.get("jk") or q.get("vjk") or [None])[0]
        if jk and re.search(r"/rc/clk|/viewjob|/pagead|/m/|/job/", path):
            return "Indeed", f"https://in.indeed.com/viewjob?jk={jk}"
    return None


def parse_html(html_text):
    """-> list of dicts: platform, url, title, company, location, text."""
    p = _Tokens()
    p.feed(html_text or "")
    p.close()
    toks, cards, i = p.items, [], 0
    while i < len(toks):
        t = toks[i]
        norm = normalize_link(t[1]) if t[0] == "link" else None
        if norm and t[2] and not _GENERIC_LINK.match(t[2]) and len(t[2]) <= 140:
            texts, j = [], i + 1
            while j < len(toks) and len(texts) < 6:
                nxt = toks[j]
                if nxt[0] == "link" and normalize_link(nxt[1]) and nxt[2] and not _GENERIC_LINK.match(nxt[2]):
                    break
                if nxt[0] == "text":
                    texts.append(nxt[1])
                elif nxt[2] and not _GENERIC_LINK.match(nxt[2]) and not normalize_link(nxt[1]):
                    texts.append(nxt[2])           # e.g. a company name that is itself a link
                j += 1
            cards.append(_to_card(norm, t[2], texts))
            i = j
        else:
            i += 1
    seen, out = set(), []
    for c in cards:
        if c["url"] not in seen:
            seen.add(c["url"])
            out.append(c)
    return out


def _to_card(norm, title, texts):
    platform, url = norm
    parts = []
    for t in texts:
        parts += [x.strip() for x in re.split(r"\s[·•|]\s", t) if x.strip()]
    company, locs = "", []
    for x in parts:
        if _LOC_WORD.search(x) and len(x) <= 80:
            locs.append(x)
        elif not company and not _SAL_LIKE.search(x) and not _EXP.search(x) and len(x) <= 80:
            company = x
    text = " | ".join(texts)
    return {"platform": platform, "url": url, "title": title, "company": company,
            "location": ", ".join(locs[:2]), "text": text}


def card_to_job(card, posted):
    text = card["text"]
    desc = f"{card['title']}. {card['company']}. {card['location']}. {text}"
    m = _EXP.search(text)
    if m:
        desc += f" Experience required: {m.group(1)}-{m.group(2)} years."
    return mk(source=SENDER_NAME[card["platform"]], source_id=card["url"], title=card["title"],
              company=card["company"], location=card["location"], url=card["url"], posted=posted,
              date_approx=True, date_unknown_ok=True, description=desc,
              salary_text=rules.extract_salary_text(text), regions=["India"], from_alert=True)


def _html_part(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace").replace("\n", "<br>")
    return ""


def email_alerts(opts):
    user, pw = os.environ.get("ALERT_INBOX_USER", "").strip(), os.environ.get("ALERT_INBOX_PASS", "").strip()
    if not (user and pw):
        raise RuntimeError("skipped: no ALERT_INBOX_USER / ALERT_INBOX_PASS")
    since = (datetime.now(timezone.utc) - timedelta(days=int(opts.get("lookback_days", 3)))).strftime("%d-%b-%Y")
    senders = opts.get("senders") or ["linkedin.com", "naukri.com", "indeed.com"]
    out, emails, unparsed = [], 0, 0
    box = imaplib.IMAP4_SSL(opts.get("host", "imap.gmail.com"), 993)
    try:
        box.login(user, pw)
        box.select("INBOX", readonly=True)       # read-only: nothing is marked read, moved or deleted
        for sender in senders:
            typ, data = box.search(None, "SINCE", since, "FROM", f'"{sender}"')
            for num in (data[0].split() if data and data[0] else []):
                typ, md = box.fetch(num, "(BODY.PEEK[])")
                if typ != "OK" or not md or not md[0]:
                    continue
                msg = email.message_from_bytes(md[0][1])
                emails += 1
                try:
                    posted = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc)
                except Exception:
                    posted = None
                cards = parse_html(_html_part(msg))
                if not cards:
                    unparsed += 1
                out += [card_to_job(c, posted) for c in cards]
    finally:
        try:
            box.logout()
        except Exception:
            pass
    print(f"   email_alerts: read {emails} alert emails, {unparsed} had no job links I could read")
    return out
