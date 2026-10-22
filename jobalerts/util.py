import hashlib
import html
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import yaml

_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"<\s*(?:br|/p|/li|/div|/h\d|/tr)\s*/?>", re.I)


def strip_html(s):
    """HTML (possibly entity-escaped, as Greenhouse sends it) -> plain text."""
    if not s:
        return ""
    s = html.unescape(str(s))
    s = _BREAK_RE.sub("\n", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v ]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def parse_dt(v):
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            if v > 1e11:
                v = v / 1000.0
            return datetime.fromtimestamp(v, tz=timezone.utc)
        s = str(v).strip()
        if s.isdigit():
            return parse_dt(int(s))
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def job_key(company, title):
    raw = f"{norm(company)}|{norm(title)[:90]}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    override = os.environ.get("JOB_CONFIG", "").strip()
    if override:
        cfg = deep_merge(cfg, yaml.safe_load(override) or {})
    return cfg
