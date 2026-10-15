"""
VOGENT LEAD FINDER (JobSpy version)
Searches LinkedIn Jobs across 5 region groups, once every morning.
Reads the FULL description and keeps only:
  - complete jobs (title, company, date and link all present)
  - real matches for your roles
  - English-language jobs that do not require another language
  - direct employers (no agencies) with 200 staff or fewer (unknown size kept)
Marks GHL jobs and Real Estate / Marketing companies as Priority,
tags Remote / Hybrid / On-site, and sends everything to your Google Sheet.

Each category + region group runs on its own GitHub machine in parallel.
You only ever edit the SETTINGS section.
"""
import json
import os
import re
import time

import pandas as pd
import requests
from jobspy import scrape_jobs
from langdetect import DetectorFactory, detect

DetectorFactory.seed = 0

# ============================ SETTINGS ============================
HOURS_OLD = 48          # one run per day, 48h window catches anything missed yesterday
MAX_EMPLOYEES = 200     # companies bigger than this are skipped (unknown size is kept)

# rank = order in your sheet. scale = fewer results per search for smaller markets.
# Each location: (LinkedIn search location, short name shown in sheet)
REGIONS = {
    "USA / Canada": {"enabled": True, "rank": 1, "scale": 1.0, "locations": [
        ("United States", "USA"), ("Canada", "Canada"),
    ]},
    "UK": {"enabled": True, "rank": 2, "scale": 0.8, "locations": [
        ("United Kingdom", "UK"),
    ]},
}

GHL_TERMS = ["go high level", "highlevel", "high level crm", "ghl", "lead connector"]
REAL_ESTATE_WORDS = ["real estate", "realty", "realtor", "properties", "homes", "property management"]
MARKETING_WORDS = ["marketing", "advertising", "media", "agency"]

