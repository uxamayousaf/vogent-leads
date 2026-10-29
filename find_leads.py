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
import datetime as dt
import json
import unicodedata
import os
import re
import time

import pandas as pd
import requests
from jobspy import scrape_jobs
from langdetect import DetectorFactory, detect

DetectorFactory.seed = 0

# ============================ SETTINGS ============================
# One run per day: 48h window catches anything missed yesterday. Mondays look back 72h so jobs
# posted on Friday (US business hours, after Friday's run) still reach Monday's outreach.
PKT = dt.timezone(dt.timedelta(hours=5))
HOURS_OLD = 72 if dt.datetime.now(PKT).weekday() == 0 else 48
MAX_EMPLOYEES = 50      # founder-led small companies only (unknown size is kept, ranked last)

# rank = order in your sheet. scale = fewer results per search for smaller markets.
# Each location: (LinkedIn search location, short name shown in sheet)
REGIONS = {
    "USA": {"enabled": True, "rank": 1, "scale": 1.8, "locations": [("United States", "USA")]},   # deeper search
}
SITES = ["linkedin", "indeed", "zip_recruiter", "google"]   # same search on all four job sites
SOURCE_LABELS = {"linkedin": "LinkedIn", "indeed": "Indeed", "zip_recruiter": "ZipRecruiter", "google": "Google Jobs",
                 "remotive": "Remotive", "remoteok": "Remote OK", "wwr": "We Work Remotely", "himalayas": "Himalayas",
                 "jobicy": "Jobicy"}

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
            "Automation Specialist", "Zapier Expert", "AI Agent Developer",
            "Workflow Automation Specialist", "AI Integration Specialist",
            "No-Code Automation", "AI Automation Consultant",
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
    "Remote Boards": {
        # Remote-first job boards (every job is remote); the role label comes from the job title
        "enabled": True, "deep_results": 0, "normal_results": 0, "regions": ["USA"],
        "deep_searches": [], "searches": [], "must_mention": [],
    },
    "Other Remote": {
        # Any other remote role (IT, HR, operations...): only used to fill the day after the priority roles
        "enabled": True, "deep_results": 60, "normal_results": 40, "regions": ["USA"],
        "deep_searches": ["work from anywhere", "remote worldwide", "remote contractor"],
        "searches": ["Data Entry", "Recruiter", "HR Coordinator", "Customer Success", "Project Coordinator",
                     "QA Tester", "Software Developer", "Full Stack Developer", "Data Analyst",
                     "Operations Coordinator", "Technical Support", "Marketing Coordinator"],
        "must_mention": [],
    },
    "SEO": {
        # US / UK clients only, so it is searched in those two region groups
        "enabled": True, "deep_results": 80, "normal_results": 40,
        "regions": ["USA"],
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
        "deep_searches": ["Performance Marketer", "Performance Marketing Specialist", "Media Buyer", "Paid Media Specialist"],
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
    "Lead Generation": {
        "enabled": True, "deep_results": 80, "normal_results": 40,
        "deep_searches": ["Sales Development Representative", "Appointment Setter", "Lead Generation Specialist"],
        "searches": ["SDR", "Business Development Representative", "BDR", "Cold Caller", "Lead Generation Assistant",
                     "Outbound Caller", "Prospecting Specialist"],
        "must_mention": ["appointment setting", "appointment setter", "lead generation", "cold calling",
                         "cold call", "outbound calls", "prospecting", "booking appointments", "set appointments",
                         "sales development", "outbound", "pipeline", "book meetings", "booking meetings"],
    },
    "Social Media": {
        "enabled": True, "deep_results": 80, "normal_results": 40,
        "deep_searches": ["Social Media Coordinator", "Social Media Specialist"],
        "searches": ["Social Media Assistant", "Social Media Content Creator", "Community Coordinator"],
        "must_mention": ["social media", "instagram", "tiktok", "facebook", "content calendar", "community management"],
    },
    "Content Writing": {
        "enabled": True, "deep_results": 80, "normal_results": 40,
        "deep_searches": ["Content Writer", "Copywriter"],
        "searches": ["Blog Writer", "SEO Writer", "Content Specialist", "Ghostwriter"],
        "must_mention": ["content writing", "copywriting", "blog", "articles", "copy", "writing", "editorial"],
    },
    "Email Marketing": {
        "enabled": True, "deep_results": 60, "normal_results": 30,
        "deep_searches": ["Email Marketing Specialist"],
        "searches": ["Email Marketing Coordinator", "Email Marketing Assistant", "Klaviyo Specialist"],
        "must_mention": ["email marketing", "klaviyo", "mailchimp", "email campaigns", "newsletter", "email automation"],
    },
    "Design & Video": {
        "enabled": True, "deep_results": 80, "normal_results": 40,
        "deep_searches": ["Content and Graphic Designer", "Graphic Designer", "Video Editor"],
        "searches": ["Content Creator Graphic Designer", "Junior Graphic Designer", "Canva Designer",
                     "Motion Graphics Designer", "Content Designer"],
        "must_mention": ["graphic design", "canva", "photoshop", "illustrator", "adobe", "video editing",
                         "premiere", "after effects", "capcut", "final cut", "figma"],
    },
    "Website Support": {
        "enabled": True, "deep_results": 60, "normal_results": 30,
        "deep_searches": ["WordPress Developer", "Shopify Specialist", "Web Developer"],
        "searches": ["Webflow Developer", "Website Coordinator", "Website Assistant", "Shopify Developer",
                     "Front End Developer", "App Developer"],
        "must_mention": ["wordpress", "shopify", "webflow", "elementor", "squarespace", "wix", "website updates",
                         "web develop", "front end", "front-end", "react", "javascript", "app develop"],
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
    # job boards and gig marketplaces (not employers)
    "isarta", "goodwork", "teema", "tech ladies", "powertofly", "opentrain", "outlier", "invisible technologies",
    "handshake ai", "scale ai", "turing", "mercor", "outlier ai", "alignerr", "dataannotation", "data annotation tech",
    "braintrust", "toptal", "crossover", "remotasks", "micro1",
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
    ("appointment setting", ["appointment setting", "booking appointments", "set appointments"]),
    ("cold calling", ["cold calling", "cold call", "outbound calls"]), ("Instagram", ["instagram"]),
    ("TikTok", ["tiktok"]), ("content calendars", ["content calendar"]), ("copywriting", ["copywriting"]),
    ("blog posts", ["blog"]), ("Klaviyo", ["klaviyo"]), ("Mailchimp", ["mailchimp"]), ("Canva", ["canva"]),
    ("Photoshop", ["photoshop"]), ("Illustrator", ["illustrator"]), ("Premiere Pro", ["premiere"]),
    ("After Effects", ["after effects"]), ("CapCut", ["capcut"]), ("Figma", ["figma"]),
    ("Webflow", ["webflow"]), ("Elementor", ["elementor"]),
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
    re.compile(r"\(\s*" + _PLACES + r"[\s-]*(?:based\s+)?only\s*\)", re.I),
    re.compile(r"\b" + _PLACES + r"[\s-]*based\s+only\b", re.I),
    re.compile(r"\b(?:remote|work from home|wfh)\W{1,3}" + _PLACES + r"[\s-]*only\b", re.I),
    re.compile(r"\b[Mm]ust\s+(?:be\s+)?(?:a\s+)?(?:US|U\.S\.|UK|Canadian|American|German|Australian)\s+(?:citizen|resident)"),
    re.compile(r"\bcandidates?\s+(?:must|need to|should)\s+(?:be\s+)?(?:located|based|residing)\s+in\b", re.I),
]

# Only CLEAR office requirements count (not "on-site training" or "in-person retreat")
STRONG_ONSITE = ["on-site role", "onsite role", "on-site position", "onsite position", "fully on-site",
                 "fully onsite", "100% on-site", "100% onsite", "in-office role", "in office role",
                 "in-office position", "must work on-site", "must work onsite", "must be in the office",
                 "must work from our office", "work from our office", "report to our office", "on-site in ",
                 "onsite in ", "days a week in the office", "days a week in office", "days per week in office",
                 "days in the office", "this is an in-person", "in-person role", "in-person position",
                 "must be able to commute", "must reside within", "not a remote", "no remote"]
STRONG_ONSITE_RE = re.compile(r"\b(?:on[- ]?site|in[- ]office|in[- ]person)\b[^.]{0,60}\b(?:role|position|job|opportunity)\b"
                              r"|\b(?:role|position|job) is (?:fully |100% )?(?:on[- ]?site|in[- ]office|in[- ]person)\b", re.I)
STRONG_HYBRID = ["hybrid role", "hybrid position", "hybrid schedule", "hybrid work", "this is a hybrid"]
STRONG_REMOTE = ["fully remote", "100% remote", "remote position", "remote role", "work from anywhere",
                 "remote-first", "this is a remote", "remote opportunity", "work remotely", "remote job",
                 "fully-remote", "remote-only", "completely remote", "remote work from home"]
# Strongest offshore signal: the employer is open to people outside the US
WORLDWIDE_RE = re.compile(r"work from anywhere|anywhere in the world|worldwide|world-wide|international "
                          r"(?:candidates|applicants|contractors|talent)|"
                          r"any time ?zone|all time ?zones|"
                          r"\basync(?:hronous)?\b|offshore|overseas", re.I)
# Hard local requirements: the job cannot be done from Pakistan
HARD_LOCAL_RE = re.compile(r"american accent|native (?:us|u\.s\.|american) english|us english accent|"
                           r"\bw-?2 (?:only|employee|employment|position|role)\b|"
                           r"must be (?:a )?(?:u\.?s\.?|united states|us) citizen|"
                           r"(?:u\.?s\.?|us) citizenship (?:is )?required|security clearance|"
                           r"must (?:live|reside|be located|be based) (?:in|within|near)|local (?:candidates|applicants) only|"
                           r"must hold an? (?:active |valid )?(?:insurance|real estate|nursing|cpa|state) licen[sc]e", re.I)
# Commission-only, MLM, "partner / co-founder" and paid-task posts are not real hires
NOT_A_HIRE_TITLE_RE = re.compile(r"commission|co-?founder|\bequity\b|\bpartner\b(?! (?:success|marketing|manager|"
                                 r"relations|account))|paid (?:data )?task|data task|representative needed|\bcloser\b|"
                                 r"high[- ]ticket|financial (?:professional|advisor|representative)|insurance (?:agent|producer)",
                                 re.I)
NOT_A_HIRE_DESC_RE = re.compile(r"commission[- ]only|commission[- ]based|100% commission|straight commission|"
                                r"equity[- ]only|unpaid|no base (?:pay|salary)|uncapped commission", re.I)
# IT services firms that resell staff to their clients
IT_STAFFING_NAME_RE = re.compile(r"\b(?:it solutions|infotech|info tech|technologies|tech solutions|softech|"
                                 r"consulting|consultancy|staffing)\b", re.I)
# Small-business clue for companies whose size is unknown
TINY_CLUE_RE = re.compile(r"small (?:business|team|company|firm|family)|family[- ]owned|owner[- ]operated|"
                          r"solo(?:preneur)?|boutique|our founder|the founder|work directly with (?:the )?(?:founder|owner|ceo)|"
                          r"report(?:ing)? (?:directly )?to (?:the )?(?:founder|owner|ceo)", re.I)
HARD_LOCAL_TITLE_RE = re.compile(r"american accent|\bw-?2\b|\benterprise\b|\bregistered\b|physician|neurolog|"
                                 r"\bnurse\b|\brn\b|dietitian|therapist|attorney|lawyer|pharmacist|dentist|"
                                 r"psycholog|counselor|clinician|\bmd\b|\bcpa\b|ai (?:training|trainer|finance review|"
                                 r"review)|\brlhf\b|annotat|\b(?:us|u\.s\.|usa|uk|canada)[- ]based\b|\blicensed\b|\bhotel\b|"
                                 r"guest (?:experience|service|services)|front desk|concierge", re.I)
ONSITE_PHRASES = [
    "on-site", "onsite", "on site", "in office", "in-office", "in person",
    "in-person", "must be able to commute", "must reside within",
]
# ==================================================================

WEBAPP_URL = os.environ["SHEET_WEBAPP_URL"]
TOKEN = os.environ["SHEET_TOKEN"]
ONLY_CATEGORY = os.environ.get("CATEGORY", "").strip()
ONLY_REGION = os.environ.get("REGION", "").strip()
WORK_ORDER = {"Remote": 0, "Not confirmed": 1, "Not stated": 2, "Hybrid": 3, "On-site": 4}
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


ROLE_NOUN = re.compile(r"\b(?:specialist|manager|coordinator|assistant|rep|representative|associate|executive|"
                       r"writer|designer|editor|bookkeeper|accountant|clerk|agent|strategist|analyst|developer|"
                       r"admin|administrator|officer|expert|consultant|marketer|producer|creator|technician|"
                       r"generalist|setter|caller|virtual assistant)s?\b", re.I)


def is_emoji_char(ch):
    """Emojis and picture symbols only; letters and normal punctuation like @ & . stay."""
    o = ord(ch)
    return unicodedata.category(ch) in ("So", "Cs") or 0xFE00 <= o <= 0xFE0F or o == 0x200D or 0x1F000 <= o <= 0x1FAFF


def clean_company(name):
    """Company name for messages: drops emojis and decorative symbols ("Citizen Agency 🐻" -> "Citizen Agency")."""
    out = "".join(ch for ch in str(name or "") if not is_emoji_char(ch))
    return re.sub(r"\s{2,}", " ", out).strip() or str(name or "").strip()


def short_role(title):
    clean = re.sub(r"^[^\w]+", "", title)                       # drop leading emojis/symbols
    clean = re.sub(r"^(?:remote|hybrid|on-?site)\s*[|:\-–—]\s*", "", clean, flags=re.I)  # "Remote | ..."
    clean = re.split(r"\s+@\s+|\s+at\s+[A-Z]", clean)[0]          # "... @ Company" / "... at Company"
    # "Paid Search / Paid Social Specialist": keep both halves when only the second half names the role
    halves = re.split(r"\s+/\s+", clean)
    if len(halves) > 1 and not ROLE_NOUN.search(halves[0]) and ROLE_NOUN.search(halves[1]):
        clean = "/".join(halves[:2])
    role = re.split(r"\s+[-|–—/]\s+|\s*\||\(|\[|,|:", clean)[0].strip()
    # "Remote Bookkeeper" -> "Bookkeeper" (work type is already its own column; reads better in messages)
    bare = re.sub(r"^(?:(?:remote|hybrid|part[- ]time|full[- ]time|freelance|contract|temporary|temp)\s+)+", "",
                  role, flags=re.I).strip()
    return bare or role or clean


def trim_role(role, limit=45):
    """Shortens a very long role at a word break and never ends on 'and', 'of', '&' and similar."""
    if len(role) <= limit:
        return role
    words = role[:limit].rsplit(" ", 1)[0].split(" ")
    while len(words) > 1 and words[-1].lower() in ("and", "or", "&", "of", "for", "with", "to", "the", "a", "an", "in"):
        words.pop()
    return " ".join(words)


def evidence(desc, regexes):
    for r in regexes:
        m = r.search(desc)
        if m:
            snippet = desc[max(0, m.start() - 80): m.start() + 120]
            return "..." + re.sub(r"\s+", " ", snippet) + "..."
    return "(matched in title)"


VA_TITLE_RE = re.compile(r"assistant|\bva\b|\bea\b|secretar|admin", re.I)
# Virtual-assistant companies and their wording: they hire VAs to place with their own clients
VA_AGENCY_NAME_RE = re.compile(r"virtual|assistan|\bva\b|\bvas\b|outsourc|offshore|remote (?:staff|team|talent)|"
                               r"delegat|belay|boldly|prialto|time etc|wing\b|myoutdesk|athena|zirtual|cyberbacker|"
                               r"uassist|equivity|persona|pearl talent|somewhere|hire ?smart|magic|support ninja|"
                               r"\bbpo\b|solutions?$|services?$", re.I)
VA_AGENCY_DESC_RE = re.compile(r"our clients|multiple clients|matched with (?:a |an )?(?:client|executive|business)|"
                               r"placed with|support (?:a |our )?(?:busy )?(?:clients?|executives) (?:across|in)|"
                               r"client portfolio|join our (?:team|network|community) of (?:virtual )?assistants|"
                               r"we (?:match|connect|pair)|talent (?:pool|network|community)|our network of|"
                               r"assigned to (?:a |one of our )?clients?", re.I)


def is_agency(company, industry, desc, title=""):
    c, ind, d = company.lower(), industry.lower(), desc.lower()
    if VA_TITLE_RE.search(title) and (VA_AGENCY_NAME_RE.search(company) or VA_AGENCY_DESC_RE.search(desc)):
        return True                              # VA / EA roles: direct hires only
    if IT_STAFFING_NAME_RE.search(company) and re.search(r"consultant|developer|engineer|architect", title, re.I):
        return True                              # IT services firm placing someone at its client
    if re.search(r"\bhire(?:d)?\b|hired|recruit|talent|staffing|\bjobs?\b|careers?\b", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", company), re.I) \
            or re.search(r"staffing|recruit", WEBSITE_OF.get(company, ""), re.I):
        return True                              # hiring / recruiting platforms ("HiredBuddy", "TalentHub")
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
OFF_TARGET_TITLE_RE = re.compile(r"\b(account executive|engineer|strategist|"
                                 r"sales representative|sales rep|account manager|business development manager|"
                                 r"founding|controller)\b", re.I)
# The CRM category only counts real CRM jobs (not sales roles that just mention HubSpot)
CRM_TITLE_RE = re.compile(r"crm|hubspot|highlevel|high level|\bghl\b|automation|funnel|email marketing|"
                          r"marketing operations|marketing ops", re.I)
GHL_RE = re.compile(r"go\s?high\s?level|\bhighlevel\b|\bghl\b|lead\s?connector", re.I)
# No budget behind these: interns, volunteers, students
NO_BUDGET_TITLE_RE = re.compile(r"\b(intern|interns|internship|volunteer|apprentice|apprenticeship|student|"
                                r"fellowship)\b", re.I)
# Gig listings dressed up as jobs ("Expert Opportunity ... $70/hr", "Publish Book in 2 weeks")
GIG_TITLE_RE = re.compile(r"expert opportunity|\$\s?\d+(?:\.\d+)?\s?(?:/|per\s)\s?(?:hr|hour)\b|"
                          r"publish (?:a |your )?book|\bbook author\b", re.I)
# "Can we staff this?" The title must look like the work its category covers.
# General marketing and communications roles count for every marketing category.
MARKETING_GENERAL = (r"\bmarketing\b|\bmarketer\b|\bcontent\b|digital (?:marketing|media)|growth market|demand gen|"
                     r"brand(?:ing)? (?:specialist|coordinator|assistant|associate|executive)|"
                     r"media (?:coordinator|specialist|assistant|associate|executive)|publishing coordinator|"
                     r"communications?\s*(?:&|and)?\s*(?:marketing\s+)?"
                     r"(?:coordinator|specialist|assistant|associate|executive|officer)")
ROLE_FIT = {
    "SEO": r"seo|\bgeo\b|\baeo\b|search|link ?build|backlink|organic|keyword|" + MARKETING_GENERAL,
    "Performance Marketing": r"paid|ppc|\bsem\b|ads\b|media buy|media plan|performance|advertising|ad ops|acquisition|influencer|"
                             + MARKETING_GENERAL,
    "Customer Service": r"customer|client (?:support|service|success|care|experience)|support|help ?desk|"
                        r"care (?:associate|agent|coordinator|representative)|call cent|contact cent|chat|"
                        r"member services|patient (?:coordinator|services|care)|guest|reservation|concierge|"
                        r"service (?:representative|rep|associate|agent|coordinator)",
    "Bookkeeping": r"bookkeep|account|fiscal|financ|revenue|receivabl|payabl|payroll|billing|treasur|\btax\b|"
                   r"quickbooks|xero|reconcil|invoic|ledger|\bap\b|\bar\b",
    "Virtual Assistant": r"assistant|virtual|admin|secretar|data entry|clerk|office (?:coordinator|manager)|"
                         r"operations (?:coordinator|associate)|scheduler|dispatch|admin(?:istrative)? coordinator",
    "Lead Generation": r"lead gen|appointment|setter|cold call|outbound|prospect|lead research|list build|"
                       r"sales development|\bsdr\b|\bbdr\b|business development rep|"
                       r"data research|\bbdc\b|telemarket|telesales|scheduler|sales (?:assistant|coordinator|support)|" + MARKETING_GENERAL,
    "Social Media": r"social|community (?:manager|coordinator|specialist)|community (?:&|and) engagement|content creator|"
                    r"tiktok|instagram|\bugc\b|influencer|creative|e[- ]?commerce|" + MARKETING_GENERAL,
    "Content Writing": r"writ|copy|blog|editor|proofread|" + MARKETING_GENERAL,
    "Email Marketing": r"email|klaviyo|newsletter|lifecycle|retention|marketing automation|\bcrm\b|e[- ]?commerce|" + MARKETING_GENERAL,
    "Design & Video": r"design|video|graphic|motion|animat|editor|illustrat|creative|\bui\b|\bux\b|3d|thumbnail|"
                      r"photograph|visual|multimedia|producer|courseware|e-?learning|content creator",
    "Website Support": r"web|wordpress|shopify|webflow|\bsite\b|\bcms\b|landing page|wix|squarespace|front.?end|"
                       r"html|e[- ]?commerce",
}
ROLE_FIT_RE = {k: re.compile(v, re.I) for k, v in ROLE_FIT.items()}
# Extra outsourceable work not covered above (automation, CRM, developers)
ANY_FIT_RE = re.compile(r"automation|\bcrm\b|hubspot|highlevel|\bghl\b|zapier|n8n|developer|\bai\b|"
                        r"chatbot|operations (?:assistant|coordinator|specialist)|project coordinator|recruit(?:ing|ment) coordinator", re.I)
# Never our kind of hire, whatever the category
NOT_STAFFABLE_RE = re.compile(r"\b(analyst|reporter|journalist|technician)\b", re.I)


def best_category(title, category):
    """Category label from the job title (the search that found it can be a different one)."""
    if category in ("CRM Automation", "AI Automation"):
        return category
    if category == "Remote Boards":
        return "CRM Automation"                  # remote boards only bring GHL / HubSpot / CRM jobs
    if category == "Other Remote":
        for cat, rx in ROLE_FIT_RE.items():
            if rx.search(title):
                return cat
        return "Other"
    if ROLE_FIT_RE.get(category) and ROLE_FIT_RE[category].search(title):
        return category
    for cat, rx in ROLE_FIT_RE.items():
        if rx.search(title):
            return cat
    if re.search(r"automation|zapier|n8n|\bai\b|chatbot", title, re.I):
        return "AI Automation"
    if re.search(r"\bcrm\b|hubspot|highlevel|\bghl\b", title, re.I):
        return "CRM Automation"
    if re.search(r"developer|programmer", title, re.I):
        return "Website Support"
    return "CRM Automation" if category == "Remote Boards" else category


OTHER_BLOCK_RE = re.compile(r"\b(account executive|sales representative|sales rep|account manager|controller|"
                            r"founding)\b", re.I)


def wrong_title(title, category, desc=""):
    """True for roles we should not pitch: in-store, off-target, no budget, gig listings, or not this category's work."""
    if category == "Other Remote":
        # any role is fine (IT, HR, data...), only the hard no's apply
        return bool(NOT_REMOTE_TITLE_RE.search(title) or OTHER_BLOCK_RE.search(title) or NO_BUDGET_TITLE_RE.search(title)
                    or GIG_TITLE_RE.search(title) or HARD_LOCAL_TITLE_RE.search(title)
                    or NOT_A_HIRE_TITLE_RE.search(title) or NOT_A_HIRE_DESC_RE.search(desc))
    if NOT_REMOTE_TITLE_RE.search(title) or OFF_TARGET_TITLE_RE.search(title):
        return True
    if NO_BUDGET_TITLE_RE.search(title) or GIG_TITLE_RE.search(title):
        return True
    if category == "CRM Automation":
        # A job that names GoHighLevel is a GHL lead whatever its title ("Marketing Assistant (GHL)")
        return not (CRM_TITLE_RE.search(title) or GHL_RE.search(title + " " + desc))
    if NOT_STAFFABLE_RE.search(title) and not re.search(r"customer", title, re.I):
        return True
    if HARD_LOCAL_TITLE_RE.search(title) or NOT_A_HIRE_TITLE_RE.search(title) or NOT_A_HIRE_DESC_RE.search(desc):
        return True
    # Any work that can be done from Pakistan counts, whichever search found it
    return not (any(r.search(title) for r in ROLE_FIT_RE.values()) or ANY_FIT_RE.search(title))


def crm_manager_title(title, category):
    """'CRM Manager' / 'Marketing Automation Manager': hands-on at small companies, allowed up to 50 staff."""
    if category != "CRM Automation" or not CRM_TITLE_RE.search(title):
        return False
    return not is_senior(re.sub(r"\bmanagers?\b", "", title, flags=re.I))


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


def decide_work(row, title, desc, page_tag="", remote_hit=False):
    """Work type. Office or hybrid wording always wins (never pitched).
    Remote when ANY of these is true (and no office / hybrid wording):
      LinkedIn's own tag says Remote, the job location says Remote, the title says Remote,
      the description uses a clear remote phrase, or the site's Remote filter returned it AND the text mentions remote.
    One vague signal alone -> 'Not confirmed' (Leads tab only, never pitched)."""
    if page_tag in ("On-site", "Hybrid"):
        return page_tag
    d = (title + " " + desc).lower()
    rem = any(p in d for p in STRONG_REMOTE)
    on = any(p in d for p in STRONG_ONSITE) or bool(STRONG_ONSITE_RE.search(d))
    hy = any(p in d for p in STRONG_HYBRID) or (bool(re.search(r"\bhybrid\b", d)) and not rem) or \
        bool(re.search(r"\d\s*days?\s*(?:a|per)?\s*(?:week\s*)?(?:in|at|from)\s*(?:the|our)?\s*office", d))
    if hy:
        return "Hybrid"
    if on and not rem:
        return "On-site"
    loc = text(row.get("location"))
    loc_remote = "remote" in loc.lower() or str(row.get("is_remote")).lower() == "true" \
        and text(row.get("site")) != "linkedin"
    # LinkedIn lists remote jobs with a country or wide area ("United States"); on-site / hybrid jobs with a city
    first_part = loc.split(",")[0].strip().lower()
    state_wide = bool(re.fullmatch(r"(?:" + US_STATES + r")", first_part))       # "California, United States"
    city = bool(CITY_LOC_RE.search(loc)) and "remote" not in loc.lower() and not state_wide
    if page_tag == "Remote" or loc_remote or "remote" in title.lower():
        return "Remote"
    if TITLE_CITY_RE.search(title):
        return "On-site"                       # "Copywriter - NYC", "Data Analyst (Omaha, NE)"
    if city and rem:
        return "Remote"                        # a clear phrase ("fully remote") beats the office's city
    if city:
        return "On-site"                       # a city location without "Remote" in the title: treat as office job
    if rem or remote_hit:
        return "Remote"                        # wide location + clear remote phrase or LinkedIn's Remote filter
    if any(w in d for w in REMOTE_WORDS):
        return "Not confirmed"
    return "Not stated"


# "Fremont, CA", "Austin, Texas, United States", "San Francisco Bay Area": a city or metro, not country-wide
CITY_LOC_RE = re.compile(r"^[^,]+,\s*(?:[A-Z]{2}|[A-Z][a-z]+(?: [A-Z][a-z]+)?)(?:,\s*United States)?$|"
                         r"(?:metropolitan|metro|bay) area|greater .+ area", re.I)


TITLE_CITY_RE = re.compile(r"\([^)]*,\s*[A-Z]{2}\)|\b(?:nyc|new york city|los angeles|chicago|san francisco|boston|"
                           r"seattle|atlanta|dallas|houston|miami|denver|austin|omaha|philadelphia|phoenix|"
                           r"san diego|nashville|charlotte|portland|minneapolis|detroit)\b", re.I)


PAGE_TAG_CACHE = {}
PAGE_STATS = {"tries": 0, "found": 0, "status": {}}
PAGE_GIVE_UP_AFTER = 15          # if the first 15 pages show no tag, LinkedIn is hiding it: stop trying today


def page_workplace(job_id):
    """Reads the workplace type LinkedIn shows on the job's own public page: 'Remote', 'Hybrid', 'On-site' or ''."""
    if not job_id:
        return ""
    if job_id in PAGE_TAG_CACHE:
        return PAGE_TAG_CACHE[job_id]
    if PAGE_STATS["tries"] >= PAGE_GIVE_UP_AFTER and PAGE_STATS["found"] == 0:
        return ""
    PAGE_STATS["tries"] += 1
    tag = ""
    # 1) the job's own card (only this job, no "similar jobs" list that could mislead)
    try:
        r = requests.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}", headers=UA, timeout=20)
        PAGE_STATS["status"][r.status_code] = PAGE_STATS["status"].get(r.status_code, 0) + 1
        if r.status_code == 200:
            h = r.text
            m = re.search(r'(?i)(?:workplace_?types?|formattedWorkplaceType|workplaceType)\W{1,40}?'
                          r'(remote|hybrid|on-?site)', h)
            if m:
                tag = m.group(1)
            else:                                          # location line on the card: "London, England (On-site)"
                for line in re.findall(r'(?i)topcard__flavor[^>]*>\s*([^<]{0,200})', h):
                    m = re.search(r'(?i)\((remote|hybrid|on-?site)\)', line)
                    if m:
                        tag = m.group(1)
                        break
    except Exception:
        pass
    time.sleep(1.2)
    # 2) the full public page: Google-style job data marks remote jobs as TELECOMMUTE
    if not tag:
        try:
            r = requests.get(f"https://www.linkedin.com/jobs/view/{job_id}/", headers=UA, timeout=20)
            if r.status_code == 200:
                for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S):
                    if '"JobPosting"' in block and re.search(r'"jobLocationType"\s*:\s*"TELECOMMUTE"', block):
                        tag = "remote"
                        break
        except Exception:
            pass
        time.sleep(1.2)
    tag = {"remote": "Remote", "hybrid": "Hybrid"}.get(tag.lower(), "On-site" if tag else "")
    if tag:
        PAGE_STATS["found"] += 1
    PAGE_TAG_CACHE[job_id] = tag
    return tag


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


