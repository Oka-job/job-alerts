"""Filtering, scoring, selection and the small amount of state (what was already sent)."""
import json
import os
from collections import Counter
from datetime import timedelta

from . import rules
from .util import job_key, norm


# ------------------------------------------------------------------ filters
def evaluate(job, cfg, now, seen):
    """Fills in derived fields on `job`. Returns a drop reason, or None to keep it."""
    if not job.get("title") or not job.get("url"):
        return "missing title or link"
    job["key"] = job_key(job.get("company"), job["title"])
    if job["key"] in seen:
        return "already sent"

    level = rules.title_level(job["title"])
    if level == "too_senior":
        return "title too senior"
    if level == "too_junior":
        return "internship / trainee"
    job["senior_title"] = level == "senior"
    if rules.is_freelance(job):
        return "freelance / part-time"
    if rules.PLACEHOLDER.search(job["title"]):
        return "generic application post"
    if rules.TITLE_EXCLUDE.search(job["title"]):
        return "title outside your field"
    tier = role_tier(job, cfg)
    if tier is None:
        return "title not in your field"
    job["role_tier"] = tier

    posted = job.get("posted")
    if posted is None:
        if not cfg.get("allow_unknown_date", False):
            return "no posting date"
        job["age_days"] = None
    else:
        job["age_days"] = max(0.0, (now - posted).total_seconds() / 86400)
        if job["age_days"] > cfg.get("max_age_days", 15):
            return "posted more than 15 days ago"

    job["mode"] = rules.classify_mode(job)
    if job["mode"] == "onsite":
        return "onsite or work mode unclear"
    if job["mode"] == "hybrid":
        if not rules.in_hybrid_city(job, cfg.get("hybrid_cities", ["bengaluru", "bangalore"])):
            return "hybrid outside Bangalore"
        job["eligibility"] = "yes"
    else:
        job["eligibility"] = rules.remote_eligibility(job)
        if job["eligibility"] == "no":
            return "remote but not open to India"
        if job["eligibility"] == "unclear":
            return "remote region not stated"

    desc = job.get("description") or ""
    job["min_years"] = rules.required_experience(f"{job['title']}. {desc}")
    if job["min_years"] is not None and job["min_years"] > cfg.get("max_experience_years", 5):
        return "asks for more than 5 years"

    if rules.needs_cold_calling(desc):
        return "cold calling / sales quota"
    if rules.needs_foreign_language(desc, cfg.get("foreign_languages", [])):
        return "needs a non-English language"
    if rules.needs_unheld_degree(desc):
        return "needs a degree he does not hold"

    rates = cfg["salary"].get("rates_to_inr", {})
    rng = rules.parse_salary(job.get("salary_text"), rates)
    if not rng:
        found = rules.extract_salary_text(desc)
        rng = rules.parse_salary(found, rates)
        if rng:
            job["salary_text"] = found
    job["salary_rng"] = rng
    job["salary_status"] = rules.salary_status(rng, cfg["salary"])
    if job["salary_status"] == "out":
        return "salary below your range"

    job["contact"] = rules.extract_contact(desc)
    job["is_contract"] = rules.is_contract(job)
    high, warn, info = rules.scam_flags(job, cfg["salary"])
    job["flags"] = ([{"level": "high", "text": t} for t in high] + [{"level": "warn", "text": t} for t in warn]
                    + [{"level": "info", "text": t} for t in info])
    job["suspicious"] = bool(high) or len(warn) >= 2
    job["warn_count"] = len(warn)
    return None


# ------------------------------------------------------------------ scoring
def role_tier(job, cfg):
    """'high' / 'medium' when the TITLE matches his field, 'desc' when the description is very PIM-heavy, else None."""
    roles = cfg["roles"]
    title = job["title"]
    if rules.has_any(title, roles.get("high", [])):
        return "high"
    if rules.has_any(title, roles.get("medium", [])):
        return "medium"
    if rules.count_terms((job.get("description") or "")[:3000], roles.get("high", [])) >= 3:
        return "desc"
    return None


