import pymysql
import pymysql.cursors
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from typing import Dict, List, Tuple
import argparse
from datetime import datetime
from database import Company, Component, CompanyHsnJunction, CompanyNicJunction, ComponentAnalysis, ComponentMatch, engine, get_session
from mca_buyer_matcher import validate_and_normalize_hsn

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

BLOCKED_DOMAINS = [
    "zaubacorp.com", "tofler.in", "indiafilings.com", "tatanex.com",
    "tradeindia.com", "indiamart.com", "justdial.com", "ambitionbox.com",
    "glassdoor.co.in", "facebook.com", "linkedin.com", "instagram.com",
    "twitter.com", "youtube.com", "easycompany.in", "quickcompany.in",
    "mca.gov.in", "tofler.co.in", "signals.sh", "zoominfo.com", "apollo.io",
    "crisil.com", "creditrating.com", "startupindia.gov.in", "indiamart.co.in",
    "corporatedir.com", "company360.in", "companydetails.in", "falconebiz.com",
    "tracxn.com", "chambers.com", "wikipedia.org",
    "yahoo.com", "yahoo.co.in", "yimg.com", "search.yahoo", "thecompanycheck.com",
    "delvein.in", "delve-in.in", "paperli.ai", "paperli", "b2bhint.com", "checkbidx.com",
    "corpintel.io", "dubleu.co", "zauba.company"
]

BLOCKED_DOMAIN_HINTS = [
    "dnb", "compfilings", "tofler", "indiamart", "justdial", "zaubacorp",
    "tradeindia", "quickcompany", "company360", "companydetails", "falconebiz",
    "tracxn", "thecompanycheck", "apollo", "allindiaitr", "mastersindia",
    "filesure", "mycorporateinfo", "instafinancials", "registerkaro", "inspex",
    "planetexim", "wisebooks",
    "zoominfo", "crisil", "ambitionbox", "glassdoor", "linkedin", "facebook",
    "instagram", "youtube", "wikipedia", "yahoo", "corpintel", "dubleu", "zauba"
]

GENERIC_WORDS = {
    "TECHNOLOGY", "TECHNOLOGIES", "SOLUTION", "SOLUTIONS", "SYSTEM", "SYSTEMS",
    "INFRA", "INFRASTRUCTURE", "GLOBAL", "GROUP", "INDUSTRIES", "INDUSTRY",
    "ENGINEERING", "ENTERPRISE", "ENTERPRISES", "INTERNATIONAL", "ASSOCIATES",
    "VENTURES", "HOLDINGS", "SERVICES", "SERVICE", "NETWORK", "NETWORKS",
    "DEVELOPMENT", "COMMUNICATIONS", "DIGITAL", "SOFTWARE", "INNOVATIONS",
    "INNOVATION", "ELECTRONICS", "ELECTRONIC", "ELECTRICAL", "ELECTRICALS"
}

CONTACT_HINTS = ["contact", "contact-us", "contactus", "reach-us", "get-in-touch", "support", "help"]
ABOUT_HINTS = ["about", "about-us", "aboutus", "company-profile", "profile", "who-we-are"]
PRODUCT_HINTS = ["product", "products", "solutions", "services", "catalog", "catalogue", "portfolio", "offerings"]

def load_env():
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

def safe_json_loads(val):
    if val is None:
        return []
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return []

from config import get_groq_client, get_llm_client_and_model

# Text and html cleaning utilities
def normalize_text(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def html_decode(text: str) -> str:
    entities = {
        "&quot;": '"',
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&nbsp;": " ",
        "&#39;": "'",
        "&apos;": "'",
    }
    for ent, val in entities.items():
        text = text.replace(ent, val)
    return text

class HTMLCleaner(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text_parts = []
        self.ignore_stack = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "head", "noscript", "iframe", "svg", "select", "form", "header", "nav", "footer", "aside"}:
            self.ignore_stack.append(tag.lower())
        
    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "head", "noscript", "iframe", "svg", "select", "form", "header", "nav", "footer", "aside"}:
            if self.ignore_stack and self.ignore_stack[-1] == tag.lower():
                self.ignore_stack.pop()
        if tag.lower() in {"p", "div", "section", "article", "tr", "td", "th", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "br", "option", "a", "span", "strong", "em", "button"}:
            self.text_parts.append(" ")

    def handle_data(self, data):
        if not self.ignore_stack:
            self.text_parts.append(data)

def clean_html_tags(text: str) -> str:
    if not text:
        return ""
    if "<" not in text:
        return html_decode(text)
    
    cleaner = HTMLCleaner()
    try:
        cleaner.feed(text)
        cleaned = "".join(cleaner.text_parts)
    except Exception:
        cleaned = re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<style.*?</style>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<.*?>", "", cleaned, flags=re.DOTALL)
        cleaned = html_decode(cleaned)
        
    return re.sub(r"\s+", " ", cleaned).strip()