NOTE_LIMIT = 300   # LinkedIn Premium allows 300 characters in a connection note


def connection_note(company, role, category, skills, work):
    """Universal connection request for every role (same wording for all categories).
    Starts with "Hi," so outreach.py can add the person's first name once it is known."""
    if category == "Virtual Assistant":
        role = "assistant"           # keep "VA" wording out of outreach copy
    role = trim_role(role)
    first_word = role.split(" ")[0] if role else ""
    if first_word.isupper() and len(first_word) > 1:          # acronyms: "an SEO", "a CRM"
        a_role = ("an " if first_word[0] in "AEFHILMNORSX" else "a ") + role
    else:
        a_role = ("an " if role[:1].lower() in "aeiou" else "a ") + role
    company = clean_company(company)
    tail = (" Curious, have you found the right person out of the applicants yet, or still going through them?"
            " Thought it'd be good to connect!")
    note = f"Hi, noticed {company} is hiring for {a_role}.{tail}"
    if len(note) + 25 > NOTE_LIMIT:                            # room for a first name added later
        note = f"Hi, noticed you're hiring for {a_role}.{tail}"   # very long company names
    return note if len(note) <= NOTE_LIMIT else note[:NOTE_LIMIT - 3].rsplit(" ", 1)[0] + "..."


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
STAFF_COUNT = {}
WEBSITE_OF = {}                  # company -> website (for the staffing check)
COMPANY_HQ = {}                  # company page "Headquarters" text                 # people on LinkedIn for the company (splits 11-50 into 11-20 and 21-50)


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
            hq = re.search(r"Headquarters\s+(.{3,80}?)\s+(?:Type|Founded|Specialties|Company size|Locations)", plain)
            COMPANY_HQ[url] = hq.group(1).strip() if hq else ""
            cnt = re.search(r"(?:See all|View all)\s+([\d,]+)\s+employees", plain, re.I) or \
                re.search(r"([\d,]+)\s+employees on LinkedIn", plain, re.I)
            if cnt:
                STAFF_COUNT[url] = int(cnt.group(1).replace(",", ""))
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
    for wt in ("2", "3", "1"):
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
                    badges.setdefault(job_id, set()).add(WT_LABELS[wt])
            except Exception:
                break
            finally:
                time.sleep(1.5)
    return badges