CATEGORIES = {
    "CRM Automation": {   # GHL jobs are tagged and shown first
        "enabled": True, "deep_results": 100, "normal_results": 40,
        "deep_searches": ["GoHighLevel", "Go High Level", "GHL", "HighLevel"],
        "searches": [
            "HubSpot Specialist", "HubSpot Administrator", "CRM Automation Specialist",
            "CRM Administrator", "CRM Manager", "CRM Specialist",
            "Marketing Automation Specialist", "Funnel Builder",
        ],
        "must_mention": GHL_TERMS + ["hubspot", "zoho crm", "keap", "activecampaign", "pipedrive"],
    },
    "AI Automation": {
        "enabled": False, "deep_results": 80, "normal_results": 40,
        "deep_searches": ["AI Automation Specialist", "n8n", "Make.com"],
        "searches": [
            "Automation Engineer", "Zapier Expert", "AI Agent Developer",
            "Workflow Automation Specialist", "AI Integration Specialist",
            "No-Code Automation", "AI Automation Engineer",
        ],
        "must_mention": [
            "n8n", "make.com", "integromat", "zapier", "ai agent", "ai agents",
            "workflow automation", "openai api", "chatgpt api", "voiceflow", "relevance ai",
        ],
    },
    "RevOps": {
        "enabled": False, "deep_results": 80, "normal_results": 40,
        "deep_searches": ["Revenue Operations", "RevOps"],
        "searches": [
            "RevOps Manager", "RevOps Specialist", "Revenue Operations Analyst",
            "Sales Operations Specialist", "Marketing Operations Specialist", "GTM Operations",
        ],
        "must_mention": [
            "revenue operations", "revops", "sales operations", "sales ops",
            "marketing operations", "gtm operations",
        ],
    },
    "SEO": {
        # US / UK clients only, so it is searched in those two region groups
        "enabled": True, "deep_results": 80, "normal_results": 40,
        "regions": ["USA / Canada", "UK"],
        "deep_searches": ["SEO Specialist", "SEO Manager"],
        "searches": [
            "SEO Analyst", "SEO Strategist", "Technical SEO", "Local SEO", "SEO Executive",
            "Search Engine Optimization", "Link Building Specialist", "SEO Content Specialist",
        ],
        "must_mention": [
            "seo", "search engine optimization", "on-page", "off-page", "link building",
            "keyword research", "technical seo", "local seo", "google search console",
            "semrush", "ahrefs", "moz",
        ],
    },
    "Bookkeeping": {
        "enabled": True, "deep_results": 80, "normal_results": 30,
        "deep_searches": ["Bookkeeper", "Remote Bookkeeper"],
        "searches": [
            "Bookkeeping Specialist", "Accounting Assistant",
            "Accounts Payable Specialist", "Accounts Receivable Specialist",
        ],
        "must_mention": [
            "bookkeeper", "bookkeeping", "quickbooks", "xero", "reconciliation",
            "reconciliations", "accounts payable", "accounts receivable",
        ],
    },
    "Performance Marketing": {
        "enabled": True, "deep_results": 80, "normal_results": 40,
        "deep_searches": ["Performance Marketing Specialist", "Media Buyer", "Paid Media Specialist"],
        "searches": [
            "Paid Ads Manager", "PPC Specialist", "Paid Search Specialist", "SEM Specialist",
            "Google Ads Specialist", "Meta Ads Specialist", "Facebook Ads Specialist",
            "Paid Social Specialist", "TikTok Ads", "Amazon PPC Specialist",
            "Growth Marketing Manager", "Digital Advertising Specialist",
            "Digital Marketing Specialist", "Agency Account Coordinator",
        ],
        "must_mention": [
            "meta ads", "facebook ads", "instagram ads", "google ads", "adwords", "ppc",
            "paid social", "paid media", "paid search", "sem", "media buying",
            "facebook advertising", "tiktok ads", "linkedin ads", "youtube ads",
            "amazon ppc", "performance marketing", "paid acquisition", "roas",
        ],
    },
    "Virtual Assistant": {
        "enabled": True, "deep_results": 100, "normal_results": 30,
        "deep_searches": ["Virtual Assistant", "Executive Assistant"],
        "searches": [
            "Remote Assistant", "Administrative Assistant", "Admin Assistant",
            "Personal Assistant", "Operations Assistant", "Executive Support",
            "Data Entry Specialist", "Appointment Setter", "Scheduling Coordinator",
            "Real Estate Assistant", "Real Estate Virtual Assistant",
            "Transaction Coordinator", "Inside Sales Agent", "Leasing Assistant",
            "Listing Coordinator",
        ],
        "must_mention": [
            "virtual assistant", "executive assistant", "administrative assistant",
            "admin assistant", "personal assistant", "remote assistant",
            "operations assistant", "executive support", "data entry",
            "appointment setter", "appointment setting", "calendar management",
            "inbox management", "transaction coordinator", "inside sales agent",
            "leasing assistant", "leasing coordinator", "listing coordinator",
        ],
    },
    "Customer Service": {
        "enabled": True, "deep_results": 80, "normal_results": 30,
        "deep_searches": ["Customer Service Representative", "Customer Support Specialist"],
        "searches": [
            "Customer Support Representative", "Customer Care Representative",
            "Customer Experience Specialist", "Customer Success Associate",
            "Client Support Specialist", "Technical Support Representative",
            "Chat Support", "Email Support", "Call Center Representative",
            "Member Services Representative", "Remote Customer Service",
        ],
        "must_mention": [
            "customer service", "customer support", "customer care", "customer experience",
            "customer success", "client support", "client services", "technical support",
            "tech support", "help desk", "live chat", "chat support", "email support",
            "call center", "call centre", "inbound calls", "zendesk", "freshdesk", "intercom",
        ],
    },
}

# ---------- Recruiter / agency filter (add names anytime, lowercase) ----------
AGENCY_BLOCKLIST = [
    "robert half", "insight global", "randstad", "adecco", "teksystems", "kforce",
    "aerotek", "kelly services", "manpower", "express employment", "beacon hill",
    "belay", "boldly", "wing assistant", "prialto", "time etc", "virtual staff finder",
    "hire overseas", "remote staff", "outsource", "upwork", "fiverr",
    "teleperformance", "concentrix", "alorica", "ttec", "sitel", "foundever",
    "hays", "reed", "michael page", "page group",
]
AGENCY_NAME_WORDS = [
    "staffing", "recruit", "talent", "placement", "workforce", "personnel",
    "headhunt", "executive search", "4rec", "recruitment",
]
AGENCY_PHRASES = [
    "our client", "on behalf of our client", "on behalf of a client",
    "for a client of ours", "client of ours",
]

# Extra agency signals, used to keep the OUTREACH list strictly to direct employers
STRICT_AGENCY_PHRASES = [
    "staffing agency", "staffing firm", "staffing company", "staffing partner", "recruitment agency",
    "recruiting agency", "recruiting firm", "recruitment firm", "talent agency", "placement agency",
    "talent acquisition partner", "our client", "client is seeking", "client is looking", "for our client",
    "on behalf of", "contract-to-hire", "contract to hire", "outsourcing company", "bpo",
    "we connect talent", "we place candidates", "hiring on behalf", "virtual assistant agency",
    "remote staffing", "offshore staffing", "we are a staffing", "we are a recruiting",
]
STRICT_INDUSTRY_WORDS = ["staffing", "recruit", "human resources services", "outsourcing", "employment"]

