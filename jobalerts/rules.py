"""Rules that decide whether a job is acceptable: work mode, region, experience,
salary, employment type, and scam signals."""
import re
from functools import lru_cache

FREE_MAIL = ("gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "outlook.com",
             "hotmail.com", "rediffmail.com", "proton.me", "protonmail.com",
             "live.com", "aol.com", "icloud.com")


@lru_cache(maxsize=None)
def term_re(term):
    esc = re.escape(term.lower()).replace(r"\*", "[a-z]*")
    return re.compile(rf"(?<![a-z0-9]){esc}(?![a-z0-9])")


def count_terms(text, terms):
    t = (text or "").lower()
    return sum(1 for term in terms if term_re(term).search(t))


def has_any(text, terms):
    t = (text or "").lower()
    return any(term_re(term).search(t) for term in terms)


# --------------------------------------------------------------------- mode
_REMOTE = re.compile(r"\b(remote|work[- ]from[- ]home|wfh|anywhere|telecommut\w*|distributed)\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_NOT_REMOTE = re.compile(
    r"\b(?:not|no)\s+(?:a\s+)?(?:fully\s+)?(?:remote|work[- ]from[- ]home|wfh)\b"
    r"|\b(?:on[- ]?site|in[- ]office|office)\s+only\b", re.I)
_STRONG_REMOTE = re.compile(
    r"(?:fully|100%|completely|entirely|totally)\s+remote|remote[- ]first"
    r"|this\s+(?:is\s+)?(?:a\s+)?remote|remote\s+(?:position|role|opportunity|job)\b"
    r"|position\s+is\s+remote|work\s+remotely|work\s+from\s+home\s+(?:position|role|job|opportunity)", re.I)


def classify_mode(job):
    """Returns 'remote', 'hybrid' or 'onsite'. Anything unclear counts as onsite
    because onsite jobs must be skipped 100%."""
    wp = (job.get("workplace") or "").lower().replace("-", "").replace("_", "").replace(" ", "")
    if wp in ("remote", "hybrid"):
        return wp
    if wp in ("onsite", "office", "inoffice"):
        return "onsite"
    if job.get("remote_source"):
        return "remote"
    head = f"{job.get('title', '')} {job.get('location', '')}"
    if _REMOTE.search(head):
        return "remote"
    if _HYBRID.search(head):
        return "hybrid"
    desc = job.get("description") or ""
    if _NOT_REMOTE.search(desc):
        return "hybrid" if _HYBRID.search(desc) else "onsite"
    if _STRONG_REMOTE.search(desc):
        return "remote"
    if _HYBRID.search(desc):
        return "hybrid"
    return "onsite"


def in_hybrid_city(job, cities):
    cities = [c.lower() for c in cities]
    pat = re.compile(r"\b(" + "|".join(re.escape(c) for c in cities) + r")\b", re.I)
    loc = job.get("location") or ""
    if pat.search(loc):
        return True
    # location is only a country / blank: fall back to the opening of the description
    if re.fullmatch(r"\s*(india|in)?\s*", loc, re.I):
        return bool(pat.search((job.get("description") or "")[:1500]))
    return False


# ----------------------------------------------------------- remote region
_STRONG_OK = re.compile(r"\b(india|worldwide|world[- ]wide|global|globally|apac|asia|asia[- ]pacific|"
                        r"bengaluru|bangalore|mumbai|pune|delhi|hyderabad|chennai|international)\b", re.I)
_ANYWHERE = re.compile(r"\b(anywhere|any location|world)\b", re.I)
_BLOCK = re.compile(
    r"\b(us|usa|u\.s\.a?\.?|united states|canada|uk|u\.k\.|united kingdom|europe|european union|eu|emea|"
    r"latam|latin america|north america|americas|germany|france|spain|netherlands|poland|portugal|"
    r"ireland|brazil|mexico|argentina|colombia|australia|new zealand|nz|singapore|japan|china|"
    r"south africa|nigeria|kenya|israel|turkey|uae|dubai|saudi arabia|egypt|philippines|indonesia|"
    r"vietnam|pakistan|bangladesh|sri lanka|italy|sweden|norway|denmark|finland|switzerland|austria|"
    r"belgium|czech\w*|romania|ukraine|greece|hungary|bulgaria|croatia|serbia|lithuania|latvia|estonia)\b",
    re.I)