def job_id_of(row):
    m = re.search(r"/jobs/view/(\d+)", text(row.get("job_url")))
    if m:
        return m.group(1)
    if text(row.get("site")) == "linkedin":
        m = re.search(r"(\d{6,})", text(row.get("id")))
        return m.group(1) if m else ""
    return text(row.get("site")) + ":" + (text(row.get("id")) or text(row.get("job_url")))


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


def search(term, n, location, remote=False, cat_name=""):
    """GHL / CRM: LinkedIn, Indeed, ZipRecruiter, Google Jobs, beBee and SimplyHired. Every other role: LinkedIn only.
    Each site on its own, so one failing site never stops the others."""
    frames = []
    sites = SITES if cat_name == "CRM Automation" else ["linkedin"]
    for site in sites:
        try:
            kw = dict(site_name=[site], search_term=term, location=location, results_wanted=n,
                      hours_old=HOURS_OLD, is_remote=remote)
            if site == "linkedin":
                kw["linkedin_fetch_description"] = True
            if site == "indeed":
                kw["country_indeed"] = "USA"
            if site == "google":
                kw["google_search_term"] = f"{term} {'remote ' if remote else ''}jobs in United States since yesterday"
            df = scrape_jobs(**kw)
            if df is not None and len(df):
                df = df.copy()
                df["site"] = site
                frames.append(df)
            print(f"  {site} | {term}{' | remote filter' if remote else ''}: {0 if df is None else len(df)} jobs")
        except Exception as e:
            print(f"  {site} | {term}: failed ({e})")
        finally:
            time.sleep(3)
    if cat_name == "CRM Automation" and not remote:
        extra = ghl_extra_sites(term)
        if extra is not None:
            frames.append(extra)
    if not frames:
        return None
    import pandas as pd
    return pd.concat(frames, ignore_index=True)


