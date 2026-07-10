import os
import json
import hashlib
import feedparser
import requests
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from jinja2 import Environment, FileSystemLoader
from bs4 import BeautifulSoup
import concurrent.futures
import html

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

# Category Mapping
def categorize_article(title, source, content):
    text = (title + " " + source + " " + content[:500]).lower()
    cat_map = {
        'Politics': ['trump', 'biden', 'election', 'congress', 'senate', 'white house', 'minister', 'vote', 'political', 'govt', 'democrat', 'republican'],
        'Sports': ['nba', 'nfl', 'soccer', 'football', 'world cup', 'tennis', 'cricket', 'olympics', 'mlb', 'champions', 'game', 'player'],
        'Technology': ['ai', 'artificial intelligence', 'software', 'tech', 'code', 'google', 'apple', 'microsoft', 'cyber', 'gadget', 'digital'],
        'Finance': ['stock', 'market', 'invest', 'crypto', 'bitcoin', 'bond', 'forex', 'bank', 'fund', 'economy', 'profit', 'loss'],
        'Health': ['covid', 'disease', 'doctor', 'hospital', 'vaccine', 'fitness', 'mental health', 'medical', 'healthcare'],
        'Entertainment': ['movie', 'film', 'music', 'celebrity', 'oscar', 'grammy', 'netflix', 'disney', 'star', 'tv', 'series'],
        'Science': ['space', 'nasa', 'climate', 'science', 'research', 'discovery', 'quantum', 'biology', 'physics']
    }
    for cat, keywords in cat_map.items():
        if any(k in text for k in keywords):
            return cat
    return 'General'

# Fetch FULL Article Content
def fetch_article_content(url):
    try:
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'lxml')
            # Remove script/style tags
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
                tag.decompose()
            # Try to find article body
            article = soup.find('article') or soup.find('main') or soup.find('div', class_='content') or soup.find('div', class_='post')
            if article:
                paragraphs = article.find_all('p')
                content = ' '.join([p.get_text(strip=True) for p in paragraphs[:15]])
                if len(content) > 200:
                    return content
            # Fallback: get all paragraphs
            paragraphs = soup.find_all('p')
            content = ' '.join([p.get_text(strip=True) for p in paragraphs[:15]])
            return content[:1500] if len(content) > 200 else ''
    except Exception:
        pass
    return ''

# Fetch OG Image
def get_og_image(url):
    try:
        resp = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'lxml')
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                return og_image.get('content')
            twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
            if twitter_image and twitter_image.get('content'):
                return twitter_image.get('content')
    except Exception:
        pass
    # Fallback: category-based emoji placeholder
    emojis = {'Sports': '⚽', 'Technology': '💻', 'Finance': '💰', 'Health': '🏥', 'Entertainment': '🎬', 'Science': '🚀', 'Politics': '🏛️', 'General': '📰'}
    return f"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='200'%3E%3Crect width='400' height='200' fill='%23e0e0e0'/%3E%3Ctext x='200' y='110' font-size='60' text-anchor='middle'%3E{emojis.get('General', '📰')}%3C/text%3E%3C/svg%3E"

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type(requests.exceptions.RequestException))
def fetch_feed(url):
    resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
    resp.raise_for_status()
    return feedparser.parse(resp.text)

def get_gemini_summary(title, content):
    if not GEMINI_KEY:
        return f"Latest updates on {title}."
    try:
        text = f"Summarize this news article in 2 short SEO-friendly sentences (max 60 words total): Title: {title}. Content: {content[:500]}"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_KEY}"
        payload = {"contents": [{"parts": [{"text": text}]}]}
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            return resp.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"Gemini error: {e}")
    return content[:150] + "..."

def process_feed(country_code):
    print(f"Fetching {country_code}...")
    try:
        # Fetch from Google News RSS for this country
        url = f"https://news.google.com/rss?hl=en-{country_code}&gl={country_code}&ceid={country_code}:en"
        feed = fetch_feed(url)
        for entry in feed.entries[:15]:
            article_id = hashlib.md5(entry.link.encode()).hexdigest()
            if not any(a['id'] == article_id for a in ARTICLES):
                # Get article content
                content = fetch_article_content(entry.link)
                if not content:
                    continue  # Skip if no content
                category = categorize_article(entry.title, getattr(entry, 'source', {}).get('title', ''), content)
                summary = get_gemini_summary(entry.title, content)
                image_url = get_og_image(entry.link)
                ARTICLES.append({
                    'id': article_id,
                    'title': entry.title,
                    'link': entry.link,
                    'source': getattr(entry, 'source', {}).get('title', 'Google News'),
                    'published': getattr(entry, 'published', 'Just now'),
                    'country': country_code,
                    'summary': summary,
                    'content': content[:800] + '...' if len(content) > 800 else content,
                    'category': category,
                    'image': image_url
                })
        print(f"✅ {country_code} done ({len([a for a in ARTICLES if a['country'] == country_code])} articles)")
    except Exception as e:
        print(f"❌ FAILED {country_code}: {str(e)}")

def build_site():
    env = Environment(loader=FileSystemLoader('templates'))
    os.makedirs('dist', exist_ok=True)
    categories = sorted(set(a['category'] for a in ARTICLES))
    
    context = {
        'articles': ARTICLES,
        'categories': categories,
        'countries': COUNTRIES,
        'country_names': COUNTRY_NAMES,
        'ads': CONFIG.get('ads', {}),
        'ga_header': CONFIG.get('google_analytics', {}).get('header', ''),
        'ga_body': CONFIG.get('google_analytics', {}).get('body', ''),
        'search_console': CONFIG.get('google_search_console', ''),
        'mondiad_meta': CONFIG.get('mondiad_meta', ''),
        'exit_link': CONFIG.get('exit_direct_link', '#'),
        'adsterra_direct': CONFIG.get('ads', {}).get('adsterra', {}).get('direct_link', '#'),
        'adsterra_social': CONFIG.get('ads', {}).get('adsterra', {}).get('social_bar', ''),
        'adsterra_728': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_728x90', ''),
        'adsterra_468': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_468x60', ''),
        'adsterra_320': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_320x50', ''),
        'adsterra_native': CONFIG.get('ads', {}).get('adsterra', {}).get('native_banner', ''),
        'aads_sticky': CONFIG.get('ads', {}).get('aads', {}).get('sticky_header', '')
    }
    
    with open('dist/index.html', 'w') as f:
        f.write(env.get_template('base.html').render(**context))
    for page in ['about', 'privacy']:
        with open(f'dist/{page}.html', 'w') as f:
            f.write(env.get_template(f'{page}.html').render(**context))
    
    # Sitemap
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    base_url = 'https://global-news-aggregator.pages.dev'
    for a in ARTICLES:
        sitemap += f'<url><loc>{base_url}/?id={a["id"]}</loc><lastmod>{datetime.now().strftime("%Y-%m-%d")}</lastmod></url>\n'
    sitemap += '</urlset>'
    with open('dist/sitemap.xml', 'w') as f:
        f.write(sitemap)
    with open('dist/robots.txt', 'w') as f:
        f.write("User-agent: *\nAllow: /\nSitemap: https://global-news-aggregator.pages.dev/sitemap.xml")
    print(f"✅ Site built! {len(ARTICLES)} articles across {len(categories)} categories")

if __name__ == "__main__":
    print("🚀 Starting Global News Pipeline...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(process_feed, COUNTRIES)
    build_site()