def get_domain(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc.lower().replace("www.", "")
    except Exception:
        return ""

def is_blocked_domain(url: str) -> bool:
    domain = get_domain(url)
    if not domain:
        return True
    if any(blocked in domain for blocked in BLOCKED_DOMAINS):
        return True
    if any(hint in domain for hint in BLOCKED_DOMAIN_HINTS):
        return True
    return False

def tokenize_company_name(company_name: str) -> List[str]:
    tokens = re.findall(r"[A-Z0-9]+", (company_name or "").upper())
    stop_words = {"PRIVATE", "LIMITED", "LTD", "PVT", "LLP", "LLC", "AND", "THE", "OF", "INDIA", "INDIAN"}
    return [tok for tok in tokens if tok not in stop_words and len(tok) >= 3]

def score_official_candidate(company_name: str, url: str, title: str = "", snippet: str = "") -> int:
    if not url or is_blocked_domain(url):
        return -100

    domain = get_domain(url)
    company_tokens = tokenize_company_name(company_name)
    score = 0

    if domain.endswith((".in", ".co", ".com")):
        score += 2

    domain_matched = False
    strong_match = False
    for token in company_tokens:
        tok_lower = token.lower()
        if tok_lower in domain:
            score += 6
            domain_matched = True
            if token not in GENERIC_WORDS:
                strong_match = True
        if tok_lower in title.lower():
            score += 3
        if tok_lower in snippet.lower():
            score += 1

    if not domain_matched:
        score -= 20
    else:
        has_strong_tokens = any(tok not in GENERIC_WORDS for tok in company_tokens)
        if has_strong_tokens and not strong_match:
            score -= 15

    if any(word in title.lower() for word in ["official", "manufacturer", "manufacturing", "solutions", "technologies", "systems"]):
        score += 2
    if any(word in snippet.lower() for word in ["manufacturer", "manufacturing", "solutions", "products", "services", "contact us"]):
        score += 1

    tld = domain.split(".")[-1]
    foreign_cctlds = {"uk", "us", "ca", "au", "nz", "sg", "my", "de", "fr", "it", "jp", "cn", "ru", "za"}
    if tld in foreign_cctlds or domain.endswith((".co.uk", ".com.au", ".co.nz", ".co.za")):
        score -= 15

    return score

def extract_page_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return normalize_text(clean_html_tags(match.group(1))) if match else ""

def extract_meta_content(html: str, names: List[str], prop_names: List[str] = None) -> List[str]:
    prop_names = prop_names or []
    values = []
    for name in names:
        values.extend(re.findall(rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']', html, flags=re.IGNORECASE))
        values.extend(re.findall(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']{re.escape(name)}["\']', html, flags=re.IGNORECASE))
    for prop in prop_names:
        values.extend(re.findall(rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']', html, flags=re.IGNORECASE))
        values.extend(re.findall(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(prop)}["\']', html, flags=re.IGNORECASE))

    cleaned = []
    for value in values:
        value = normalize_text(value)
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned

def extract_links(html: str, base_url: str) -> List[str]:
    links = []
    for anchor in re.findall(r"<a\b[^>]*>", html, re.IGNORECASE):
        href_match = re.search(r'href=[\'"]([^\'"]+)[\'"]', anchor, re.IGNORECASE)
        if not href_match:
            href_match = re.search(r'href=([^\s>]+)', anchor, re.IGNORECASE)
            
        if href_match:
            href = href_match.group(1).strip()
            if href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            if re.search(r"\.(css|js|xml|json|png|jpg|jpeg|gif|webp|svg|pdf|zip|ico)(?:\?|$)", href.lower()):
                continue
                
            lower_href = href.lower()
            if any(p in lower_href for p in ["/wp-json/", "xmlrpc.php", "/wp-content/", "/wp-includes/", "format=xml", "format=json", "/oembed/", "feed/"]):
                continue
                
            absolute = urllib.parse.urljoin(base_url, href)
            if absolute not in links:
                links.append(absolute)
    return links

def is_valid_email(email: str) -> bool:
    email = email.lower().strip()
    blocked_email_domains = {
        "rfuenzalida.com", "impallari.com", "pixelspread.com", "example.com", 
        "bootstrap.com", "w3schools.com", "w3.org", "domain.com", "yourdomain.com",
        "email.com", "test.com", "website.com", "gmail.com.co", "github.com",
        "wordpress.org", "wordpress.com", "templatemonster.com", "wix.com", "pixelspread.co.in"
    }
    parts = email.split("@")
    if len(parts) != 2:
        return False
    email_user, email_domain = parts
    if email_domain in blocked_email_domains:
        return False
    blocked_usernames = {"info@bootstrap", "hello@template", "support@yourdomain"}
    if any(b in email for b in blocked_usernames):
        return False
    return True

def filter_generic_emails_if_custom_exists(emails: List[str], base_url: str) -> List[str]:
    domain = get_domain(base_url)
    if not domain:
        return emails
    has_custom = any(email.split("@")[-1] == domain for email in emails if "@" in email)
    if not has_custom:
        return emails
    generic_domains = {"gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com", "rediffmail.com"}
    return [email for email in emails if email.split("@")[-1] not in generic_domains]

def extract_emails(html: str, text: str) -> List[str]:
    candidates = set()
    for source in [html, text]:
        for email in re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", source or ""):
            email = email.lower().strip()
            if email.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")):
                continue
            if is_valid_email(email):
                candidates.add(email)
    return sorted(candidates)

def extract_phones(text: str) -> List[str]:
    candidates = set()
    if not text:
        return []
        
    for match in re.finditer(r"(?:\+91[\-\s]?)?[6-9]\d{9}\b|\b\d{3,5}[\-\s]\d{6,8}\b", text):
        phone_str = match.group()
        phone_clean = phone_str.replace(" ", "").replace("-", "").strip()
        
        if len(phone_clean) >= 10:
            if "+91" in phone_str:
                candidates.add(phone_clean)
                continue
                
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 50)
            context = text[start:end].lower()
            
            keywords = ["phone", "ph", "mob", "call", "tel", "contact", "whatsapp", "+91"]
            if any(kw in context for kw in keywords):
                candidates.add(phone_clean)
                
    return sorted(candidates)

def extract_social_links(links: List[str]) -> List[str]:
    social = []
    social_hints = ["linkedin.com", "facebook.com", "instagram.com", "youtube.com", "x.com", "twitter.com"]
    for link in links:
        lower = link.lower()
        if any(hint in lower for hint in social_hints) and link not in social:
            social.append(link)
    return social

INDIAN_STATES_AND_CITIES = {
    "delhi", "new delhi", "mumbai", "bombay", "bangalore", "bengaluru", "chennai", "madras", 
    "kolkata", "calcutta", "hyderabad", "pune", "ahmedabad", "surat", "jaipur", "lucknow", 
    "kanpur", "nagpur", "indore", "thane", "bhopal", "visakhapatnam", "patna", "vadodara", 
    "ghaziabad", "ludhiana", "agra", "nashik", "faridabad", "meerut", "rajkot", "varanasi", 
    "srinagar", "amritsar", "navi mumbai", "allahabad", "ranchi", "howrah", "coimbatore", 
    "jabalpur", "gwalior", "vijayawada", "jodhpur", "madurai", "raipur", "kota", "chandigarh", 
    "guwahati", "solapur", "hubli", "dharwad", "bareilly", "moradabad", "mysore", "gurgaon", 
    "gurugram", "noida", "aligarh", "jalandhar", "tiruchirappalli", "bhuneshwar", "bhubaneswar", 
    "dehradun", "jammu", "panaji", "goa", "maharashtra", "karnataka", "tamil nadu", "gujarat", 
    "uttar pradesh", "west bengal", "rajasthan", "haryana", "punjab", "bihar", "madhya pradesh", 
    "andhra pradesh", "telangana", "odisha", "kerala", "assam", "uttarakhand", "jharkhand", 
    "chhattisgarh", "himachal pradesh", "tripura", "meghalaya", "manipur", "nagaland", 
    "arunachal pradesh", "mizoram", "sikkim"
}

def extract_addresses(text: str) -> List[str]:
    candidates = []
    normalized_text = text or ""
    chunks = re.split(r"[\n\r;]|\s{3,}", normalized_text)
    
    exclude_keywords = {
        "sign in", "sign up", "log in", "register", "cart", "checkout", "javascript", 
        "browser", "cookie", "privacy policy", "terms of", "all rights reserved", 
        "copyright", "subscribe", "newsletter", "search", "loading", "button", 
        "carousel", "slideshow", "click here", "hover", "menu", "iframe"
    }

    for chunk in chunks:
        cleaned = normalize_text(chunk)
        if not cleaned or len(cleaned) < 20 or len(cleaned) > 250:
            continue
            
        lowered = cleaned.lower()
        if any(ek in lowered for ek in exclude_keywords):
            continue
        if "@" in lowered or "http:" in lowered or "https:" in lowered or "www." in lowered:
            continue
            
        has_pincode = bool(re.search(r"\b[1-9]\d{5}\b", cleaned))
        has_location = any(loc in lowered for loc in INDIAN_STATES_AND_CITIES)
        
        address_keywords = ["road", "street", "sector", "phase", "plot", "building", "industrial", 
                            "village", "district", "nagar", "park", "estate", "office", "floor", 
                            "block", "avenue", "bazar", "marg", "complex", "lane", "chowk", "bypass"]
        keyword_count = sum(1 for kw in address_keywords if kw in lowered)
        
        if (has_pincode and (has_location or keyword_count >= 1)) or (has_location and keyword_count >= 2):
            cleaned_address = cleaned.strip(",. \t")
            if cleaned_address not in candidates:
                candidates.append(cleaned_address)
                
    return candidates[:5]

def extract_product_signals(text: str) -> List[str]:
    signals = []
    sentences = re.split(r"(?<=[.!?])\s+", text or "")
    keywords = [
        "manufactur", "product", "solution", "service", "design", "supply", "assembly", 
        "electronics", "embedded", "hardware", "software", "solar", "battery", "led", "iot", 
        "automation", "telecom", "network", "ai cluster", "gpu server", "in-memory database", 
        "sap hana", "cloud virtualization", "edge computing node", "telecom core", 
        "vfx workstation", "system integration"
    ]
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            cleaned = normalize_text(sentence)
            if cleaned and cleaned not in signals:
                signals.append(cleaned)
        if len(signals) >= 8:
            break
    return signals

def extract_main_content(html: str) -> str:
    if not html:
        return ""
    
    content = html
    tags_to_remove = [
        r"<script[\s\S]*?</script>",
        r"<style[\s\S]*?</style>",
        r"<head[\s\S]*?</head>",
        r"<header[\s\S]*?</header>",
        r"<nav[\s\S]*?</nav>",
        r"<footer[\s\S]*?</footer>",
        r"<aside[\s\S]*?</aside>",
        r"<form[\s\S]*?</form>",
        r"<iframe[\s\S]*?>.*?</iframe>",
        r"<select[\s\S]*?</select>",
        r"<noscript[\s\S]*?</noscript>"
    ]
    for tag in tags_to_remove:
        content = re.sub(tag, "", content, flags=re.IGNORECASE)
    
    block_pattern = r"<(p|div|section|article|td|li|tr)[^>]*>([\s\S]*?)</\1>"
    blocks = re.findall(block_pattern, content, re.IGNORECASE)
    
    retained_blocks = []
    for tag_name, block_html in blocks:
        block_text = clean_html_tags(block_html)
        if len(block_text) < 30:
            continue
            
        links_text = "".join(re.findall(r"<a[^>]*>([\s\S]*?)</a>", block_html, re.IGNORECASE))
        links_text_clean = clean_html_tags(links_text)
        
        total_len = len(block_text)
        link_len = len(links_text_clean)
        link_density = link_len / total_len if total_len > 0 else 0
        
        if link_density > 0.4:
            continue
            
        retained_blocks.append(block_text)
        
    if not retained_blocks:
        return clean_html_tags(content)
        
    unique_blocks = []
    for block in retained_blocks:
        is_sub = False
        for existing in unique_blocks:
            if block in existing:
                is_sub = True
                break
        if is_sub:
            continue
        unique_blocks = [existing for existing in unique_blocks if existing not in block]
        unique_blocks.append(block)
        
    return "\n\n".join(unique_blocks)

def is_generic_title(title: str, company_name: str = "") -> bool:
    t = (title or "").lower().strip()
    if not t or len(t) < 3:
        return True
    t = re.sub(r"[^\w\s]", "", t).strip()
    generics = {
        "home", "welcome", "about us", "about", "contact us", "contact", 
        "privacy policy", "privacy", "terms of use", "terms", "services", 
        "our services", "our industry", "assets", "gallery", "careers", 
        "sitemap", "disclaimer", "cookie policy", "legal", "infrastructure",
        "facilities", "news", "events", "blog", "ems",
        "sign in", "sign up", "log in", "login", "register", "cart", "checkout",
        "my account", "account", "your cart is empty", "search", "menu",
        "add to cart", "wishlist", "shop", "all products", "buy now", "quick view",
        "quantity", "select options", "view cart", "subscribe", "newsletter", "close",
        "filter", "sort", "options", "reviews", "next", "previous", "submit", "button",
        "collection", "category", "collections", "categories", "all collections", "all categories",
        "view all", "sort by", "products list", "services list", "our list", "no products"
    }
    if t in generics:
        return True
    if any(g in t for g in ["cart", "add to cart", "sign in to", "collection", "category", "policy", "terms", "shipping", "refund", "returns", "faq", "not found", "404", "error", "warranty", "delivery", "replacement", "payment", "pricing", "price"]):
        return True
        
    if company_name:
        clean_company = re.sub(r"[^\w\s]", "", company_name.lower()).strip()
        clean_company = re.sub(r"\b(pvt|ltd|limited|private|llp|inc|corp|co)\b", "", clean_company).strip()
        
        words = clean_company.split()
        t_words = t.split()
        non_company_words = [w for w in t_words if w not in words and w not in ["welcome", "to", "our", "the", "website", "ems"]]
        if not non_company_words:
            return True
            
    return False

def is_valid_product_name(name: str, company_name: str = "") -> bool:
    if not name or len(name) < 3:
        return False
    if "<" in name or ">" in name or "class=" in name or "id=" in name or "style=" in name or "href=" in name:
        return False
    if "@" in name or "http:" in name or "https:" in name or "www." in name or ".com" in name:
        return False
        
    lowname = name.lower().strip()
    invalid_starters = ["our ", "we ", "to ", "welcome ", "about ", "why ", "how ", "partner ", "established ", "founded ", "the ", "with ", "for ", "by ", "in "]
    if any(lowname.startswith(starter) for starter in invalid_starters):
        return False
        
    if name.endswith("..."):
        return False
    if "?" in name or any(k in lowname for k in ["get in touch", "contact us", "contact our", "read more", "click here", "call us", "request a"]):
        return False
    if re.search(r"[a-z][A-Z]", name):
        return False
    if lowname in ["dell", "hp", "lenovo", "samsung", "apple", "asus", "acer"]:
        return False
    if is_generic_title(name, company_name):
        return False
    return True

def is_machinery_spec(key: str, val: str) -> bool:
    k = (key or "").lower()
    v = (val or "").lower()
    machinery_keywords = [
        "squeegee", "heating zone", "cooling zone", "printing speed", 
        "printing accuracy", "feeder capacity", "placement speed", 
        "reflow", "stencil printer", "pcb load", "screen frame", 
        "mount speed", "wave solder"
    ]
    return any(kw in k or kw in v for kw in machinery_keywords)

def strip_boilerplate(html: str) -> str:
    patterns = [
        r"<header[\s\S]*?</header>",
        r"<nav[\s\S]*?</nav>",
        r"<footer[\s\S]*?</footer>",
        r"<aside[\s\S]*?</aside>",
        r"<div[^>]+class=[\'\"][^\'\"]*(?:site[-_]?header|site[-_]?footer|cookie[-_]?banner|navbar|main[-_]?nav|menu|navigation|footer[-_]?menu|header[-_]?menu|widget|social[-_]?links|sub[-_]?menu|dropdown[-_]?menu|sidebar|popup|modal)[^\'\"]*[\'\"][\s\S]*?</div>",
        r"<div[^>]+id=[\'\"][^\'\"]*(?:site[-_]?header|site[-_]?footer|cookie[-_]?banner|navbar|main[-_]?nav|menu|navigation|footer[-_]?menu|header[-_]?menu|widget|social[-_]?links|sub[-_]?menu|dropdown[-_]?menu|sidebar|popup|modal)[^\'\"]*[\'\"][\s\S]*?</div>",
        r"<ul[^>]+(?:class|id)=[\'\"][^\'\"]*(?:menu|nav|navigation|dropdown|sub[-_]?menu)[^\'\"]*[\'\"][\s\S]*?</ul>"
    ]
    cleaned = html
    for pat in patterns:
        try:
            cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
        except Exception:
            continue
    return cleaned

def classify_type(name: str, desc: str) -> str:
    service_keywords = [
        "cabling", "installation", "assembly", "consulting", "service", 
        "maintenance", "integration", "contracting", "engineering", 
        "design", "management", "support", "solutions for", "repair", "mro"
    ]
    text = (name + " " + desc).lower()
    if any(kw in text for kw in service_keywords):
        return "service"
    return "product"

def parse_product_signal(sentence: str) -> Dict[str, str]:
    s = normalize_text(sentence)
    parts = re.split(r"\s*[:\-–—|]\s*", s, maxsplit=1)
    name = parts[0]
    desc = parts[1] if len(parts) > 1 else s
    name_words = name.split()
    if len(name_words) > 8:
        name = " ".join(name_words[:8]) + "..."
    type_val = classify_type(name, desc)
    return {"name": name, "description": desc, "type": type_val}

def is_product_link(url: str) -> bool:
    lower = (url or "").lower()
    product_patterns = ["/product", "/products/", "/product-", "/item/", "/shop/", "/catalog", "/catalogue", "/p/", "?product="]
    return any(pat in lower for pat in product_patterns)

def extract_product_page(html: str, url: str) -> object:
    sub_items = []
    
    bullet_pattern = r"<li[^>]*>\s*<(?:strong|b)[^>]*>(.*?)</\s*(?:strong|b)>\s*[:\-–—]?\s*(.*?)\s*</li>"
    for m_name, m_desc in re.findall(bullet_pattern, html, re.IGNORECASE | re.DOTALL):
        clean_name = normalize_text(clean_html_tags(m_name))
        clean_desc = normalize_text(clean_html_tags(m_desc))
        if len(clean_name) >= 3 and len(clean_name) < 100 and clean_desc and not is_generic_title(clean_name):
            sub_items.append({
                "name": clean_name,
                "description": clean_desc,
                "specs": [],
                "type": classify_type(clean_name, clean_desc)
            })

    header_para_pattern = r"<(h3|h4)[^>]*>(.*?)</\1>\s*<p[^>]*>(.*?)</p>"
    for tag, m_name, m_desc in re.findall(header_para_pattern, html, re.IGNORECASE | re.DOTALL):
        clean_name = normalize_text(clean_html_tags(m_name))
        clean_desc = normalize_text(clean_html_tags(m_desc))
        if len(clean_name) >= 3 and len(clean_name) < 100 and len(clean_desc) >= 20 and not is_generic_title(clean_name):
            sub_items.append({
                "name": clean_name,
                "description": clean_desc,
                "specs": [],
                "type": classify_type(clean_name, clean_desc)
            })
            
    if sub_items:
        return sub_items

    title = ""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = normalize_text(clean_html_tags(m.group(1)))
    else:
        m = re.search(r"<h2[^>]*>(.*?)</h2>", html, re.IGNORECASE | re.DOTALL)
        if m:
            title = normalize_text(clean_html_tags(m.group(1)))

    meta = extract_meta_content(html, ["description", "og:description", "twitter:description"])
    desc = meta[0] if meta else ""
    if not desc:
        m = re.search(r"<div[^>]+class=[\'\"][^\'\"]*(description|product-desc|product-description|prod-desc)[^\'\"]*[\'\"][^>]*>(.*?)</div>", html, re.IGNORECASE | re.DOTALL)
        if m:
            desc = normalize_text(clean_html_tags(m.group(2)))

    if not desc:
        txt = clean_html_tags(html)
        desc = normalize_text(txt[:600])

    specs = []
    for tab in re.findall(r"<table.*?</table>", html, re.IGNORECASE | re.DOTALL):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tab, re.IGNORECASE | re.DOTALL)
        for row in rows:
            cols = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.IGNORECASE | re.DOTALL)
            if len(cols) >= 2:
                k = normalize_text(clean_html_tags(cols[0]))
                v = normalize_text(clean_html_tags(cols[1]))
                if k and v:
                    if len(k) > 2 and len(v) > 1 and not re.search(r"https?://|\.(png|jpg|jpeg|gif|webp|svg|css|js|ico)", k + v, re.IGNORECASE):
                        if len(k) <= 200 and len(v) <= 200:
                            specs.append({"key": k, "value": v})

    if not specs:
        for ul in re.findall(r"<(?:ul|ol)[^>]*>(.*?)</(?:ul|ol)>", html, re.IGNORECASE | re.DOTALL):
            for li in re.findall(r"<li[^>]*>(.*?)</li>", ul, re.IGNORECASE | re.DOTALL):
                txt = normalize_text(clean_html_tags(li))
                if ":" in txt:
                    k, v = [p.strip() for p in txt.split(":", 1)]
                    k = normalize_text(k)
                    v = normalize_text(v)
                    if k and v and len(k) <= 200 and len(v) <= 200 and not re.search(r"https?://|\.(png|jpg|jpeg|gif|webp|svg|css|js|ico)", k + v, re.IGNORECASE):
                        specs.append({"key": k, "value": v})

    p_name = title or normalize_text(clean_html_tags(extract_page_title(html) or url))
    return {
        "name": p_name,
        "description": desc,
        "specs": specs,
        "type": classify_type(p_name, desc)
    }