def ghl_extra_sites(term):
    """beBee and SimplyHired have no free feed: read their public search pages (job data embedded in the page).
    Best effort: if a site blocks us, the run continues and the log shows 0."""
    import pandas as pd
    from urllib.parse import quote
    rows = []
    pages = [("bebee", f"https://us.bebee.com/jobs?term={quote(term)}&location=United%20States"),
             ("simplyhired", f"https://www.simplyhired.com/search?q={quote(term)}&l=remote")]
    for site, url in pages:
        n = 0
        try:
            r = requests.get(url, headers=UA, timeout=30)
            html = r.text if r.status_code == 200 else ""
            jobs = []
            for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
                try:
                    data = json.loads(block)
                except Exception:
                    continue
                items = data if isinstance(data, list) else data.get("itemListElement", [data])
                for it in items:
                    it = it.get("item", it) if isinstance(it, dict) else {}
                    if it.get("@type") == "JobPosting":
                        jobs.append(it)
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
            if m:
                try:
                    props = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
                    for j in props.get("jobs", []) or []:
                        jobs.append({"title": j.get("title"), "hiringOrganization": {"name": j.get("company")},
                                     "description": j.get("snippet", ""), "url": "https://www.simplyhired.com/job/" +
                                     str(j.get("jobKey", "")), "jobLocation": j.get("location", ""),
                                     "datePosted": j.get("dateOnIndeed", "")})
                except Exception:
                    pass
            for j in jobs:
                org = j.get("hiringOrganization") or {}
                title, company = str(j.get("title") or ""), str(org.get("name") if isinstance(org, dict) else org or "")
                if not title or not company:
                    continue
                loc = json.dumps(j.get("jobLocation", "")) + " " + str(j.get("jobLocationType", ""))
                rows.append({"site": site, "id": f"{site}:{j.get('url') or title + company}",
                             "job_url": str(j.get("url") or url), "title": title, "company": company,
                             "description": re.sub(r"<[^>]+>", " ", str(j.get("description") or "")),
                             "location": "Remote" if re.search(r"remote|telecommute", loc, re.I) else loc[:80],
                             "date_posted": str(j.get("datePosted") or "")[:10], "company_url": "",
                             "is_remote": bool(re.search(r"remote|telecommute", loc, re.I)), "company_url_direct": ""})
                n += 1
        except Exception as e:
            print(f"  {site} | {term}: failed ({e})")
        print(f"  {site} | {term}: {n} jobs")
        time.sleep(3)
    return pd.DataFrame(rows) if rows else None


