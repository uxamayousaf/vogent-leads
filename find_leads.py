"""
VOGENT LEAD FINDER (JobSpy version)
Finds fresh US jobs on LinkedIn, reads the FULL description, keeps only
real matches from direct employers (no recruiting agencies), and sends
them to your Google Sheet.

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
DEEP_RESULTS = 100            # results for "deep_searches"
NORMAL_RESULTS = 50           # results for normal "searches"

CATEGORIES = {
    "GHL / CRM": {
        "enabled": True,
        "deep_searches": ["GoHighLevel", "GHL", "HighLevel"],
        "searches": [
            "CRM Manager", "CRM Specialist", "CRM Administrator", "CRM Automation",
            "Marketing Automation Specialist", "Automation Specialist",
            "Funnel Builder", "Email Marketing Specialist",
        ],
        "must_mention": ["go high level", "highlevel", "high level crm", "ghl", "lead connector"],
        "remote_only": False,
    },
    "Performance Marketing": {
        "enabled": False,     # turn on after GHL testing
        "deep_searches": ["Performance Marketing Specialist", "Media Buyer"],
        "searches": [
            "Paid Media Specialist", "Paid Ads Manager", "PPC Specialist",
            "Google Ads Specialist", "Meta Ads Specialist", "Facebook Ads Specialist",
            "Paid Social Specialist",
        ],
        "must_mention": [
            "meta ads", "facebook ads", "google ads", "adwords", "ppc",
            "paid social", "paid media", "media buying", "facebook advertising",
        ],
        "remote_only": False,
    },
    "Virtual Assistant": {
        "enabled": False,     # turn on last
        "deep_searches": ["Virtual Assistant"],
        "searches": ["Remote Executive Assistant", "Remote Administrative Assistant"],
        "must_mention": [
            "virtual assistant", "executive assistant",
            "administrative assistant", "personal assistant",
        ],
        "remote_only": True,
    },
}

# ---------- Recruiter / agency filter (add names anytime, lowercase) ----------
AGENCY_BLOCKLIST = [
    "robert half", "insight global", "randstad", "adecco", "teksystems", "kforce",
    "aerotek", "kelly services", "manpower", "express employment", "beacon hill",
    "belay", "boldly", "wing assistant", "prialto", "time etc", "virtual staff finder",
    "hire overseas", "remote staff", "outsource", "upwork", "fiverr",
]
AGENCY_NAME_WORDS = [
    "staffing", "recruit", "talent", "placement", "workforce", "personnel",
    "headhunt", "executive search",
]
AGENCY_PHRASES = [
    "our client", "on behalf of our client", "on behalf of a client",
    "for a client of ours", "client of ours",
]
# ==================================================================

WEBAPP_URL = os.environ["SHEET_WEBAPP_URL"]
TOKEN = os.environ["SHEET_TOKEN"]


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
    role = re.split(r"\s+[-|–—/]\s+|\(|\[|,|:", title)[0].strip()
    return role or title


def evidence(desc, regexes):
    for r in regexes:
        m = r.search(desc)
        if m:
            snippet = desc[max(0, m.start() - 80): m.start() + 120]
            return "..." + re.sub(r"\s+", " ", snippet) + "..."
    return "(matched in title)"


def is_agency(company, industry, desc):
    c = company.lower()
    if "staffing" in industry.lower() or "recruit" in industry.lower():
        return True
    if any(b in c for b in AGENCY_BLOCKLIST):
        return True
    if any(w in c for w in AGENCY_NAME_WORDS):
        return True
    d = desc.lower()
    return any(p in d for p in AGENCY_PHRASES)


def is_remote(row, title, desc):
    if str(row.get("is_remote")).lower() == "true":
        return True
    blob = (text(row.get("location")) + " " + title + " " + desc).lower()
    return "remote" in blob


def search(term, n, remote_only):
    try:
        df = scrape_jobs(
            site_name=SITES,
            search_term=term,
            location=LOCATION,
            results_wanted=n,
            hours_old=HOURS_OLD,
            is_remote=remote_only,
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

    for cat_name, cfg in CATEGORIES.items():
        if not cfg["enabled"]:
            continue
        print(f"\n== {cat_name} ==")
        regexes = [build_regex(k) for k in cfg["must_mention"]]
        terms = [(t, DEEP_RESULTS) for t in cfg["deep_searches"]] + \
                [(t, NORMAL_RESULTS) for t in cfg["searches"]]

        for term, n in terms:
            df = search(term, n, cfg["remote_only"])
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
                if cfg["remote_only"] and not is_remote(row, title, desc):
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
                    "matched": ", ".join(matched),
                    "evidence": evidence(desc, regexes),
                    "url": url,
                    "company_url": text(row.get("company_url")),
                }

    leads = list(found.values())
    print(f"\nChecked {len(checked)} jobs | agencies excluded {len(excluded)} | matches {len(leads)}")

    r = requests.post(
        WEBAPP_URL,
        json={"token": TOKEN, "leads": leads,
              "checked": len(checked), "excluded": len(excluded)},
        timeout=120,
    )
    print("Sheet response:", r.text[:300])


if __name__ == "__main__":
    main()
