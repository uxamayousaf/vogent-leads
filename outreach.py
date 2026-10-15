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
import os
import re
import time
from urllib.parse import quote

import requests

# ============================ SETTINGS ============================
DAILY_LINKEDIN = 20                      # people per day for your LinkedIn account
MAX_COMPANY_SIZE = 200                   # 1 to 200 staff; 50 or fewer rank first, unknown size last
SMALL_COMPANY = 50
WEEKDAYS_ONLY = True                     # Monday to Friday (Pakistan time)
REGION_ORDER = {"USA": 1, "Canada": 2, "UK": 3}
# Outreach roles. CRM (GHL / HubSpot) is always first. The others are re-ordered automatically
# every day by how many small, junior companies posted them in the last 7 days (most first).
FIXED_FIRST_ROLE = "CRM Automation"
DATA_ROLES = ["Customer Service", "Bookkeeping", "Performance Marketing", "SEO", "Lead Generation",
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
                           "municipality", "university", "school district", "college"]
NOT_EMPLOYER_TITLE_WORDS = ["entry level client", "client relations associate", "booking hotels",
                            "travel agent", "brand ambassador", "commission only", "commission-only"]
APIFY_DAILY_BUDGET_USD = 0.24            # both lookup passes together (about $5 a month on weekdays)
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
EMAIL_LOOKUP_MAX = 45                    # companies checked for an email address each day
SENDER_NAME = "Usama Yousaf"
SENDER_TITLE = "Founder, Vogent Labs"
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
WORK_ORDER = {"Remote": 0, "Remote (check)": 1, "Remote (mentioned)": 1, "Not stated": 2, "Hybrid": 3, "On-site": 4}
OUTREACH_WORK_TYPES = ["Remote", "Remote (check)", "Remote (mentioned)", "Not stated"]   # never On-site / Hybrid
EMAIL_DAILY = 30                         # emails per day (USA first, then Canada, then UK)
TITLE_RANK = ["founder", "co-founder", "ceo", "chief executive", "owner", "president",
              "managing director", "principal", "partner", "coo", "general manager"]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


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


def eligible(l):
    if l.get("work_type") not in OUTREACH_WORK_TYPES:
        return False                       # clearly in-office jobs stay in the Leads tab only
    if l.get("region") not in REGION_ORDER:
        return False
    if l.get("category") not in ROLES:
        return False
    if l.get("category") in ROLE_REGIONS and l.get("region") not in ROLE_REGIONS[l["category"]]:
        return False
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
    return all(str(l.get(k) or "").strip() for k in ("company", "title", "url"))


GROWING = set()                          # companies posting 2+ matching roles today


