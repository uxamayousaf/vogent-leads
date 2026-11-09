"""
HER ACCOUNT (colleague's LinkedIn, sent by HeyReach)
Runs inside outreach.py, after your own LinkedIn rows are built. Nothing in your own flow changes.

  1. Picks today's REMOTE TECH leads that are LinkedIn rows (never email rows), 1-10 staff first, then 11-50
  2. Finds the founder / CEO for free (company page box, then two public web searches). Not found = skipped
  3. Writes a custom connection note, DM, follow-up 1 and follow-up 2 per person (Gemini API, free tier),
     testing 3 opening angles. If the AI is unavailable, safe written fallbacks are used
  4. Sends the leads straight into the HeyReach campaign (if the HeyReach keys are set in GitHub Secrets)
     and always writes them to a "Her Upload" tab in the Sheet (CSV-ready backup + record)

Your copy of each picked row in your Outreach tab gets Status "Her account": don't message those people.
"""
import datetime as dt
import json
import os
import re
import time

import requests

# ============================ SETTINGS ============================
HER_ENABLED = True
HER_START_DATE = "2026-11-11"            # first sending day: week 1 = 10 a day, then 15 (never above 20)
HER_RAMP = [(7, 10), (14, 15)]           # (days since start, daily leads); after that HER_MAX
HER_MAX = 15
HER_SEARCH_LIMIT = 45                    # most companies checked for a founder per day (free web search)
GEMINI_MODELS = [os.environ.get("GEMINI_MODEL", "").strip() or "gemini-flash-latest",
                 "gemini-3.5-flash", "gemini-2.5-flash"]
GEMINI_GAP_SECONDS = 7                   # free tier: about 10 requests a minute
CALENDLY_URL = "https://calendly.com/vogentlabs"
FOUNDER_NAME = "Usama"
NOTE_MAX = 280                           # LinkedIn Premium allows 300; we keep a margin
ANGLES = ["A", "B", "C"]                 # A stack match, B hiring pain, C give first
HEYREACH_URL = "https://api.heyreach.io/api/public/campaign/AddLeadsToCampaignV2"

# Tech roles for her account. GHL / CRM / HubSpot / WordPress / SEO / VA stay on your account.
HER_TECH_RE = re.compile(
    r"developer|engineer|programmer|full[- ]?stack|front[- ]?end|back[- ]?end|devops|\bqa\b|\bsdet\b|"
    r"data (?:analyst|engineer|scientist)|machine learning|\bml\b|\bai\b|\bllm\b|software|mobile app|"
    r"\bios\b|android|python|react|node|automation (?:engineer|specialist|developer|expert)", re.I)
HER_NOT_RE = re.compile(r"wordpress|shopify|webflow|\bwix\b|squarespace|go\s?high\s?level|highlevel|\bghl\b|"
                        r"hubspot|\bcrm\b|\bseo\b|salesforce|sales engineer", re.I)