# ---------- Language filter ----------
OTHER_LANGUAGES = [
    "Dutch", "Flemish", "German", "French", "Spanish", "Italian", "Portuguese", "Polish",
    "Swedish", "Danish", "Norwegian", "Finnish", "Czech", "Hungarian", "Romanian", "Greek",
    "Arabic", "Turkish", "Hebrew", "Russian", "Ukrainian", "Mandarin", "Cantonese",
    "Japanese", "Korean", "Malay", "Tagalog",
]
REQUIRE_WORDS = ["fluent", "fluency", "native", "proficient", "proficiency", "required",
                 "must", "mandatory", "essential", "bilingual", "speaking", "speaker"]
PLUS_WORDS = ["plus", "preferred", "nice to have", "bonus", "advantage", "asset",
              "desirable", "beneficial", "helpful", "ideally"]

# ---------- Key skills (shown in the sheet and used in each custom note) ----------
# (name shown, words to look for in the job description)
SKILLS = [
    ("GHL", ["gohighlevel", "go high level", "highlevel", "ghl"]),
    ("HubSpot", ["hubspot"]), ("Salesforce", ["salesforce"]), ("Zoho", ["zoho"]),
    ("Keap", ["keap", "infusionsoft"]), ("ActiveCampaign", ["activecampaign"]),
    ("Pipedrive", ["pipedrive"]), ("Zapier", ["zapier"]), ("Make", ["make.com", "integromat"]),
    ("n8n", ["n8n"]), ("Airtable", ["airtable"]), ("AI agents", ["ai agent", "ai agents"]),
    ("chatbots", ["chatbot", "chatbots", "voiceflow"]), ("voice AI", ["voice agent", "voice ai", "vapi", "retell"]),
    ("OpenAI", ["openai", "chatgpt", "gpt-4", "gpt api"]), ("APIs and webhooks", ["webhook", "webhooks", "api integration", "api integrations"]),
    ("funnels", ["funnel", "funnels"]), ("landing pages", ["landing page", "landing pages"]),
    ("SMS campaigns", ["sms"]), ("email campaigns", ["email campaign", "email campaigns", "email marketing", "drip"]),
    ("workflows", ["workflow", "workflows", "automations"]), ("pipelines", ["pipeline management", "pipelines"]),
    ("lead routing", ["lead routing"]), ("forecasting", ["forecasting", "forecast"]), ("attribution", ["attribution"]),
    ("dashboards", ["dashboards", "reporting dashboards"]),
    ("Meta Ads", ["meta ads", "facebook ads", "instagram ads", "facebook advertising"]),
    ("Google Ads", ["google ads", "adwords", "google search ads"]), ("TikTok Ads", ["tiktok ads"]),
    ("LinkedIn Ads", ["linkedin ads"]), ("PPC", ["ppc", "paid search"]), ("ROAS", ["roas"]),
    ("creative testing", ["creative testing", "a/b testing", "ab testing"]), ("GA4", ["ga4", "google analytics"]),
    ("retargeting", ["retargeting", "remarketing"]), ("Shopify", ["shopify"]),
    ("SEMrush", ["semrush"]), ("Ahrefs", ["ahrefs"]), ("Search Console", ["google search console", "search console"]),
    ("keyword research", ["keyword research"]), ("link building", ["link building", "backlinks"]),
    ("technical SEO", ["technical seo", "site audits", "site audit"]), ("on-page SEO", ["on-page", "on page seo"]),
    ("local SEO", ["local seo", "google business profile", "gmb"]), ("WordPress", ["wordpress"]),
    ("QuickBooks", ["quickbooks", "qbo"]), ("Xero", ["xero"]), ("month-end close", ["month-end", "month end close"]),
    ("reconciliations", ["reconciliation", "reconciliations", "reconcile"]), ("payroll", ["payroll", "gusto"]),
    ("payables", ["accounts payable"]), ("receivables", ["accounts receivable"]), ("invoicing", ["invoicing", "invoices"]),
    ("sales tax", ["sales tax"]), ("financial reports", ["financial statements", "financial reporting", "p&l"]),
    ("calendar management", ["calendar management", "calendar"]), ("inbox management", ["inbox management", "inbox", "email management"]),
    ("scheduling", ["scheduling", "appointment setting", "appointments"]), ("data entry", ["data entry"]),
    ("travel booking", ["travel arrangements", "travel booking"]), ("transaction coordination", ["transaction coordination", "transaction coordinator"]),
    ("CRM updates", ["crm updates", "updating the crm", "crm data"]), ("research", ["research"]),
    ("social media", ["social media"]), ("Google Workspace", ["google workspace", "g suite"]),
    ("phone support", ["phone support", "inbound calls", "answering calls", "phone calls"]),
    ("live chat", ["live chat", "chat support"]), ("email support", ["email support"]),
    ("Zendesk", ["zendesk"]), ("Freshdesk", ["freshdesk"]), ("Intercom", ["intercom"]), ("Gorgias", ["gorgias"]),
    ("ticketing", ["ticketing", "tickets"]), ("returns and refunds", ["returns", "refunds"]),
    ("order support", ["order status", "order issues", "orders"]),
]
SKILL_REGEXES = None  # built on first use