_DESC_RESTRICT = re.compile(
    r"(?:must|need to|should)\s+(?:be\s+)?(?:located|based|residing|reside|living|live)\s+(?:in|within)\s+(?:the\s+)?"
    r"(?:us|usa|u\.s\.|united states|uk|united kingdom|canada|europe|eu)\b"
    r"|(?:us|u\.s\.|united states|uk|canadian|eu)\s+(?:residents?|citizens?)\s+only"
    r"|authori[sz]ed\s+to\s+work\s+in\s+(?:the\s+)?(?:us|u\.s\.|united states|uk|united kingdom|canada|eu)\b"
    r"|(?:us|u\.s\.|uk|canada)[- ]based\s+(?:candidates|applicants|only)", re.I)


def remote_eligibility(job):
    """'yes' = open to someone in India, 'no' = restricted elsewhere, 'unclear' = not stated."""
    regions = job.get("regions")
    if regions is not None:
        if not regions:
            return "yes"
        return "yes" if _STRONG_OK.search(" ".join(regions)) else "no"
    loc = job.get("location") or ""
    if _STRONG_OK.search(loc):
        return "yes"
    if _BLOCK.search(loc):
        return "no"
    if _ANYWHERE.search(loc):
        return "yes"
    desc = job.get("description") or ""
    if _DESC_RESTRICT.search(desc) and not re.search(r"\bindia\b", desc, re.I):
        return "no"
    return "unclear"


# --------------------------------------------------------------- experience
_EXP = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*(\d{1,2})\s*\+?)?\s*(?:years?|yrs?)\b", re.I)
_SENIOR_DROP = re.compile(r"\b(principal|director|vp|vice president|head of|chief|cxo|avp|general manager|architect)\b", re.I)
_JUNIOR_DROP = re.compile(r"\b(intern|internship|trainee|apprentice|fresher|freshers)\b", re.I)
_SENIOR_PENALTY = re.compile(r"\b(senior|sr\.?|lead|staff)\b", re.I)


def required_experience(text):
    """Highest minimum-years figure that looks like a requirement, or None."""
    text = text or ""
    mins = []
    for m in _EXP.finditer(text):
        ctx = text[max(0, m.start() - 80): m.end() + 80].lower()
        if not re.search(r"experien|\bexp\b|worked|background|track record", ctx):
            continue
        pre = text[max(0, m.start() - 40): m.start()].lower()
        if re.search(r"\b(we|our|we've|we have|company|firm|team|over)\b", pre):
            continue
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else None
        if lo > 30 or (hi is not None and hi < lo):
            continue
        mins.append(lo)
    return max(mins) if mins else None


def title_level(title):
    if _SENIOR_DROP.search(title):
        return "too_senior"
    if _JUNIOR_DROP.search(title):
        return "too_junior"
    if _SENIOR_PENALTY.search(title):
        return "senior"
    return "ok"


# --------------------------------------------------------------- employment
_FREELANCE = re.compile(
    r"\bfreelanc\w*\b|independent contractor|\b1099\b|self[- ]employed|\bpart[- ]time\b"
    r"|on a (?:freelance|contractor) basis|per[- ]project basis", re.I)


def is_freelance(job):
    et = (job.get("employment_type") or "").lower()
    if "freelanc" in et or "part" in et and "time" in et:
        return True
    return bool(_FREELANCE.search(job.get("title", ""))) or bool(_FREELANCE.search((job.get("description") or "")[:1500]))


def is_contract(job):
    blob = f"{job.get('employment_type', '')} {job.get('title', '')}".lower()
    return "contract" in blob or bool(re.search(r"\bcontract (?:role|position|basis|to hire)\b",
                                                  (job.get("description") or "")[:1500], re.I))


# ------------------------------------------------------------------- salary
_UNIT = {"k": 1e3, "m": 1e6, "l": 1e5, "lpa": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5,
         "lacs": 1e5, "cr": 1e7, "crore": 1e7, "crores": 1e7}