def fetch_html_playwright(url: str, timeout: int = 15000) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[-] Playwright sync API is not installed. Skipping rendering fallback.")
        return ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        print(f"[-] Playwright rendering failed for {url}: {e}")
        return ""

def fetch_html(url: str, timeout: int = 12, proxy_url: str = None) -> Tuple[str, str]:
    import gzip
    import zlib
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Encoding": "gzip, deflate",
    }
    html_content = ""
    fetched_url = url
    
    def read_and_decompress(resp) -> str:
        encoding = resp.info().get("Content-Encoding", "").lower()
        raw = resp.read()
        if "gzip" in encoding:
            try:
                return gzip.decompress(raw).decode("utf-8", errors="ignore")
            except Exception:
                pass
        elif "deflate" in encoding:
            try:
                return zlib.decompress(raw).decode("utf-8", errors="ignore")
            except Exception:
                pass
        return raw.decode("utf-8", errors="ignore")

    try:
        req = urllib.request.Request(url, headers=headers)
        if proxy_url:
            proxy_support = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
            opener = urllib.request.build_opener(proxy_support)
        else:
            opener = urllib.request.build_opener()
            
        with opener.open(req, timeout=timeout) as response:
            html_content = read_and_decompress(response)
            fetched_url = response.geturl()
    except Exception:
        if url.startswith("https"):
            alt_url = url.replace("https", "http", 1)
            try:
                req = urllib.request.Request(alt_url, headers=headers)
                if proxy_url:
                    proxy_support = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
                    opener = urllib.request.build_opener(proxy_support)
                else:
                    opener = urllib.request.build_opener()
                with opener.open(req, timeout=timeout) as response:
                    html_content = read_and_decompress(response)
                    fetched_url = response.geturl()
            except Exception:
                pass

    is_js_template = False
    if html_content:
        lower_content = html_content.lower()
        if "enable javascript" in lower_content or "you need to enable javascript" in lower_content:
            is_js_template = True
        elif "noscript" in lower_content and len(html_content) < 4000:
            is_js_template = True
        elif len(html_content) < 1500 and "window.location" in lower_content:
            is_js_template = True
        elif len(html_content) < 800 and "<script" in lower_content:
            is_js_template = True

    if (not html_content or is_js_template):
        rendered = fetch_html_playwright(url, timeout=timeout * 1000)
        if rendered:
            return rendered, url

    return html_content, fetched_url

def clean_company_name(company_name: str) -> str:
    clean_name = company_name.strip().upper()
    suffixes = [
        " PRIVATE LIMITED", " PRIVATELIMITED", " PVT LTD", " PVT. LTD.", 
        " LIMITED", " LTD", " LLP", " LLC", " INC", " CORP", " CORPORATION"
    ]
    for s in suffixes:
        if clean_name.endswith(s):
            clean_name = clean_name[:-len(s)].strip()
            break
    return clean_name

