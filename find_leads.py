"""
VOGENT LEAD FINDER (JobSpy version)
Finds fresh jobs on LinkedIn in US / UK / Europe / UAE, reads the FULL
description, keeps only real matches from small direct employers
(no agencies, no companies over 200 staff), marks Real Estate and
Marketing / Advertising companies as Priority, tags Remote / Hybrid /
On-site, and sends everything to your Google Sheet.

Each category + region pair runs on its own GitHub machine in parallel.
You only ever edit the SETTINGS section.
"""
import os
import re
import time

import pandas as pd
import requests
from jobspy import scrape_jobs

# ============================ SETTINGS ============================
SITES = ["linkedin"]          # later add: "indeed", "glassdoor", "zip_recruiter"
HOURS_OLD = 12                # only jobs posted in the last X hours (runs overlap, nothing slips)
MAX_EMPLOYEES = 200           # companies bigger than this are skipped (unknown size is kept)

REGIONS = {                   # switch a region off with False
    "US":     {"enabled": True, "location": "United States"},
    "UK":     {"enabled": True, "location": "United Kingdom"},
    "Europe": {"enabled": True, "location": "European Union"},
    "UAE":    {"enabled": True, "location": "United Arab Emirates"},
}

PRIORITY_INDUSTRY_WORDS = ["real estate", "marketing", "advertising"]
PRIORITY_NAME_WORDS = ["realty", "real estate", "properties", "homes", "realtor",
                       "marketing", "media", "advertising", "agency", "digital"]

