"""
VOGENT OUTREACH QUEUE (Phase 2, Step A)
Runs right after the morning job search. Picks today's best direct-hire leads,
finds each company's founder / CEO / owner with Apify, and sends a ranked
"Outreach" tab to your Google Sheet with ready-to-send LinkedIn messages.

Your LinkedIn account is never used. You only edit the SETTINGS section.
"""
import datetime as dt
import glob
import json
import math
import unicodedata
import os
import re
import time
from urllib.parse import quote

import requests

# ============================ SETTINGS ============================
DAILY_TOTAL = 50                         # quality leads per day (email + LinkedIn); Upwork GHL rows come on top
MAX_COMPANY_SIZE = 50                    # founder-led companies: 1 to 50 staff (unknown size ranked last)
SMALL_COMPANY = 50
WEEKDAYS_ONLY = True                     # Monday to Friday (Pakistan time)
REGION_ORDER = {"USA": 1}                # USA only for now
# Role names are labels only: any role that meets the criteria can be pitched (RevOps excluded).
FIXED_FIRST_ROLE = "CRM Automation"
DATA_ROLES = ["AI Automation", "Customer Service", "Bookkeeping", "Performance Marketing", "SEO", "Lead Generation",
              "Social Media", "Content Writing", "Email Marketing", "Design & Video", "Website Support"]
ROLE_RANK = {FIXED_FIRST_ROLE: 1, **{r: i + 2 for i, r in enumerate(DATA_ROLES)}}
ROLES = list(ROLE_RANK)                  # VA, AI Automation, RevOps: never contacted automatically
ROLE_REGIONS = {"SEO": ["USA", "UK"]}    # roles limited to certain client regions
GHL_ONLY_FOR_CRM = False                 # CRM Automation = GHL or HubSpot (or other CRM) jobs
APIFY_ACTOR = "harvestapi~linkedin-company-employees"
APIFY_MAX_PEOPLE = 40                    # most people looked up in one pass
# Job boards, job aggregators and "entry level, commission" style posts are never contacted
NOT_EMPLOYER_NAME_WORDS = ["careers", "jobs", "job board", "jobboard", "hiring", "hunter", "recruit",
                           "staffing", "talent", "wiraa", "remotehunter", "jobot", "dice", "ziprecruiter",
                           "abilitylinks", "linkedin", "indeed", "glassdoor", "upwork", "fiverr",
                           "jobgether", "town of", "city of", "county", "government", "ministry",
                           "municipality", "university", "school district", "college", "authority",
                           "department", "district", "state of", "commonwealth", "public schools"]
NOT_EMPLOYER_TITLE_WORDS = ["entry level client", "client relations associate", "booking hotels",
                            "travel agent", "brand ambassador", "commission only", "commission-only"]
APIFY_DAILY_BUDGET_USD = 0.30            # whole Apify budget per weekday: Upwork first, the rest for LinkedIn founders
FOUNDER_TITLES = ["Founder", "Co-Founder", "CEO", "Owner", "President", "Managing Director"]
SPECIALISTS = {
    "Lead Generation": "appointment setters and lead generation specialists",
    "Social Media": "social media specialists",
    "Content Writing": "content writers",
    "Email Marketing": "email marketing specialists",
    "Design & Video": "designers and video editors",
    "Website Support": "website specialists (WordPress, Shopify, Webflow)",
    "CRM Automation": "GoHighLevel specialists",
    "Virtual Assistant": "remote support specialists",
    "AI Automation": "AI and automation specialists",
    "Customer Service": "customer support specialists",
    "RevOps": "RevOps and sales operations specialists",
    "Bookkeeping": "experienced remote bookkeepers",
    "Performance Marketing": "performance marketing specialists",
    "SEO": "SEO specialists",
}
# ---------- Email (Phase 2, Step B) ----------
EMAIL_ENABLED = True
EMAIL_START_DATE = "2026-10-13"          # warm-up: week 1 = 10 a day, week 2 = 20, then 30
EMAIL_LOOKUP_MAX = 90                    # companies checked for an email address each day
SENDER_NAME = "Usama Yousaf"
SENDER_TITLE = "Founder, Vogent Labs"
SIGNATURE = f"{SENDER_NAME}\n{SENDER_TITLE}"   # every email; no website line (better for spam filters)
LINKEDIN_URL = "linkedin.com/in/uxamayousaf"
CALENDLY_URL = "https://calendly.com/vogentlabs"
LINKEDIN_IN_FIRST_EMAIL_AFTER_DAYS = 14   # first email gets the LinkedIn link once warm-up is done
EMAIL_SPECIALISTS = {
    "Lead Generation": "appointment setters and lead generation specialists",
    "Social Media": "social media specialists",
    "Content Writing": "content writers",
    "Email Marketing": "email marketing specialists",
    "Design & Video": "designers and video editors",
    "Website Support": "website specialists (WordPress, Shopify, Webflow)",
    "CRM Automation": "GoHighLevel specialists",
    "Virtual Assistant": "remote support staff",
    "AI Automation": "AI and automation specialists",
    "Customer Service": "customer support specialists",
    "RevOps": "RevOps and sales operations specialists",
    "Bookkeeping": "experienced remote bookkeepers",
    "Performance Marketing": "performance marketing specialists",
    "SEO": "SEO specialists",
}
# ==================================================================

WEBAPP_URL = os.environ["SHEET_WEBAPP_URL"]
TOKEN = os.environ["SHEET_TOKEN"]
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
PKT = dt.timezone(dt.timedelta(hours=5))
WORK_ORDER = {"Remote": 0, "Not confirmed": 1, "Remote (check)": 1, "Remote (mentioned)": 1, "Not stated": 2, "Hybrid": 3, "On-site": 4}
OUTREACH_WORK_TYPES = ["Remote"]   # only jobs LinkedIn itself shows as Remote (never On-site, Hybrid or unconfirmed)
EMAIL_DAILY = 22                         # new emails per day (USA first, then Canada, then UK).
                                         # 22 new + 3 follow-ups each = about 80 sends a day, under the 95 cap
TITLE_RANK = ["founder", "co-founder", "ceo", "chief executive", "owner", "president",
              "managing director", "principal", "partner", "coo", "general manager"]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def co_key(name):
    """One company, one row: 'Blueprint Manufacturing Co.' = 'Blueprint Manufacturing',
    'A Full Renovation – Projects' = 'A Full Renovation'."""
    n = re.split(r"\s+[–—|]\s+|\s+-\s+", str(name or ""))[0]
    n = re.sub(r"[,.]?\s+(?:co|company|llc|inc|incorporated|ltd|limited|corp|corporation|group|pllc|lp|llp)\.?$",
               "", n.strip(), flags=re.I)
    return norm(n) or norm(name)


def slug(url):
    m = re.search(r"linkedin\.com/company/([^/?#]+)", str(url or ""))
    return m.group(1).lower() if m else ""


def post(payload):
    payload["token"] = TOKEN
    r = requests.post(WEBAPP_URL, json=payload, timeout=180)
    return r.text


# ---------- 1. Load today's leads from the job search ----------
def load_candidates():
    leads = []
    for path in glob.glob("candidates/**/*.json", recursive=True):
        try:
            with open(path) as fh:
                leads.extend(json.load(fh))
        except Exception as e:
            print("Could not read", path, e)
    return leads


def onsite_ask(l):
    """On-site / hybrid job at a company with 1 to 10 staff: worth asking if they would hire remotely."""
    return l.get("work_type") in ("On-site", "Hybrid") and l.get("size_min") is not None and l["size_min"] <= 10