def find_website_via_yahoo(company_name: str) -> str:
    clean_name = clean_company_name(company_name)
    url = "https://search.yahoo.com/search?" + urllib.parse.urlencode({"q": clean_name})
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            html = res.read().decode("utf-8", errors="ignore")

        hrefs = re.findall(r'href="([^"]+)"', html)
        best_url = "N/A"
        best_score = -999
        for href in hrefs:
            if "r.search.yahoo.com" not in href or "/RU=" not in href:
                continue
            try:
                ru_part = href.split("/RU=")[1].split("/RK=")[0]
                target_url = urllib.parse.unquote(ru_part)
                parsed_url = urllib.parse.urlparse(target_url)
                if parsed_url.scheme not in ["http", "https"]:
                    continue
                candidate_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                score = score_official_candidate(company_name, candidate_url, title=parsed_url.netloc)
                if score > best_score:
                    best_score = score
                    best_url = candidate_url
            except Exception:
                continue
        if best_url != "N/A" and best_score >= 0:
            return best_url
        return "N/A"
    except Exception:
        return "N/A"

def find_official_website(company_name: str) -> str:
    clean_name = clean_company_name(company_name)
    url = "https://lite.duckduckgo.com/lite/"
    payload = urllib.parse.urlencode({"q": clean_name}).encode("utf-8")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html_content = response.read().decode("utf-8", errors="ignore")

        if "bots use DuckDuckGo too" not in html_content:
            link_matches = re.findall(r'<a[^>]+class=[\'\"]result-link[\'\"][^>]+href="([^"]+)"[^>]*>(.*?)</a>', html_content, re.DOTALL)
            if not link_matches:
                link_matches = re.findall(r'<a[^>]+href="([^"]+)"[^+]+class=[\'\"]result-link[\'\"]*>(.*?)</a>', html_content, re.DOTALL)

            best_url = "N/A"
            best_score = -999
            for href, title in link_matches:
                if "/lite/o/?u=" in href:
                    href = urllib.parse.unquote(href.split("/lite/o/?u=")[1].split("&")[0])

                parsed_url = urllib.parse.urlparse(href)
                if parsed_url.scheme not in ["http", "https"]:
                    continue
                candidate_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                score = score_official_candidate(company_name, candidate_url, title=clean_html_tags(title))
                if score > best_score:
                    best_score = score
                    best_url = candidate_url

            if best_url != "N/A" and best_score >= 1:
                return best_url
    except Exception:
        pass

    return find_website_via_yahoo(company_name)

def search_yahoo_playwright(query: str):
    from playwright.sync_api import sync_playwright
    print(f"[+] Querying Yahoo Search via Playwright for: {query}...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        url = f"https://search.yahoo.com/search?p={urllib.parse.quote(query)}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            try:
                page.wait_for_selector("div#web", timeout=6000)
            except Exception:
                pass
            
            results = page.evaluate("""() => {
                const items = [];
                const snippets = document.querySelectorAll('div.compText');
                snippets.forEach(snippetEl => {
                    let parent = snippetEl.parentElement;
                    while (parent && parent.tagName !== 'BODY') {
                        const titleEl = parent.querySelector('h3 a, h2 a, a h3, a span.fz-20, h3');
                        if (titleEl) {
                            const anchor = titleEl.tagName === 'A' ? titleEl : titleEl.closest('a');
                            const url = anchor ? anchor.href : '';
                            const alreadyAdded = items.some(item => item.url === url);
                            if (!alreadyAdded && url) {
                                items.push({
                                    title: titleEl.innerText,
                                    url: url,
                                    snippet: snippetEl ? snippetEl.innerText : ''
                                });
                            }
                            break;
                        }
                        parent = parent.parentElement;
                    }
                });
                return items;
            }""")
            browser.close()
            return results
        except Exception as e:
            print(f"[-] Playwright Yahoo Search failed: {e}")
            browser.close()
            return []

def search_via_serper(query: str, api_key: str) -> list:
    print(f"[+] Querying Google Search via Serper API for: {query}...")
    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json"
    }
    data = json.dumps({"q": query}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            res_data = json.loads(res.read().decode("utf-8"))
            organic = res_data.get("organic", [])
            results = []
            for item in organic:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", "")
                })
            return results
    except Exception as e:
        print(f"[-] Serper search failed: {e}")
        return []

def synthesize_profile_from_snippets(company_name: str, search_results: list) -> dict:
    client, model = get_llm_client_and_model()
    context_lines = []
    for i, res in enumerate(search_results):
        context_lines.append(
            f"Result {i+1}:\n"
            f"Title: {res['title']}\n"
            f"URL: {res['url']}\n"
            f"Snippet: {res['snippet']}\n"
        )
    context = "\n".join(context_lines)
    
    prompt = f"""
You are an expert market intelligence assistant.
Your task is to synthesize a structured profile for the following company based on search engine results snippets:
Company Name: "{company_name}"

Search Results Snippets:
{context}

Guidelines:
1. Synthesize a clean, professional description of the company (what they do, what sector they are in, their business activity).
2. Extract any products or services they offer, or what they manufacture/deal in (e.g. from directory pages like IndiaMART or company directories).
3. Identify their official standalone website URL if mentioned in the search results (do NOT return directories like zaubacorp.com, tofler.in, filesure, falconebiz, justdial, indiamart, tradeindia, linkedin, facebook as the official website). If no official website is found, output "N/A".
4. Extract any contact details (emails, phone numbers, registered addresses) mentioned in the snippets.
5. Set "is_pure_software_only" to true if the company only deals in software development, SaaS, IT consulting, digital/web services with no physical products/hardware.
6. Set "is_hardware_related" to true if the company manufactures, sells, or services physical electronic/electrical components, IT hardware (like servers, storage, IoT), embedded systems, switchgears, machinery, or general physical hardware products. Set it to false if the company deals strictly in software/digital services, raw materials (like raw steel pipes, tubes, copper mining, chemicals), or other unrelated sectors.

Respond strictly in JSON format with the following keys:
{{
  "company_name": "{company_name}",
  "website": "<official website or N/A>",
  "company_description": "<synthesized description>",
  "is_pure_software_only": <true or false>,
  "is_hardware_related": <true or false>,
  "offerings": [
    {{
      "name": "<product/service name>",
      "description": "<brief description of product>",
      "type": "<product or service>"
    }}
  ],
  "emails": ["<extracted emails>"],
  "phones": ["<extracted phone numbers>"],
  "addresses": ["<extracted addresses>"]
}}
"""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"[-] LLM synthesis failed: {e}")
        return {
            "company_name": company_name,
            "website": "N/A",
            "company_description": "N/A",
            "is_pure_software_only": False,
            "is_hardware_related": True,
            "offerings": [],
            "emails": [],
            "phones": [],
            "addresses": []
        }

def build_page_summary(url: str, html: str) -> Dict[str, object]:
    full_text = clean_html_tags(html)
    main_text = extract_main_content(html)
    title = extract_page_title(html)
    meta_description = extract_meta_content(html, ["description", "og:description", "twitter:description"])
    keywords = extract_meta_content(html, ["keywords"], ["article:tag"])
    links = extract_links(html, url)
    return {
        "url": url,
        "title": title,
        "text": main_text,
        "meta_description": meta_description[0] if meta_description else "",
        "keywords": keywords,
        "links": links,
        "emails": extract_emails(html, full_text),
        "phones": extract_phones(full_text),
        "social_links": extract_social_links(links),
        "addresses": extract_addresses(full_text),
        "product_signals": extract_product_signals(main_text),
    }

def select_internal_links(base_url: str, links: List[str]) -> List[str]:
    domain = get_domain(base_url)
    selected = []
    for link in links:
        if get_domain(link) != domain:
            continue
        lower = link.lower()
        if any(hint in lower for hint in CONTACT_HINTS + ABOUT_HINTS + PRODUCT_HINTS) and link not in selected:
            selected.append(link)
    return selected[:5]

def get_candidate_product_links(base_url: str, pages: List[Dict[str, object]], max_candidates: int = 12) -> List[str]:
    candidates = []
    seen = set()
    for page in pages:
        links = page.get("links", [])
        text = page.get("text", "") or ""
        for link in links:
            if get_domain(link) != get_domain(base_url):
                continue
            lower = link.lower()
            if re.search(r"\.(png|jpg|jpeg|gif|webp|svg|css|js|ico|pdf|zip|xml|json)(?:\?|$)", lower):
                continue

            generic_paths = ["/about", "/contact", "/privacy", "/terms", "/careers", "/blog", "/news", "/gallery", "/sitemap", "/assets", "/portfolio", "/testimonials", "/our-team"]
            if any(p in lower for p in generic_paths):
                continue

            if is_product_link(lower) or any(hint in lower for hint in PRODUCT_HINTS):
                if link not in seen:
                    if "/collections/" in lower or "/collection" in lower:
                        candidates.append(link)
                    else:
                        candidates.insert(0, link)
                    seen.add(link)
                    if len(candidates) >= max_candidates:
                        return candidates
            else:
                if any(hint in (text or "").lower() for hint in PRODUCT_HINTS) and link not in seen:
                    parsed_link = urllib.parse.urlparse(link)
                    if parsed_link.path in ("", "/"):
                        continue
                    candidates.append(link)
                    seen.add(link)
                    if len(candidates) >= max_candidates:
                        return candidates
    return candidates

