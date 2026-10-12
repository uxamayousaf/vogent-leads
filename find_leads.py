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
    "UK / Europe / EMEA": {"enabled": True, "rank": 2, "scale": 0.6, "locations": [
        ("United Kingdom", "UK"), ("European Union", "Europe"),
        ("Switzerland", "Switzerland"), ("South Africa", "South Africa"),
    ]},
    "Gulf": {"enabled": True, "rank": 3, "scale": 0.5, "locations": [
        ("United Arab Emirates", "UAE"), ("Saudi Arabia", "Saudi Arabia"), ("Qatar", "Qatar"),
        ("Kuwait", "Kuwait"), ("Bahrain", "Bahrain"), ("Oman", "Oman"),
    ]},
    "Australia / NZ": {"enabled": True, "rank": 4, "scale": 0.6, "locations": [
        ("Australia", "Australia"), ("New Zealand", "New Zealand"),
    ]},
    "Asia (English)": {"enabled": True, "rank": 5, "scale": 0.5, "locations": [
        ("Singapore", "Singapore"), ("Malaysia", "Malaysia"), ("Hong Kong", "Hong Kong"),
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
        "enabled": True, "deep_results": 80, "normal_results": 40,
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
        "enabled": True, "deep_results": 80, "normal_results": 40,
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
    "headhunt", "executive search",
]
AGENCY_PHRASES = [
    "our client", "on behalf of our client", "on behalf of a client",
    "for a client of ours", "client of ours",
]

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
    clean = re.sub(r"^[^\w]+", "", title)  # drop leading emojis/symbols
    role = re.split(r"\s+[-|–—/]\s+|\(|\[|,|:", clean)[0].strip()
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


def work_type(row, title, desc):
    head = (text(row.get("location")) + " " + title).lower()
    d = desc.lower()
    if str(row.get("is_remote")).lower() == "true" or "remote" in head:
        return "Remote"
    if "hybrid" in head or "hybrid" in d:
        return "Hybrid"
    if any(p in d for p in ONSITE_PHRASES):
        return "On-site"
    if "remote" in d:
        return "Remote (mentioned)"
    return "Not stated"


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


def company_size(company_url, row):
    """Returns (label, smallest headcount) or ("Unknown", None)."""
    js = text(row.get("company_num_employees"))
    if js:
        return parse_size(js)
    url = company_url.split("?")[0].rstrip("/")
    if "linkedin.com/company/" not in url:
        return "Unknown", None
    if url in SIZE_CACHE:
        return SIZE_CACHE[url]
    result = ("Unknown", None)
    try:
        r = requests.get(url + "/", headers=UA, timeout=20, allow_redirects=True)
        if r.status_code == 200 and "authwall" not in r.url:
            plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
            m = re.search(r"Company size\s*([\d,]+\s*(?:-\s*[\d,]+)?\+?)\s*employees", plain, re.I)
            if m:
                result = parse_size(m.group(1))
    except Exception:
        pass
    SIZE_CACHE[url] = result
    time.sleep(2)
    return result


def search(term, n, location):
    try:
        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=term,
            location=location,
            results_wanted=n,
            hours_old=HOURS_OLD,
            linkedin_fetch_description=True,
        )
        print(f"  {term} | {location}: {len(df)} jobs")
        return df
    except Exception as e:
        print(f"  {term} | {location}: failed ({e})")
        return None
    finally:
        time.sleep(4)


def main():
    found = {}
    checked, ex_agency, ex_big, ex_lang, ex_incomplete = set(), set(), set(), set(), set()
    ran = []

    for region_name, rcfg in REGIONS.items():
        if not rcfg["enabled"] or (ONLY_REGION and region_name != ONLY_REGION):
            continue
        for cat_name, cfg in CATEGORIES.items():
            if not cfg["enabled"] or (ONLY_CATEGORY and cat_name != ONLY_CATEGORY):
                continue
            ran.append(f"{cat_name} | {region_name}")
            print(f"\n== {cat_name} | {region_name} ==")
            regexes = [build_regex(k) for k in cfg["must_mention"]]
            deep_n = max(10, int(cfg["deep_results"] * rcfg["scale"]))
            normal_n = max(10, int(cfg["normal_results"] * rcfg["scale"]))
            terms = [(t, deep_n) for t in cfg["deep_searches"]] + \
                    [(t, normal_n) for t in cfg["searches"]]

            for location, short in rcfg["locations"]:
                for term, n in terms:
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
                        if key in found or key in ex_agency or key in ex_big or key in ex_lang:
                            continue
                        checked.add(key)
                        desc = text(row.get("description"))
                        industry = text(row.get("company_industry"))

                        if not any(r.search(title + " " + desc) for r in regexes):
                            continue
                        if not is_english(desc) or needs_other_language(desc):
                            ex_lang.add(key)
                            continue
                        if is_agency(company, industry, desc):
                            ex_agency.add(key)
                            continue
                        company_url = text(row.get("company_url")).split("?")[0]
                        size_label, size_min = company_size(company_url, row)
                        if size_min is not None and size_min > MAX_EMPLOYEES:
                            ex_big.add(key)
                            continue

                        p_label, p_rank = priority(title, desc, company, industry)
                        found[key] = {
                            "id": key,
                            "posted": posted,
                            "priority": p_label,
                            "priority_rank": p_rank,
                            "category": cat_name,
                            "region": short,
                            "region_rank": rcfg["rank"],
                            "title": title,
                            "role": short_role(title),
                            "company": company,
                            "size": size_label,
                            "work_type": work_type(row, title, desc),
                            "evidence": evidence(desc, regexes),
                            "url": url,
                            "company_url": company_url,
                        }

    if not ran:
        print("This category or region is switched off. Nothing to do.")
        return

    leads = sorted(found.values(), key=lambda l: (
        l["priority_rank"], l["region_rank"], WORK_ORDER.get(l["work_type"], 9), l["posted"]))
    print(f"\nChecked {len(checked)} | agencies {len(ex_agency)} | big {len(ex_big)} | "
          f"non-English {len(ex_lang)} | incomplete {len(ex_incomplete)} | matches {len(leads)}")

    r = requests.post(
        WEBAPP_URL,
        json={"token": TOKEN, "category": ", ".join(ran), "leads": leads,
              "checked": len(checked), "excluded": len(ex_agency),
              "excluded_big": len(ex_big), "excluded_lang": len(ex_lang),
              "excluded_incomplete": len(ex_incomplete)},
        timeout=180,
    )
    print("Sheet response:", r.text[:300])


if __name__ == "__main__":
    main()