def rank_key(l):
    size_min = l.get("size_min")
    size_bucket = 3 if size_min is None else (0 if size_min <= 10 else (1 if size_min <= SMALL_COMPANY else 2))
    role_rank = ROLE_RANK.get(l.get("category"), 99)
    try:
        newest = -dt.date.fromisoformat(str(l.get("posted"))[:10]).toordinal()
    except ValueError:
        newest = 0
    benefits = 1 if l.get("us_benefits") else 0          # full US payroll benefits = harder to convert
    growing = 0 if norm(l.get("company")) in GROWING else 1
    return (size_bucket, growing, role_rank, WORK_ORDER.get(l.get("work_type"), 9), benefits,
            REGION_ORDER.get(l.get("region"), 9), newest)


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
UPWORK_QUERIES = ["GoHighLevel", "HighLevel", "GHL"]
UPWORK_LIMIT = 40                        # newest jobs checked per day (about $0.12)
UPWORK_COST_CAP_USD = 0.20
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
key what's we're you'll we've it's let's""".split())


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
            if first in NOT_A_NAME or len(name) < 3 or name.lower() in NOT_A_NAME:
                continue
            tokens = [t.lower().strip("'’&.,") for t in name.split()]
            distinctive = [t for t in tokens if t and t not in GENERIC_WORDS]
            if not distinctive:
                continue                                   # e.g. "Very Important We", "There", "Below"
            confirmed = (p == pats[0] or p == pats[2] or
                         len(re.findall(re.escape(name.split(" ")[0]), desc)) >= 2)
            if confirmed:
                return name
    return ""


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
    for it in items:
        desc = str(get_path(it, "data.opening.description", "") or "")
        country = str(get_path(it, "buyer.location.country", "") or "")
        region = "USA" if country in ("USA", "United States") else ("Canada" if country == "Canada" else "")
        size = get_path(it, "buyer.company.profile.size")
        spent = get_path(it, "buyer.stats.totalCharges.amount", 0) or 0
        verified = get_path(it, "buyer.isPaymentMethodVerified", True)
        if not verified or float(spent or 0) < 100:
            continue                                       # only clients who have actually paid freelancers
        name = client_name(desc)
        title = str(it.get("title") or "")
        # Senior / Lead / Manager titles are allowed on Upwork (remote by nature)
        if not (desc and region and name) or commission_heavy(desc):
            continue                                       # named clients only, no commission-heavy posts
        if isinstance(size, (int, float)) and size > MAX_COMPANY_SIZE:
            continue
        if norm(name) in seen:
            continue
        seen.add(norm(name))
        d = desc.lower()
        skills = [n for n, words in GHL_SKILLS if any(w in d for w in words)][:3]
        leads.append({"company": name, "region": region, "size": str(int(size)) if isinstance(size, (int, float)) else "Unknown",
                      "title": str(it.get("title") or "GoHighLevel role"), "url": str(it.get("link") or ""),
                      "skills": skills, "keyword": proposal_keyword(desc), "desc": desc})
    print(f"Upwork: {len(items)} GHL jobs checked, {len(leads)} with a named client")
    UPWORK_STATS.update({"checked": len(items), "named": len(leads)})
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



def follow_up_dm(lead, first):
    role = "assistant" if lead["category"] == "Virtual Assistant" else lead.get("role") or "open"
    who = specialists(lead, SPECIALISTS)
    hi = f"Thanks for connecting, {first}." if first else "Thanks for connecting."
    return (f"{hi} If you are still filling the {role} role, we place dedicated {who} who can start soon at a "
            f"much lower cost than a local hire. Happy to share two profiles that match your post if useful "
            f"or a quick call if it helps: {CALENDLY_URL}")


def named_note(note, first):
    if first and note.startswith("Hi,"):
        candidate = f"Hi {first}," + note[3:]
        if len(candidate) <= 300:
            return candidate
    return note


def founder_search_url(company):
    return ("https://www.linkedin.com/search/results/people/?keywords="
            + quote(f'{company} founder OR CEO OR owner'))


# ---------- 4. Email: find a real address on the company's own website (free) ----------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD_LOCAL = ["noreply", "no-reply", "donotreply", "privacy", "legal", "abuse", "webmaster", "postmaster",
             "delete", "remove", "request", "help", "dataprotection", "dpo", "optout", "opt-out",
             "unsubscribe", "billing", "invoice", "accounts", "example", "test", "user", "email", "name",
             "yourname", "press", "media", "dmca", "security", "compliance", "gdpr"]
BAD_DOMAIN = ["example.com", "domain.com", "sentry", "wixpress", "godaddy", "squarespace", "wordpress",
              "email.com", "yourdomain", "company.com", "mysite", "schema.org", "w3.org"]
HIRING_LOCAL = ["careers", "career", "jobs", "job", "hr", "hiring", "recruit", "talent", "people", "work"]
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
        if any(b in local for b in BAD_LOCAL) or any(b in dom for b in BAD_DOMAIN):
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", e) or len(local) > 40:
            continue
        if not (dom.endswith(domain) or domain.endswith(dom)):
            continue                                            # only the company's own domain
        head = re.split(r"[._-]", local)[0]
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
    if first:
        return f"Hi {first},"
    local = email.split("@")[0]
    if kind == "personal" and re.fullmatch(r"[a-z]{3,15}\.[a-z]{2,20}", local):
        name = local.split(".")[0]                       # only clear first.last addresses, e.g. sarah.lee@
        if name not in DEPARTMENT_WORDS:
            return f"Hi {name.capitalize()},"
    return f"Hi {company} team," if len(company) <= 28 else "Hi there,"


def build_email(lead, email, kind, first=""):
    company = lead["company"]
    role = re.sub(r"virtual assistant", "assistant", lead.get("role") or "open role", flags=re.I)
    role_line = role if lead["category"] != "Virtual Assistant" else "assistant"
    skills = [x for x in str(lead.get("skills") or "").split(" · ") if x]
    who = specialists(lead, EMAIL_SPECIALISTS)
    hi = greeting(email, kind, company, first)
    skill_txt = (", ".join(skills[:-1]) + " and " + skills[-1]) if len(skills) > 1 else (skills[0] if skills else "")

    variant = "A" if sum(map(ord, company)) % 2 == 0 else "B"           # weekly hook test, about 50/50
    if variant == "A":
        subject = f"{role} at {company}" if len(role) <= 40 else f"Your open role at {company}"
    else:
        subject = f"Quick question about your {role_line} hire" if len(role_line) <= 40 else \
            f"Quick question about your hire at {company}"
    line1 = (f"Saw {company} is hiring {article(role_line)}" + (f" to handle {skill_txt}." if skill_txt else "."))
    line2 = f"We have {who} who do exactly this kind of work every day"
    line2 += (" and can join in days, not weeks, at a much lower cost than a local hire. "
              "We handle everything on our side, so it stays hassle free for you.")
    if variant == "A":
        line3 = ("What matters most for this hire? Share the top one or two things and I'll tell you "
                 "within a day if we have someone who fits.")
    else:
        line3 = ("Is this role still open? If it is, I can explain in two lines how we would fill it faster "
                 "and for less.")
    sign = f"{SENDER_NAME}\n{SENDER_TITLE}"          # clean signature: name and title only
    sign_li = f"{sign}\n{LINKEDIN_URL}"
    try:
        warm = (dt.datetime.now(PKT).date() - dt.date.fromisoformat(EMAIL_START_DATE)).days \
            >= LINKEDIN_IN_FIRST_EMAIL_AFTER_DAYS
    except ValueError:
        warm = False
    body1 = f"{hi}\n\n{line1}\n\n{line2}\n\n{line3}\n\n{sign_li if warm else sign}"

    top = skills[0] if skills else "day to day"
    fu1 = (f"{hi}\n\nFollowing up in case this got buried. If the {role_line} role is still open, I can send "
           f"two pre-vetted candidate profiles today so you can compare them with your current applicants. "
           f"No commitment either way.\n\n{sign_li}")
    fu2 = (f"{hi}\n\nA quick note on how this usually works for small teams like {company}: we shortlist "
           f"candidates who already do {top} work, you interview the ones you like, and your pick can start "
           f"within days. We take care of the admin side, so you only manage the work.\n\n"
           f"Would a short call this week be useful? You can pick a time here: {CALENDLY_URL}\n\n{sign_li}")
    fu3 = (f"{hi}\n\nI'll close the loop here so I don't crowd your inbox. If {company} ever needs extra hands "
           f"on {top} or similar work, just reply to this email and I'll send matching profiles the same day.\n\n"
           f"Wishing you a great hire either way.\n\n{sign_li}")
    return subject, body1, [fu1, fu2, fu3], variant


# ---------- Main ----------
def main():
    today = dt.datetime.now(PKT)
    if WEEKDAYS_ONLY and today.weekday() >= 5:
        print("Weekend: no outreach queue today.")
        return

    # Role order: CRM first, the rest by last 7 days of small, junior companies (most first)
    try:
        stats = json.loads(post({"action": "role_stats", "days": 7})).get("counts", {})
        order = sorted(DATA_ROLES, key=lambda r: -int(stats.get(r, 0)))
        ROLE_RANK.clear()
        ROLE_RANK.update({FIXED_FIRST_ROLE: 1, **{r: i + 2 for i, r in enumerate(order)}})
        print("Role order today:", [FIXED_FIRST_ROLE] + order, stats)
    except Exception as e:
        print("Using default role order:", e)

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
        best_per_company.setdefault(norm(l["company"]), l)       # one row per company, best job
    ranked = sorted(best_per_company.values(), key=rank_key)
    print(f"Eligible direct-hire companies today: {len(ranked)}")
    if not ranked:
        post({"action": "outreach_add", "rows": [], "eligible": 0})
        return

    # Skip companies already queued on an earlier day
    reply = post({"action": "outreach_check", "companies": [norm(l["company"]) for l in ranked[:200]]})
    try:
        new_keys = set(json.loads(reply).get("new", []))
    except Exception:
        print("Could not check earlier outreach, continuing:", reply[:200])
        new_keys = {norm(l["company"]) for l in ranked}
    fresh_ranked = [l for l in ranked if norm(l["company"]) in new_keys]
    # ----- Smart split, strict rank order -----
    # Each company, best first: confirmed size + good email found -> Email (until today's email limit),
    # otherwise -> LinkedIn (until 20). So both channels reach the smallest companies first.
    cap = email_cap(today) if EMAIL_ENABLED else 0
    rows, linkedin_n, emailed, found_n, looked = [], 0, 0, 0, 0
    for l in fresh_ranked:
        if linkedin_n >= DAILY_LINKEDIN and emailed >= cap:
            break
        email, kind = "", ""
        if emailed < cap and l.get("size_min") is not None and looked < EMAIL_LOOKUP_MAX:
            looked += 1
            email, kind = find_email(l.get("website", ""))
            if email and email.split("@")[-1].lower() in BLOCKED_DOMAINS:
                email, kind = "", ""
        if not email and linkedin_n >= DAILY_LINKEDIN:
            continue                                      # LinkedIn is full and no email for this one
        p = free_founder(l["company"], l.get("people"))
        found_n += 1 if p else 0
        base = {
            "key": norm(l["company"]),
            "date": today.strftime("%Y-%m-%d"),
            "rank": len(rows) + 1,
            "region": l["region"],
            "company": l["company"],
            "company_url": l.get("company_url", ""),
            "size": l.get("size", "Unknown"),
            "job_url": l["url"],
            "title": l["title"],
            "work_type": l.get("work_type", ""),
            "dm_name": p["name"] if p else "Find founder ↗",
            "dm_url": p["url"] if p else founder_search_url(l["company"]),
            "dm_title": p["title"] if p else "",
        }
        if email:
            first = p["first"] if (p and kind != "personal") else ""
            subject, body1, fups, variant = build_email(l, email, kind, first)
            emailed += 1
            rows.append({**base, "channel": "Email", "email": email,
                         "note": body1,           # email 1 (shown in the Connection Note column)
                         "followup": fups[0],     # follow-up 1 (shown in the Follow-up DM column)
                         "followups": fups,       # all 3 follow-ups, sent automatically if there is no reply
                         "variant": variant,      # A/B hook test
                         "email_status": "Queued", "subject": subject})
        else:
            first = p["first"] if p else ""
            linkedin_n += 1
            rows.append({**base, "channel": "LinkedIn", "email": "",
                         "note": named_note(l.get("note", ""), first),
                         "followup": follow_up_dm(l, first), "email_status": ""})
    found = {r["company"]: 1 for r in rows if r["dm_name"] != "Find founder ↗"}
    print(f"LinkedIn rows: {linkedin_n} | emails queued: {emailed} (limit {cap}) | decision makers found: {len(found)}")

    # ----- Upwork GHL leads with a named client -----
    up = upwork_leads(today, DAILY_LINKEDIN + emailed)
    if up:
        reply = post({"action": "outreach_check", "companies": [norm(u["company"]) for u in up]})
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
            rows.append({
                "key": norm(u["company"]),
                "date": today.strftime("%Y-%m-%d"),
                "rank": len(rows) + 1,
                "channel": "Upwork",
                "region": u["region"],
                "company": u["company"],
                "company_url": linkedin_company_url(u["company"]),
                "size": u["size"],
                "job_url": u["url"],
                "title": u["title"],
                "work_type": "Remote",
                "dm_name": p["name"] if p else "Find founder ↗",
                "dm_url": p["url"] if p else founder_search_url(u["company"]),
                "dm_title": p["title"] if p else "",
                "email": "",
                "note": upwork_note(u, first),          # LinkedIn note (Connection Note column)
                "followup": upwork_proposal(u, first),  # Upwork proposal draft (Follow-up DM column)
                "email_status": "",
            })

    print("Sheet response:", post({"action": "outreach_add", "rows": rows,
                                   "eligible": len(ranked), "found": len(found),
                                   "emails": emailed,
                                   "upwork": f'{UPWORK_STATS["checked"]} checked, {UPWORK_STATS["named"]} named, '
                                             f'{UPWORK_STATS["new"]} new'})[:300])


if __name__ == "__main__":
    main()