FOUNDER_WORDS = ["founder", "co-founder", "ceo", "chief executive", "owner", "president", "cto",
                 "chief technology", "managing director"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
# ==================================================================

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
HR_KEY = os.environ.get("HEYREACH_API_KEY", "").strip()
HR_CAMPAIGN = os.environ.get("HEYREACH_CAMPAIGN_ID", "").strip()
HR_ACCOUNT = os.environ.get("HEYREACH_ACCOUNT_ID", "").strip()
STATS = {"tech": 0, "searched": 0, "box": 0, "ddg": 0, "bing": 0, "no_founder": 0,
         "ai": 0, "fallback": 0, "picked": 0, "pushed": ""}


# ---------- 1. Which leads are hers ----------
def is_her_lead(lead):
    title, cat = str(lead.get("title") or ""), str(lead.get("category") or "")
    if HER_NOT_RE.search(title) or "GHL" in str(lead.get("priority") or "") or "HubSpot" in str(lead.get("priority") or ""):
        return False
    if not (HER_TECH_RE.search(title) or cat == "AI Automation"):
        return False
    size_min = lead.get("size_min")
    return size_min is not None and size_min <= 50 and lead.get("work_type") == "Remote"


def her_rank(lead):
    size_min, staff = lead.get("size_min"), lead.get("staff_count")
    tier = 0 if size_min <= 10 else (1 if staff is not None and staff <= 20 else 2)
    try:
        newest = -dt.date.fromisoformat(str(lead.get("posted"))[:10]).toordinal()
    except ValueError:
        newest = 0
    return (tier, 0 if lead.get("worldwide") else 1, newest)


def daily_cap(today):
    try:
        days = (today.date() - dt.date.fromisoformat(HER_START_DATE)).days
    except ValueError:
        return 0
    if days < 0:
        return 0                                  # before the start date: nothing is picked
    for limit_days, n in HER_RAMP:
        if days < limit_days:
            return n
    return min(HER_MAX, 20)


# ---------- 2. Free founder search ----------
def _words(company):
    return [w for w in re.findall(r"[a-z0-9]+", str(company).lower())
            if len(w) > 2 and w not in ("inc", "llc", "ltd", "the", "corp", "company", "group", "labs")][:2]


def _pick(results, company):
    words = _words(company)
    for url, title in results:
        if "linkedin.com/in/" not in url:
            continue
        t = title.lower()
        if not any(w in t for w in FOUNDER_WORDS) or not words or not all(w in t for w in words):
            continue                               # must be a founder-type title AT this company
        parts = [x.strip() for x in re.split(r"\s[-|–]\s", title)]
        name = re.sub(r"[^A-Za-z\s'\-]", "", parts[0]).strip()
        if 2 <= len(name.split()) <= 4:
            return {"name": name, "first": name.split()[0], "url": url.split("?")[0],
                    "title": parts[1] if len(parts) > 1 else ""}
    return None


def ddg(query):
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


def bing(query):
    import html as htmlmod
    try:
        r = requests.get("https://www.bing.com/search", params={"q": query}, headers=UA, timeout=20)
        out = []
        for href, title in re.findall(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', r.text):
            out.append((htmlmod.unescape(href), htmlmod.unescape(re.sub(r"<[^>]+>", "", title)).strip()))
        return out
    except Exception:
        return []
    finally:
        time.sleep(2)


def find_founder(lead, free_founder):
    p = free_founder(lead["company"], lead.get("people"))
    if p:
        STATS["box"] += 1
        return p
    q = f'"{lead["company"]}" founder OR CEO OR owner site:linkedin.com/in'
    p = _pick(ddg(q), lead["company"])
    if p:
        STATS["ddg"] += 1
        return p
    p = _pick(bing(q), lead["company"])
    if p:
        STATS["bing"] += 1
        return p
    STATS["no_founder"] += 1
    return None


# ---------- 3. AI drafting ----------
ANGLE_BRIEF = {
    "A": "Stack match: name the exact rare skill combination this post asks for, then ask one short question.",
    "B": "Hiring pain: ask how the search is going, using one specific detail from the post. No offer yet.",
    "C": "Give first: offer to share one anonymized candidate profile that matches this post, free to review.",
}


def prompt_for(lead, person, angle):
    desc = re.sub(r"\s+", " ", str(lead.get("desc") or ""))[:1800]
    return f"""You write LinkedIn outreach for a Business Development Partner at Vogent Labs, a remote staffing company.
Vogent places pre-vetted remote tech professionals (developers, engineers, data and AI specialists) with small companies.
Terms you may use: no upfront fee, they only pay once the hire starts, free replacement if not the right fit,
much lower cost than a local hire, profiles free to review.

Prospect: {person.get('first') or 'there'} ({person.get('title') or 'founder'}) at {lead['company']}
Company size: {lead.get('size') or 'small'} staff. Industry: {lead.get('industry') or 'unknown'}
Job post: {lead['title']}
Key skills found: {lead.get('skills') or 'none listed'}
Job description (excerpt): {desc or 'not available'}

Opening angle for this person: {ANGLE_BRIEF[angle]}

Write 4 messages, each specific to THIS post (mention real details from it, never generic):
1. note: connection request note, under 250 characters, a question, no pitch, no link.
2. dm: sent 1 day after they accept, under 60 words, follows the angle.
3. fu1: 3 days later if no reply, under 55 words, a new specific point from the post plus one risk-free term.
4. fu2: 5 days later if no reply, under 55 words: offer a quick call with our founder {FOUNDER_NAME}, include {CALENDLY_URL}, and say no worries if they already hired.

Rules: sound like a real person, short sentences, friendly and plain. Use their first name.
Never use em dashes or en dashes. No "I hope this finds you well", no flattery, no buzzwords, no invented numbers,
no prices, never say where the talent is based. Do not sign off with a name.
Return only JSON: {{"note": "...", "dm": "...", "fu1": "...", "fu2": "..."}}"""


BANNED = re.compile(r"hope (?:this|you)|pakistan|\$\s?\d|per hour|/hr\b|synerg|leverage|game[- ]?chang", re.I)


def clean(text):
    t = str(text or "").replace("\u2014", ", ").replace("\u2013", ", ").replace(" , ", ", ")
    return re.sub(r"\s{2,}", " ", t).strip()


def valid(m):
    if not all(m.get(k) for k in ("note", "dm", "fu1", "fu2")):
        return False
    if len(m["note"]) > NOTE_MAX or CALENDLY_URL not in m["fu2"]:
        return False
    return not any(BANNED.search(m[k]) for k in ("note", "dm", "fu1", "fu2"))


def gemini(prompt):
    if not GEMINI_KEY:
        return None
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}}
    for model in GEMINI_MODELS:
        for attempt in range(2):
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=60)
            except Exception as e:
                print("Gemini not reachable:", e)
                return None
            finally:
                time.sleep(GEMINI_GAP_SECONDS)
            if r.status_code == 429 and attempt == 0:
                time.sleep(30)
                continue
            if r.status_code == 404:
                break                               # model name not available: try the next one
            if r.status_code != 200:
                print(f"Gemini {model} said {r.status_code}: {r.text[:200]}")
                return None
            try:
                txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                txt = re.sub(r"^```(?:json)?|```$", "", txt.strip()).strip()
                return {k: clean(v) for k, v in json.loads(txt).items()}
            except Exception:
                return None
    return None


