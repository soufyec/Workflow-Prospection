"""
Stage 1b: Discover companies from tech news RSS feeds.

Monitors funding announcements from multiple sources:
  - TechCrunch (Fundraising category)
  - EU-Startups
  - Silicon Canals (Benelux focus)
  - Tech.eu
  - Sifted (European tech)

For each funding article, follows the link and extracts company URLs
from the article body. Returns company records in the same format as vc_scraper.
"""

import re
import time
import random
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.utils.http_client import fetch_page, get_random_headers

NEWS_FEEDS = [
    {
        "name": "TechCrunch Fundraising",
        "url": "https://techcrunch.com/category/fundraising/feed/",
    },
    {
        "name": "EU-Startups",
        "url": "https://www.eu-startups.com/feed/",
    },
    {
        "name": "Silicon Canals",
        "url": "https://siliconcanals.com/feed/",
    },
    {
        "name": "Tech.eu",
        "url": "https://tech.eu/feed",
    },
    {
        "name": "Sifted",
        "url": "https://sifted.eu/feed",
    },
]

MAX_ARTICLES_PER_FEED = 10

FUNDING_KEYWORDS = [
    "raises", "raised", "funding", "series a", "series b", "series c",
    "seed round", "pre-seed", "investment", "million", "closes round",
    "secures", "backed by", "venture", "capital raise", "round led",
    "€", "$",
]

SOCIAL_DOMAINS = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "github.com", "medium.com", "crunchbase.com", "tiktok.com",
    "t.co", "bit.ly", "goo.gl", "ow.ly",
}

NEWS_DOMAINS = {
    "techcrunch.com", "eu-startups.com", "siliconcanals.com", "tech.eu",
    "sifted.eu", "bloomberg.com", "reuters.com", "wsj.com", "forbes.com",
    "venturebeat.com", "wired.com", "theverge.com", "thenextweb.com",
}

SKIP_URL_PATTERNS = re.compile(
    r"/cdn-cgi/|/wp-content/|/wp-includes/|/feed/"
    r"|#|/tag/|/category/|/author/|/page/|/search/"
    r"|\.pdf$|\.png$|\.jpg$|javascript:",
    re.IGNORECASE,
)


def _fetch_rss(url: str) -> str | None:
    """Fetch RSS feed XML directly (no delay needed for RSS)."""
    try:
        headers = get_random_headers()
        headers["Accept"] = "application/rss+xml, application/xml, text/xml, */*"
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except requests.RequestException as e:
        print(f"    [ERROR] RSS fetch failed: {e}")
    return None


def _is_funding_article(title: str, description: str) -> bool:
    text = f"{title} {description}".lower()
    return any(kw in text for kw in FUNDING_KEYWORDS)


def _infer_funding_stage(text: str) -> str:
    t = text.lower()
    if "series c" in t or "series d" in t or "series e" in t:
        return "growth"
    if "series b" in t:
        return "Series B"
    if "series a" in t:
        return "Series A"
    if "pre-seed" in t:
        return "pre-seed"
    if "seed" in t:
        return "seed"
    return "unknown"


def _infer_industry(text: str) -> str:
    t = text.lower()
    mapping = {
        "fintech": ["fintech", "financial technology", "banking", "payments", "insurtech", "neobank"],
        "healthtech": ["healthtech", "health tech", "medtech", "medical", "healthcare", "digital health"],
        "saas": ["saas", "software-as-a-service", "b2b software", "enterprise software", "b2b saas"],
        "ecommerce": ["e-commerce", "ecommerce", "retail tech", "d2c", "direct-to-consumer"],
        "cleantech": ["cleantech", "clean tech", "climate", "sustainability", "green energy", "carbon"],
        "edtech": ["edtech", "education", "learning platform", "e-learning"],
        "deeptech": ["deeptech", "deep tech", "quantum", "robotics"],
        "ai": ["artificial intelligence", " ai ", "machine learning", "generative ai", "llm"],
        "biotech": ["biotech", "biotechnology", "pharma", "life science", "genomics"],
        "proptech": ["proptech", "real estate tech", "property technology"],
        "foodtech": ["foodtech", "food tech", "agritech", "agtech", "agriculture"],
        "mobility": ["mobility", "autonomous", "electric vehicle", "logistics tech"],
        "cybersecurity": ["cybersecurity", "cyber security", "infosec", "security platform"],
        "hrtech": ["hrtech", "hr tech", "recruitment tech", "talent platform", "workforce"],
        "legaltech": ["legaltech", "legal tech", "regtech", "compliance"],
        "martech": ["martech", "marketing tech", "adtech", "advertising tech"],
        "constructiontech": ["construction tech", "contech", "built environment"],
        "spacetech": ["spacetech", "space tech", "satellite", "aerospace"],
    }
    for industry, keywords in mapping.items():
        if any(kw in t for kw in keywords):
            return industry
    return "tech"