# ---------- Location-locked filter ----------
# Jobs that REQUIRE the person to live in a specific country / place are skipped,
# even if the job is remote ("must be located in the US", "Remote (US only)", "UK residents only").
_PLACES = (r"(?:US|U\.S\.?|USA|U\.S\.A\.?|United States|UK|U\.K\.?|United Kingdom|EU|Europe|Canada|Germany|"
           r"Australia|New Zealand|UAE|Singapore|Ireland|Netherlands|France|Spain|Switzerland|"
           r"Saudi Arabia|Qatar|Malaysia|Hong Kong|South Africa|North America|EST|PST|CST)")
LOCATION_LOCK_PATTERNS = [
    re.compile(r"\b[Mm]ust\s+(?:be\s+)?(?:currently\s+)?(?:located|based|residing|reside|live|living)\s+(?:in|within)\s+(?:the\s+)?[A-Z]"),
    re.compile(r"\b" + _PLACES + r"[\s-]*(?:based\s+)?(?:residents?|citizens?|candidates?|applicants?|persons?)\s+only\b", re.I),
    re.compile(r"\b(?:only|exclusively)\s+(?:open\s+to|accepting|considering|hiring)\s+(?:candidates|applicants|people|those|individuals)"
               r"\s+(?:who\s+(?:are|live)\s+)?(?:located|based|residing|living|in|from)\b", re.I),
    re.compile(r"\(\s*" + _PLACES + r"[\s-]*only\s*\)", re.I),
    re.compile(r"\b(?:remote|work from home|wfh)\W{1,3}" + _PLACES + r"[\s-]*only\b", re.I),
    re.compile(r"\b[Mm]ust\s+(?:be\s+)?(?:a\s+)?(?:US|U\.S\.|UK|Canadian|American|German|Australian)\s+(?:citizen|resident)"),
    re.compile(r"\bcandidates?\s+(?:must|need to|should)\s+(?:be\s+)?(?:located|based|residing)\s+in\b", re.I),
]

ONSITE_PHRASES = [
    "on-site", "onsite", "on site", "in office", "in-office", "in person",
    "in-person", "must be able to commute", "must reside within",
]
# ==================================================================

WEBAPP_URL = os.environ["SHEET_WEBAPP_URL"]
TOKEN = os.environ["SHEET_TOKEN"]
ONLY_CATEGORY = os.environ.get("CATEGORY", "").strip()
ONLY_REGION = os.environ.get("REGION", "").strip()
WORK_ORDER = {"Remote": 0, "Remote (mentioned)": 1, "Hybrid": 2, "Not stated": 3, "On-site": 4}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36"}
SIZE_CACHE = {}


def build_regex(keyword):
    parts = [re.escape(p) for p in re.split(r"[\s-]+", keyword.strip()) if p]
    return re.compile(r"\b" + r"[\s-]*".join(parts) + r"\b", re.IGNORECASE)


GHL_REGEXES = [build_regex(k) for k in GHL_TERMS]
LANG_RE = re.compile(r"\b(" + "|".join(OTHER_LANGUAGES) + r")\b")  # case-sensitive on purpose


def text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def short_role(title):
    clean = re.sub(r"^[^\w]+", "", title)                       # drop leading emojis/symbols
    clean = re.sub(r"^(?:remote|hybrid|on-?site)\s*[|:\-–—]\s*", "", clean, flags=re.I)  # "Remote | ..."
    clean = re.split(r"\s+@\s+|\s+at\s+[A-Z]", clean)[0]          # "... @ Company" / "... at Company"
    role = re.split(r"\s+[-|–—/]\s+|\s*\||\(|\[|,|:", clean)[0].strip()
    return role or clean


def evidence(desc, regexes):
    for r in regexes:
        m = r.search(desc)
        if m:
            snippet = desc[max(0, m.start() - 80): m.start() + 120]
            return "..." + re.sub(r"\s+", " ", snippet) + "..."
    return "(matched in title)"