def scrape_website_content(url: str, max_pages: int = 5, company_name: str = "", proxy_url: str = None) -> Dict[str, object]:
    homepage_html, canonical_url = fetch_html(url, proxy_url=proxy_url)
    if not homepage_html:
        return {
            "website": "N/A",
            "canonical_url": url,
            "company_description": "N/A",
            "emails": [],
            "phones": [],
            "offerings": [],
            "addresses": []
        }

    homepage_summary = build_page_summary(canonical_url, homepage_html)
    all_pages = [homepage_summary]

    selected_links = select_internal_links(canonical_url, homepage_summary.get("links", []))
    for link in selected_links[: max(0, max_pages - 1)]:
        if link == canonical_url:
            continue
        time.sleep(0.4)
        page_html, fetched_url = fetch_html(link, proxy_url=proxy_url)
        if not page_html:
            continue
        all_pages.append(build_page_summary(fetched_url, page_html))

    emails: List[str] = []
    phones: List[str] = []
    social_links: List[str] = []
    addresses: List[str] = []
    keywords: List[str] = []
    product_signals: List[str] = []
    page_titles: List[str] = []
    page_snippets: List[str] = []

    for page in all_pages:
        if page.get("title"):
            page_titles.append(page["title"])
        if page.get("meta_description"):
            page_snippets.append(page["meta_description"])
        for source, target in [
            (page.get("emails", []), emails),
            (page.get("phones", []), phones),
            (page.get("social_links", []), social_links),
            (page.get("addresses", []), addresses),
            (page.get("keywords", []), keywords),
            (page.get("product_signals", []), product_signals),
        ]:
            for item in source:
                if item and item not in target:
                    target.append(item)

    about_text = ""
    for page in all_pages:
        purl = page.get("url", "").lower()
        if any(hint in purl for hint in ABOUT_HINTS):
            text_val = normalize_text(page.get("text", ""))
            if len(text_val) > 150:
                about_text = text_val
                break

    description_candidates = []
    if about_text:
        description_candidates.append(about_text)
    
    home_meta = normalize_text(homepage_summary.get("meta_description", ""))
    if home_meta and home_meta != "N/A":
        description_candidates.append(home_meta)
        
    for s in page_snippets:
        s_val = normalize_text(s)
        if s_val and s_val != "N/A":
            description_candidates.append(s_val)
            
    home_text = normalize_text(homepage_summary.get("text", ""))
    if home_text and home_text != "N/A":
        description_candidates.append(home_text[:1000])

    description = "N/A"
    for candidate in description_candidates:
        if candidate and candidate != "N/A":
            description = candidate
            break

    if description != "N/A" and len(description) > 1000:
        description = description[:1000].rstrip() + "..."

    products: List[Dict[str, object]] = []
    candidate_links = get_candidate_product_links(canonical_url, all_pages, max_candidates=max_pages * 3)
    followed = 0
    for plink in candidate_links:
        if followed >= max_pages:
            break
        try:
            time.sleep(0.4)
            phtml, pfetched = fetch_html(plink, proxy_url=proxy_url)
            if not phtml:
                continue
            cleaned_html = strip_boilerplate(phtml)
            product_data = extract_product_page(cleaned_html, pfetched)
            
            extracted_items = []
            if isinstance(product_data, list):
                extracted_items = product_data
            elif isinstance(product_data, dict):
                extracted_items = [product_data]
                
            for item in extracted_items:
                name = (item.get("name") or "").strip()
                desc = (item.get("description") or "").strip()
                specs = item.get("specs", []) or []
                type_val = item.get("type", "product")

                cleaned_specs = []
                machinery_count = 0
                for spec in specs:
                    key = spec.get("key", "")
                    val = spec.get("value", "")
                    if is_machinery_spec(key, val):
                        machinery_count += 1
                    else:
                        cleaned_specs.append(spec)
                
                if specs and (machinery_count / len(specs)) > 0.4:
                    continue

                if not is_valid_product_name(name, company_name):
                    continue

                if name and len(clean_html_tags(name)) >= 3 and (len(clean_html_tags(desc)) >= 25 or len(cleaned_specs) > 0):
                    is_duplicate = False
                    for p in products:
                        if p["name"].lower() == name.lower():
                            is_duplicate = True
                            break
                        if p["description"][:100].lower() == desc[:100].lower():
                            is_duplicate = True
                            break
                    
                    if not is_duplicate:
                        products.append({
                            "name": name, 
                            "description": desc, 
                            "specs": cleaned_specs,
                            "type": type_val
                        })
                        followed += 1
        except Exception:
            continue

    if not products and product_signals:
        for p in product_signals:
            parsed = parse_product_signal(p)
            name = parsed.get("name", "")
            desc = parsed.get("description", "")
            type_val = parsed.get("type", "product")
            
            if not is_valid_product_name(name, company_name):
                continue
                
            if "\x00" in desc or "\x00" in name:
                continue
            if len(name) >= 3 and len(clean_html_tags(desc)) >= 30:
                is_duplicate = False
                for existing in products:
                    if existing["name"].lower() == name.lower():
                        is_duplicate = True
                        break
                    if existing["description"][:100].lower() == desc[:100].lower():
                        is_duplicate = True
                        break
                if not is_duplicate:
                    products.append({
                        "name": name, 
                        "description": desc, 
                        "specs": [],
                        "type": type_val
                    })
            if len(products) >= 10:
                break

    return {
        "website": canonical_url,
        "canonical_url": canonical_url,
        "company_description": description,
        "offerings": products,
        "emails": filter_generic_emails_if_custom_exists(emails, canonical_url),
        "phones": phones,
        "addresses": addresses,
    }

def classify_company_profile(company_name: str, description: str, offerings: list) -> dict:
    client, model = get_llm_client_and_model()
    offerings_str = json.dumps(offerings, indent=2)
    prompt = f"""
You are an expert market intelligence assistant.
Classify the following company based on its description and product/service offerings.

Company Name: "{company_name}"
Description: {description}
Offerings:
{offerings_str}

We are targeting companies that actually manufacture, assemble, design, or integrate physical hardware, electronic/electrical components, IT hardware (like servers, storage, GPU servers, AI clusters, edge computing nodes, telecom core nodes, VFX workstations), embedded systems, power boards, machinery, or general physical hardware products.
Note: Companies building, assembling, or integrating AI clusters, GPU servers, In-Memory database servers (like SAP HANA setups), virtualization hypervisors, edge computing nodes, telecom core routing hardware, or high-end rendering workstations are explicitly considered hardware-related (is_hardware_related = true) as they integrate high-density physical systems.
We want to flag and filter out:
1. Pure traders, retail resellers, or distributors of third-party finished goods (with no in-house manufacturing, assembly, or hardware design/integration).
2. Companies that are pure software, software development, SaaS, IT consulting, or digital services only with no physical hardware product/system integration.
3. Companies that are strictly in raw materials (like raw steel pipes, tubes, copper mining, chemicals) or completely unrelated sectors.

Respond strictly in JSON format with the following keys:
{{
  "is_pure_software_only": <true or false>,
  "is_hardware_related": <true or false>
}}
"""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"[-] LLM classification failed: {e}")
        return {
            "is_pure_software_only": False,
            "is_hardware_related": True
        }

def process_lead(lead: dict, max_pages: int = 5, serper_key: str = None, proxy_url: str = None) -> dict:
    company_name = lead.get("company_name", "")
    print(f"[+] Starting search/scrape for: {company_name}")

    # Load cached search snippets if available to avoid duplicate API calls
    cached_snippets_str = lead.get("search_snippets")
    cached_snippets = []
    if cached_snippets_str:
        try:
            if isinstance(cached_snippets_str, str):
                cached_snippets = json.loads(cached_snippets_str)
            elif isinstance(cached_snippets_str, list):
                cached_snippets = cached_snippets_str
        except Exception:
            pass

    website = lead.get("website")
    if not website or website == "N/A" or is_blocked_domain(website):
        if cached_snippets:
            # Find best URL from cached snippets
            best_url = "N/A"
            best_score = -999
            for res in cached_snippets:
                parsed_url = urllib.parse.urlparse(res["url"])
                candidate_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                score = score_official_candidate(company_name, candidate_url, title=res["title"], snippet=res["snippet"])
                if score > best_score:
                    best_score = score
                    best_url = candidate_url
            website = best_url if (best_url != "N/A" and best_score >= 1) else "N/A"
        elif serper_key:
            search_results = search_via_serper(company_name, serper_key)
            best_url = "N/A"
            best_score = -999
            for res in search_results:
                parsed_url = urllib.parse.urlparse(res["url"])
                candidate_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                score = score_official_candidate(company_name, candidate_url, title=res["title"], snippet=res["snippet"])
                if score > best_score:
                    best_score = score
                    best_url = candidate_url
            website = best_url if (best_url != "N/A" and best_score >= 1) else "N/A"
            cached_snippets = search_results  # Cache them for the fallback below
        else:
            website = find_official_website(company_name)
        
    scraped = None
    if website and website != "N/A":
        print(f"  -> Found website: {website}. Scraping details...")
        scraped = scrape_website_content(website, max_pages=max_pages, company_name=company_name, proxy_url=proxy_url)
        scraped["website"] = website

    if not scraped or scraped.get("website") == "N/A" or scraped.get("company_description") in ("N/A", "") or not scraped.get("offerings"):
        print(f"  -> Direct crawl yielded no details. Running search snippet synthesis fallback for: {company_name}...")
        if cached_snippets:
            search_results = cached_snippets
            print("  -> Reusing cached search snippets.")
        elif serper_key:
            search_results = search_via_serper(company_name, serper_key)
            cached_snippets = search_results
        else:
            search_results = search_yahoo_playwright(company_name)
            
        if search_results:
            synthesized = synthesize_profile_from_snippets(company_name, search_results)
            if not scraped:
                scraped = {
                    "website": "N/A",
                    "canonical_url": "N/A",
                    "company_description": "N/A",
                    "offerings": [],
                    "emails": [],
                    "phones": [],
                    "addresses": [],
                }
            
            merged_emails = sorted(list(set(str(x) for x in (scraped.get("emails", []) or []) + (synthesized.get("emails", []) or []))))
            resolved_web = synthesized.get("website") or scraped.get("website")
            scraped.update({
                "company_description": synthesized.get("company_description") or scraped.get("company_description", "N/A"),
                "offerings": synthesized.get("offerings") or scraped.get("offerings", []),
                "emails": filter_generic_emails_if_custom_exists(merged_emails, resolved_web),
                "phones": sorted(list(set(str(x) for x in (scraped.get("phones", []) or []) + (synthesized.get("phones", []) or [])))),
                "addresses": sorted(list(set(str(x) for x in (scraped.get("addresses", []) or []) + (synthesized.get("addresses", []) or [])))),
                "is_pure_software_only": synthesized.get("is_pure_software_only"),
                "is_hardware_related": synthesized.get("is_hardware_related"),
            })
            if synthesized.get("website") and synthesized.get("website") != "N/A":
                scraped["website"] = synthesized["website"]
                scraped["canonical_url"] = synthesized["website"]

    if not scraped:
        scraped = {
            "website": "N/A",
            "canonical_url": "N/A",
            "company_description": "N/A",
            "offerings": [],
            "emails": [],
            "phones": [],
            "addresses": [],
        }

    # Pass the snippets back so they get updated in the database
    scraped["search_snippets"] = cached_snippets

    if scraped and scraped.get("company_description") not in ("N/A", "", None):
        if "is_pure_software_only" not in scraped or scraped.get("is_pure_software_only") is None or "is_hardware_related" not in scraped or scraped.get("is_hardware_related") is None:
            cls_res = classify_company_profile(
                company_name, 
                scraped.get("company_description", ""), 
                scraped.get("offerings", [])
            )
            scraped["is_pure_software_only"] = cls_res.get("is_pure_software_only", False)
            scraped["is_hardware_related"] = cls_res.get("is_hardware_related", True)
    else:
        scraped["is_pure_software_only"] = False
        scraped["is_hardware_related"] = False

    return scraped