def eligible(l):
    if l.get("work_type") not in OUTREACH_WORK_TYPES and not onsite_ask(l):
        return False                       # office jobs only at 1-10 staff companies (asked about remote)
    if l.get("region") not in REGION_ORDER:
        return False
    if l.get("category") in ("RevOps",):
        return False                       # US companies hire RevOps locally
    if l.get("category") == "CRM Automation" and GHL_ONLY_FOR_CRM and "GHL" not in (l.get("priority") or ""):
        return False
    if not l.get("direct", True):
        return False                       # staffing / recruiting / outsourcing: never contacted
    comp, title = str(l.get("company", "")).lower(), str(l.get("title", "")).lower()
    if any(w in comp for w in NOT_EMPLOYER_NAME_WORDS) or any(w in title for w in NOT_EMPLOYER_TITLE_WORDS):
        return False                       # job boards, aggregators, entry-level commission posts
    size_min = l.get("size_min")
    if size_min is not None and size_min > MAX_COMPANY_SIZE:
        return False
    if is_nonprofit(l) or norm(l.get("company")) in ("confidential", "confidentialcompany", "stealth", "stealthstartup"):
        return False                       # nonprofits, associations, credit unions, hidden company names
    if size_min is None and not ("GHL" in str(l.get("priority") or "") or l.get("worldwide") or l.get("tiny_clue")):
        return False                       # unknown size only with a strong signal (GHL, worldwide, tiny business)
    return all(str(l.get(k) or "").strip() for k in ("company", "title", "url"))


GROWING = set()                          # companies posting 2+ matching roles today


NONPROFIT_IND = re.compile(r"non-?profit|philanthrop|civic|social organization|government|public policy|"
                           r"religious|fund-?raising|museums?|libraries", re.I)
NONPROFIT_NAME = re.compile(r"\b(foundation|council|association|society|charity|charitable|institute|"
                            r"federation|ministries|church|alliance|credit union|university|college|school|board|"
                            r"asu)\b", re.I)
UNKNOWN_SIZE_MAX = 10                    # unknown-size companies per day, and only with a strong signal


def is_nonprofit(l):
    """Nonprofits and public bodies rarely buy offshore staffing: ranked lower, never removed."""
    site = str(l.get("website") or "").lower()
    return bool(NONPROFIT_IND.search(str(l.get("industry") or "")) or NONPROFIT_NAME.search(str(l.get("company") or ""))
                or re.search(r"\.org(?:\.[a-z]{2})?(?:/|$)", site))


def size_tier(l):
    """1-10 staff first, then 11-20, then 21-50, unknown size last."""
    size_min, staff = l.get("size_min"), l.get("staff_count")
    if size_min is None:
        return 3
    if size_min <= 10:
        return 0
    if staff is not None and staff <= 20:
        return 1
    return 2


# Roles that are easiest to staff, in this order. Others only fill the day if these are not enough.
ROLE_PRIORITY = [
    ("GHL / CRM", lambda t, c: c == "CRM Automation"),
    ("SDR", lambda t, c: bool(re.search(r"sales development|\bsdr\b|\bbdr\b|business development rep", t, re.I))),
    ("SEO", lambda t, c: bool(re.search(r"\bseo\b|search engine", t, re.I))),
    ("Performance Marketer", lambda t, c: bool(re.search(r"performance market|paid (?:media|search|social|ads)|"
                                                     r"\bppc\b|media buyer|google ads|meta ads|facebook ads", t, re.I))),
    ("WordPress Developer", lambda t, c: bool(re.search(r"wordpress", t, re.I))),
    ("Content & Graphics", lambda t, c: bool(re.search(r"graphic|content creator|content designer|visual designer",
                                                       t, re.I))),
    ("Executive Assistant", lambda t, c: bool(re.search(r"executive assistant|virtual assistant|personal assistant|"
                                                        r"\bea\b|\bva\b", t, re.I))),
]


def role_tier(l):
    t, c = str(l.get("title") or ""), str(l.get("category") or "")
    for i, (_, test) in enumerate(ROLE_PRIORITY):
        if test(t, c):
            return i
    return len(ROLE_PRIORITY)                          # any other qualifying remote role: only to fill the day


def rank_key(l):
    """Best first: role priority (your list), then smallest companies, then 'open worldwide', GoHighLevel, newest."""
    try:
        newest = -dt.date.fromisoformat(str(l.get("posted"))[:10]).toordinal()
    except ValueError:
        newest = 0
    ghl = 0 if "GHL" in str(l.get("priority") or "") else 1
    return (role_tier(l), 1 if onsite_ask(l) else 0, size_tier(l), 0 if l.get("worldwide") else 1, ghl, newest)


# ---------- 2. Find decision makers with Apify ----------
def apify_run(body, cost_cap):
    return apify_run_actor(APIFY_ACTOR, body, cost_cap)


def apify_run_actor(actor, body, cost_cap):
    base = "https://api.apify.com/v2"
    run = requests.post(f"{base}/acts/{actor}/runs",
                        params={"token": APIFY_TOKEN, "maxTotalChargeUsd": round(cost_cap, 2)},
                        json=body, timeout=60).json()["data"]
    run_id, dataset = run["id"], run["defaultDatasetId"]
    status = ""
    for _ in range(90):                                   # wait up to ~15 minutes
        time.sleep(10)
        status = requests.get(f"{base}/actor-runs/{run_id}", params={"token": APIFY_TOKEN},
                              timeout=30).json()["data"]["status"]
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    print("Apify run status:", status)
    items = requests.get(f"{base}/datasets/{dataset}/items",
                         params={"token": APIFY_TOKEN, "clean": "true"}, timeout=60).json()
    return items if isinstance(items, list) else []


def apify_people(company_urls, second_pass=False, budget=APIFY_DAILY_BUDGET_USD):
    """Pass 1: senior people (CXO, Owner / Partner). Pass 2: founder-type job titles for the misses."""
    if not APIFY_TOKEN or not company_urls or budget < 0.06:
        return []
    max_people = int((budget - 0.02) / 0.004)             # $0.02 start + about $0.004 per person
    body = {
        "companies": company_urls,
        "companyBatchMode": "all_at_once",
        "profileScraperMode": "Short ($4 per 1k)",
        "maxItems": max(5, min(max_people, 2 * len(company_urls), APIFY_MAX_PEOPLE)),
    }
    if second_pass:
        body["jobTitles"] = FOUNDER_TITLES
    else:
        body["seniorityLevelIds"] = ["310", "320"]        # CXO, Owner / Partner
    try:
        return apify_run(body, budget)
    except Exception as e:
        print("Apify lookup failed:", e)
        return []


def clean_name(n):
    n = re.sub(r"\(.*?\)|\[.*?\]", "", str(n or ""))          # "(Let's connect)" etc.
    n = n.split(",")[0]                                          # ", MBA" / ", CPA"
    n = re.sub(r"[^\w\s.'-]", "", n, flags=re.UNICODE)           # emojis and symbols
    return re.sub(r"\s+", " ", n).strip()


def person_info(item):
    first = clean_name(item.get("firstName"))
    last = clean_name(item.get("lastName"))
    name = (first + " " + last).strip() or clean_name(item.get("fullName") or item.get("name"))
    url = item.get("linkedinUrl") or item.get("profileUrl") or item.get("linkedInUrl") or item.get("url") or ""
    title = str(item.get("headline") or "")
    comp_url, comp_name = "", ""
    positions = item.get("currentPosition") or item.get("currentPositions") or item.get("experience") or []
    if isinstance(positions, dict):
        positions = [positions]
    if positions and isinstance(positions[0], dict):
        p = positions[0]
        title = p.get("position") or p.get("title") or title
        comp_url = p.get("companyLinkedinUrl") or p.get("companyUrl") or p.get("companyLinkedInUrl") or ""
        comp_name = p.get("companyName") or p.get("company") or ""
    return {"first": first or name.split(" ")[0] if name else "", "name": name, "url": url,
            "title": str(title).strip(), "comp_slug": slug(comp_url), "comp_name": norm(comp_name)}


def title_score(title):
    t = title.lower()
    for i, word in enumerate(TITLE_RANK):
        if word in t:
            return i
    return len(TITLE_RANK)


def match_people(targets, items):
    people = [person_info(i) for i in items if isinstance(i, dict)]
    people = [p for p in people if p["name"] and p["url"]]
    best = {}
    for t in targets:
        t_slug, t_name = slug(t["company_url"]), norm(t["company"])
        cands = [p for p in people if (t_slug and p["comp_slug"] == t_slug)
                 or (t_name and p["comp_name"] and (p["comp_name"] == t_name
                     or t_name in p["comp_name"] or p["comp_name"] in t_name))]
        if cands:
            best[t["company"]] = sorted(cands, key=lambda p: title_score(p["title"]))[0]
    return best