CATEGORIES = {
    "GHL / CRM": {
        # Broad CRM / automation titles, kept ONLY if the description mentions GHL
        "enabled": True,
        "deep_results": 100, "normal_results": 40,
        "deep_searches": ["GoHighLevel", "Go High Level", "GHL", "HighLevel"],
        "searches": [
            "CRM Manager", "CRM Specialist", "CRM Administrator", "CRM Coordinator",
            "CRM Automation", "Marketing Automation Specialist", "Automation Specialist",
            "Sales Automation", "Marketing Operations", "Funnel Builder",
            "Email Marketing Specialist", "SMS Marketing", "Zapier",
        ],
        "must_mention": ["go high level", "highlevel", "high level crm", "ghl", "lead connector"],
    },
    "Performance Marketing": {
        "enabled": True,
        "deep_results": 80, "normal_results": 40,
        "deep_searches": ["Performance Marketing Specialist", "Media Buyer", "Paid Media Specialist"],
        "searches": [
            "Paid Ads Manager", "PPC Specialist", "Paid Search Specialist", "SEM Specialist",
            "Google Ads Specialist", "Meta Ads Specialist", "Facebook Ads Specialist",
            "Paid Social Specialist", "TikTok Ads", "Amazon PPC Specialist",
            "Growth Marketing Manager", "Digital Advertising Specialist",
            "Digital Marketing Specialist", "Marketing Agency", "Agency Account Coordinator",
        ],
        "must_mention": [
            "meta ads", "facebook ads", "instagram ads", "google ads", "adwords", "ppc",
            "paid social", "paid media", "paid search", "sem", "media buying",
            "facebook advertising", "tiktok ads", "linkedin ads", "youtube ads",
            "amazon ppc", "performance marketing", "paid acquisition", "roas",
        ],
    },
    "Virtual Assistant": {
        # VA / EA / admin support family, including real estate support roles
        "enabled": True,
        "deep_results": 100, "normal_results": 30,
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
        # CSR / support / customer experience family
        "enabled": True,
        "deep_results": 80, "normal_results": 30,
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


def is_priority(company, industry):
    ind, c = industry.lower(), company.lower()
    return any(w in ind for w in PRIORITY_INDUSTRY_WORDS) or \
        any(w in c for w in PRIORITY_NAME_WORDS)


def company_size(company_url, row):
    """Returns (label, smallest headcount) or ("Size unknown", None)."""
    js = text(row.get("company_num_employees"))
    if js:
        return parse_size(js)
    url = company_url.split("?")[0].rstrip("/")
    if "linkedin.com/company/" not in url:
        return "Size unknown", None
    if url in SIZE_CACHE:
        return SIZE_CACHE[url]
    result = ("Size unknown", None)
    try:
        r = requests.get(url + "/", headers=UA, timeout=20, allow_redirects=True)
        if r.status_code == 200 and "authwall" not in r.url:
            plain = re.sub(r"<[^>]+>", " ", r.text)
            plain = re.sub(r"\s+", " ", plain)
            m = re.search(r"Company size\s*([\d,]+\s*(?:-\s*[\d,]+)?\+?)\s*employees", plain, re.I)
            if m:
                result = parse_size(m.group(1))
    except Exception:
        pass
    SIZE_CACHE[url] = result
    time.sleep(2)
    return result


def parse_size(raw):
    nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", raw) if n.replace(",", "")]
    if not nums:
        return "Size unknown", None
    label = raw.strip()
    if "employee" not in label.lower():
        label += " employees"
    return label, nums[0]


def search(term, n, location):
    try:
        df = scrape_jobs(
            site_name=SITES,
            search_term=term,
            location=location,
            results_wanted=n,
            hours_old=HOURS_OLD,
            linkedin_fetch_description=True,
        )
        print(f"  {term}: {len(df)} jobs")
        return df
    except Exception as e:
        print(f"  {term}: failed ({e})")
        return None
    finally:
        time.sleep(5)


def main():
    found = {}
    checked, excluded_agency, excluded_big = set(), set(), set()
    ran = []

    for region, rcfg in REGIONS.items():
        if not rcfg["enabled"] or (ONLY_REGION and region != ONLY_REGION):
            continue
        for cat_name, cfg in CATEGORIES.items():
            if not cfg["enabled"] or (ONLY_CATEGORY and cat_name != ONLY_CATEGORY):
                continue
            ran.append(f"{cat_name} | {region}")
            print(f"\n== {cat_name} | {region} ==")
            regexes = [build_regex(k) for k in cfg["must_mention"]]
            terms = [(t, cfg["deep_results"]) for t in cfg["deep_searches"]] + \
                    [(t, cfg["normal_results"]) for t in cfg["searches"]]

            for term, n in terms:
                df = search(term, n, rcfg["location"])
                if df is None or not len(df):
                    continue
                for _, row in df.iterrows():
                    url = text(row.get("job_url"))
                    if not url or url in found or url in excluded_agency or url in excluded_big:
                        continue
                    checked.add(url)
                    title = text(row.get("title"))
                    desc = text(row.get("description"))
                    company = text(row.get("company"))
                    industry = text(row.get("company_industry"))

                    matched = [k for k, r in zip(cfg["must_mention"], regexes)
                               if r.search(title + " " + desc)]
                    if not matched:
                        continue
                    if is_agency(company, industry, desc):
                        excluded_agency.add(url)
                        continue

                    company_url = text(row.get("company_url"))
                    size_label, size_min = company_size(company_url, row)
                    if size_min is not None and size_min > MAX_EMPLOYEES:
                        excluded_big.add(url)
                        continue

                    found[url] = {
                        "id": text(row.get("id")) or url,
                        "posted": text(row.get("date_posted")),
                        "priority": "⭐ Priority" if is_priority(company, industry) else "",
                        "category": cat_name,
                        "region": region,
                        "title": title,
                        "role": short_role(title),
                        "company": company,
                        "industry": industry or "Not shown",
                        "size": size_label,
                        "location": text(row.get("location")),
                        "work_type": work_type(row, title, desc),
                        "matched": ", ".join(matched),
                        "evidence": evidence(desc, regexes),
                        "url": url,
                        "company_url": company_url.split("?")[0],
                    }

    if not ran:
        print("This category or region is switched off. Nothing to do.")
        return

    leads = sorted(found.values(), key=lambda l: (
        0 if l["priority"] else 1, WORK_ORDER.get(l["work_type"], 9), l["posted"]))
    print(f"\nChecked {len(checked)} | agencies excluded {len(excluded_agency)} | "
          f"big companies excluded {len(excluded_big)} | matches {len(leads)}")

    r = requests.post(
        WEBAPP_URL,
        json={"token": TOKEN, "category": ", ".join(ran), "leads": leads,
              "checked": len(checked), "excluded": len(excluded_agency),
              "excluded_big": len(excluded_big)},
        timeout=120,
    )
    print("Sheet response:", r.text[:300])


if __name__ == "__main__":
    main()
