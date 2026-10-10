"""
VOGENT LEAD FINDER (JobSpy version)
Finds fresh US jobs on LinkedIn, reads the FULL description, keeps only
real matches from direct employers (no recruiting agencies), tags each
job as Remote / Hybrid / On-site, and sends them to your Google Sheet.

Each category runs on its own GitHub machine at the same time (faster,
and less likely to be blocked by LinkedIn).

You only ever edit the SETTINGS section.
To switch a category on or off, change "enabled": True / False.
"""
import os
import re
import time

import pandas as pd
import requests
from jobspy import scrape_jobs

# ============================ SETTINGS ============================
SITES = ["linkedin"]          # later add: "indeed", "glassdoor", "zip_recruiter"
LOCATION = "United States"
HOURS_OLD = 12                # only jobs posted in the last X hours (runs overlap, nothing slips)

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
            "Growth Marketing Manager", "Digital Advertising Specialist", "Digital Marketing Specialist",
        ],
        "must_mention": [
            "meta ads", "facebook ads", "instagram ads", "google ads", "adwords", "ppc",
            "paid social", "paid media", "paid search", "sem", "media buying",
            "facebook advertising", "tiktok ads", "linkedin ads", "youtube ads",
            "amazon ppc", "performance marketing", "paid acquisition", "roas",
        ],
    },
    "Virtual Assistant": {
        # VA / EA / admin support family
        "enabled": True,
        "deep_results": 100, "normal_results": 30,
        "deep_searches": ["Virtual Assistant", "Executive Assistant"],
        "searches": [
            "Remote Assistant", "Administrative Assistant", "Admin Assistant",
            "Personal Assistant", "Operations Assistant", "Executive Support",
            "Data Entry Specialist", "Appointment Setter", "Scheduling Coordinator",
            "Real Estate Assistant", "Transaction Coordinator",
        ],
        "must_mention": [
            "virtual assistant", "executive assistant", "administrative assistant",
            "admin assistant", "personal assistant", "remote assistant",
            "operations assistant", "executive support", "data entry",
            "appointment setter", "appointment setting", "calendar management",
            "inbox management", "transaction coordinator",
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
WORK_ORDER = {"Remote": 0, "Remote (mentioned)": 1, "Hybrid": 2, "Not stated": 3, "On-site": 4}


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
    if any(b in c for b in AGENCY_BLOCKLIST):
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


def search(term, n):
    try:
        df = scrape_jobs(
            site_name=SITES,
            search_term=term,
            location=LOCATION,
            results_wanted=n,
            hours_old=HOURS_OLD,
            linkedin_fetch_description=True,
            country_indeed="USA",
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
    checked, excluded = set(), set()
    ran = []

    for cat_name, cfg in CATEGORIES.items():
        if not cfg["enabled"] or (ONLY_CATEGORY and cat_name != ONLY_CATEGORY):
            continue
        ran.append(cat_name)
        print(f"\n== {cat_name} ==")
        regexes = [build_regex(k) for k in cfg["must_mention"]]
        terms = [(t, cfg["deep_results"]) for t in cfg["deep_searches"]] + \
                [(t, cfg["normal_results"]) for t in cfg["searches"]]

        for term, n in terms:
            df = search(term, n)
            if df is None or not len(df):
                continue
            for _, row in df.iterrows():
                url = text(row.get("job_url"))
                if not url or url in found or url in excluded:
                    continue
                checked.add(url)
                title = text(row.get("title"))
                desc = text(row.get("description"))
                company = text(row.get("company"))

                matched = [k for k, r in zip(cfg["must_mention"], regexes)
                           if r.search(title + " " + desc)]
                if not matched:
                    continue
                if is_agency(company, text(row.get("company_industry")), desc):
                    excluded.add(url)
                    continue

                found[url] = {
                    "id": text(row.get("id")) or url,
                    "posted": text(row.get("date_posted")),
                    "category": cat_name,
                    "title": title,
                    "role": short_role(title),
                    "company": company,
                    "location": text(row.get("location")),
                    "work_type": work_type(row, title, desc),
                    "matched": ", ".join(matched),
                    "evidence": evidence(desc, regexes),
                    "url": url,
                    "company_url": text(row.get("company_url")),
                }

    if not ran:
        print("Category is switched off. Nothing to do.")
        return

    leads = sorted(found.values(),
                   key=lambda l: (WORK_ORDER.get(l["work_type"], 9), l["posted"]))
    print(f"\nChecked {len(checked)} jobs | agencies excluded {len(excluded)} | matches {len(leads)}")

    r = requests.post(
        WEBAPP_URL,
        json={"token": TOKEN, "category": ", ".join(ran), "leads": leads,
              "checked": len(checked), "excluded": len(excluded)},
        timeout=120,
    )
    print("Sheet response:", r.text[:300])


if __name__ == "__main__":
    main()