_ACTION_VERBS = (
    r"(?:raises?|raised|secures?|secured|closes?|closed|lands?|gets?|receives?|announces?|bags?|wins?)"
)

# Pattern 1: "[Descriptor] [Company] raises €X" — captures just the company name after
# common descriptor words (e.g. "AI startup Flink raises €5M")
_DESCRIPTOR_PREFIX_RE = re.compile(
    r"^(?:[\w\-]+\s+){1,4}"           # 1-4 generic descriptor words
    r"(?P<name>[A-Z][A-Za-z0-9\-\.&']{1,30}(?:\s+[A-Z][A-Za-z0-9\-\.&']{1,20})?)"
    r"\s+" + _ACTION_VERBS,
    re.IGNORECASE,
)

# Pattern 2: "Company raises €X" — company name at the very start
_DIRECT_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z0-9\-\.&' ]{1,40}?)\s+" + _ACTION_VERBS + r"\s+[€$£\d]",
)

# Pattern 3: "Company closes seed/pre-seed round"
_SEED_ROUND_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z0-9\-\.&' ]{1,40}?)"
    r"\s+(?:closes?|raises?|secures?)\s+(?:\w+\s+)?(?:seed|pre-seed)\s+(?:round|funding|investment)",
    re.IGNORECASE,
)

_DESCRIPTOR_WORDS = frozenset([
    "startup", "startups", "company", "companies", "platform", "app", "service",
    "tool", "firm", "tech", "venture", "ai", "saas", "fintech", "healthtech",
    "new", "the", "a", "an", "eu", "european", "dutch", "german", "french",
    "speedy", "grocery", "ev", "software", "hardware",
])

_STOP_WORDS = frozenset([
    "the", "a", "an", "this", "that", "these", "those", "new", "how", "why",
    "when", "where", "what", "who", "which", "investors", "startup", "startups",
    "company", "companies", "funding", "round", "series",
])


def _clean_company_name(name: str) -> str:
    """Strip leading descriptor words from an extracted name."""
    words = name.strip().rstrip(".,;:").split()
    while words and words[0].lower() in _DESCRIPTOR_WORDS:
        words = words[1:]
    return " ".join(words)


def _extract_company_from_text(title: str, description: str) -> Optional[dict]:
    """
    Extract a company name from the RSS headline/description using regex patterns.
    Returns a minimal dict with 'name', or None if nothing found.
    """
    for text in (title, description):
        text = re.sub(r"<[^>]+>", " ", text).strip()  # strip any HTML tags

        # Try descriptor-prefix pattern first (e.g. "AI startup Flink raises")
        m = _DESCRIPTOR_PREFIX_RE.match(text)
        if m:
            name = _clean_company_name(m.group("name"))
            if name and 1 <= len(name.split()) <= 4 and name[0].isupper():
                return {"name": name}

        # Try seed-round pattern
        m = _SEED_ROUND_RE.match(text)
        if m:
            name = _clean_company_name(m.group("name"))
            if name and 1 <= len(name.split()) <= 4 and name[0].isupper():
                return {"name": name}

        # Try direct pattern
        m = _DIRECT_RE.match(text)
        if m:
            name = _clean_company_name(m.group("name"))
            words = name.split()
            if (
                1 <= len(words) <= 4
                and words[0][0].isupper()
                and words[0].lower() not in _STOP_WORDS
            ):
                return {"name": name}

    return None