def fallback(lead, person, angle, skills):
    first = person.get("first") or "there"
    role, company = lead.get("role") or lead["title"], lead["company"]
    sk = skills[:2]
    both = f"{sk[0]} and {sk[1]}" if len(sk) == 2 else (sk[0] if sk else "this skill set")
    note = {"A": f"Hi {first}, saw {company} needs a {role} strong in {both}. Not an easy mix to find. "
                 f"How is the search going so far?",
            "B": f"Hi {first}, noticed {company} is hiring a {role}. Curious, have you found the right person "
                 f"yet, or still going through applicants?",
            "C": f"Hi {first}, saw your {role} opening at {company}. I may have a profile that fits what you "
                 f"described. Happy to connect!"}[angle]
    dm = {"A": f"Thanks for connecting, {first}! Finding someone solid in {both} usually takes a while. "
               f"We place pre-vetted remote professionals with exactly that background. Would a couple of profiles help?",
          "B": f"Thanks for connecting, {first}! How has the {role} search been going? If it's slow, we have "
               f"pre-vetted remote professionals ready to interview, free to review.",
          "C": f"Thanks for connecting, {first}! I can send you one anonymized profile that matches your {role} "
               f"post, free to review. Want me to share it?"}[angle]
    fu1 = (f"Hi {first}, quick note on the {role} role. There's no upfront fee with us, you only pay once your "
           f"hire starts, and we replace them free if it's not the right fit.")
    fu2 = (f"Hi {first}, if it's easier to talk it through, our founder {FOUNDER_NAME} can walk you through "
           f"matching profiles: {CALENDLY_URL}. If you've already hired, no worries at all!")
    return {"note": note[:NOTE_MAX], "dm": dm, "fu1": fu1, "fu2": fu2}