def is_agency(company, industry, desc):
    c, ind, d = company.lower(), industry.lower(), desc.lower()
    if "staffing" in ind or "recruit" in ind:
        return True
    if any(re.search(r"\b" + re.escape(b) + r"\b", c) for b in AGENCY_BLOCKLIST):
        return True
    if any(w in c for w in AGENCY_NAME_WORDS):
        return True
    return any(p in d for p in AGENCY_PHRASES)


def is_english(desc):
    if len(desc) < 80:
        return True  # too short to judge, keep it
    try:
        return detect(desc[:3000]) == "en"
    except Exception:
        return True


def needs_other_language(desc):
    for sentence in re.split(r"(?<=[.!?\n])\s+", desc):
        langs = [m for m in LANG_RE.finditer(sentence.strip())
                 if not (m.group(1) == "Polish" and m.start() == 0)]  # "Polish your..." is a verb
        if not langs:
            continue
        low = sentence.lower()
        if any(w in low for w in REQUIRE_WORDS) and not any(w in low for w in PLUS_WORDS):
            return True
    return False


def strict_agency(company, industry, desc):
    d, ind = desc.lower(), industry.lower()
    return (is_agency(company, industry, desc) or any(w in ind for w in STRICT_INDUSTRY_WORDS)
            or any(p in d for p in STRICT_AGENCY_PHRASES))


# Senior roles are skipped everywhere (small companies rarely outsource them)
SENIOR_RE = re.compile(r"\b(senior|sr|lead|head|director|vp|vice president|chief|principal|staff|manager)\b", re.I)


def is_senior(title):
    return bool(SENIOR_RE.search(title))


# Jobs that can't be done remotely, and roles we don't staff
NOT_REMOTE_TITLE_RE = re.compile(r"\b(retail|in[- ]store|store|cashier|receptionist|front desk|barista|"
                                 r"warehouse|driver|server|host|hostess)\b", re.I)
OFF_TARGET_TITLE_RE = re.compile(r"\b(account executive|engineer|strategist|sales development|sdr|bdr|"
                                 r"sales representative|sales rep|account manager)\b", re.I)
# The CRM category only counts real CRM jobs (not sales roles that just mention HubSpot)
CRM_TITLE_RE = re.compile(r"crm|hubspot|highlevel|high level|\bghl\b|automation|funnel|email marketing|"
                          r"marketing operations|marketing ops", re.I)


def wrong_title(title, category):
    if NOT_REMOTE_TITLE_RE.search(title) or OFF_TARGET_TITLE_RE.search(title):
        return True
    return category == "CRM Automation" and not CRM_TITLE_RE.search(title)


def title_not_english(title):
    """Catches titles like 'Intégrateur(trice) web' or 'German and English Speaking'."""
    if needs_other_language(title) or LANG_RE.search(title):
        return True
    words = re.findall(r"[^\W\d_]+", title)
    if len(words) >= 3 and re.search(r"[À-ÿ]", title):
        try:
            return detect(title) != "en"
        except Exception:
            return False
    return False


def location_locked(title, location, desc):
    blob = title + " | " + location + " | " + desc
    return any(p.search(blob) for p in LOCATION_LOCK_PATTERNS)


REMOTE_WORDS = ["remote", "work from home", "work-from-home", "wfh", "work from anywhere",
                "fully distributed", "distributed team", "telecommute", "telecommuting"]


def work_type(row, title, desc):
    head = (text(row.get("location")) + " " + title).lower()
    d = desc.lower()
    if str(row.get("is_remote")).lower() == "true" or any(w in head for w in REMOTE_WORDS):
        return "Remote"
    if "hybrid" in head or "hybrid" in d:
        return "Hybrid"
    if any(p in d for p in ONSITE_PHRASES):
        return "On-site"
    if any(w in d for w in REMOTE_WORDS):
        return "Remote (mentioned)"
    return "Not stated"


def key_skills(title, desc, limit=3):
    """Finds up to 3 tools / tasks named in the job, in the order the post mentions them.
    Skips anything already in the job title so the note does not repeat itself."""
    global SKILL_REGEXES
    if SKILL_REGEXES is None:
        SKILL_REGEXES = [(name, [build_regex(w) for w in words]) for name, words in SKILLS]
    source = desc or title
    hits = []
    for name, regs in SKILL_REGEXES:
        if any(r.search(title) for r in regs):
            continue
        pos = min((m.start() for r in regs for m in [r.search(source)] if m), default=None)
        if pos is not None:
            hits.append((pos, name))
    return [n for _, n in sorted(hits)][:limit]