# ---------- 2b. Free founder lookup (no Apify, no LinkedIn login) ----------
FOUNDER_WORDS = ["founder", "co-founder", "ceo", "chief executive", "owner", "president",
                 "managing director", "principal", "partner"]


def ddg_search(query):
    """Public web search (DuckDuckGo). Returns [(url, title)]."""
    from urllib.parse import parse_qs, unquote, urlparse
    try:
        r = requests.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=UA, timeout=20)
        out = []
        for href, title in re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', r.text):
            if "uddg=" in href:
                href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
            out.append((href, re.sub(r"<[^>]+>", "", title).strip()))
        return out
    except Exception:
        return []
    finally:
        time.sleep(2)


def free_founder(company, people=None):
    """1) the public 'Employees at' box on the company page, 2) a public web search."""
    for name, url, title in (people or []):
        if any(w in title.lower() for w in FOUNDER_WORDS):
            return {"name": clean_name(name), "first": clean_name(name).split(" ")[0], "url": url, "title": title}
    return None                                          # no web search: "Find founder ↗" button instead
    words = [w for w in re.findall(r"[a-z0-9]+", company.lower()) if len(w) > 2][:3]
    for url, title in ddg_search(f'"{company}" founder OR CEO OR owner site:linkedin.com/in'):
        if "linkedin.com/in/" not in url:
            continue
        t = title.lower()
        if not any(w in t for w in FOUNDER_WORDS) or not all(w in t for w in words[:2]):
            continue                                       # must be a founder-type title AT this company
        parts = [x.strip() for x in re.split(r"\s[-|–]\s", title)]
        name = clean_name(parts[0])
        if 2 <= len(name.split()) <= 4:
            return {"name": name, "first": name.split(" ")[0], "url": url.split("?")[0],
                    "title": parts[1] if len(parts) > 1 else ""}
    return None


def linkedin_company_url(company):
    return "https://www.linkedin.com/search/results/companies/?keywords=" + quote(company)


def _old_linkedin_company_url(company):
    for url, title in ddg_search(f'"{company}" site:linkedin.com/company'):
        if "linkedin.com/company/" in url and norm(company)[:8] in norm(title):
            return url.split("?")[0]
    return ""


# ---------- Upwork GHL leads (Apify), named clients only ----------
UPWORK_ENABLED = True
UPWORK_ACTOR = "flash_mage~upwork"
UPWORK_QUERIES = ["GoHighLevel", "HighLevel", "GHL"]   # Upwork: GoHighLevel jobs only
UPWORK_LIMIT = 95                        # newest GHL jobs checked per day (about $0.30, the whole Apify budget)
UPWORK_COST_CAP_USD = 0.30
UPWORK_RECONTACT_DAYS = 30               # a past Upwork client posting a NEW GHL job after 30+ days is contacted again
FOUNDER_LOOKUP = False                   # paid founder search off: you find founders on LinkedIn ("Find founder" link)
GHL_SKILLS = [("funnels", ["funnel"]), ("workflows", ["workflow", "automation"]), ("pipelines", ["pipeline"]),
              ("SMS follow-ups", ["sms", "text follow"]), ("email campaigns", ["email marketing", "email campaign",
              "email sequence", "newsletter"]), ("calendars", ["calendar", "appointment"]),
              ("landing pages", ["landing page"]), ("integrations", ["zapier", "make.com", "webhook", "api"]),
              ("A2P setup", ["a2p", "10dlc"]), ("lead follow-up", ["lead follow", "follow-up", "nurture"]),
              ("CRM management", ["crm"])]
NOT_A_NAME = {"growth", "digital", "email", "content", "performance", "social", "affiliate", "b2b", "paid",
              "inbound", "outbound", "real", "full", "part", "senior", "junior", "new", "local", "online", "sales",
              "lead", "funnel", "automation", "marketing", "seo", "ai", "virtual", "executive", "medical", "custom",
              "we", "i", "our", "this", "the", "you", "your", "gohighlevel", "highlevel", "ghl", "upwork",
              "meta", "google", "facebook", "zapier", "make", "hubspot", "crm", "looking", "about", "company",
              "seeking", "hiring", "remote", "ideal", "responsibilities", "requirements", "us", "it", "they"}


def get_path(d, path, default=None):
    for k in path.split("."):
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return default if d is None else d


GENERIC_WORDS = set("""a an and the of for to in on at by with we us our you your i me my they it this that these
there here who what when where why how very important short below above overview job role position title
looking hiring seeking need needed help about company business team operations marketing sales growth
digital assistant manager specialist expert support services service solutions systems group agency media
labs studio partners consulting holdings llc inc ltd co founder owner ceo real estate remote full part time
long term ongoing new local online virtual executive medical custom lead leads funnel automation ai seo
summary description responsibilities requirements qualifications benefits compensation details project
apply application proposal note please thank thanks hello hi welcome intro introduction background mission
key what's we're you'll we've it's let's value values vision culture story offer stack full-stack
who what why how""".split())


# Phrases and product names that look like company names but are not the client
NOT_A_COMPANY_RE = re.compile(r"account[- ]based|looker|google|microsoft|hubspot|salesforce|highlevel|"
                              r"zapier|monday|notion|shopify|wordpress|linkedin|facebook|meta ads|tiktok|amazon|"
                              r"apple|canva|slack|openai|chatgpt|claude", re.I)


def client_name(desc):
    """Only returns a name the job post itself states, and only if it looks like a real company name."""
    pats = [
        r"(?im)^\s*company\s*:\s*([A-Z][^\n]{1,50})$",
        r"(?m)^\s*About\s+([A-Z][\w&'.-]*(?:\s+[A-Z&][\w&'.-]*){0,4})\s*$",
        r"\b([A-Z][\w&'.-]*(?:\s+(?:[A-Z&][\w&'.-]*|of|and)){0,4}\s+(?:LLC|Inc\.?|Ltd\.?|Co\.|Group|Solutions|"
        r"Systems|Agency|Media|Marketing(?: AI)?|Realty|Remodeling|Labs|Wellness|Partners|Holdings|Consulting|Studio))\b",
        r"(?m)(?:^|[.!?]\s+)([A-Z][\w&'.-]*(?:\s+(?:[A-Z&][\w&'.-]*|of|and)){0,4})\s+(?:is|are|seeks|helps|"
        r"connects|has been|specializes|was founded)\b",
    ]
    for p in pats:
        for m in re.finditer(p, desc):
            name = re.sub(r"\s+", " ", m.group(1)).strip(" .,:")
            first = name.split(" ")[0].lower()
            if first in NOT_A_NAME or len(name) < 3 or name.lower() in NOT_A_NAME or NOT_A_COMPANY_RE.search(name):
                continue
            tokens = [t.lower().strip("'’&.,") for t in re.split(r"[\s-]+", name)]
            if tokens and tokens[0] in GENERIC_WORDS:
                continue                                   # "What We Value We", "Our Team" ...
            distinctive = [t for t in tokens if t and t not in GENERIC_WORDS]
            if not distinctive:
                continue                                   # e.g. "Very Important We", "There", "Below"
            confirmed = (p == pats[0] or p == pats[2] or
                         len(re.findall(re.escape(name.split(" ")[0]), desc)) >= 2)
            if confirmed:
                return name
    return ""


# Domains that never identify the client (tools, social sites, mail providers)
NOT_CLIENT_DOMAINS = re.compile(r"monday|hunter|smartlead|instantly|lemlist|clay|asana|clickup|trello|atlassian|jira|"
                                r"salesforce|pipedrive|intercom|mailchimp|klaviyo|semrush|ahrefs|looker|quickbooks|xero|"
                                r"gusto|docusign|calendly|zendesk|freshdesk|gorgias|aircall|ringcentral|twilio|"
                                r"upwork|gohighlevel|highlevel|leadconnector|msgsndr|hubspot|google|youtube|youtu\.be|"
                                r"loom|calendly|facebook|fb\.com|instagram|linkedin|twitter|x\.com|tiktok|zapier|"
                                r"make\.com|gmail|yahoo|outlook|hotmail|icloud|bit\.ly|tinyurl|canva|notion|figma|"
                                r"dropbox|wix|squarespace|shopify\.com|wordpress\.(?:com|org)|godaddy|clickfunnels|"
                                r"kajabi|stripe|paypal|slack|zoom|example|apple\.com|microsoft|amazon|github|airtable|"
                                r"typeform|jotform|vimeo|medium\.com|trustpilot|yelp|apollo\.io|n8n|openai|chatgpt", re.I)