def worker_task(lead: dict, max_pages: int, serper_key: str, proxy_url: str):
    db = LeadsDatabase()
    try:
        scraped_info = process_lead(lead, max_pages=max_pages, serper_key=serper_key, proxy_url=proxy_url)
        db.update_lead_crawled(
            cin_number=lead["cin_number"],
            website=scraped_info.get("website", "N/A"),
            canonical_url=scraped_info.get("canonical_url", "N/A"),
            company_description=scraped_info.get("company_description", "N/A"),
            offerings=scraped_info.get("offerings", []),
            emails=scraped_info.get("emails", []),
            phones=scraped_info.get("phones", []),
            addresses=scraped_info.get("addresses", []),
            status="synthesized" if scraped_info.get("company_description") != "N/A" else "failed",
            search_snippets=scraped_info.get("search_snippets", []),
            is_pure_software_only=scraped_info.get("is_pure_software_only"),
            is_hardware_related=scraped_info.get("is_hardware_related")
        )
    except Exception as e:
        print(f"[-] Thread failure for {lead.get('company_name')}: {e}")
        db.update_lead_crawled(
            cin_number=lead["cin_number"],
            website="N/A",
            canonical_url="N/A",
            company_description="N/A",
            offerings=[],
            emails=[],
            phones=[],
            addresses=[],
            status="failed",
            search_snippets=[],
            is_pure_software_only=False,
            is_hardware_related=False
        )
    finally:
        db.close()

def pre_crawl_worker_task(lead: dict, serper_key: str, proxy_url: str):
    db = LeadsDatabase()
    company_name = lead.get("company_name", "")
    try:
        snippets = []
        snippets_str = lead.get("search_snippets")
        if snippets_str:
            try:
                if isinstance(snippets_str, str):
                    snippets = json.loads(snippets_str)
                elif isinstance(snippets_str, list):
                    snippets = snippets_str
            except:
                pass
        
        website = lead.get("website")
        if not snippets:
            if serper_key:
                snippets = search_via_serper(company_name, serper_key)
                if not snippets:
                    raise Exception("Serper search failed or returned no results (likely quota exhausted)")
                
                best_url = "N/A"
                best_score = -999
                for res in snippets:
                    parsed_url = urllib.parse.urlparse(res["url"])
                    candidate_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                    score = score_official_candidate(company_name, candidate_url, title=res["title"], snippet=res["snippet"])
                    if score > best_score:
                        best_score = score
                        best_url = candidate_url
                if best_url != "N/A" and best_score >= 1:
                    website = best_url
            else:
                website = find_official_website(company_name)
        
        with get_session() as session:
            l = session.query(Company).filter(Company.cin_number == lead["cin_number"]).first()
            if l:
                l.website = website or "N/A"
                l.canonical_url = website or "N/A"
                l.search_snippets = json.dumps(snippets)
                l.crawl_status = 'crawled'
                
        print(f"[+] Pre-crawled: {company_name} -> Website: {website}")
    except Exception as e:
        print(f"[-] Pre-crawl failed for {company_name}: {e}")
        # Revert status to pending so it can be retried
        try:
            with get_session() as session:
                l = session.query(Company).filter(Company.cin_number == lead["cin_number"]).first()
                if l:
                    l.crawl_status = 'pending'
        except Exception as db_err:
            print(f"[-] Failed to revert status to pending: {db_err}")
    finally:
        db.close()