def _extract_companies_from_article(article_url: str) -> list:
    """
    Follow article link and extract company websites from the article body.
    Returns a list of dicts with 'name', 'url', 'domain'.
    Falls back gracefully when the page is behind a paywall (returns []).
    """
    html = fetch_page(article_url)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    article_domain = urlparse(article_url).netloc.replace("www.", "")

    # Try to narrow to article body for better precision
    body = (
        soup.find("article")
        or soup.find("div", class_=re.compile(r"entry|content|article|post", re.I))
        or soup
    )

    candidates = []
    for a in body.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)

        if not href.startswith("http"):
            continue
        if SKIP_URL_PATTERNS.search(href):
            continue

        parsed = urlparse(href)
        domain = parsed.netloc.lower().replace("www.", "")

        if domain == article_domain:
            continue
        if any(sd in domain for sd in SOCIAL_DOMAINS):
            continue
        if any(nd in domain for nd in NEWS_DOMAINS):
            continue

        if text and 1 <= len(text.split()) <= 6 and len(text) > 2:
            candidates.append({
                "name": text,
                "url": f"{parsed.scheme}://{parsed.netloc}",
                "domain": domain,
            })

    # Deduplicate by domain
    seen = set()
    unique = []
    for c in candidates:
        if c["domain"] not in seen:
            seen.add(c["domain"])
            unique.append(c)

    return unique[:5]


def _parse_rss_items(xml_text: str) -> list:
    """Parse RSS 2.0 or Atom feed. Returns list of (title, description, link)."""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    # RSS 2.0: <rss><channel><item>
    for item in root.iter("item"):
        title = item.findtext("title", "")
        desc = item.findtext("description", "")
        link = item.findtext("link", "")
        items.append((title, desc, link))

    # Atom: <feed><entry>
    if not items:
        atom_ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.iter(f"{atom_ns}entry"):
            title = entry.findtext(f"{atom_ns}title", "")
            desc = entry.findtext(f"{atom_ns}summary", "")
            link_el = entry.find(f"{atom_ns}link[@rel='alternate']")
            if link_el is None:
                link_el = entry.find(f"{atom_ns}link")
            link = link_el.get("href", "") if link_el is not None else ""
            items.append((title, desc, link))

    return items


def discover_from_news(max_articles_per_feed: int = MAX_ARTICLES_PER_FEED) -> list:
    """
    Scrape tech news RSS feeds for startup funding announcements.
    Returns list of company dicts compatible with the pipeline.
    """
    all_companies = []

    for feed in NEWS_FEEDS:
        print(f"  [NEWS] {feed['name']}...")
        xml_text = _fetch_rss(feed["url"])
        if not xml_text:
            continue

        items = _parse_rss_items(xml_text)
        funding_articles = [
            (t, d, link) for t, d, link in items
            if _is_funding_article(t, d) and link
        ][:max_articles_per_feed]

        print(f"    → {len(funding_articles)} funding articles")

        for title, desc, link in funding_articles:
            combined_text = f"{title} {desc}"
            funding_stage = _infer_funding_stage(combined_text)

            # Try following the article link first (may fail on paywalled sites)
            companies = _extract_companies_from_article(link)

            # Fallback: extract company name directly from headline text
            if not companies:
                text_match = _extract_company_from_text(title, desc)
                if text_match:
                    companies = [{"name": text_match["name"], "url": "", "domain": ""}]

            for co in companies:
                all_companies.append({
                    "company_name": co["name"],
                    "website": co.get("url", ""),
                    "vc_source": "",
                    "funding_stage": funding_stage,
                    "industry": _infer_industry(combined_text),
                    "discovery_source": f"news:{feed['name']}",
                    "post_text": title[:300],
                    "stakeholder_email": None,
                    "stakeholder_name": None,
                    "email_source_url": None,
                    "email_subject": None,
                    "email_body": None,
                    "review_status": "pending",
                })

        time.sleep(random.uniform(1.0, 2.0))

    # Deduplicate by domain (or by company name when no website found)
    seen = set()
    unique = []
    for co in all_companies:
        domain = urlparse(co["website"]).netloc if co.get("website") else ""
        key = domain if domain else co["company_name"].lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(co)

    print(f"  [NEWS] Total unique companies from news: {len(unique)}")
    return unique