def join_words(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def connection_note(company, role, category, skills, work):
    """Custom note per lead, under LinkedIn's 300 character limit. No pitch, ends with a question.
    On-site / Hybrid jobs get the "hiring locally or remote" question; remote jobs get the other styles."""
    if category == "Virtual Assistant":
        role = "assistant"           # keep "VA" wording out of outreach copy
    role = role if len(role) <= 45 else role[:45].rsplit(" ", 1)[0]
    first_word = role.split(" ")[0] if role else ""
    if first_word.isupper() and len(first_word) > 1:          # acronyms: "an SEO", "a CRM"
        a_role = ("an " if first_word[0] in "AEFHILMNORSX" else "a ") + role
    else:
        a_role = ("an " if role[:1].lower() in "aeiou" else "a ") + role
    local_angle = work in ("On-site", "Hybrid")
    for n in (3, 2, 1, 0):
        sk = skills[:n]
        if sk:
            s_txt = join_words(sk)
            if local_angle:
                note = (f"Hi, came across the {role} opening at {company}. Strong {s_txt} experience "
                        f"{'is a tough mix' if len(sk) > 1 else 'is hard'} to find. Are you set on hiring locally, "
                        f"or open to remote talent? Happy to connect.")
            else:
                options = [
                    f"Hi, saw {company} is hiring {a_role} to handle {s_txt}. That usually means a busy stretch "
                    f"for the team. Still looking, or close to filling it? Would be good to connect.",
                    f"Hi, noticed {company} needs someone strong on {s_txt}. How is the search for your {role} "
                    f"going so far? Would be glad to connect.",
                ]
                note = options[sum(map(ord, company + role)) % 2]   # same lead always gets the same style
        elif local_angle:
            note = (f"Hi, saw {company} is hiring {a_role}. Are you set on hiring locally, or open to remote "
                    f"talent? Happy to connect.")
        else:
            note = (f"Hi, saw {company} is hiring {a_role}. Are you still looking, or close to filling it? "
                    f"Would be good to connect.")
        if len(note) <= 300:
            return note
    return note[:297].rsplit(" ", 1)[0] + "..."


def priority(title, desc, company, industry):
    """Returns (label, rank). Rank 0 = GHL, 1 = other priority, 2 = none."""
    tags = []
    if any(r.search(title + " " + desc) for r in GHL_REGEXES):
        tags.append("GHL")
    ind, c = industry.lower(), company.lower()
    if any(w in ind or w in c for w in REAL_ESTATE_WORDS):
        tags.append("Real Estate")
    elif any(w in ind or w in c for w in MARKETING_WORDS):
        tags.append("Marketing")
    if not tags:
        return "", 2
    return "⭐ " + " · ".join(tags), (0 if "GHL" in tags else 1)


def parse_size(raw):
    nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", raw) if n.replace(",", "")]
    if not nums:
        return "Unknown", None
    label = re.sub(r"\s*employees?", "", raw.strip(), flags=re.I)
    return label, nums[0]


PEOPLE_CACHE = {}


def company_people(html):
    """People shown on the public company page: [(name, profile url, title)].
    Each person card on LinkedIn's public page is a list item with a profile link, a name and a title."""
    people = []
    for card in re.split(r"<li\b", html):
        url = re.search(r'href="(https://[a-z]{2,3}\.linkedin\.com/in/[^"?]+)', card)
        name = re.search(r'base-main-card__title[^>]*>\s*([^<]+?)\s*<', card) or \
            re.search(r'<h3[^>]*>\s*([^<]{3,60}?)\s*</h3>', card)
        title = re.search(r'base-main-card__subtitle[^>]*>\s*([^<]+?)\s*<', card) or \
            re.search(r'<h4[^>]*>\s*([^<]{2,120}?)\s*</h4>', card)
        if url and name:
            people.append((re.sub(r"\s+", " ", name.group(1)).strip(), url.group(1),
                           re.sub(r"\s+", " ", title.group(1)).strip() if title else ""))
    return people[:15]


def company_size(company_url, row):
    """Returns (size label, smallest headcount or None, industry, website) from the company page."""
    url = company_url.split("?")[0].rstrip("/")
    if "linkedin.com/company/" not in url:
        js = text(row.get("company_num_employees"))
        label, mn = parse_size(js) if js else ("Unknown", None)
        return label, mn, "", ""
    if url in SIZE_CACHE:
        return SIZE_CACHE[url]
    result = ("Unknown", None, "", "")
    try:
        r = requests.get(url + "/", headers=UA, timeout=20, allow_redirects=True)
        if r.status_code == 200 and "authwall" not in r.url:
            plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
            m = re.search(r"Company size\s*([\d,]+\s*(?:-\s*[\d,]+)?\+?)\s*employees", plain, re.I)
            ind = re.search(r"Industry\s+(.{3,60}?)\s+(?:Company size|Headquarters|Type|Founded)", plain)
            label, mn = parse_size(m.group(1)) if m else ("Unknown", None)
            result = (label, mn, ind.group(1).strip() if ind else "", company_website(r.text))
            PEOPLE_CACHE[url] = company_people(r.text)
    except Exception:
        pass
    SIZE_CACHE[url] = result
    time.sleep(2)
    return result


US_BENEFIT_WORDS = ["401(k)", "401k", "dental", "vision insurance", "health insurance",
                    "medical insurance", "medical, dental", "paid time off", "pto", "hsa", "fsa"]
WT_LABELS = {"1": "On-site", "3": "Hybrid", "2": "Remote"}    # LinkedIn's workplace badge filters


def us_benefits(desc):
    d = desc.lower()
    return sum(1 for w in US_BENEFIT_WORDS if w in d) >= 2       # two or more = full US benefits package


def badge_map(term, location, pages=3):
    """Reads LinkedIn's own On-site / Hybrid / Remote badges from the public search pages.
    Returns {job_id: badge}. On-site and Hybrid always win over Remote."""
    badges = {}
    for wt in ("2", "3", "1"):                                   # Remote first, so On-site/Hybrid overwrite it
        for page in range(pages):
            try:
                r = requests.get(
                    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                    params={"keywords": term, "location": location, "f_TPR": f"r{HOURS_OLD * 3600}",
                            "f_WT": wt, "start": page * 10},
                    headers=UA, timeout=20)
                if r.status_code != 200:
                    break
                ids = re.findall(r"jobPosting:(\d+)", r.text)
                if not ids:
                    break
                for job_id in ids:
                    badges[job_id] = WT_LABELS[wt]
            except Exception:
                break
            finally:
                time.sleep(1.5)
    return badges


def job_id_of(row):
    m = re.search(r"/jobs/view/(\d+)", text(row.get("job_url"))) or re.search(r"(\d{6,})", text(row.get("id")))
    return m.group(1) if m else ""


def company_website(html):
    """Finds the company's own website link on its public LinkedIn page."""
    from urllib.parse import parse_qs, unquote, urlparse
    for tag in re.findall(r"<a[^>]+>", html):
        if "about_website" not in tag and "website" not in tag.lower():
            continue
        href = re.search(r'href="([^"]+)"', tag)
        if not href:
            continue
        link = href.group(1).replace("&amp;", "&")
        if "linkedin.com/redir" in link:
            link = unquote(parse_qs(urlparse(link).query).get("url", [""])[0])
        if link.startswith("http") and "linkedin.com" not in link:
            return link
    return ""


def search(term, n, location, remote=False):
    try:
        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=term,
            location=location,
            results_wanted=n,
            hours_old=HOURS_OLD,
            is_remote=remote,              # True = LinkedIn's own "Remote" filter
            linkedin_fetch_description=True,
        )
        print(f"  {term} | {location}{' | remote filter' if remote else ''}: {len(df)} jobs")
        return df
    except Exception as e:
        print(f"  {term} | {location}: failed ({e})")
        return None
    finally:
        time.sleep(4)