def client_from_domain(desc):
    """Anonymous posts often still show the client's own website or email: 'see acme-roofing.com'.
    Returns (company name, website) or ('', '')."""
    for m in re.finditer(r"(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9-]{1,40}\.(?:com|co|io|net|org|us|ai|agency|"
                         r"marketing|biz|pro|app|health|law|realty|homes))\b(?![.@\w])", desc, re.I):
        dom = m.group(1).lower()
        if NOT_CLIENT_DOMAINS.search(dom):
            continue
        before = desc[max(0, m.start() - 60):m.start()].lower()
        if re.search(r"\b(?:use|using|used|uses|tools?|platforms?|software|like|via|with|in|on|familiar|experience|"
                     r"knowledge|proficien|stack|integrat)\W*(?:\w+\W+){0,3}$", before) and \
                not re.search(r"\b(?:our|my|visit|website|site|check out|about us|learn more|see)\b", before):
            continue                       # a tool they use, not the client's own website
        stem = dom.rsplit(".", 1)[0]
        name = " ".join(w.capitalize() for w in re.split(r"[-_]", stem) if w)
        if len(stem) >= 3 and name.lower() not in NOT_A_NAME:
            return name, "https://" + dom
    for m in re.finditer(r"\b[\w.+-]+@([a-z0-9-]{2,40}\.(?:com|co|io|net|org|us|ai|agency))\b", desc, re.I):
        dom = m.group(1).lower()
        if not NOT_CLIENT_DOMAINS.search(dom):
            stem = dom.rsplit(".", 1)[0]
            return " ".join(w.capitalize() for w in re.split(r"[-_]", stem) if w), "https://" + dom
    return "", ""


def proposal_keyword(desc):
    """Many posts say 'Start your proposal with the word X'. We put X first, as asked."""
    m = re.search(r"(?i)(?:start|begin)\s+(?:your|the)\s+(?:proposal|application|cover letter|response)[^.\n]{0,30}?"
                  r"(?:with|using)\s+(?:(?:the\s+)?(?:word|words|phrase)s?)?\s*:?\s*([\s\S]{0,70})", desc)
    if not m:
        return ""
    tail = m.group(1)
    q = re.search(r"[\"“'‘]\s*([^\"”'’\n]{2,50}?)\s*[.,!]?\s*[\"”'’]", tail)
    if q:
        return q.group(1).strip(" .,")
    caps = re.match(r"\s*([A-Z][A-Z0-9' ]{1,40}[A-Z0-9])\b", tail)
    return caps.group(1).strip() if caps else ""


def commission_heavy(desc):
    d = desc.lower()
    return "commission" in d and bool(re.search(r"commission[- ]only|base pay|\$\s?[1-9](?:\.\d+)?\s*/\s*h(?:ou)?r|"
                                                  r"\$\s?\d{2,3}\s*/\s*month base", d))


UPWORK_STATS = {"checked": 0, "named": 0, "new": 0}


def upwork_link(it):
    """The job's Upwork link. Some results come without one: then an Upwork search for that exact title,
    so the row is never dropped by the Sheet (it needs a link)."""
    link = str(it.get("link") or it.get("url") or "")
    if link.startswith("http"):
        return link
    cipher = str(it.get("ciphertext") or get_path(it, "data.opening.ciphertext", "") or "")
    if cipher:
        return "https://www.upwork.com/jobs/" + (cipher if cipher.startswith("~") else "~" + cipher)
    return "https://www.upwork.com/nx/search/jobs/?q=" + requests.utils.quote(str(it.get("title") or "GoHighLevel"))


def upwork_leads(today, start_rank):
    if not (UPWORK_ENABLED and APIFY_TOKEN):
        return []
    body = {"query": UPWORK_QUERIES, "limit": UPWORK_LIMIT, "sort": "newest", "hourly": True, "fixed": False,
            "project_length": ["ongoing"], "location_subregions": ["Northern America"]}
    try:
        items = apify_run_actor(UPWORK_ACTOR, body, UPWORK_COST_CAP_USD)
    except Exception as e:
        print("Upwork lookup failed:", e)
        return []
    leads, seen = [], set()
    drop = {"no_name": 0, "not_verified": 0, "not_us": 0, "commission": 0, "big": 0, "duplicate": 0}
    for it in items:
        desc = str(get_path(it, "data.opening.description", "") or "")
        country = str(get_path(it, "buyer.location.country", "") or "")
        region = "USA" if country in ("USA", "United States") else ("Canada" if country == "Canada" else "")
        size = get_path(it, "buyer.company.profile.size")
        spent = get_path(it, "buyer.stats.totalCharges.amount", 0) or 0
        verified = get_path(it, "buyer.isPaymentMethodVerified", True)
        if not UPWORK_GHL_RE.search(str(it.get("title") or "") + " " + desc[:400]):
            drop["not_ghl"] = drop.get("not_ghl", 0) + 1
            continue                     # Upwork: GoHighLevel jobs only (title or the start of the post)
        if not verified:
            drop["not_verified"] += 1
            continue                     # payment method verified (new clients with $0 spent are fine)
        name, website = client_name(desc), ""
        if not name:
            name, website = client_from_domain(desc)       # website or email domain in the post
        title = str(it.get("title") or "")
        # Senior / Lead / Manager titles are allowed on Upwork (remote by nature)
        if not region:
            drop["not_us"] += 1
            continue
        if not (desc and name):
            drop["no_name"] += 1
            continue                                       # we can only reach clients we can name
        if commission_heavy(desc):
            drop["commission"] += 1
            continue
        if not CRM_UPWORK_TITLE.search(title) and re.search(
                r"\b(?:senior|sr\.?|lead|head|director|vp|chief|principal|manager|strategist|\bgm\b|"
                r"general manager|account executive|sales rep|sales representative|fractional)\b", title, re.I):
            drop["wrong_role"] = drop.get("wrong_role", 0) + 1
            continue                       # non-GHL Upwork jobs follow the same title rules as LinkedIn
        if isinstance(size, (int, float)) and size > MAX_COMPANY_SIZE:
            drop["big"] += 1
            continue
        if norm(name) in seen:
            drop["duplicate"] += 1
            continue
        seen.add(norm(name))
        d = desc.lower()
        skills = [n for n, words in GHL_SKILLS if any(w in d for w in words)][:3]
        leads.append({"company": name, "region": region, "size": str(int(size)) if isinstance(size, (int, float)) else "Unknown",
                      "title": str(it.get("title") or "GoHighLevel role"), "url": upwork_link(it),
                      "skills": skills, "keyword": proposal_keyword(desc), "desc": desc, "website": website})
    print(f"Upwork: {len(items)} GHL jobs checked, {len(leads)} with a named client", drop)
    UPWORK_STATS.update({"checked": len(items), "named": len(leads), **drop})
    return leads


def upwork_proposal(lead, first):
    skills = lead["skills"] or ["your GoHighLevel setup"]
    s_txt = skills[0] if len(skills) == 1 else ", ".join(skills[:-1]) + " and " + skills[-1]
    top = f"{lead['keyword']}\n\n" if lead["keyword"] else ""
    hi = f"Hi {first}," if first else "Hi there,"
    return (f"{top}{hi}\n\nYou need someone to own {s_txt} in GoHighLevel long term, and that is exactly the "
            f"work our GHL specialists do every day.\n\nI'm Usama, founder of Vogent Labs. We place dedicated "
            f"GoHighLevel specialists who join your team in days, not weeks, and stay with you long term, at a much "
            f"lower cost than a local hire. We handle everything on our side, so it stays hassle free for you.\n\n"
            f"Quick question so I can match the right person: what is the one thing this hire must get right in the "
            f"first 30 days?\n\nUsama")


