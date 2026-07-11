import os, json, hashlib, feedparser, requests, time
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from jinja2 import Environment, FileSystemLoader
from bs4 import BeautifulSoup
import concurrent.futures

with open('config.json', 'r') as f:
    CONFIG = json.load(f)

GEMINI_KEY = os.getenv('GEMINI_API_KEY')
ARTICLES = []
COUNTRIES = ['US', 'GB', 'CA', 'AU', 'DE', 'FR', 'IT', 'ES', 'JP', 'IN', 'BR']
COUNTRY_NAMES = {
    'US': 'United States', 'GB': 'United Kingdom', 'CA': 'Canada',
    'AU': 'Australia', 'DE': 'Germany', 'FR': 'France',
    'IT': 'Italy', 'ES': 'Spain', 'JP': 'Japan', 'IN': 'India', 'BR': 'Brazil'
}

# Category mapping (improved)
def categorize_article(title, source):
    text = (title + " " + source).lower()
    cat_map = {
        'Politics': ['trump', 'biden', 'election', 'congress', 'senate', 'white house', 'minister', 'vote', 'political', 'govt', 'democrat', 'republican'],
        'Sports': ['nba', 'nfl', 'soccer', 'football', 'world cup', 'tennis', 'cricket', 'olympics', 'mlb', 'champions', 'game', 'player'],
        'Technology': ['ai', 'artificial intelligence', 'software', 'tech', 'code', 'google', 'apple', 'microsoft', 'cyber', 'gadget', 'digital'],
        'Finance': ['stock', 'market', 'invest', 'crypto', 'bitcoin', 'bond', 'forex', 'bank', 'fund', 'economy', 'profit'],
        'Health': ['covid', 'disease', 'doctor', 'hospital', 'vaccine', 'fitness', 'mental health', 'medical', 'healthcare'],
        'Entertainment': ['movie', 'film', 'music', 'celebrity', 'oscar', 'grammy', 'netflix', 'disney', 'star', 'tv'],
        'Science': ['space', 'nasa', 'climate', 'science', 'research', 'discovery', 'quantum', 'biology', 'physics']
    }
    for cat, keywords in cat_map.items():
        if any(k in text for k in keywords):
            return cat
    return 'General'

# Robust article content + image scraper
def fetch_article_content_and_image(url):
    try:
        resp = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code != 200:
            return None, None
        soup = BeautifulSoup(resp.text, 'lxml')
        # Remove scripts, styles, nav, footer, etc.
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript']):
            tag.decompose()
        # Try multiple selectors for article body
        content = None
        for selector in ['article', 'main', '.content', '.post', '.entry-content', '.article-body', '.story-body']:
            article = soup.find('article') or soup.find('main') or soup.find('div', class_=selector.replace('.', ''))
            if article:
                paragraphs = article.find_all('p')
                raw = ' '.join([p.get_text(strip=True) for p in paragraphs])
                if len(raw) > 200:
                    content = raw
                    break
        # Fallback: all paragraphs
        if not content:
            paragraphs = soup.find_all('p')
            raw = ' '.join([p.get_text(strip=True) for p in paragraphs])
            if len(raw) > 200:
                content = raw
        # Get image
        image = None
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            image = og_image.get('content')
        else:
            # fallback: find first large image
            img = soup.find('img')
            if img and img.get('src'):
                if img.get('src').startswith('http'):
                    image = img.get('src')
        return content, image
    except Exception as e:
        print(f"Scrape error: {e}")
        return None, None

# Gemini summary (with fallback)
def get_gemini_summary(title, content):
    if not GEMINI_KEY:
        return content[:200] + "..." if content else f"Latest news on {title}."
    try:
        text = f"Summarize this article in 2 short SEO sentences (max 60 words): Title: {title}. Content: {content[:500]}"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_KEY}"
        payload = {"contents": [{"parts": [{"text": text}]}]}
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            return resp.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"Gemini error: {e}")
    return content[:200] + "..." if content else f"Breaking news on {title}."

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_feed(url):
    resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
    resp.raise_for_status()
    return feedparser.parse(resp.text)