REMOTE_FILTER_IDS = set()
BOARD_SITES = {"remotive", "remoteok", "wwr", "himalayas", "jobicy"}
CRM_BOARD_RE = re.compile(r"go\s?high\s?level|\bhighlevel\b|\bghl\b|hubspot|\bcrm (?:specialist|admin|"
                          r"administrator|automation|manager|consultant|developer)|lead\s?connector", re.I)
US_STATES = ("alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|"
             "illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
             "mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|"
             "north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|"
             "south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming|"
             "district of columbia|united states|usa")
US_HQ_RE = re.compile(r"\b(?:" + US_STATES + r")\b|,\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|"
                      r"MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b",
                      re.I)
# Board location tags we can pitch: open to anyone (or to Asia / Pakistan)
OPEN_LOCATION_RE = re.compile(r"worldwide|anywhere|global|international|\bremote\b$|^remote$|asia|apac|pakistan|"
                              r"emea|any location|no restriction", re.I)


def us_company(company_url):
    """Remote boards list companies from everywhere: keep US ones (from the LinkedIn company page)."""
    hq = COMPANY_HQ.get(company_url.split("?")[0].rstrip("/"), "")
    return bool(hq and US_HQ_RE.search(hq))


def linkedin_company_guess(name):
    """Remote boards don't link LinkedIn: try the company page by name (the page check confirms the match)."""
    base = re.sub(r"[,.]?\s+(?:inc|llc|ltd|co|corp|corporation|company)\.?$", "", str(name or "").strip(), flags=re.I)
    slug_ = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return f"https://www.linkedin.com/company/{slug_}" if slug_ else ""