_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(lpa|lakhs?|lacs?|crores?|cr|k|m|l)?\b", re.I)
_CURRENCIES = [
    ("AUD", re.compile(r"a\$|\baud\b", re.I)),
    ("CAD", re.compile(r"c\$|\bcad\b", re.I)),
    ("SGD", re.compile(r"s\$|\bsgd\b", re.I)),
    ("USD", re.compile(r"\$|\busd\b|us\$", re.I)),
    ("EUR", re.compile(r"€|\beur\b", re.I)),
    ("GBP", re.compile(r"£|\bgbp\b", re.I)),
    ("AED", re.compile(r"\baed\b", re.I)),
    ("INR", re.compile(r"₹|\binr\b|(?<![a-z])rs\.?(?![a-z])|rupee|\blpa\b|\blakhs?\b|\blacs?\b", re.I)),
]
_SAL_RANGE = re.compile(
    r"(?:₹|rs\.?|inr|\$|usd|€|£|gbp|eur)\s*\d[\d,]*(?:\.\d+)?\s*(?:k|lpa|lakhs?|l|cr|m)?\s*(?:-|–|—|to)\s*"
    r"(?:₹|rs\.?|inr|\$|usd|€|£)?\s*\d[\d,]*(?:\.\d+)?\s*(?:k|lpa|lakhs?|l|cr|m)?[^\n]{0,25}"
    r"|\d{1,3}(?:\.\d+)?\s*(?:-|–|to)\s*\d{1,3}(?:\.\d+)?\s*(?:lpa|lakhs?|lacs?)[^\n]{0,15}", re.I)
_SAL_CTX = re.compile(r"(?:salary|compensation|ctc|package|remuneration)[^\n]{0,80}", re.I)


def extract_salary_text(desc):
    if not desc:
        return ""
    m = _SAL_RANGE.search(desc)
    if m:
        return m.group(0)
    m = _SAL_CTX.search(desc)
    if m and re.search(r"\d", m.group(0)):
        return m.group(0)
    return ""


def parse_salary(text, rates):
    """Free text -> (low, high) in INR per year, or None."""
    if not text:
        return None
    t = str(text).lower().replace(",", "")
    cur = next((c for c, rx in _CURRENCIES if rx.search(t)), None)
    if not cur:
        return None
    nums = _NUM.findall(t)
    if not nums:
        return None
    v1, u1 = float(nums[0][0]), nums[0][1].lower()
    if len(nums) > 1:
        v2, u2 = float(nums[1][0]), nums[1][1].lower()
    else:
        v2, u2 = v1, u1
    u1 = u1 or u2
    u2 = u2 or u1
    if cur == "INR" and not u1 and v1 < 1000:
        return None
    lo = v1 * _UNIT.get(u1, 1)
    hi = v2 * _UNIT.get(u2, 1)
    if hi < lo:
        lo, hi = hi, lo
    mult = 1
    if re.search(r"per hour|/hr|/hour|hourly|an hour", t):
        mult = 2080
    elif re.search(r"per month|/month|/mo\b|monthly|a month", t):
        mult = 12
    rate = rates.get(cur, 1)
    lo_inr, hi_inr = lo * mult * rate, hi * mult * rate
    if not (1e5 <= hi_inr <= 2e8):
        return None
    return lo_inr, hi_inr


def salary_status(rng, cfg):
    """unlisted | in_range | near | out   (above the range counts as in_range)."""
    if not rng:
        return "unlisted"
    mn = cfg.get("min_lpa")
    if mn is None:
        return "in_range"
    hi_lpa = rng[1] / 1e5
    tol = cfg.get("near_tolerance", 0.2)
    if hi_lpa >= mn:
        return "in_range"
    if hi_lpa >= mn * (1 - tol):
        return "near"
    return "out"


def format_salary(rng):
    if not rng:
        return "Salary not listed"
    lo, hi = rng[0] / 1e5, rng[1] / 1e5
    if abs(hi - lo) < 0.05:
        return f"about {hi:.0f} LPA"
    return f"{lo:.0f}-{hi:.0f} LPA"