def draft(lead, person, angle, skills):
    m = gemini(prompt_for(lead, person, angle))
    if m and valid(m):
        STATS["ai"] += 1
        return m, "AI"
    STATS["fallback"] += 1
    return fallback(lead, person, angle, skills), "Fallback"


# ---------- 4. HeyReach ----------
def push_heyreach(picked):
    """Returns (ok, message). Only leads that really reached HeyReach are marked 'Her account'."""
    pairs = []
    for x in picked:
        name = x["person"]["name"].split()
        lead = {"firstName": name[0], "lastName": " ".join(name[1:]), "profileUrl": x["person"]["url"],
                "companyName": x["lead"]["company"], "position": x["person"].get("title", ""),
                "customUserFields": [{"name": k, "value": x["msgs"][k]} for k in ("note", "dm", "fu1", "fu2")] +
                                    [{"name": "angle", "value": x["angle"]}]}
        pair = {"lead": lead}
        if HR_ACCOUNT:
            pair["linkedInAccountId"] = int(HR_ACCOUNT)
        pairs.append(pair)
    try:
        r = requests.post(HEYREACH_URL, headers={"X-API-KEY": HR_KEY, "Content-Type": "application/json"},
                          json={"campaignId": int(HR_CAMPAIGN), "accountLeadPairs": pairs}, timeout=60)
        return r.status_code == 200, f"HeyReach {r.status_code}: {r.text[:150]}"
    except Exception as e:
        return False, f"HeyReach not reachable: {e}"


# ---------- Main step (called from outreach.py) ----------
def run(rows, today, post, free_founder, skill_list):
    """rows: today's Outreach rows (LinkedIn rows still carry '_lead'). Marks picked rows 'Her account'."""
    if not HER_ENABLED:
        return
    if not (HR_KEY and HR_CAMPAIGN):
        print("Her account: off until the HeyReach keys are added in GitHub Secrets")
        return                                         # switched on by adding the keys, nothing else
    cap = daily_cap(today)
    pool = [r for r in rows if r.get("channel") == "LinkedIn" and r.get("_lead") and is_her_lead(r["_lead"])]
    STATS["tech"] = len(pool)
    pool.sort(key=lambda r: her_rank(r["_lead"]))
    picked = []
    for r in pool:
        if len(picked) >= cap or STATS["searched"] >= HER_SEARCH_LIMIT:
            break
        lead = r["_lead"]
        STATS["searched"] += 1
        person = find_founder(lead, free_founder)
        if not person:
            continue                                   # no founder found: stays a normal row for you
        angle = ANGLES[len(picked) % len(ANGLES)]
        msgs, how = draft(lead, person, angle, skill_list(lead))
        picked.append({"row": r, "lead": lead, "person": person, "angle": angle, "msgs": msgs, "how": how})
    STATS["picked"] = len(picked)
    if not picked:
        print(f"Her account: cap {cap}, {STATS}")
        post({"action": "her_add", "rows": [], "stats": STATS, "cap": cap})
        return
    ok, STATS["pushed"] = push_heyreach(picked)
    for x in picked:
        x["drafted_by"] = x["how"] if ok else x["how"] + ", NOT in HeyReach"
        if ok:
            x["row"]["email_status"] = "Her account"      # your copy: don't message this person
    out = [{"profile_url": x["person"]["url"], "first_name": x["person"]["name"].split()[0],
            "last_name": " ".join(x["person"]["name"].split()[1:]), "company": x["lead"]["company"],
            "position": x["person"].get("title", ""), "note": x["msgs"]["note"], "dm": x["msgs"]["dm"],
            "fu1": x["msgs"]["fu1"], "fu2": x["msgs"]["fu2"], "angle": x["angle"], "drafted_by": x["drafted_by"],
            "job_title": x["lead"]["title"], "job_url": x["lead"]["url"], "size": x["lead"].get("size", "")}
           for x in picked]
    print(f"Her account: cap {cap}, {STATS}")
    print("Sheet response (her):", post({"action": "her_add", "rows": out, "stats": STATS, "cap": cap})[:200])