UPWORK_GHL_RE = re.compile(r"go\s?high\s?level|\bhigh\s?level\b|\bghl\b|lead\s?connector", re.I)
CRM_UPWORK_TITLE = re.compile(r"go\s?high\s?level|high\s?level|\bghl\b|hubspot|\bcrm\b|funnel|automation|"
                              r"zapier|workflow|lead\s?connector|snapshot|saas mode", re.I)


def upwork_role_messages(lead, first):
    """Upwork jobs that are not GHL / HubSpot roles (an SDR, a VA...): the normal LinkedIn sequence for the real role."""
    title = re.split(r"\s+[–—|-]\s+|\s*\(|,|:", str(lead.get("title") or ""))[0].strip() or "open role"
    t = title.lower()
    cat = ("Lead Generation" if re.search(r"sales development|\bsdr\b|\bbdr\b|appointment|setter|lead gen", t) else
           "Virtual Assistant" if re.search(r"assistant|\bva\b|admin", t) else
           "SEO" if "seo" in t else "Performance Marketing" if re.search(r"ads|media buyer|ppc|marketing", t) else
           "Website Support" if re.search(r"wordpress|developer|website", t) else
           "Design & Video" if re.search(r"design|graphic|video", t) else "Customer Service")
    l = {"company": lead["company"], "role": title, "category": cat, "skills": ""}
    role, company = msg_role(l), clean_company(lead["company"])
    f = fix_case(first)
    note = (f"Hi {f}, " if f else "Hi, ") + (f"noticed {company} is hiring for {article(role)}. Curious, have you found "
                                             f"the right person out of the applicants yet, or still going through them? "
                                             f"Thought it'd be good to connect!")
    if len(note) > 300:
        note = note[:297].rsplit(" ", 1)[0] + "..."
    return [note] + linkedin_messages(l, first)


def upwork_messages(lead, first):
    """Upwork clients are contacted on LinkedIn, so they get the same sequence as LinkedIn rows,
    written for GoHighLevel: connection note, DM after accept, follow-up 1, follow-up 2."""
    first = fix_case(first)
    company = clean_company(lead["company"])
    d = str(lead.get("desc") or "") + " " + str(lead.get("title") or "")
    tool = "HubSpot" if re.search(r"hubspot", d, re.I) and not re.search(r"high\s?level|\bghl\b", d, re.I) else "GoHighLevel"
    skills = [x for x in lead.get("skills") or [] if x]
    note = (f"Hi {first}, " if first else "Hi, ") + (
        f"noticed {company} is hiring for {tool} help. Curious, have you found the right person out of the "
        f"applicants yet, or still going through them? Thought it'd be good to connect!")
    if len(note) > 300:
        note = note[:297].rsplit(" ", 1)[0] + "..."
    sk = join_skills(skills[:3])
    who = (f"We work with remote professionals experienced in {join_skills([tool] + skills[:2])}, and there's no "
           f"upfront fee: you only pay once your hire starts." if sk
           else f"We work with remote {tool} specialists who do this work every day, and there's no upfront fee: "
                f"you only pay once your hire starts.")
    dm = (f"Thanks for connecting, {first}!" if first else "Thanks for connecting!") + \
        f" Saw your {tool} opening at {company}. {who} Would it help to see a couple of profiles that match your post?"
    hard = (f"finding someone strong in both {tool} and {skills[0]} is not easy" if skills
            else f"finding a strong {tool} specialist is not easy")
    fu1 = (f"{'Hi ' + first if first else 'Hi'}, {hard}, which is why I reached out. At Vogent Labs, we place dedicated remote "
           f"professionals at a much lower cost than a local hire, and if the person isn't the right fit, we replace "
           f"them free. Happy to share a couple of profiles that match your {tool} post if useful.")
    fu2 = (f"{'Hey ' + first if first else 'Hi'}, checking back on the {tool} opening. If you're still hiring, I can "
           f"send a couple of relevant profiles today, with nothing to pay until your hire starts. Or if it's easier to "
           f"talk it through, you can pick a time here: {CALENDLY_URL}. If you've already filled it, no worries at all!")
    return [note, dm, fu1, fu2]


def upwork_note(lead, first):
    skills = lead["skills"][:2]
    s_txt = " and ".join(skills) if skills else "GoHighLevel"
    hi = f"Hi {first}," if first else "Hi,"
    note = (f"{hi} saw {lead['company']} is looking for GoHighLevel help with {s_txt}. How is the search going "
            f"so far? Would be glad to connect.")
    return note if len(note) <= 300 else note[:297].rsplit(" ", 1)[0] + "..."


# ---------- 3. Messages ----------
def specialists(lead, table):
    if lead.get("category") == "CRM Automation":
        blob = (str(lead.get("priority")) + " " + str(lead.get("title")) + " " + str(lead.get("skills"))).lower()
        if "ghl" in blob or "highlevel" in blob:
            return "GoHighLevel specialists"
        if "hubspot" in blob:
            return "HubSpot specialists"
        return "CRM and automation specialists"
    return table.get(lead.get("category"), "remote specialists")


def article(role):
    first = role.split(" ")[0] if role else ""
    if first.isupper() and len(first) > 1:                 # acronyms: "an SEO", "an SDR", "a CRM"
        return ("an " if first[0] in "AEFHILMNORSX" else "a ") + role
    return ("an " if role[:1].lower() in "aeiou" else "a ") + role



def is_emoji_char(ch):
    """Emojis and picture symbols only; letters and normal punctuation like @ & . stay (same as find_leads.py)."""
    o = ord(ch)
    return unicodedata.category(ch) in ("So", "Cs") or 0xFE00 <= o <= 0xFE0F or o == 0x200D or 0x1F000 <= o <= 0x1FAFF


def fix_case(name):
    """'ALI' or 'ali' -> 'Ali'; names already in normal case stay as they are ('McKenzie')."""
    name = str(name or "").strip()
    if name and (name.isupper() or name.islower()):
        return "-".join(part.capitalize() for part in name.split("-"))
    return name


def clean_company(name):
    """Company name for messages: drops emojis and decorative symbols (same as find_leads.py)."""
    out = "".join(ch for ch in str(name or "") if not is_emoji_char(ch))
    return re.sub(r"\s{2,}", " ", out).strip() or str(name or "").strip()


def msg_role(lead):
    """Short role used in every message. Virtual Assistant roles are never called VA."""
    if lead.get("category") == "Virtual Assistant":
        return "assistant"
    role = re.sub(r"virtual assistant", "assistant", lead.get("role") or "", flags=re.I).strip()
    role = re.sub(r"^(?:(?:remote|hybrid|part[- ]time|full[- ]time|freelance|contract|temporary|temp)\s+)+", "",
                  role, flags=re.I).strip() or role          # also cleans leads found before this change
    if len(role) > 45:                                       # never end on "and", "of", "&" ...
        words = role[:45].rsplit(" ", 1)[0].split(" ")
        while len(words) > 1 and words[-1].lower() in ("and", "or", "&", "of", "for", "with", "to", "the", "a", "an", "in"):
            words.pop()
        role = " ".join(words)
    return role or "open"


# Skills used in messages (the Leads tab keeps the full list as found):
# named tools first (GoHighLevel, QuickBooks...), then specific tasks, vague words last or dropped.
GENERIC_SKILLS = {"research", "scheduling", "social media", "data entry", "workflows", "calendar management",
                  "inbox management", "dashboards"}
# Role-typical skills when a job post has no usable skills (messages only)
DEFAULT_SKILLS = {
    "CRM Automation": ["CRM setup", "automations", "pipeline management"],
    "SEO": ["on-page SEO", "keyword research", "Search Console"],
    "Performance Marketing": ["Google Ads", "Meta Ads", "conversion tracking"],
    "Customer Service": ["ticketing", "live chat", "phone support"],
    "Bookkeeping": ["QuickBooks", "reconciliations", "month-end close"],
    "Lead Generation": ["prospecting", "lead lists", "appointment setting"],
    "Social Media": ["Instagram", "TikTok", "content calendars"],
    "Content Writing": ["blog posts", "SEO writing", "copywriting"],
    "Email Marketing": ["email campaigns", "automated flows", "list segmentation"],
    "Design & Video": ["Photoshop", "Canva", "video editing"],
    "Website Support": ["WordPress", "site updates", "landing pages"],
    "Virtual Assistant": ["calendar management", "inbox management", "travel booking"],
}