def main():
    found = {}
    checked, ex_agency, ex_big, ex_lang, ex_incomplete, ex_loc = set(), set(), set(), set(), set(), set()
    companies_today = set()
    ran = []

    for region_name, rcfg in REGIONS.items():
        if not rcfg["enabled"] or (ONLY_REGION and region_name != ONLY_REGION):
            continue
        for cat_name, cfg in CATEGORIES.items():
            if not cfg["enabled"] or (ONLY_CATEGORY and cat_name != ONLY_CATEGORY):
                continue
            if cfg.get("regions") and region_name not in cfg["regions"]:
                continue
            ran.append(f"{cat_name} | {region_name}")
            print(f"\n== {cat_name} | {region_name} ==")
            regexes = [build_regex(k) for k in cfg["must_mention"]]
            deep_n = max(10, int(cfg["deep_results"] * rcfg["scale"]))
            normal_n = max(10, int(cfg["normal_results"] * rcfg["scale"]))
            terms = [(t, deep_n) for t in cfg["deep_searches"] if not is_senior(t)] + \
                    [(t, normal_n) for t in cfg["searches"] if not is_senior(t)]   # no "Manager" searches

            for location, short in rcfg["locations"]:
                for term, n in terms:
                    badges = badge_map(term, location)
                    df = search(term, n, location)
                    if df is None or not len(df):
                        continue
                    for _, row in df.iterrows():
                        url = text(row.get("job_url"))
                        title = text(row.get("title")).strip()
                        company = text(row.get("company")).strip()
                        posted = text(row.get("date_posted")).strip()
                        # Half-loaded LinkedIn cards are skipped, never added
                        if not (url and title and company and posted):
                            ex_incomplete.add(url or str(len(ex_incomplete)))
                            continue
                        # Same job seen twice counts once (company + title)
                        key = re.sub(r"\W+", "", company.lower()) + "|" + re.sub(r"\W+", "", title.lower())
                        if key in found or key in ex_agency or key in ex_big or key in ex_lang or key in ex_loc:
                            continue
                        checked.add(key)
                        desc = text(row.get("description"))
                        industry = text(row.get("company_industry"))

                        if is_senior(title) or wrong_title(title, cat_name):
                            continue                                     # senior, in-store, off-target roles
                        if not any(r.search(title + " " + desc) for r in regexes):
                            continue
                        co_key = re.sub(r"\W+", "", company.lower())
                        if co_key in companies_today:
                            continue                                     # one row per company per day
                        if title_not_english(title) or not is_english(desc) or needs_other_language(desc):
                            ex_lang.add(key)
                            continue
                        if location_locked(title, text(row.get("location")), desc):
                            ex_loc.add(key)
                            continue
                        if is_agency(company, industry, desc):
                            ex_agency.add(key)
                            continue
                        company_url = text(row.get("company_url")).split("?")[0]
                        size_label, size_min, page_industry, website = company_size(company_url, row)
                        if size_min is not None and size_min > MAX_EMPLOYEES:
                            ex_big.add(key)
                            continue

                        p_label, p_rank = priority(title, desc, company, industry)
                        skills = key_skills(title, desc)
                        role = short_role(title)
                        # Work type: clear wording in the post first, then LinkedIn's own badge
                        wtype = work_type(row, title, desc)
                        badge = badges.get(job_id_of(row), "")
                        if badge and wtype in ("Not stated", "Remote (mentioned)"):
                            wtype = badge
                        elif badge in ("On-site", "Hybrid") and wtype == "Remote" and "remote" not in title.lower():
                            wtype = badge                                # badge beats a loose "remote" mention
                        companies_today.add(co_key)
                        found[key] = {
                            "id": key,
                            "posted": posted,
                            "priority": p_label,
                            "priority_rank": p_rank,
                            "category": cat_name,
                            "region": short,
                            "region_rank": rcfg["rank"],
                            "title": title,
                            "role": role,
                            "skills": " · ".join(skills),
                            "note": connection_note(company, role, cat_name, skills, wtype),
                            "company": company,
                            "size": size_label,
                            "work_type": wtype,
                            "evidence": evidence(desc, regexes),
                            "url": url,
                            "company_url": company_url,
                            # extra details for Phase 2 outreach (not shown in the Leads tab)
                            "size_min": size_min,
                            "industry": page_industry or industry,
                            "direct": not strict_agency(company, page_industry or industry, desc),
                            "us_benefits": us_benefits(desc),
                            "website": website,
                            "people": PEOPLE_CACHE.get(company_url.rstrip("/"), []),
                        }

    if not ran:
        print("This category or region is switched off. Nothing to do.")
        return

    leads = sorted(found.values(), key=lambda l: (
        l["priority_rank"], l["region_rank"], WORK_ORDER.get(l["work_type"], 9), l["posted"]))
    print(f"\nChecked {len(checked)} | agencies {len(ex_agency)} | big {len(ex_big)} | "
          f"non-English {len(ex_lang)} | location-locked {len(ex_loc)} | incomplete {len(ex_incomplete)} | "
          f"matches {len(leads)}")

    # Hand today's leads to the Phase 2 outreach step (GitHub passes this file along)
    os.makedirs("candidates", exist_ok=True)
    fname = re.sub(r"\W+", "_", (ONLY_CATEGORY or "all") + "_" + (ONLY_REGION or "all")) + ".json"
    with open(os.path.join("candidates", fname), "w") as fh:
        json.dump(leads, fh)

    r = requests.post(
        WEBAPP_URL,
        json={"token": TOKEN, "category": ", ".join(ran), "leads": leads,
              "checked": len(checked), "excluded": len(ex_agency),
              "excluded_big": len(ex_big), "excluded_lang": len(ex_lang),
              "excluded_incomplete": len(ex_incomplete), "excluded_location": len(ex_loc)},
        timeout=180,
    )
    print("Sheet response:", r.text[:300])


if __name__ == "__main__":
    main()
