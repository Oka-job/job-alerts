"""Fetch jobs from free, automation-friendly sources and normalise them.
Every source is wrapped so one failure never stops the daily run."""
import os
import time
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

import requests

from . import rules
from .util import parse_dt, parse_relative, strip_html

UA = {"User-Agent": "personal-job-alerts/1.0 (individual job search; contact via GitHub)"}


def mk(**kw):
    base = dict(source="", source_id="", title="", company="", location="", url="", posted=None,
                date_approx=False, date_unknown_ok=False, description="", salary_text="", employment_type="",
                workplace=None, regions=None, remote_source=False)
    base.update(kw)
    base["title"] = (base["title"] or "").strip()
    base["company"] = (base["company"] or "").strip()
    return base


def http(url, params=None, headers=None, method="GET", json_body=None, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            h = dict(UA)
            h.update(headers or {})
            r = requests.request(method, url, params=params, headers=h, json=json_body, timeout=30)
            if r.status_code == 404:
                raise FileNotFoundError("404 not found")
            r.raise_for_status()
            return r
        except FileNotFoundError:
            raise
        except Exception as e:  # network blips, 5xx, 429
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


# ------------------------------------------------------------- remote boards
def remotive(opts):
    out = []
    for q in (opts.get("queries") or [])[:4]:
        data = http("https://remotive.com/api/remote-jobs", params={"search": q, "limit": 100}).json()
        for j in data.get("jobs", []):
            out.append(mk(source="Remotive", source_id=str(j.get("id")), title=j.get("title"),
                          company=j.get("company_name"), location=j.get("candidate_required_location", ""),
                          url=j.get("url"), posted=parse_dt(j.get("publication_date")),
                          description=strip_html(j.get("description")), salary_text=j.get("salary") or "",
                          employment_type=j.get("job_type") or "", remote_source=True))
    return out


def remoteok(opts):
    data = http("https://remoteok.com/api").json()
    out = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        sal = ""
        if j.get("salary_min") and j.get("salary_max"):
            sal = f"USD {j['salary_min']} - {j['salary_max']} per year"
        out.append(mk(source="RemoteOK", source_id=str(j.get("id")), title=j["position"],
                      company=j.get("company"), location=j.get("location") or "",
                      url=j.get("url") or j.get("apply_url"), posted=parse_dt(j.get("date") or j.get("epoch")),
                      description=strip_html(j.get("description")), salary_text=sal, remote_source=True))
    return out


def weworkremotely(opts):
    out = []
    for feed in ("remote-jobs.rss", "categories/all-other-remote-jobs.rss"):
        r = http(f"https://weworkremotely.com/{feed}")
        root = ET.fromstring(r.content)
        for it in root.iter("item"):
            raw = (it.findtext("title") or "")
            company, _, title = raw.partition(": ")
            if not title:
                company, title = "", raw
            out.append(mk(source="WeWorkRemotely", source_id=it.findtext("guid") or it.findtext("link"),
                          title=title, company=company, location=it.findtext("region") or "",
                          url=it.findtext("link"), posted=parse_dt(it.findtext("pubDate")),
                          description=strip_html(it.findtext("description")),
                          employment_type=it.findtext("type") or "", remote_source=True))
    return out


def himalayas(opts):
    out = []
    for page in range(int(opts.get("pages", 5))):
        data = http("https://himalayas.app/jobs/api", params={"limit": 20, "offset": page * 20}).json()
        jobs = data.get("jobs", [])
        if not jobs:
            break
        for j in jobs:
            sal = ""
            if j.get("minSalary") and j.get("maxSalary"):
                per = str(j.get("salaryPeriod") or "year").lower()
                per = "per month" if "month" in per else "per hour" if "hour" in per else "per year"
                sal = f"{j.get('currency') or 'USD'} {j['minSalary']} - {j['maxSalary']} {per}"
            regions = j.get("locationRestrictions")
            out.append(mk(source="Himalayas", source_id=str(j.get("guid") or j.get("applicationLink")),
                          title=j.get("title"), company=j.get("companyName"),
                          location=", ".join(regions) if regions else "Worldwide",
                          url=j.get("applicationLink") or j.get("guid"), posted=parse_dt(j.get("pubDate")),
                          description=strip_html(j.get("description") or j.get("excerpt")), salary_text=sal,
                          employment_type=str(j.get("employmentType") or ""), regions=list(regions or []),
                          remote_source=True))
    return out


def jobicy(opts):
    out, errors = [], []
    for geo in opts.get("geos", ["india"]):
        try:
            data = http("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "geo": geo}).json()
        except Exception as e:
            errors.append(f"{geo}: {str(e)[:40]}")
            continue
        for j in data.get("jobs", []):
            sal = ""
            if j.get("salaryMin") and j.get("salaryMax"):
                per = "per month" if "month" in str(j.get("salaryPeriod", "")).lower() else "per year"
                sal = f"{j.get('salaryCurrency') or 'USD'} {j['salaryMin']} - {j['salaryMax']} {per}"
            jt = j.get("jobType")
            out.append(mk(source="Jobicy", source_id=str(j.get("id")), title=j.get("jobTitle"),
                          company=j.get("companyName"), location=str(j.get("jobGeo") or ""), url=j.get("url"),
                          posted=parse_dt(j.get("pubDate")),
                          description=strip_html(j.get("jobDescription") or j.get("jobExcerpt")),
                          salary_text=sal, employment_type=" ".join(jt) if isinstance(jt, list) else str(jt or ""),
                          remote_source=True))
    if not out and errors:
        raise RuntimeError("; ".join(errors))
    return out


# ------------------------------------------------------------- Indian search
def adzuna(opts):
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise RuntimeError("skipped: no ADZUNA_APP_ID / ADZUNA_APP_KEY")
    out, seen = [], set()
    for q in opts.get("queries", []):
        for what, where in ((q + " remote", ""), (q + " hybrid", "bangalore"), (q + " hybrid", "mumbai")):
            params = {"app_id": app_id, "app_key": app_key, "what": what, "results_per_page": 50,
                      "max_days_old": int(opts.get("max_days_old", 30)), "sort_by": "date",
                      "content-type": "application/json"}
            if where:
                params["where"] = where
            data = http("https://api.adzuna.com/v1/api/jobs/in/search/1", params=params).json()
            for j in data.get("results", []):
                if j.get("id") in seen:
                    continue
                seen.add(j.get("id"))
                sal = ""
                if j.get("salary_min") and j.get("salary_max") and str(j.get("salary_is_predicted")) != "1":
                    sal = f"INR {j['salary_min']} - {j['salary_max']} per year"
                out.append(mk(source="Adzuna", source_id=str(j.get("id")), title=j.get("title"),
                              company=(j.get("company") or {}).get("display_name"),
                              location=(j.get("location") or {}).get("display_name", ""),
                              url=j.get("redirect_url"), posted=parse_dt(j.get("created")),
                              description=strip_html(j.get("description")), salary_text=sal,
                              employment_type=f"{j.get('contract_type') or ''} {j.get('contract_time') or ''}",
                              regions=["India"]))
    return out


def serpapi(opts):
    """Google Jobs through SerpApi (free plan). Covers postings that also appear on LinkedIn, Naukri, Indeed."""
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        raise RuntimeError("skipped: no SERPAPI_KEY")
    out = []
    qs, n = list(opts.get("queries") or []), int(opts.get("max_searches", 4))
    if len(qs) > n:   # rotate so every query gets its turn across the week
        start = (datetime.now(timezone.utc).toordinal() * n) % len(qs)
        qs = [qs[(start + i) % len(qs)] for i in range(n)]
    for q in qs:
        data = http("https://serpapi.com/search.json",
                    params={"engine": "google_jobs", "q": q, "location": "India", "hl": "en", "gl": "in",
                            "api_key": key}).json()
        for j in data.get("jobs_results", []):
            ext = j.get("detected_extensions") or {}
            links = [o.get("link") for o in (j.get("apply_options") or []) if o.get("link")]
            url = links[0] if links else (j.get("share_link") or "")
            out.append(mk(source="Google Jobs", source_id=str(j.get("job_id") or url), title=j.get("title"),
                          company=j.get("company_name"), location=j.get("location") or "", url=url,
                          posted=parse_relative(ext.get("posted_at")), date_approx=True, date_unknown_ok=True,
                          description=strip_html(j.get("description")),
                          employment_type=str(ext.get("schedule_type") or ""),
                          workplace="remote" if ext.get("work_from_home") else None, regions=["India"]))
    return out


def jooble(opts):
    key = os.environ.get("JOOBLE_KEY")
    if not key:
        raise RuntimeError("skipped: no JOOBLE_KEY")
    out = []
    for q in opts.get("queries", []):
        data = http(f"https://jooble.org/api/{key}", method="POST",
                    json_body={"keywords": q, "location": "India", "page": 1}).json()
        for j in data.get("jobs", []):
            out.append(mk(source="Jooble", source_id=str(j.get("id") or j.get("link")), title=j.get("title"),
                          company=j.get("company"), location=j.get("location") or "", url=j.get("link"),
                          posted=parse_dt(j.get("updated")), description=strip_html(j.get("snippet")),
                          salary_text=j.get("salary") or "", employment_type=j.get("type") or "",
                          regions=["India"]))
    return out


# ------------------------------------------------------- company job boards
def _companies(opts):
    for c in opts.get("companies", []) or []:
        if isinstance(c, dict):
            yield c["token"], c.get("name") or c["token"].title()
        else:
            yield c, str(c).title()


def greenhouse(opts):
    out = []
    for token, name in _companies(opts):
        try:
            data = http(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"}).json()
        except FileNotFoundError:
            print(f"   greenhouse/{token}: board not found (check the name)")
            continue
        for j in data.get("jobs", []):
            first = j.get("first_published")
            out.append(mk(source="Greenhouse", source_id=f"{token}-{j.get('id')}", title=j.get("title"), company=name,
                          location=(j.get("location") or {}).get("name", ""), url=j.get("absolute_url"),
                          posted=parse_dt(first or j.get("updated_at")), date_approx=not first,
                          description=strip_html(j.get("content"))))
    return out


def lever(opts):
    out = []
    for token, name in _companies(opts):
        try:
            data = http(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"}).json()
        except FileNotFoundError:
            print(f"   lever/{token}: board not found (check the name)")
            continue
        for j in data:
            cats = j.get("categories") or {}
            sr = j.get("salaryRange") or {}
            sal = ""
            if sr.get("min") and sr.get("max"):
                per = "per month" if "month" in str(sr.get("interval", "")).lower() else "per year"
                sal = f"{sr.get('currency') or 'USD'} {sr['min']} - {sr['max']} {per}"
            out.append(mk(source="Lever", source_id=f"{token}-{j.get('id')}", title=j.get("text"), company=name,
                          location=cats.get("location") or ", ".join(cats.get("allLocations") or []),
                          url=j.get("hostedUrl"), posted=parse_dt(j.get("createdAt")),
                          description=j.get("descriptionPlain") or strip_html(j.get("description")),
                          salary_text=sal, employment_type=cats.get("commitment") or "",
                          workplace=j.get("workplaceType")))
    return out


def ashby(opts):
    out = []
    for token, name in _companies(opts):
        try:
            data = http(f"https://api.ashbyhq.com/posting-api/job-board/{token}",
                        params={"includeCompensation": "true"}).json()
        except FileNotFoundError:
            print(f"   ashby/{token}: board not found (check the name)")
            continue
        for j in data.get("jobs", []):
            wp = j.get("workplaceType") or ("Remote" if j.get("isRemote") else None)
            comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
            out.append(mk(source="Ashby", source_id=f"{token}-{j.get('id')}", title=j.get("title"), company=name,
                          location=j.get("location") or "", url=j.get("jobUrl") or j.get("applyUrl"),
                          posted=parse_dt(j.get("publishedAt")),
                          description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
                          salary_text=comp, employment_type=j.get("employmentType") or "", workplace=wp))
    return out


def workable(opts):
    out = []
    for token, name in _companies(opts):
        try:
            data = http(f"https://apply.workable.com/api/v1/widget/accounts/{token}", params={"details": "true"}).json()
        except FileNotFoundError:
            print(f"   workable/{token}: board not found (check the name)")
            continue
        name = data.get("name") or name
        for j in data.get("jobs", []):
            loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
            out.append(mk(source="Workable", source_id=f"{token}-{j.get('shortcode') or j.get('title')}",
                          title=j.get("title"), company=name, location=loc,
                          url=j.get("url") or j.get("shortlink") or j.get("application_url"),
                          posted=parse_dt(j.get("published_on") or j.get("created_at")),
                          description=strip_html(j.get("description")),
                          employment_type=j.get("employment_type") or "",
                          workplace="remote" if j.get("telecommuting") else None))
    return out


def workday(opts):
    """Big retailers / FMCG firms. Searches each company's public Workday feed, then reads the detail page
    of the few titles that look relevant (to limit requests)."""
    out = []
    queries = opts.get("queries") or ["product data"]
    cap = int(opts.get("max_details_per_company", 25))
    roles = ((opts.get("_cfg") or {}).get("roles")) or {}
    role_terms = list(roles.get("high", [])) + list(roles.get("medium", []))

    def in_field(title):
        return not role_terms or rules.has_any(title, role_terms)

    for c in opts.get("companies", []) or []:
        host, tenant, site = c["host"], c["tenant"], c["site"]
        name = c.get("name") or tenant.title()
        base = f"https://{host}/wday/cxs/{tenant}/{site}"
        found = {}
        try:
            for q in queries:
                for page in range(int(opts.get("pages", 2))):
                    r = http(f"{base}/jobs", method="POST",
                             json_body={"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": q})
                    posts = r.json().get("jobPostings", [])
                    for p in posts:
                        t = p.get("title") or ""
                        if p.get("externalPath") and not rules.TITLE_EXCLUDE.search(t) and in_field(t):
                            found[p["externalPath"]] = p
                    if len(posts) < 20:
                        break
        except FileNotFoundError:
            print(f"   workday/{tenant}: board not found (check host / tenant / site)")
            continue
        except Exception as e:
            print(f"   workday/{tenant}: FAILED ({str(e)[:50]})")
            continue
        for path in list(found)[:cap]:
            try:
                d = http(f"{base}{path}").json().get("jobPostingInfo", {})
            except Exception:
                continue
            rt = str(d.get("remoteType") or "").lower()
            wp = "remote" if "remote" in rt else "hybrid" if "hybrid" in rt else "onsite" if "site" in rt else None
            locs = [d.get("location")] + list(d.get("additionalLocations") or []) + \
                   [((d.get("country") or {}).get("descriptor"))]
            out.append(mk(source="Workday", source_id=f"{tenant}-{d.get('jobReqId') or path}",
                          title=d.get("title") or found[path].get("title"), company=name,
                          location=", ".join(str(x) for x in locs if x),
                          url=d.get("externalUrl") or f"https://{host}/{site}{path}",
                          posted=parse_dt(d.get("startDate")), description=strip_html(d.get("jobDescription")),
                          employment_type=str(d.get("timeType") or ""), workplace=wp))
    return out


SOURCES = {
    "remotive": remotive, "remoteok": remoteok, "weworkremotely": weworkremotely, "himalayas": himalayas,
    "jobicy": jobicy, "adzuna": adzuna, "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
    "workable": workable, "workday": workday, "serpapi": serpapi, "jooble": jooble,
}


def collect(cfg):
    """Returns (jobs, health) where health maps source -> 'N jobs' or the error."""
    jobs, health = [], {}
    for name, fn in SOURCES.items():
        opts = dict((cfg.get("sources") or {}).get(name) or {})
        if not opts.get("enabled", False):
            continue
        opts["_cfg"] = cfg
        try:
            got = fn(opts)
            jobs.extend(got)
            health[name] = f"{len(got)} jobs"
        except Exception as e:
            msg = str(e)
            health[name] = f"SKIPPED: {msg[9:].strip()}" if msg.startswith("skipped") else f"FAILED: {msg[:80]}"
        print(f"   {name}: {health[name]}")
    return jobs, health