class LeadsDatabase:
    def __init__(self):
        pass

    def get_pending_leads(self, limit: int) -> list:
        with get_session() as session:
            results = session.query(Company).filter(Company.crawl_status == 'pending').limit(limit).all()
            leads = []
            for r in results:
                hsn_list = [h.product_hsn for h in session.query(CompanyHsnJunction).filter(CompanyHsnJunction.company_id == r.company_id).all()]
                nic_list = [n.buyer_industry_code for n in session.query(CompanyNicJunction).filter(CompanyNicJunction.company_id == r.company_id).all()]
                leads.append({
                    "cin_number": r.cin_number,
                    "company_name": r.company_name,
                    "registration_date": r.registration_date,
                    "registered_office_address": r.registered_office_address,
                    "mca_status": r.mca_status,
                    "state_code": r.state_code,
                    "target_hsn_markets": hsn_list,
                    "target_hsn_descriptions": r.target_hsn_descriptions,
                    "industry_nic_codes": nic_list,
                    "website": r.website,
                    "canonical_url": r.canonical_url,
                    "company_description": r.company_description,
                    "emails": r.emails,
                    "phones": r.phones,
                    "addresses": r.addresses,
                    "offerings": r.offerings,
                    "crawl_status": r.crawl_status,
                    "scraped_at": r.scraped_at,
                    "search_snippets": r.search_snippets,
                    "is_pure_software_only": r.is_pure_software_only,
                    "is_hardware_related": r.is_hardware_related
                })
            return leads

    def update_lead_crawled(self, cin_number: str, website: str, canonical_url: str, company_description: str, offerings: list, emails: list, phones: list, addresses: list, status: str, search_snippets: list = None, is_pure_software_only: bool = None, is_hardware_related: bool = None):
        with get_session() as session:
            lead = session.query(Company).filter(Company.cin_number == cin_number).first()
            if lead:
                lead.website = website or "N/A"
                lead.canonical_url = canonical_url or "N/A"
                lead.company_description = company_description or "N/A"
                lead.emails = emails or []
                lead.phones = phones or []
                lead.addresses = addresses or []
                lead.offerings = offerings or []
                lead.crawl_status = status
                lead.scraped_at = datetime.now().isoformat()
                lead.search_snippets = json.dumps(search_snippets or [])
                lead.is_pure_software_only = is_pure_software_only
                lead.is_hardware_related = is_hardware_related

    def import_raw_leads(self, leads_list: list) -> int:
        inserted = 0
        
        def get_buyer_industry_code(nic_raw):
            if not nic_raw:
                return None
            if str(nic_raw).startswith("NIC_"):
                return str(nic_raw)
            clean_nic = "".join(filter(str.isdigit, str(nic_raw)))
            if len(clean_nic) >= 4:
                return f"NIC_{clean_nic[:4]}"
            elif clean_nic:
                return f"NIC_{clean_nic}"
            return None

        with get_session() as session:
            # Keep track of already added junctions in this session to prevent duplicate session.add()
            added_hsns = set()
            added_nics = set()
            
            for lead in leads_list:
                cin = lead.get("cin_number")
                if not cin:
                    continue
                
                # Check if company already exists
                company = session.query(Company).filter(Company.cin_number == cin).first()
                if not company:
                    company = Company(
                        company_name=lead.get("company_name"),
                        website=lead.get("website", "N/A"),
                        cin_number=cin,
                        registration_date=lead.get("registration_date"),
                        registered_office_address=lead.get("registered_office_address"),
                        mca_status=lead.get("status"),
                        state_code=lead.get("state_code"),
                        canonical_url=lead.get("canonical_url", "N/A"),
                        company_description=lead.get("company_description", "N/A"),
                        emails=lead.get("emails", []),
                        phones=lead.get("phones", []),
                        addresses=lead.get("addresses", []),
                        offerings=lead.get("offerings", []),
                        crawl_status=lead.get("crawl_status", "pending"),
                        scraped_at=lead.get("scraped_at"),
                        search_snippets=lead.get("search_snippets"),
                        is_pure_software_only=lead.get("is_pure_software_only"),
                        is_hardware_related=lead.get("is_hardware_related")
                    )
                    session.add(company)
                    session.flush() # Secure auto-increment company_id immediately
                    inserted += 1
                
                # Handle HSN code population from raw json rows (which might be target_hsn_market or target_hsn_markets)
                hsn_val = lead.get("target_hsn_market")
                hsn_list = []
                if hsn_val:
                    hsn_list.append(str(hsn_val))
                else:
                    raw_hsn_markets = lead.get("target_hsn_markets", [])
                    if isinstance(raw_hsn_markets, list):
                        hsn_list.extend(str(h) for h in raw_hsn_markets)
                
                for hsn in hsn_list:
                    hsn = validate_and_normalize_hsn(hsn)
                    if hsn:
                        hsn_key = (company.company_id, hsn)
                        if hsn_key not in added_hsns:
                            hsn_exists = session.query(CompanyHsnJunction).filter(
                                CompanyHsnJunction.company_id == company.company_id,
                                CompanyHsnJunction.product_hsn == hsn
                            ).first()
                            if not hsn_exists:
                                hsn_j = CompanyHsnJunction(company_id=company.company_id, product_hsn=hsn)
                                session.add(hsn_j)
                            added_hsns.add(hsn_key)
                
                # Handle NIC code population (which might be industry_nic_code or industry_nic_codes)
                nic_val = lead.get("industry_nic_code")
                nic_list = []
                if nic_val:
                    nic_list.append(str(nic_val))
                else:
                    raw_nic_codes = lead.get("industry_nic_codes", [])
                    if isinstance(raw_nic_codes, list):
                        nic_list.extend(str(n) for n in raw_nic_codes)
                        
                for nic in nic_list:
                    nic_code = get_buyer_industry_code(nic)
                    if nic_code:
                        nic_key = (company.company_id, nic_code)
                        if nic_key not in added_nics:
                            nic_exists = session.query(CompanyNicJunction).filter(
                                CompanyNicJunction.company_id == company.company_id,
                                CompanyNicJunction.buyer_industry_code == nic_code
                            ).first()
                            if not nic_exists:
                                nic_j = CompanyNicJunction(company_id=company.company_id, buyer_industry_code=nic_code)
                                session.add(nic_j)
                            added_nics.add(nic_key)
                            
        return inserted

    def import_scraped_leads(self, scraped_list: list) -> int:
        updated = 0
        with get_session() as session:
            for r in scraped_list:
                cin = r.get("cin_number")
                if not cin:
                    continue
                lead = session.query(Company).filter(Company.cin_number == cin).first()
                if lead:
                    lead.website = r.get("website", "N/A")
                    lead.canonical_url = r.get("canonical_url", "N/A")
                    lead.company_description = r.get("company_description", "N/A")
                    lead.emails = r.get("emails", [])
                    lead.phones = r.get("phones", [])
                    lead.addresses = r.get("addresses", [])
                    lead.offerings = r.get("offerings", [])
                    lead.crawl_status = 'synthesized'
                    lead.scraped_at = datetime.now().isoformat()
                    lead.is_pure_software_only = r.get("is_pure_software_only")
                    lead.is_hardware_related = r.get("is_hardware_related")
                    updated += 1
        return updated

    def get_stats(self) -> dict:
        from sqlalchemy import func
        with get_session() as session:
            total = session.query(Company).count()
            counts = session.query(Company.crawl_status, func.count(Company.cin_number)).group_by(Company.crawl_status).all()
            status_counts = {row[0]: row[1] for row in counts}
            return {
                "total": total,
                "pending": status_counts.get("pending", 0),
                "crawled": status_counts.get("crawled", 0),
                "synthesized": status_counts.get("synthesized", 0),
                "failed": status_counts.get("failed", 0)
            }

    def export_to_json(self, output_path: str, only_scraped: bool = False) -> int:
        with get_session() as session:
            if only_scraped:
                records = session.query(Company).filter(Company.crawl_status != 'pending').all()
            else:
                records = session.query(Company).all()
            
            results = []
            for record in records:
                is_sw = record.is_pure_software_only
                is_hw = record.is_hardware_related
                if record.crawl_status == "synthesized" or (only_scraped and record.crawl_status != "pending"):
                    if is_sw == True or is_hw == False:
                        continue

                hsn_list = [h.product_hsn for h in session.query(CompanyHsnJunction).filter(CompanyHsnJunction.company_id == record.company_id).all()]
                nic_list = [n.buyer_industry_code for n in session.query(CompanyNicJunction).filter(CompanyNicJunction.company_id == record.company_id).all()]

                results.append({
                    "company_name": record.company_name,
                    "cin_number": record.cin_number,
                    "registration_date": record.registration_date,
                    "registered_office_address": record.registered_office_address,
                    "state_code": record.state_code,
                    "target_hsn_markets": hsn_list,
                    "target_hsn_descriptions": record.target_hsn_descriptions or [],
                    "industry_nic_codes": nic_list,
                    "website": record.website,
                    "canonical_url": record.canonical_url,
                    "company_description": record.company_description,
                    "emails": record.emails or [],
                    "phones": record.phones or [],
                    "addresses": record.addresses or [],
                    "offerings": record.offerings or [],
                    "status": record.mca_status
                })
                
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
                
            return len(results)

    def export_batch_requests(self, file_path: str, limit: int, serper_key: str) -> int:
        pending = self.get_pending_leads(limit)
        if not pending:
            return 0
            
        print(f"[+] Generating batch requests for {len(pending)} leads...")
        count = 0
        with open(file_path, "w", encoding="utf-8") as f:
            for lead in pending:
                cin = lead["cin_number"]
                company_name = lead["company_name"]
                
                snippets = lead.get("search_snippets")
                if isinstance(snippets, str) and snippets.startswith("["):
                    try:
                        snippets = json.loads(snippets)
                    except:
                        snippets = []
                elif not isinstance(snippets, list):
                    snippets = []
                    
                if not snippets:
                    if serper_key:
                        snippets = search_via_serper(company_name, serper_key)
                    else:
                        snippets = search_yahoo_playwright(company_name)
                    
                    with get_session() as session:
                        l = session.query(Company).filter(Company.cin_number == cin).first()
                        if l:
                            l.search_snippets = json.dumps(snippets)
                            l.crawl_status = 'crawled'
                
                if not snippets:
                    continue
                
                req_line = make_batch_request_line(cin, company_name, snippets)
                f.write(json.dumps(req_line) + "\n")
                count += 1
                
        return count

    def import_batch_responses(self, file_path: str) -> int:
        updated = 0
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    cin = data.get("custom_id")
                    if not cin:
                        continue
                    
                    response = data.get("response", {})
                    body = response.get("body", {})
                    choices = body.get("choices", [])
                    if not choices:
                        continue
                    
                    content = choices[0].get("message", {}).get("content", "")
                    profile = json.loads(content)
                    
                    with get_session() as session:
                        lead = session.query(Company).filter(Company.cin_number == cin).first()
                        if lead:
                            lead.website = profile.get("website", "N/A")
                            lead.canonical_url = profile.get("website", "N/A")
                            lead.company_description = profile.get("company_description", "N/A")
                            lead.emails = profile.get("emails", [])
                            lead.phones = profile.get("phones", [])
                            lead.addresses = profile.get("addresses", [])
                            lead.offerings = profile.get("offerings", [])
                            lead.crawl_status = 'synthesized'
                            lead.scraped_at = datetime.now().isoformat()
                            lead.is_pure_software_only = profile.get("is_pure_software_only")
                            lead.is_hardware_related = profile.get("is_hardware_related")
                            updated += 1
                except Exception as e:
                    print(f"[-] Failed to import line in batch: {e}")
        return updated

    def save_component_analysis(self, analysis_id: str, component_name: str, part_number: str, manufacturer: str, component_type: str, specs: dict, applications: list, report: str, qa_notes: str):
        with get_session() as session:
            exists = session.query(ComponentAnalysis).filter(ComponentAnalysis.id == analysis_id).first()
            if exists:
                exists.component_name = component_name
                exists.part_number = part_number
                exists.manufacturer = manufacturer
                exists.component_type = component_type
                exists.specs = specs
                exists.applications = applications
                exists.report = report
                exists.qa_notes = qa_notes
            else:
                new_analysis = ComponentAnalysis(
                    id=analysis_id,
                    component_name=component_name,
                    part_number=part_number,
                    manufacturer=manufacturer,
                    component_type=component_type,
                    specs=specs,
                    applications=applications,
                    report=report,
                    qa_notes=qa_notes
                )
                session.add(new_analysis)

            # Update/insert into Component master table
            comp = session.query(Component).filter(Component.component_id == part_number).first()
            if not comp:
                comp = Component(
                    component_id=part_number,
                    component_type=component_type,
                    manufacturer=manufacturer
                )
                session.add(comp)
            else:
                comp.component_type = component_type
                comp.manufacturer = manufacturer

    def get_component_analysis(self, analysis_id: str) -> dict:
        with get_session() as session:
            row = session.query(ComponentAnalysis).filter(ComponentAnalysis.id == analysis_id).first()
            if row:
                return {
                    "id": row.id,
                    "component_name": row.component_name,
                    "part_number": row.part_number,
                    "manufacturer": row.manufacturer,
                    "component_type": row.component_type,
                    "specs": row.specs,
                    "applications": row.applications,
                    "report": row.report,
                    "qa_notes": row.qa_notes,
                    "analyzed_at": row.analyzed_at
                }
            return None

    def get_all_component_analyses(self) -> list:
        with get_session() as session:
            rows = session.query(ComponentAnalysis).order_by(ComponentAnalysis.analyzed_at.desc()).all()
            return [{
                "id": r.id,
                "component_name": r.component_name,
                "part_number": r.part_number,
                "manufacturer": r.manufacturer,
                "component_type": r.component_type,
                "analyzed_at": r.analyzed_at
            } for r in rows]

    def delete_component_analysis(self, analysis_id: str) -> bool:
        with get_session() as session:
            row = session.query(ComponentAnalysis).filter(ComponentAnalysis.id == analysis_id).first()
            if row:
                session.delete(row)
                return True
            return False

    def get_component_matches(self, analysis_id: str) -> list:
        with get_session() as session:
            rows = (
                session.query(ComponentMatch, Company)
                .join(Company, ComponentMatch.company_id == Company.company_id)
                .filter(ComponentMatch.analysis_id == analysis_id)
                .order_by(ComponentMatch.match_score.desc(), Company.company_name.asc())
                .all()
            )
            matches = []
            for match, company in rows:
                hsn_list = [h.product_hsn for h in session.query(CompanyHsnJunction).filter(CompanyHsnJunction.company_id == company.company_id).all()]
                nic_list = [n.buyer_industry_code for n in session.query(CompanyNicJunction).filter(CompanyNicJunction.company_id == company.company_id).all()]
                matches.append({
                    "match_id": match.match_id,
                    "match_score": match.match_score,
                    "match_status": match.status,
                    "matched_at": match.matched_at,
                    "cin_number": company.cin_number,
                    "company_name": company.company_name,
                    "website": company.website,
                    "canonical_url": company.canonical_url,
                    "company_description": company.company_description,
                    "emails": company.emails or [],
                    "phones": company.phones or [],
                    "addresses": company.addresses or [],
                    "offerings": company.offerings or [],
                    "state_code": company.state_code,
                    "target_hsn_markets": hsn_list,
                    "industry_nic_codes": nic_list
                })
            return matches

    def save_component_matches(self, analysis_id: str, matches: list):
        with get_session() as session:
            session.query(ComponentMatch).filter(ComponentMatch.analysis_id == analysis_id).delete()
            for m in matches:
                new_match = ComponentMatch(
                    analysis_id=analysis_id,
                    company_id=m["company_id"],
                    match_score=m.get("match_score", 1.0),
                    status=m.get("status", "uncontacted")
                )
                session.add(new_match)

    def find_target_buyers_for_component(self, product_hsn: str, buyer_industry_code: str) -> list:
        query = """
            SELECT DISTINCT c.company_id, c.company_name, c.website 
            FROM companies c
            LEFT JOIN company_hsn_junction hj ON c.company_id = hj.company_id
            LEFT JOIN company_nic_junction nj ON c.company_id = nj.company_id
            WHERE hj.product_hsn = :product_hsn OR nj.buyer_industry_code = :buyer_industry_code
        """
        from sqlalchemy import text
        with get_session() as session:
            result = session.execute(text(query), {"product_hsn": product_hsn, "buyer_industry_code": buyer_industry_code})
            return [{"company_id": row[0], "company_name": row[1], "website": row[2]} for row in result]

    def match_and_save_leads_for_component(self, analysis_id: str) -> int:
        analysis = self.get_component_analysis(analysis_id)
        if not analysis:
            return 0
        
        graph_mappings = analysis.get("applications") or []
        matching_company_ids = set()
        for mapping in graph_mappings:
            hsn = mapping.get("product_hsn")
            nic = mapping.get("buyer_industry_code")
            
            if hsn:
                hsn = validate_and_normalize_hsn(str(hsn))
            
            if hsn or nic:
                buyers = self.find_target_buyers_for_component(hsn, nic)
                for buyer in buyers:
                    matching_company_ids.add(buyer["company_id"])
                    
        with get_session() as session:
            session.query(ComponentMatch).filter(ComponentMatch.analysis_id == analysis_id).delete()
            for cid in matching_company_ids:
                new_match = ComponentMatch(
                    analysis_id=analysis_id,
                    company_id=cid,
                    match_score=1.0,
                    status="uncontacted"
                )
                session.add(new_match)
            return len(matching_company_ids)

    def close(self):
        pass