def linkedin_company_guesses(name):
    """Possible LinkedIn page names: 'Tiny Co' -> tiny-co, tiny, tinyco."""
    full = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    first = linkedin_company_guess(name).rsplit("/", 1)[-1]
    out = []
    for sl in (full, first, full.replace("-", "")):
        u = f"https://www.linkedin.com/company/{sl}"
        if sl and u not in out:
            out.append(u)
    return out[:3]


def board_row(site, jid, title, company, desc, url, loc, posted):
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(desc or "")))
    return {"site": site, "id": f"{site}:{jid}", "job_url": url, "title": str(title or "").strip(),
            "company": str(company or "").strip(), "description": plain, "location": "Remote",
            "board_location": str(loc or ""), "date_posted": posted, "company_url": linkedin_company_guess(company),
            "is_remote": True, "company_url_direct": ""}


def remote_boards():
    """Jobs from Remotive, Remote OK, We Work Remotely, Himalayas and Jobicy posted in the look-back window.
    Only jobs open to people outside the US (Worldwide / Anywhere / Asia); 'USA only' and other regions are skipped."""
    import pandas as pd
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=HOURS_OLD)
    rows, stats = [], {}

    def keep(r, when):
        try:
            ok_time = when is None or when >= cutoff
        except TypeError:
            ok_time = True
        loc = r["board_location"].strip()
        open_loc = (not loc) or bool(OPEN_LOCATION_RE.search(loc)) and not re.search(
            r"\b(?:us|usa|u\.s\.|united states|north america|americas|canada|europe|uk|latam)\b(?! ?\+)", loc, re.I) \
            or bool(re.search(r"worldwide|anywhere", loc, re.I))
        crm_job = bool(CRM_BOARD_RE.search(r["title"] + " " + r["description"]))
        if ok_time and open_loc and crm_job and r["title"] and r["company"]:
            if re.search(r"worldwide|anywhere|global|international|no restriction", loc, re.I):
                r["description"] += " work from anywhere"            # counts as an 'open worldwide' signal
            rows.append(r)
            stats[r["site"]] = stats.get(r["site"], 0) + 1

    def get(url):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            return r if r.status_code == 200 else None
        except Exception:
            return None

    def ts(v):
        try:
            if isinstance(v, (int, float)):
                return dt.datetime.fromtimestamp(v if v < 1e12 else v / 1000, dt.timezone.utc)
            v = str(v).replace("Z", "+00:00")
            d = dt.datetime.fromisoformat(v[:25]) if "T" in v else dt.datetime.fromisoformat(v[:10])
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except Exception:
            return None

    r = get("https://remotive.com/api/remote-jobs?limit=300")
    for j in (r.json().get("jobs", []) if r else []):
        keep(board_row("remotive", j.get("id"), j.get("title"), j.get("company_name"), j.get("description"),
                       j.get("url"), j.get("candidate_required_location"), str(j.get("publication_date"))[:10]),
             ts(j.get("publication_date")))
    r = get("https://remoteok.com/api")
    for j in ((r.json()[1:] if r and isinstance(r.json(), list) else [])):
        keep(board_row("remoteok", j.get("id"), j.get("position"), j.get("company"), j.get("description"),
                       j.get("url"), j.get("location") or "Worldwide", str(j.get("date"))[:10]), ts(j.get("date")))
    r = get("https://weworkremotely.com/remote-jobs.rss")
    if r:
        for item in re.findall(r"<item>(.*?)</item>", r.text, re.S):
            tag = lambda t: (re.search(rf"<{t}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>", item, re.S) or [None, ""])[1]
            head = tag("title")
            company, _, title = head.partition(":")
            when = None
            try:
                from email.utils import parsedate_to_datetime
                when = parsedate_to_datetime(tag("pubDate"))
            except Exception:
                pass
            keep(board_row("wwr", tag("guid") or tag("link"), title, company, tag("description"), tag("link"),
                           tag("region"), when.strftime("%Y-%m-%d") if when else ""), when)
    for offset in (0, 100):
        r = get(f"https://himalayas.app/jobs/api?limit=100&offset={offset}")
        for j in (r.json().get("jobs", []) if r else []):
            locs = j.get("locationRestrictions") or []
            loc = ", ".join(x if isinstance(x, str) else str(x.get("name", "")) for x in locs) or "Worldwide"
            when = ts(j.get("pubDate"))
            keep(board_row("himalayas", j.get("guid") or j.get("applicationLink"), j.get("title"), j.get("companyName"),
                           j.get("description") or j.get("excerpt"), j.get("applicationLink") or j.get("guid"), loc,
                           when.strftime("%Y-%m-%d") if when else ""), when)
    r = get("https://jobicy.com/api/v2/remote-jobs?count=100")
    for j in (r.json().get("jobs", []) if r else []):
        keep(board_row("jobicy", j.get("id"), j.get("jobTitle"), j.get("companyName"), j.get("jobDescription"),
                       j.get("url"), j.get("jobGeo"), str(j.get("pubDate"))[:10]), ts(j.get("pubDate")))
    print("Remote boards, jobs open worldwide in the window:", stats)
    return pd.DataFrame(rows) if rows else None
        # jobs LinkedIn returned for its Remote filter (one signal, not proof on its own)


