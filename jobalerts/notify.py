"""Build the daily message and send it by Telegram and/or email."""
import html
import os
import smtplib
from email.message import EmailMessage

import requests

from . import rules

SECTIONS = [("salary", "Salary in your range"),
            ("unlisted", "No salary mentioned (ask about pay before applying)"),
            ("near", "Near matches")]
SUS_TITLE = "Flagged as suspicious (not counted in today's list)"


def _esc(s):
    return html.escape(str(s or ""), quote=True)


def ago(job):
    d = job.get("age_days")
    if d is None:
        return "posting date unknown"
    if d < 1:
        return "posted today"
    if d > 30:
        return f"posted {int(d)} days ago, OLD POSTING (may already be filled)"
    return f"posted {int(d)}d ago"


def mode_label(job):
    if job["mode"] == "hybrid":
        return "Hybrid, " + (job.get("location") or "Bengaluru")
    base = "Remote"
    if job.get("eligibility") == "unclear":
        base += " (eligibility for India not stated, check first)"
    return base


def salary_label(job):
    label = rules.format_salary(job.get("salary_rng"))
    txt = (job.get("salary_text") or "").lower()
    if job.get("salary_rng") and txt and not any(k in txt for k in ("inr", "lpa", "₹", "lakh")):
        label += " (converted)"
    return label


def contact_line(job):
    c = job.get("contact") or {}
    bits = list(c.get("emails", [])) + list(c.get("phones", [])) + list(c.get("linkedin", []))
    return ", ".join(bits)


def _flags(job):
    out = []
    for f in job.get("flags", []):
        icon = {"high": "🚩", "warn": "⚠️", "info": "ℹ️"}[f["level"]]
        out.append(f"{icon} {f['text']}")
    return out


def _groups(picked, flagged):
    groups = [(title, [j for j in picked if j["section"] == key]) for key, title in SECTIONS]
    groups.append((SUS_TITLE, flagged))
    return [(t, js) for t, js in groups if js]


def build(picked, flagged, health, date_label, tracker_url=""):
    """Returns dict(subject, telegram=[chunks], html, text)."""
    n_sal = sum(1 for j in picked if j["section"] == "salary")
    n_unl = sum(1 for j in picked if j["section"] == "unlisted")
    n_near = sum(1 for j in picked if j["section"] == "near")
    if picked:
        summary = f"{len(picked)} new jobs: {n_sal} in your salary range, {n_unl} salary not listed, {n_near} near matches"
    else:
        summary = "No new jobs matched today"
    active = {k: v for k, v in health.items() if not str(v).startswith("SKIPPED")}
    ok = sum(1 for v in active.values() if not str(v).startswith("FAILED"))
    foot = f"Sources checked: {ok}/{len(active)} worked."
    failed = [k for k, v in health.items() if str(v).startswith("FAILED")]
    if failed:
        foot += " Not available today: " + ", ".join(failed) + "."

    tg_blocks, html_blocks, text_blocks = [], [], []
    i = 0
    for title, jobs in _groups(picked, flagged):
        tg_blocks.append(f"\n<b>{_esc(title.upper())}</b>")
        html_blocks.append(f"<h3 style='margin:22px 0 8px'>{_esc(title)}</h3>")
        text_blocks.append(f"\n== {title.upper()} ==")
        for j in jobs:
            i += 1
            meta = f"{mode_label(j)} | {salary_label(j)} | {j['score']}% match | {ago(j)} | {j['source']}"
            if j.get("is_contract"):
                meta += " | Contract"
            fl, ct = _flags(j), contact_line(j)
            company = j.get("company") or "Unknown company"
            tg = [f"<b>{i}. {_esc(j['title'])}</b>", f"{_esc(company)}", _esc(meta)]
            tg += [_esc(x) for x in fl]
            if ct:
                tg.append("Contact: " + _esc(ct))
            tg.append(f"<a href=\"{_esc(j['url'])}\">Apply here</a>")
            tg_blocks.append("\n".join(tg))

            h = [f"<div style='margin:0 0 14px;padding:10px 12px;border:1px solid #ddd;border-radius:8px'>",
                 f"<div style='font-weight:600'>{i}. {_esc(j['title'])}</div>",
                 f"<div>{_esc(company)}</div><div style='color:#555;font-size:13px'>{_esc(meta)}</div>"]
            h += [f"<div style='font-size:13px'>{_esc(x)}</div>" for x in fl]
            if ct:
                h.append(f"<div style='font-size:13px'>Contact: {_esc(ct)}</div>")
            h.append(f"<div style='margin-top:6px'><a href='{_esc(j['url'])}'>Apply here</a></div></div>")
            html_blocks.append("".join(h))

            t = [f"{i}. {j['title']} - {company}", f"   {meta}"] + [f"   {x}" for x in fl]
            if ct:
                t.append(f"   Contact: {ct}")
            t.append(f"   Apply: {j['url']}")
            text_blocks.append("\n".join(t))

    head = f"Daily jobs, {date_label}"
    tail_tg = foot + (f"\nTrack applications: {_esc(tracker_url)}" if tracker_url else "")
    # Telegram messages are capped at 4096 characters: split on job boundaries.
    chunks, cur = [], f"<b>{_esc(head)}</b>\n{_esc(summary)}\n"
    for b in tg_blocks:
        if len(cur) + len(b) + 2 > 3800:
            chunks.append(cur)
            cur = ""
        cur += "\n" + b + "\n"
    cur += "\n" + tail_tg
    chunks.append(cur)

    html_doc = (f"<div style='font-family:Arial,sans-serif;max-width:680px;margin:auto'>"
                f"<h2 style='margin-bottom:4px'>{_esc(head)}</h2><div>{_esc(summary)}</div>"
                + "".join(html_blocks)
                + (f"<p><a href='{_esc(tracker_url)}'>Open the application tracker</a></p>" if tracker_url else "")
                + f"<p style='color:#777;font-size:12px'>{_esc(foot)} Salaries in other currencies are converted at approximate rates. "
                  f"Always verify a company and recruiter before sharing documents.</p></div>")
    text = head + "\n" + summary + "\n" + "\n".join(text_blocks) + "\n\n" + foot + \
        (f"\nTracker: {tracker_url}" if tracker_url else "")
    return {"subject": f"{head}: {len(picked)} new" if picked else f"{head}: nothing new",
            "telegram": chunks, "html": html_doc, "text": text}


def send_telegram(chunks):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chats = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
    if not (token and chats):
        print("   telegram: not configured, skipped")
        return False
    delivered = False
    for chat in chats:
        try:
            for ch in chunks:
                r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  json={"chat_id": chat, "text": ch, "parse_mode": "HTML",
                                        "disable_web_page_preview": True}, timeout=30)
                r.raise_for_status()
            delivered = True
            print(f"   telegram: sent to {len(chunks)} message(s)")
        except Exception as e:
            print(f"   telegram: FAILED for one recipient ({type(e).__name__})")
    return delivered


def send_email(subject, html_doc, text):
    user, pw = os.environ.get("EMAIL_USER", "").strip(), os.environ.get("EMAIL_PASS", "").strip()
    to = [t.strip() for t in os.environ.get("EMAIL_TO", "").split(",") if t.strip()]
    if not (user and pw and to):
        print("   email: not configured, skipped")
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.set_content(text)
    msg.add_alternative(html_doc, subtype="html")
    host, port = os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "465"))
    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
        print("   email: sent")
        return True
    except Exception as e:
        print(f"   email: FAILED ({type(e).__name__})")
        return False