# Words that belong to each field, so a Bookkeeper's hook never says "returns and refunds"
SKILL_FIELD = {
    "CRM Automation": r"crm|hubspot|highlevel|ghl|zapier|\bmake\b|automation|workflow|pipeline|funnel|salesforce|"
                      r"zoho|keap|activecampaign|pipedrive|\bsms\b|email",
    "SEO": r"seo|keyword|backlink|link build|search console|ahrefs|semrush|on-page|technical|content|analytics|\bga4\b",
    "Performance Marketing": r"\bads\b|ppc|google|meta|facebook|tiktok|\bga4\b|analytics|conversion|campaign|"
                             r"bidding|roas|\bsem\b|tracking|media",
    "Customer Service": r"support|ticket|zendesk|freshdesk|gorgias|intercom|chat|phone|returns|refunds|order|"
                        r"customer|crm|help|shopify",
    "Bookkeeping": r"quickbooks|xero|invoic|payable|receivable|reconcil|payroll|month-end|financial|ledger|"
                   r"billing|\btax\b|accounting|sage|netsuite|expense|excel",
    "Lead Generation": r"\blead|prospect|apollo|linkedin|sales navigator|cold|outreach|appointment|crm|\blist|email",
    "Social Media": r"social|instagram|tiktok|facebook|linkedin|content|canva|community|reels|\bugc\b|analytics|"
                    r"copywriting|video",
    "Content Writing": r"writ|copy|blog|seo|content|\bedit|proofread|wordpress|newsletter",
    "Email Marketing": r"email|klaviyo|mailchimp|flows|automation|segmentation|newsletter|copy|campaign|hubspot",
    "Design & Video": r"photoshop|illustrator|figma|canva|after effects|premiere|video|motion|design|brand|\b3d\b|"
                      r"blender|landing page|\bui\b|\bux\b|indesign|editing|animation",
    "Website Support": r"wordpress|shopify|webflow|wix|squarespace|html|css|landing page|\bsite\b|elementor|seo|"
                       r"javascript|php",
    "Virtual Assistant": r"calendar|inbox|email|data entry|scheduling|travel|admin|crm|google workspace|excel",
}


def skill_list(lead):
    """Skills for messages: the role's own field first (tools first), role-typical skills fill any gap,
    vague words last. The Leads tab keeps the full list as found."""
    raw = [x.strip() for x in str(lead.get("skills") or "").split(" · ") if x.strip()]
    cat = lead.get("category")
    field = re.compile(SKILL_FIELD[cat], re.I) if cat in SKILL_FIELD else None
    generic = lambda x: x.lower() in GENERIC_SKILLS
    tools_first = lambda xs: [x for x in xs if any(ch.isupper() for ch in x)] + \
        [x for x in xs if not any(ch.isupper() for ch in x)]
    own = tools_first([x for x in raw if not generic(x) and (field is None or field.search(x))])
    if field is not None and len(own) < 2:                       # top up with role-typical skills, after the post's own
        own += [d for d in DEFAULT_SKILLS.get(cat, []) if d.lower() not in {x.lower() for x in own}]
    if not own:
        own = tools_first([x for x in raw if not generic(x)]) or list(DEFAULT_SKILLS.get(cat, [])) or \
            [x for x in raw if generic(x)]
    return own


def join_skills(skills):
    if not skills:
        return ""
    return skills[0] if len(skills) == 1 else ", ".join(skills[:-1]) + " and " + skills[-1]


def skill_text(lead):
    return join_skills(skill_list(lead)[:3])


def onsite_dm(lead, first):
    """DM after accept for on-site / hybrid posts at tiny companies."""
    first = fix_case(first)
    role, company = msg_role(lead), clean_company(lead["company"])
    sk = join_skills(skill_list(lead)[:3])
    who = f" We work with remote professionals experienced in {sk}." if sk else ""
    return ((f"Thanks for connecting, {first}!" if first else "Thanks for connecting!") +
            f" Saw your {role} opening at {company}. I know it's listed as {str(lead.get('work_type')).lower()}, "
            f"but would you be open to a remote hire for it? There's no upfront fee: you only pay once your hire "
            f"starts.{who}")


def linkedin_messages(lead, first):
    """DM after accept, Follow-up 1, Follow-up 2 (one universal sequence for every role)."""
    first = fix_case(first)
    role, company = msg_role(lead), clean_company(lead["company"])
    skills = skill_list(lead)
    sk3 = join_skills(skills[:3])
    who = (f"We work with remote professionals experienced in {sk3}, and there's no upfront fee: you only pay once "
           f"your hire starts." if sk3 else "We work with remote professionals who do this kind of work every day, and "
           "there's no upfront fee: you only pay once your hire starts.")
    dm = (f"Thanks for connecting, {first}!" if first else "Thanks for connecting!") + \
        (f" Saw your {role} opening at {company}. {who} Would it help to see a couple of profiles that match "
         f"your post?")
    if len(skills) >= 2:
        hard = f"finding someone strong in both {skills[0]} and {skills[1]} is not easy"
    elif skills:
        hard = f"finding someone really strong in {skills[0]} is not easy"
    else:
        hard = f"finding the right {role} is not easy"
    fu1 = (f"{'Hi ' + first if first else 'Hi'}, {hard}, which is why I reached out. At Vogent Labs, we place dedicated remote "
           f"professionals at a much lower cost than a local hire, and if the person isn't the right fit, we replace "
           f"them free. Happy to share a couple of profiles that match your {role} post if useful.")
    fu2 = (f"{'Hey ' + first if first else 'Hi'}, checking back on the {role} opening. If you're still hiring, I can "
           f"send a couple of relevant profiles today, with nothing to pay until your hire starts. Or if it's easier to "
           f"talk it through, you can pick a time here: {CALENDLY_URL}. If you've already filled it, no worries at all!")
    return [dm, fu1, fu2]


def named_note(note, first):
    first = fix_case(first)
    if first and note.startswith("Hi,"):
        candidate = f"Hi {first}," + note[3:]
        if len(candidate) <= 300:                          # LinkedIn Premium limit
            return candidate
    return note


def founder_search_url(company, company_url=""):
    """Opens the company's own LinkedIn People page; a search only if we have no company page."""
    if slug(company_url):
        return "https://www.linkedin.com/company/" + slug(company_url) + "/people/"
    return ("https://www.linkedin.com/search/results/companies/?keywords=" + quote(company))


# ---------- 4. Email: find a real address on the company's own website (free) ----------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD_LOCAL = ["noreply", "no-reply", "donotreply", "privacy", "legal", "abuse", "webmaster", "postmaster",
             "delete", "remove", "request", "help", "dataprotection", "dpo", "optout", "opt-out",
             "clerk", "records", "regulatory", "accessibility", "submit",
             "unsubscribe", "billing", "invoice", "accounts", "example", "test", "user", "email", "name",
             "yourname", "press", "media", "dmca", "security", "compliance", "gdpr",
             "enroll", "admission", "orders", "booking", "reservation", "donate", "donation", "membership",
             "members", "events", "bug", "training", "learning", "course", "academy",
             "shareholder", "investor", "affiliate", "partners"]
BAD_LOCAL_EXACT = {"care", "customercare", "customer.care", "customer-care"}   # exact only, so careers@ still works
BAD_DOMAIN = ["example.com", "domain.com", "sentry", "wixpress", "godaddy", "squarespace", "wordpress",
              "email.com", "yourdomain", "company.com", "mysite", "schema.org", "w3.org"]
HIRING_LOCAL = ["careers", "career", "jobs", "job", "hr", "hiring", "recruit", "talent", "people", "work",
                "applications", "apply"]