# -------------------------------------------------------------------- scams
_FEE = re.compile(
    r"\b(registration|security|training|processing|application|joining|refundable|kit|onboarding)\s+"
    r"(fee|fees|deposit|charges?|amount)\b|\bpay\b[^.\n]{0,40}\bto\s+(?:apply|register|confirm|book)\b"
    r"|\bsend\s+(?:us\s+)?(?:money|₹|rs)", re.I)
_CHAT_APPLY = re.compile(
    r"(whatsapp|telegram|signal)\b[^.\n]{0,60}(apply|send|contact|resume|cv|message|dm)"
    r"|(apply|send|contact|message|dm)[^.\n]{0,60}\b(whatsapp|telegram)\b", re.I)
_EARN = re.compile(
    r"\b(earn|income)\b[^.\n]{0,25}\b(per day|daily|per week|weekly)\b"
    r"|\b(typing job|data entry job|like and subscribe|task[- ]based)\b", re.I)
_URGENT = re.compile(
    r"\b(urgent(?:ly)? hiring|limited (?:seats|slots|vacancies)|hurry|instant joining|no interview|"
    r"direct joining|100% (?:placement|job) (?:guarantee|assurance))\b", re.I)
_SHORTENERS = ("bit.ly", "tinyurl.com", "cutt.ly", "t.co/", "goo.gl", "rb.gy", "shorturl.at",
               "wa.me", "t.me", "forms.gle", "docs.google.com/forms")
_AGENCY_NAME = re.compile(r"\b(staffing|recruit\w*|manpower|placement|resourcing|talent|headhunt\w*|hr solutions)\b", re.I)
_VAGUE_COMPANY = re.compile(r"^(confidential|our client|client|undisclosed|stealth|unknown|n/?a|)$", re.I)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)")
_LINKEDIN = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_%-]+", re.I)
_SKIP_EMAIL = re.compile(r"no-?reply|do-?not-?reply|example\.|privacy@|@sentry|\.png$|\.jpg$", re.I)


def extract_contact(text):
    text = text or ""
    emails = [e for e in dict.fromkeys(_EMAIL.findall(text)) if not _SKIP_EMAIL.search(e)][:3]
    phones = list(dict.fromkeys(m.strip() for m in _PHONE.findall(text)))[:2]
    links = list(dict.fromkeys(_LINKEDIN.findall(text)))[:2]
    return {"emails": emails, "phones": phones, "linkedin": links}


def scam_flags(job, cfg_salary=None):
    """Returns (high, warn, info) lists of short messages."""
    high, warn, info = [], [], []
    desc = job.get("description") or ""
    blob = f"{job.get('title', '')}\n{desc}"
    company = (job.get("company") or "").strip()
    contact = job.get("contact") or extract_contact(desc)
    if _FEE.search(blob):
        high.append("asks for a fee or deposit")
    if _CHAT_APPLY.search(blob):
        high.append("applications via WhatsApp/Telegram")
    if _EARN.search(blob):
        high.append("'earn per day' / task-style wording")
    for e in contact["emails"]:
        if e.split("@")[-1].lower() in FREE_MAIL:
            warn.append("recruiter uses a free email address (" + e.split("@")[-1] + ")")
            break
    if _URGENT.search(blob):
        warn.append("pressure wording (urgent / no interview / guaranteed)")
    url = (job.get("url") or "").lower()
    if any(s in url for s in _SHORTENERS) or not url.startswith("https://"):
        warn.append("apply link is a shortener, form or not https")
    if _VAGUE_COMPANY.match(company):
        warn.append("company name missing or hidden")
    if len(desc) < 250:
        warn.append("very short description")
    if cfg_salary and job.get("salary_rng"):
        mx = cfg_salary.get("max_lpa")
        yrs = job.get("min_years")
        if mx and job["salary_rng"][0] / 1e5 > 3 * mx and (yrs is None or yrs <= 2):
            warn.append("pay looks too high for the experience asked")
    if _AGENCY_NAME.search(company):
        info.append("staffing/recruitment firm posting; the end client may not be named")
    return high, warn, info