def main():
    found = {}
    checked, ex_agency, ex_big, ex_lang, ex_incomplete, ex_loc = set(), set(), set(), set(), set(), set()
    ex_senior, ex_role = set(), set()                       # skip reasons, shown in the Run Log
    wt_checked, wt_confirmed = set(), set()                 # work type read from LinkedIn's job page
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
                    [(t, normal_n) for t in cfg["searches"]
                     if not is_senior(t) or crm_manager_title(t, cat_name)]   # "Manager" only for CRM

            if cat_name == "Remote Boards":
                terms = [("remote job boards", 0)]
            for location, short in rcfg["locations"]:
                passes = (True,) if cat_name == "Remote Boards" else (True, False)
                for term, n, remote_pass in [(t, k, rp) for t, k in terms for rp in passes]:
                    df = remote_boards() if cat_name == "Remote Boards" else \
                        search(term, n, location, remote_pass, cat_name)
                    if remote_pass and df is not None and len(df):
                        REMOTE_FILTER_IDS.update(job_id_of(r) for _, r in df.iterrows())
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

                        crm_mgr = is_senior(title) and crm_manager_title(title, cat_name)
                        if is_senior(title) and not crm_mgr:
                            ex_senior.add(key)
                            continue
                        if wrong_title(title, cat_name, desc):
                            ex_role.add(key)                             # in-store, off-target, intern, gig
                            continue
                        if text(row.get("site")) not in ("linkedin", "") and not (CRM_TITLE_RE.search(title) or
                                                                       GHL_RE.search(title)):
                            ex_role.add(key)
                            continue                                     # other sites: GHL / CRM titles only
                        if regexes and not any(r.search(title + " " + desc) for r in regexes):
                            continue
                        co_key = re.sub(r"\W+", "", company.lower())
                        if co_key in companies_today:
                            continue                                     # one row per company per day
                        if title_not_english(title) or not is_english(desc) or needs_other_language(desc):
                            ex_lang.add(key)
                            continue
                        if location_locked(title, text(row.get("location")), desc) or HARD_LOCAL_RE.search(desc):
                            ex_loc.add(key)                              # must live in the US, W-2, licensed...
                            continue
                        if is_agency(company, industry, desc, title):
                            ex_agency.add(key)
                            continue
                        company_url = text(row.get("company_url")).split("?")[0]
                        if text(row.get("site")) in BOARD_SITES:
                            for cand in linkedin_company_guesses(company):   # find the company's LinkedIn page
                                company_size(cand, row)
                                if COMPANY_HQ.get(cand):
                                    company_url = cand
                                    break
                        size_label, size_min, page_industry, website = company_size(company_url, row)
                        WEBSITE_OF[company] = website or text(row.get("company_url_direct"))
                        if is_agency(company, industry, desc, title):
                            ex_agency.add(key)
                            continue                                     # website shows a staffing firm
                        if size_min is not None and size_min > MAX_EMPLOYEES:
                            ex_big.add(key)
                            continue
                        if text(row.get("site")) in BOARD_SITES and not us_company(company_url):
                            ex_loc.add(key)                              # remote boards: US companies only
                            continue
                        if crm_mgr and (size_min is None or size_min > 50):
                            ex_senior.add(key)                           # CRM managers only at small companies
                            continue

                        p_label, p_rank = priority(title, desc, company, industry)
                        skills = key_skills(title, desc)
                        role = short_role(title)
                        # Work type: LinkedIn's own tags first, clear wording second
                        page_tag = page_workplace(job_id_of(row)) if text(row.get("site")) == "linkedin" else ""
                        wt_checked.add(key)
                        if page_tag:
                            wt_confirmed.add(key)
                        wtype = decide_work(row, title, desc, page_tag,
                                            remote_pass or job_id_of(row) in REMOTE_FILTER_IDS)
                        companies_today.add(co_key)
                        found[key] = {
                            "id": key,
                            "posted": posted,
                            "priority": p_label,
                            "priority_rank": p_rank,
                            "category": best_category(title, cat_name),
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
                            "website": website or text(row.get("company_url_direct")),
                            "worldwide": bool(WORLDWIDE_RE.search(desc)),
                            "tiny_clue": bool(TINY_CLUE_RE.search(desc)),
                            "staff_count": STAFF_COUNT.get(company_url.rstrip("/")),
                            "site": text(row.get("site")) or "linkedin",
                            "source": SOURCE_LABELS.get(text(row.get("site")) or "linkedin", "LinkedIn"),
                            "people": PEOPLE_CACHE.get(company_url.rstrip("/"), []),
                        }

    if not ran:
        print("This category or region is switched off. Nothing to do.")
        return

    leads = sorted(found.values(), key=lambda l: (
        l["priority_rank"], l["region_rank"], WORK_ORDER.get(l["work_type"], 9), l["posted"]))
    print(f"\nChecked {len(checked)} | agencies {len(ex_agency)} | big {len(ex_big)} | "
          f"non-English {len(ex_lang)} | location-locked {len(ex_loc)} | incomplete {len(ex_incomplete)} | "
          f"senior {len(ex_senior)} | wrong role {len(ex_role)} | "
          f"work type read from LinkedIn {len(wt_confirmed)}/{len(wt_checked)} "
          f"(page answers {PAGE_STATS['status']}) | "
          f"matches {len(leads)}")

    # Hand today's leads to the Phase 2 outreach step (GitHub passes this file along)
    os.makedirs("candidates", exist_ok=True)
    fname = re.sub(r"\W+", "_", (ONLY_CATEGORY or "all") + "_" + (ONLY_REGION or "all")) + ".json"
    with open(os.path.join("candidates", fname), "w") as fh:
        json.dump(leads, fh)

    payload = {"token": TOKEN, "category": ", ".join(ran), "leads": leads,
              "checked": len(checked), "excluded": len(ex_agency),
              "excluded_big": len(ex_big), "excluded_lang": len(ex_lang),
              "excluded_incomplete": len(ex_incomplete), "excluded_location": len(ex_loc),
              "skipped_senior": len(ex_senior), "skipped_role": len(ex_role),
              "wt_confirmed": len(wt_confirmed), "wt_checked": len(wt_checked),
              "wt_remote": sum(1 for l in leads if l.get("work_type") == "Remote")}
    # The 14 searches finish close together and the Sheet saves them one at a time: retry if it was busy
    # (safe: the Sheet never adds the same lead twice)
    r = None
    for attempt, wait in enumerate([0, 60, 120, 240]):
        if wait:
            print(f"Sheet busy, retrying in {wait}s (try {attempt + 1} of 4)")
            time.sleep(wait)
        try:
            r = requests.post(WEBAPP_URL, json=payload, timeout=240)
            if r.status_code == 200 and str(r.text).startswith("OK"):
                break
            print("Sheet answered:", str(r.text)[:200])
        except Exception as e:
            print("Sheet not reachable:", e)
    print("Sheet response:", (r.text[:300] if r is not None else "no answer after 4 tries"))


if __name__ == "__main__":
    main()
