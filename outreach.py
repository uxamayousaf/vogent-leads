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
MAX_COMPANY_SIZE = 50                    # 50 staff or fewer; unknown size is kept but ranked last
WEEKDAYS_ONLY = True                     # Monday to Friday (Pakistan time)
REGION_ORDER = {"USA": 1, "Canada": 2, "UK": 3}
ROLES = ["CRM Automation", "Virtual Assistant", "AI Automation", "Customer Service"]
GHL_ONLY_FOR_CRM = True                  # CRM Automation leads only if the job mentions GHL
APIFY_ACTOR = "harvestapi~linkedin-company-employees"
APIFY_MAX_PEOPLE = 40                    # keeps usage inside Apify's free monthly credit
APIFY_COST_CAP_USD = 0.25                # hard spending cap per daily run
SPECIALISTS = {
    "CRM Automation": "GoHighLevel specialists",
    "Virtual Assistant": "remote support specialists",
    "AI Automation": "AI and automation specialists",
    "Customer Service": "customer support specialists",
}
# ==================================================================

WEBAPP_URL = os.environ["SHEET_WEBAPP_URL"]
TOKEN = os.environ["SHEET_TOKEN"]
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
PKT = dt.timezone(dt.timedelta(hours=5))
WORK_ORDER = {"Remote": 0, "Remote (mentioned)": 1, "Hybrid": 2, "Not stated": 3, "On-site": 4}
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
    if l.get("region") not in REGION_ORDER:
        return False
    if l.get("category") not in ROLES:
        return False
    if l.get("category") == "CRM Automation" and GHL_ONLY_FOR_CRM and "GHL" not in (l.get("priority") or ""):
        return False
    if not l.get("direct", True):
        return False                       # staffing / recruiting / outsourcing: never contacted
    size_min = l.get("size_min")
    if size_min is not None and size_min > MAX_COMPANY_SIZE:
        return False
    return all(str(l.get(k) or "").strip() for k in ("company", "title", "url"))


def rank_key(l):
    size_known_small = 0 if l.get("size_min") is not None else 1
    ghl = 0 if "GHL" in (l.get("priority") or "") else 1
    try:
        newest = -dt.date.fromisoformat(str(l.get("posted"))[:10]).toordinal()
    except ValueError:
        newest = 0
    return (size_known_small, WORK_ORDER.get(l.get("work_type"), 9),
            REGION_ORDER.get(l.get("region"), 9), ghl, newest)


# ---------- 2. Find decision makers with Apify ----------
def apify_people(company_urls):
    if not APIFY_TOKEN or not company_urls:
        print("No Apify token or no LinkedIn company pages: skipping decision-maker lookup.")
        return []
    body = {
        "companies": company_urls,
        "companyBatchMode": "all_at_once",
        "seniorityLevelIds": ["310", "320"],          # CXO, Owner / Partner
        "profileScraperMode": "Short ($4 per 1k)",
        "maxItems": min(APIFY_MAX_PEOPLE, 2 * len(company_urls)),
    }
    base = "https://api.apify.com/v2"
    try:
        run = requests.post(f"{base}/acts/{APIFY_ACTOR}/runs",
                            params={"token": APIFY_TOKEN, "maxTotalChargeUsd": APIFY_COST_CAP_USD},
                            json=body, timeout=60).json()["data"]
        run_id, dataset = run["id"], run["defaultDatasetId"]
        for _ in range(90):                               # wait up to ~15 minutes
            time.sleep(10)
            status = requests.get(f"{base}/actor-runs/{run_id}", params={"token": APIFY_TOKEN},
                                  timeout=30).json()["data"]["status"]
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
        print("Apify run status:", status)
        items = requests.get(f"{base}/datasets/{dataset}/items",
                             params={"token": APIFY_TOKEN, "clean": "true"}, timeout=60).json()
        return items if isinstance(items, list) else []
    except Exception as e:
        print("Apify lookup failed:", e)
        return []


def person_info(item):
    first = str(item.get("firstName") or "").strip()
    last = str(item.get("lastName") or "").strip()
    name = (first + " " + last).strip() or str(item.get("fullName") or item.get("name") or "").strip()
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


# ---------- 3. Messages ----------
def article(role):
    return ("an " if role[:1].lower() in "aeiou" else "a ") + role


def follow_up_dm(lead, first):
    role = "assistant" if lead["category"] == "Virtual Assistant" else lead.get("role") or "open"
    who = SPECIALISTS.get(lead["category"], "remote specialists")
    hi = f"Thanks for connecting, {first}." if first else "Thanks for connecting."
    if lead.get("work_type") in ("On-site", "Hybrid"):
        body = (f" If a remote setup could work for the {role} role, we place dedicated {who} who can start "
                f"in days, not weeks, at a much lower cost than a local hire.")
    else:
        body = (f" If you are still filling the {role} role, we place dedicated {who} who can start in days, "
                f"not weeks, at a much lower cost than a local hire.")
    return hi + body + " Happy to share two profiles that match your post if useful."


def named_note(note, first):
    if first and note.startswith("Hi,"):
        candidate = f"Hi {first}," + note[3:]
        if len(candidate) <= 300:
            return candidate
    return note


def founder_search_url(company):
    return ("https://www.linkedin.com/search/results/people/?keywords="
            + quote(f'{company} founder OR CEO OR owner'))


# ---------- Main ----------
def main():
    today = dt.datetime.now(PKT)
    if WEEKDAYS_ONLY and today.weekday() >= 5:
        print("Weekend: no outreach queue today.")
        return

    leads = [l for l in load_candidates() if eligible(l)]
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
    todays = [l for l in ranked if norm(l["company"]) in new_keys][:DAILY_LINKEDIN]
    print(f"New companies for today's queue: {len(todays)}")

    urls = sorted({l["company_url"] for l in todays if slug(l.get("company_url"))})
    found = match_people(todays, apify_people(urls))
    print(f"Decision makers found: {len(found)} of {len(todays)}")

    rows = []
    for i, l in enumerate(todays, 1):
        p = found.get(l["company"])
        first = p["first"] if p else ""
        rows.append({
            "key": norm(l["company"]),
            "date": today.strftime("%Y-%m-%d"),
            "rank": i,
            "channel": "LinkedIn",
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
            "email": "",
            "note": named_note(l.get("note", ""), first),
            "followup": follow_up_dm(l, first),
            "email_status": "",
        })

    print("Sheet response:", post({"action": "outreach_add", "rows": rows,
                                   "eligible": len(ranked), "found": len(found)})[:300])


if __name__ == "__main__":
    main()
