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


def _extract_companies_from_article(article_url: str) -> list:
    """Follow article link and extract company websites from the article body."""
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
            companies = _extract_companies_from_article(link)
            combined_text = f"{title} {desc}"

            for co in companies:
                all_companies.append({
                    "company_name": co["name"],
                    "website": co["url"],
                    "vc_source": "",
                    "funding_stage": _infer_funding_stage(combined_text),
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

    # Deduplicate by domain
    seen = set()
    unique = []
    for co in all_companies:
        domain = urlparse(co["website"]).netloc
        if domain and domain not in seen:
            seen.add(domain)
            unique.append(co)

    print(f"  [NEWS] Total unique companies from news: {len(unique)}")
    return unique