def make_batch_request_line(cin_number: str, company_name: str, snippets: list) -> dict:
    context_lines = []
    for i, res in enumerate(snippets):
        context_lines.append(
            f"Result {i+1}:\n"
            f"Title: {res['title']}\n"
            f"URL: {res['url']}\n"
            f"Snippet: {res['snippet']}\n"
        )
    context = "\n".join(context_lines)
    
    prompt = f"""
You are an expert market intelligence assistant.
Your task is to synthesize a structured profile for the following company based on search engine results snippets:
Company Name: "{company_name}"

Search Results Snippets:
{context}

Guidelines:
1. Synthesize a clean, professional description of the company (what they do, what sector they are in, their business activity).
2. Extract any products or services they offer, or what they manufacture/deal in (e.g. from directory pages like IndiaMART or company directories).
3. Identify their official standalone website URL if mentioned in the search results (do NOT return directories like zaubacorp.com, tofler.in, filesure, falconebiz, justdial, indiamart, tradeindia, linkedin, facebook as the official website). If no official website is found, output "N/A".
4. Extract any contact details (emails, phone numbers, registered addresses) mentioned in the snippets.
5. Set "is_pure_software_only" to true if the company only deals in software development, SaaS, IT consulting, digital/web services with no physical products/hardware/system integration.
6. Set "is_hardware_related" to true ONLY if the company actually manufactures, assembles, designs, or integrates physical electronic/electrical components, IT hardware (like servers, storage, GPU servers, AI clusters, edge computing nodes, telecom core nodes, VFX workstations), embedded systems, power boards, machinery, or general physical hardware products. Set it to true for companies building or integrating AI clusters, GPU servers, In-Memory database servers, virtualization hypervisors, edge computing nodes, telecom core routing hardware, or high-end rendering workstations. Set it to false if the company is a pure reseller, trader, distributor of finished goods (with no in-house manufacturing/assembly/design value-add), or deals strictly in software, raw materials, or other unrelated sectors.

Respond strictly in JSON format with the following keys:
{{
  "company_name": "{company_name}",
  "website": "<official website or N/A>",
  "company_description": "<synthesized description>",
  "is_pure_software_only": <true or false>,
  "is_hardware_related": <true or false>,
  "offerings": [
    {{
      "name": "<product/service name>",
      "description": "<brief description of product>",
      "type": "<product or service>"
    }}
  ],
  "emails": ["<extracted emails>"],
  "phones": ["<extracted phone numbers>"],
  "addresses": ["<extracted addresses>"]
}}
"""
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.0
    }
    return {
        "custom_id": cin_number,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": body
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scalable MSME Lead Scraping & MySQL Manager")
    parser.add_argument("--import-json", type=str, help="Import raw leads JSON file")
    parser.add_argument("--import-scraped", type=str, help="Import already scraped leads JSON file")
    parser.add_argument("--export-json", type=str, help="Export DB contents back to JSON file")
    parser.add_argument("--only-scraped", action="store_true", help="Only export leads that have been scraped/synthesized")
    parser.add_argument("--stats", action="store_true", help="Print database statistics")
    
    # Scaling execution parameters
    parser.add_argument("--crawl", action="store_true", help="Run search & scraper on pending leads in DB")
    parser.add_argument("--limit", type=int, default=10, help="Number of pending leads to process (default: 10)")
    parser.add_argument("--max-workers", type=int, default=2, help="Parallel worker threads for crawling")
    parser.add_argument("--max-pages", type=int, default=5, help="Max internal website pages to crawl per lead")
    parser.add_argument("--serper-key", type=str, default=None, help="Google Serper API Key")
    parser.add_argument("--proxy-url", type=str, default=None, help="Residential Proxy URL")
    
    # OpenAI Batch CLI parameters
    parser.add_argument("--export-batch", type=str, help="Generate OpenAI Batch request .jsonl file")
    parser.add_argument("--import-batch", type=str, help="Ingest completed OpenAI Batch response .jsonl file")
    parser.add_argument("--pre-crawl", action="store_true", help="Only run Serper search and website discovery to cache snippets in the database, without LLM calls")

    args = parser.parse_args()
    load_env()

    db = LeadsDatabase()
    
    if args.import_json:
        if not os.path.exists(args.import_json):
            print(f"[-] Error: File not found: {args.import_json}")
        else:
            print(f"[+] Loading {args.import_json}...")
            with open(args.import_json, "r", encoding="utf-8") as f:
                leads = json.load(f)
            inserted = db.import_raw_leads(leads)
            print(f"[+] Successfully imported {inserted} new raw leads.")
            stats = db.get_stats()
            print(json.dumps(stats, indent=2))
            
    elif args.import_scraped:
        if not os.path.exists(args.import_scraped):
            print(f"[-] Error: File not found: {args.import_scraped}")
        else:
            print(f"[+] Loading {args.import_scraped}...")
            with open(args.import_scraped, "r", encoding="utf-8") as f:
                scraped = json.load(f)
            updated = db.import_scraped_leads(scraped)
            print(f"[+] Successfully updated {updated} scraped leads in database.")
            stats = db.get_stats()
            print(json.dumps(stats, indent=2))
            
    elif args.export_json:
        print(f"[+] Exporting to {args.export_json}...")
        exported = db.export_to_json(args.export_json, only_scraped=args.only_scraped)
        print(f"[+] Successfully exported {exported} records.")
        
    elif args.export_batch:
        serper_api_key = args.serper_key or os.environ.get("SERPER_API_KEY")
        exported = db.export_batch_requests(args.export_batch, args.limit, serper_api_key)
        print(f"[+] Successfully generated {exported} Batch API requests in '{args.export_batch}'.")
        
    elif args.import_batch:
        if not os.path.exists(args.import_batch):
            print(f"[-] Error: File not found: {args.import_batch}")
        else:
            imported = db.import_batch_responses(args.import_batch)
            print(f"[+] Successfully ingested {imported} Batch API responses.")
            stats = db.get_stats()
            print(json.dumps(stats, indent=2))

    elif args.crawl:
        pending_leads = db.get_pending_leads(args.limit)
        if not pending_leads:
            print("[+] No pending leads to crawl.")
        else:
            print(f"[+] Starting parallel crawl for {len(pending_leads)} leads with {args.max_workers} threads...")
            serper_api_key = args.serper_key or os.environ.get("SERPER_API_KEY")
            proxy_url = args.proxy_url or os.environ.get("PROXY_URL")
            
            db.close()
            
            with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
                futures = []
                for lead in pending_leads:
                    time.sleep(0.5)
                    future = executor.submit(worker_task, lead, args.max_pages, serper_api_key, proxy_url)
                    futures.append(future)
                
                for f in as_completed(futures):
                    pass
            
            db = LeadsDatabase()
            print("\n[+] Crawl execution finished!")
            stats = db.get_stats()
            print(json.dumps(stats, indent=2))
            
    elif args.pre_crawl:
        pending_leads = db.get_pending_leads(args.limit)
        if not pending_leads:
            print("[+] No pending leads to pre-crawl.")
        else:
            print(f"[+] Starting parallel pre-crawl (Serper + Website Discovery) for {len(pending_leads)} leads with {args.max_workers} threads...")
            serper_api_key = args.serper_key or os.environ.get("SERPER_API_KEY")
            proxy_url = args.proxy_url or os.environ.get("PROXY_URL")
            
            db.close()
            
            with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
                futures = []
                for lead in pending_leads:
                    time.sleep(0.1)
                    future = executor.submit(pre_crawl_worker_task, lead, serper_api_key, proxy_url)
                    futures.append(future)
                
                for f in as_completed(futures):
                    pass
            
            db = LeadsDatabase()
            print("\n[+] Pre-crawl execution finished!")
            stats = db.get_stats()
            print(json.dumps(stats, indent=2))

    elif args.stats or (not args.import_json and not args.import_scraped and not args.export_json and not args.crawl and not args.export_batch and not args.import_batch):
        stats = db.get_stats()
        print("[+] Database Statistics:")
        print(json.dumps(stats, indent=2))
        
    db.close()