def process_feed(country_code):
    print(f"Fetching {country_code}...")
    try:
        url = f"https://news.google.com/rss?hl=en-{country_code}&gl={country_code}&ceid={country_code}:en"
        feed = fetch_feed(url)
        count = 0
        for entry in feed.entries[:12]:
            article_id = hashlib.md5(entry.link.encode()).hexdigest()
            if any(a['id'] == article_id for a in ARTICLES):
                continue
            
            # Try to get full content and image
            content, image = fetch_article_content_and_image(entry.link)
            
            # If content scraping fails, use the entry summary or title
            if not content:
                if hasattr(entry, 'summary'):
                    content = entry.summary
                else:
                    content = entry.title + " - Read the full article at the source."
            
            # Get summary via Gemini (or fallback)
            summary = get_gemini_summary(entry.title, content)
            category = categorize_article(entry.title, getattr(entry, 'source', {}).get('title', ''))
            
            ARTICLES.append({
                'id': article_id,
                'title': entry.title,
                'link': entry.link,
                'source': getattr(entry, 'source', {}).get('title', 'Google News'),
                'published': getattr(entry, 'published', 'Just now'),
                'country': country_code,
                'summary': summary,
                'content': content,
                'category': category,
                'image': image or ''  # fallback handled in template
            })
            count += 1
        print(f"✅ {country_code} ({count} articles)")
    except Exception as e:
        print(f"❌ {country_code} failed: {e}")

def build_site():
    env = Environment(loader=FileSystemLoader('templates'))
    os.makedirs('dist', exist_ok=True)
    categories = sorted(set(a['category'] for a in ARTICLES))
    
    # Shared context for all pages
    context = {
        'articles': ARTICLES,
        'categories': categories if categories else ['General'],
        'countries': COUNTRIES,
        'country_names': COUNTRY_NAMES,
        'ads': CONFIG.get('ads', {}),
        'ga_header': CONFIG.get('google_analytics', {}).get('header', ''),
        'ga_body': CONFIG.get('google_analytics', {}).get('body', ''),
        'search_console': CONFIG.get('google_search_console', ''),
        'mondiad_meta': CONFIG.get('mondiad_meta', ''),
        'exit_link': CONFIG.get('exit_direct_link', '#'),
        'adsterra_social': CONFIG.get('ads', {}).get('adsterra', {}).get('social_bar', ''),
        'adsterra_728': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_728x90', ''),
        'adsterra_468': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_468x60', ''),
        'adsterra_320': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_320x50', ''),
        'adsterra_native': CONFIG.get('ads', {}).get('adsterra', {}).get('native_banner', ''),
        'aads_sticky': CONFIG.get('ads', {}).get('aads', {}).get('sticky_header', '')
    }

    # Generate homepage
    with open('dist/index.html', 'w') as f:
        f.write(env.get_template('base.html').render(**context))

    # Generate article pages
    os.makedirs('dist/article', exist_ok=True)
    for a in ARTICLES:
        article_context = context.copy()
        article_context['article'] = a
        with open(f'dist/article/{a["id"]}.html', 'w') as f:
            f.write(env.get_template('article.html').render(**article_context))

    # About & Privacy
    for page in ['about', 'privacy']:
        with open(f'dist/{page}.html', 'w') as f:
            f.write(env.get_template(f'{page}.html').render(**context))

    # robots.txt
    with open('dist/robots.txt', 'w') as f:
        f.write("User-agent: *\nAllow: /\nSitemap: https://global-news-aggregator.pages.dev/sitemap.xml")

    # Sitemap
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    base_url = 'https://global-news-aggregator.pages.dev'
    for a in ARTICLES:
        sitemap += f'<url><loc>{base_url}/article/{a["id"]}.html</loc><lastmod>{datetime.now().strftime("%Y-%m-%d")}</lastmod></url>\n'
    sitemap += '</urlset>'
    with open('dist/sitemap.xml', 'w') as f:
        f.write(sitemap)

    print(f"✅ Site built! {len(ARTICLES)} real articles across {len(categories)} categories")

if __name__ == "__main__":
    print("🚀 Starting Global News Pipeline...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        executor.map(process_feed, COUNTRIES)
    build_site()