def score(job, cfg):
    """Match % is mostly about fit: role (up to 50), his tools/skills (25), industry (12); small extras for
    freshness, work mode and salary. A medium-fit role cannot reach the 50% bar without tools or industry fit."""
    title, desc = job["title"], (job.get("description") or "")[:3000]
    s = {"high": 50, "medium": 38, "desc": 28}[job["role_tier"]]
    s += 25 * min(1.0, rules.count_terms(f"{title} {desc}", cfg.get("skills", [])) / 3)
    s += 12 * min(1.0, rules.count_terms(f"{job.get('company', '')} {desc}", cfg.get("industry", [])) / 2)
    age = job.get("age_days")
    s += 2 if age is None else 5 if age <= 3 else 3 if age <= 7 else 1
    s += {"in_range": 5, "unlisted": 2, "near": 1}.get(job["salary_status"], 0)
    s += 3
    if job.get("senior_title"):
        s -= 8
    if job.get("warn_count") == 1:
        s -= 8
    if job.get("date_approx"):
        s -= 2
    job["score"] = int(max(0, min(100, round(s))))
    return job["score"]


def select(jobs, cfg):
    """Returns (picked, flagged). picked is at most daily_count jobs."""
    n, cap = cfg.get("daily_count", 10), cfg.get("max_per_company", 2)
    ok = [j for j in jobs if not j["suspicious"]]
    sus = [j for j in jobs if j["suspicious"]]

    def rank(j):
        return (-j["score"], 99 if j.get("age_days") is None else j["age_days"])

    strict = sorted([j for j in ok if j["salary_status"] in ("in_range", "unlisted")
                     and j["score"] >= cfg.get("strict_min_score", 50)], key=rank)
    strict_ids = {id(j) for j in strict}
    near = sorted([j for j in ok if id(j) not in strict_ids and j["score"] >= cfg.get("near_min_score", 25)], key=rank)

    picked, per = [], Counter()

    def take(lst, section_of):
        for j in lst:
            if len(picked) >= n:
                return
            c = norm(j.get("company"))
            if c and per[c] >= cap:
                continue
            per[c] += 1
            j["section"] = section_of(j)
            picked.append(j)

    take(strict, lambda j: "salary" if j["salary_status"] == "in_range" else "unlisted")
    if cfg.get("near_match_fill", False):
        take(near, lambda j: "near")
    flagged = sorted(sus, key=rank)[: cfg.get("suspicious_listed_max", 5)]
    for j in flagged:
        j["section"] = "suspicious"
    return picked, flagged


def run_filters(raw_jobs, cfg, now, seen):
    """Dedupe -> filter -> score. Returns (candidates, drop_counter)."""
    drops, kept, keys = Counter(), [], set()
    for job in raw_jobs:
        reason = evaluate(job, cfg, now, seen)
        if reason is None and job["key"] in keys:
            reason = "duplicate across sources"
        if reason:
            drops[reason] += 1
            continue
        keys.add(job["key"])
        score(job, cfg)
        kept.append(job)
    return kept, drops


# -------------------------------------------------------------------- state
def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_state(data_dir):
    seen = _read(os.path.join(data_dir, "seen.json"), {}).get("seen", {})
    history = _read(os.path.join(data_dir, "jobs.json"), [])
    return seen, history


def public_record(job, today):
    sal = rules.format_salary(job.get("salary_rng"))
    return {
        "id": job["key"], "title": job["title"], "company": job.get("company") or "Unknown company",
        "location": job.get("location") or "", "mode": job["mode"], "eligibility": job.get("eligibility"),
        "url": job["url"], "posted": job["posted"].date().isoformat() if job.get("posted") else None,
        "salary": sal, "match": job["score"], "section": job["section"],
        "contract": bool(job.get("is_contract")),
        "flags": [f for f in job.get("flags", []) if f["level"] in ("high", "warn", "info")],
        "contact": job.get("contact") or {}, "source": job["source"], "sent_on": today,
    }


def save_state(data_dir, seen, history, jobs_sent, today, keep_days=90):
    os.makedirs(data_dir, exist_ok=True)
    for j in jobs_sent:
        seen[j["key"]] = today
    cutoff = (__import__("datetime").date.fromisoformat(today) - timedelta(days=keep_days)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    history = [public_record(j, today) for j in jobs_sent] + history
    history = [h for h in history if h["sent_on"] >= cutoff]
    with open(os.path.join(data_dir, "seen.json"), "w", encoding="utf-8") as f:
        json.dump({"seen": seen}, f, indent=0)
    with open(os.path.join(data_dir, "jobs.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, indent=1, ensure_ascii=False)