GENERAL_LOCAL = ["hello", "hi", "info", "contact", "team", "office", "admin", "sales", "inquiries",
                 "enquiries", "connect", "mail", "general"]
SKIP_LOCAL_WORDS = ["sales", "ventas", "vendor", "intl", "practice", "partner", "support", "service",
                    "billing", "order", "shop", "store", "press", "media", "marketing", "accounts", "invoice"]
LAST_RESORT_LOCAL = ["support", "help", "care", "service", "customerservice", "orders", "shop", "store"]
DEPARTMENT_WORDS = ["marketing", "art", "design", "creative", "studio", "partners", "partnerships",
                    "business", "newbusiness", "growth", "ops", "operations", "finance", "booking",
                    "bookings", "reservations", "events", "membership", "members", "social", "digital",
                    "web", "dev", "tech", "it", "ask", "enquiry", "inquiry", "questions", "feedback",
                    "contactus", "appointments", "reception", "frontdesk", "staff", "media", "pr",
                    "brand", "content", "projects", "studio", "agency", "accounts", "billing"]
PAGES = ["", "contact", "contact-us", "about", "about-us", "team", "our-team", "careers", "jobs"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36"}


def root_domain(url):
    host = re.sub(r"^https?://", "", str(url or "")).split("/")[0].lower()
    host = host.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def find_email(website):
    """Returns (email, kind) where kind is personal / hiring / general, or ("", "")."""
    domain = root_domain(website)
    if not domain:
        return "", ""
    base = "https://" + domain
    found = set()
    for page in PAGES:
        try:
            r = requests.get(f"{base}/{page}", headers=UA, timeout=10, allow_redirects=True)
            if r.status_code != 200:
                continue
            html = r.text.replace("[at]", "@").replace("(at)", "@").replace("&#64;", "@")
            for e in EMAIL_RE.findall(html):
                found.add(e.strip(".").lower())
        except Exception:
            continue
        finally:
            time.sleep(0.5)
        if len(found) >= 5:
            break
    good = []
    for e in found:
        local, _, dom = e.partition("@")
        if any(b in local for b in BAD_LOCAL) or local in BAD_LOCAL_EXACT or any(b in dom for b in BAD_DOMAIN) \
                or re.search(r"staffing|recruit|talent", dom):
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", e) or len(local) > 40:
            continue
        if not (dom.endswith(domain) or domain.endswith(dom)):
            continue                                            # only the company's own domain
        head = re.split(r"[._-]", local)[0]
        if head in ("pr", "press", "media", "news", "foia"):
            continue
        if local in HIRING_LOCAL or any(local.startswith(h) for h in HIRING_LOCAL):
            kind, rank = "hiring", 1
        elif local in GENERAL_LOCAL and local not in ("sales", "admin"):
            kind, rank = "general", 2
        elif any(w in local for w in SKIP_LOCAL_WORDS) or head in DEPARTMENT_WORDS or head in GENERAL_LOCAL:
            continue                                            # sales@, ventas@, vendors@, support@, team.px@ ...
        elif re.fullmatch(r"[a-z]{3,15}(\.[a-z]{2,20})?", local):
            kind, rank = "personal", 0
        else:
            continue
        good.append((rank, e, kind))
    if not good:
        return "", ""
    _, email, kind = sorted(good)[0]
    return email, kind


BLOCKED_DOMAINS = set()                  # bounced or opted-out domains, never emailed again


def email_cap(today):
    return EMAIL_DAILY


def _old_email_cap(today):
    try:
        start = dt.date.fromisoformat(EMAIL_START_DATE)
    except ValueError:
        return 0
    days = (today.date() - start).days
    if days < 0:
        return 0
    return 10 if days < 7 else (20 if days < 14 else 30)


def greeting(email, kind, company, first=""):
    first = fix_case(first)
    if first:
        return f"Hi {first},"
    local = email.split("@")[0]
    if kind == "personal" and re.fullmatch(r"[a-z]{3,15}\.[a-z]{2,20}", local):
        name = local.split(".")[0]                       # only clear first.last addresses, e.g. sarah.lee@
        if name not in DEPARTMENT_WORDS:
            return f"Hi {name.capitalize()},"
    return f"Hi {company} team," if len(company) <= 28 else "Hi there,"


def build_email(lead, email, kind, first=""):
    """One universal sequence for every role: email 1 + 3 follow-ups (sent as replies in the same thread)."""
    company = clean_company(lead["company"])
    role = msg_role(lead)
    skills = skill_text(lead)
    hi = greeting(email, kind, company, first)

    # Subject line test only (bodies are identical). A2 / B2 so older A / B results never mix in.
    variant = "A2" if sum(map(ord, company)) % 2 == 0 else "B2"
    if variant == "A2":
        subject = f"{role} opening at {company}" if len(role) <= 40 and len(company) <= 40 \
            else f"Your open role at {company}"
    else:
        subject = f"Quick question about your {role} hire" if len(role) <= 40 else \
            f"Quick question about your hire at {company}"

    fit = (f"We have candidates with experience in {skills} who could be a good fit for what you're "
           f"looking for, at a much lower cost than a local hire.") if skills else \
        "We have candidates who could be a good fit for what you're looking for, at a much lower cost than a local hire."
    body1 = (f"{hi}\n\nI noticed {company} is hiring for {article(role)} and thought I'd reach out.\n\n"
             f"I run Vogent Labs, where we help businesses hire dedicated remote professionals. {fit}\n\n"
             f"We handle the staffing and payroll on our end, so you can focus on getting the right person "
             f"into your team.\n\nWould you be open to reviewing a couple of profiles?\n\n"
             f"Best,\n{SIGNATURE}")
    with_sk = f" with experience in {skills}" if skills else ""
    # Follow-up 1: the offer (risk-free terms, same as the agency emails)
    fu1 = (f"{hi}\n\nFollowing up on the {role} position. Happy to send over a couple of profiles{with_sk}, "
           f"free to review with no commitment.\n\n"
           f"How it works: there's no upfront fee, you only pay once your hire is onboarded, and if they're not "
           f"the right fit we replace them at no cost.\n\nWant me to send two profiles over?\n\nBest,\n{SIGNATURE}")
    # Follow-up 2: the pain of hiring locally, plus the booking link
    fu2 = (f"{hi}\n\nNot sure if you've filled the {role} role yet. Hiring locally usually means weeks of screening "
           f"and a full local salary. With Vogent Labs you get a dedicated remote professional working your hours, "
           f"we handle staffing and payroll, and you only start paying once they've joined your team.\n\n"
           f"If it's easier to talk it through, you can pick a time here: {CALENDLY_URL}. Or just reply and I'll send "
           f"a couple of profiles.\n\nBest,\n{SIGNATURE}")
    # Follow-up 3: close, with the risk-free reminder
    fu3 = (f"{hi}\n\nI'll leave it here for now. If the {role} position is still open, I'm happy to share a couple "
           f"of candidates, with no upfront fee and nothing to pay until your hire starts. And if you've already "
           f"hired, perhaps we can connect for future openings.\n\nAll the best with the search!\n\n{SIGNATURE}")
    subject = subject[:1].upper() + subject[1:]
    return subject, body1, [fu1, fu2, fu3], variant


# ---------- Main ----------
def main():
    today = dt.datetime.now(PKT)
    if WEEKDAYS_ONLY and today.weekday() >= 5:
        print("Weekend: no outreach queue today.")
        return


    all_leads = load_candidates()
    per_co = {}
    for l in all_leads:
        per_co.setdefault(norm(l.get("company")), set()).add(l.get("title"))
    GROWING.update(k for k, v in per_co.items() if len(v) >= 2)
    leads = [l for l in all_leads if eligible(l)]
    try:
        BLOCKED_DOMAINS.update(json.loads(post({"action": "blocked_domains"})).get("domains", []))
    except Exception:
        pass
    best_per_company = {}
    for l in sorted(leads, key=rank_key):
        best_per_company.setdefault(co_key(l["company"]), l)     # one row per company, best job
    ranked = sorted(best_per_company.values(), key=rank_key)
    print(f"Eligible direct-hire companies today: {len(ranked)}")
    if not ranked:
        post({"action": "outreach_add", "rows": [], "eligible": 0})
        return

    # Skip companies already queued on an earlier day
    # (checks the new company key and the older one, so nobody contacted before slips through)
    check = sorted({k for l in ranked[:250] for k in (co_key(l["company"]), norm(l["company"]))})
    reply = post({"action": "outreach_check", "companies": check})
    try:
        new_keys = set(json.loads(reply).get("new", []))
    except Exception:
        print("Could not check earlier outreach, continuing:", reply[:200])
        new_keys = set(check)
    fresh_ranked = [l for l in ranked if co_key(l["company"]) in new_keys and norm(l["company"]) in new_keys]
    # ----- Upwork first: it gets the Apify budget before LinkedIn founders -----
    up = upwork_leads(today, 0)
    upwork_spent = 0.01 + 0.003 * UPWORK_STATS["checked"] if UPWORK_ENABLED and APIFY_TOKEN else 0

    # A company found on Upwork and on a job site gets one row only (the Upwork row, at the top)
    up_keys = {co_key(u["company"]) for u in (up or [])}
    fresh_ranked = [l for l in fresh_ranked if co_key(l["company"]) not in up_keys]

    # ----- Best leads first, up to 50 a day -----
    # Each company, best first: confirmed size + good email found -> Email (until the sender's daily limit),
    # otherwise -> LinkedIn. The channel never decides who gets in: the ranking does.
    cap = email_cap(today) if EMAIL_ENABLED else 0
    rows, linkedin_n, emailed, found_n, looked = [], 0, 0, 0, 0
    unknown_n = 0
    for l in fresh_ranked:
        if len(rows) >= DAILY_TOTAL:
            break
        if l.get("size_min") is None:
            if unknown_n >= UNKNOWN_SIZE_MAX:
                continue
            unknown_n += 1
        email, kind = "", ""
        if emailed < cap and l.get("size_min") is not None and looked < EMAIL_LOOKUP_MAX and not onsite_ask(l):
            looked += 1
            email, kind = find_email(l.get("website", ""))
            if email and email.split("@")[-1].lower() in BLOCKED_DOMAINS:
                email, kind = "", ""
        p = free_founder(l["company"], l.get("people"))
        found_n += 1 if p else 0
        base = {
            "key": co_key(l["company"]),
            "date": today.strftime("%Y-%m-%d"),
            "rank": len(rows) + 1,
            "region": l["region"],
            "company": l["company"],
            "company_url": l.get("company_url", ""),
            "size": l.get("size", "Unknown"),
            "job_url": l["url"],
            "title": l["title"],
            "work_type": l.get("work_type", ""),
            "category": l.get("category", ""),
            "source": l.get("source", "LinkedIn") + (f" · {l['work_type']}" if onsite_ask(l) else ""),
            "dm_name": p["name"] if p else "Find founder ↗",
            "dm_url": p["url"] if p else founder_search_url(l["company"], l.get("company_url", "")),
            "dm_title": p["title"] if p else "",
        }
        if email:
            first = p["first"] if (p and kind != "personal") else ""
            subject, body1, fups, variant = build_email(l, email, kind, first)
            emailed += 1
            rows.append({**base, "channel": "Email", "email": email,
                         "note": body1,           # email 1
                         "followup": fups[0],     # follow-up 1
                         "followups": fups,       # all 3 follow-ups, sent automatically if there is no reply
                         "messages": [body1] + fups,   # Message 1-4 columns
                         "variant": variant,      # A/B hook test
                         "email_status": "Queued", "subject": subject})
        else:
            first = p["first"] if p else ""
            linkedin_n += 1
            note, li = named_note(l.get("note", ""), first), linkedin_messages(l, first)
            if onsite_ask(l):
                li[0] = onsite_dm(l, first)     # the post says on-site: ask if a remote hire works
            rows.append({**base, "channel": "LinkedIn", "email": "", "note": note, "followup": li[0],
                         "messages": [note] + li, "email_status": "", "_lead": l})

    # ----- Apify founder lookup for LinkedIn rows, with whatever budget Upwork left today -----
    left = APIFY_DAILY_BUDGET_USD - upwork_spent
    missing = [r for r in rows if r["channel"] == "LinkedIn" and r["dm_name"] == "Find founder ↗"
               and slug(r.get("company_url"))]
    if FOUNDER_LOOKUP and missing and left >= 0.06:
        hits = match_people([r["_lead"] for r in missing],
                            apify_people(sorted({r["company_url"] for r in missing}), budget=left))
        for r in missing:
            p = hits.get(r["company"])
            if not p:
                continue
            note, li = named_note(r["_lead"].get("note", ""), p["first"]), linkedin_messages(r["_lead"], p["first"])
            r.update({"dm_name": p["name"], "dm_url": p["url"], "dm_title": p["title"],
                      "note": note, "followup": li[0], "messages": [note] + li})
        print(f"Apify founders: {len(hits)} found with ${left:.2f} left after Upwork")
    for r in rows:
        r.pop("_lead", None)
    found = {r["company"]: 1 for r in rows if r["dm_name"] != "Find founder ↗"}
    print(f"LinkedIn rows: {linkedin_n} | emails queued: {emailed} (limit {cap}) | decision makers found: {len(found)}")

    # ----- Upwork GHL leads with a named client (searched at the start) -----
    up_rows = []
    if up:
        reply = post({"action": "outreach_check", "companies": [norm(u["company"]) for u in up],
                      "recontact_days": UPWORK_RECONTACT_DAYS})
        try:
            new_up = set(json.loads(reply).get("new", []))
        except Exception:
            new_up = {norm(u["company"]) for u in up}
        queued_keys = {r["key"] for r in rows}
        fresh_up = [u for u in up if norm(u["company"]) in new_up and norm(u["company"]) not in queued_keys]
        UPWORK_STATS["new"] = len(fresh_up)
        for u in fresh_up:
            p = free_founder(u["company"])
            first = p["first"] if p else ""
            up_msgs = upwork_messages(u, first) if CRM_UPWORK_TITLE.search(u["title"]) \
                else upwork_role_messages(u, first)
            up_rows.append({
                "key": norm(u["company"]),
                "date": today.strftime("%Y-%m-%d"),
                "rank": len(rows) + 1,
                "channel": "Upwork",
                "region": u["region"],
                "company": u["company"],
                "company_url": u.get("website") or linkedin_company_url(u["company"]),
                "size": u["size"],
                "job_url": u["url"],
                "title": u["title"],
                "work_type": "Remote",
                "dm_name": p["name"] if p else "Find founder ↗",
                "dm_url": p["url"] if p else founder_search_url(u["company"]),
                "dm_title": p["title"] if p else "",
                "email": "",
                "note": up_msgs[0],                     # LinkedIn connection note (Message 1)
                "followup": up_msgs[1],                 # DM after accept (Message 2)
                "messages": up_msgs,                    # note, DM, follow-up 1, follow-up 2 (same as LinkedIn)
                "email_status": "",
                "recontact_days": UPWORK_RECONTACT_DAYS,
            })

    # Upwork GoHighLevel leads always at the top of the tab
    rows = up_rows + rows
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    print("Sheet response:", post({"action": "outreach_add", "rows": rows,
                                   "eligible": len(ranked), "found": len(found),
                                   "emails": emailed,
                                   "upwork": f'{UPWORK_STATS["checked"]} checked · {UPWORK_STATS.get("no_name", 0)} no name · '
                                             f'{UPWORK_STATS.get("not_verified", 0)} unverified · '
                                             f'{UPWORK_STATS.get("not_us", 0)} not US/Canada · '
                                             f'{UPWORK_STATS.get("commission", 0)} commission · '
                                             f'{UPWORK_STATS.get("big", 0)} too big · '
                                             f'{UPWORK_STATS.get("not_ghl", 0)} not GHL · '
                                             f'{UPWORK_STATS.get("wrong_role", 0)} wrong role · '
                                             f'{UPWORK_STATS["named"] - UPWORK_STATS["new"]} already contacted · '
                                             f'{UPWORK_STATS["new"]} new'})[:300])


if __name__ == "__main__":
    main()
