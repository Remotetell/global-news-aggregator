import os, json, hashlib, feedparser, requests, time
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from jinja2 import Environment, FileSystemLoader
import concurrent.futures

with open('config.json', 'r') as f:
    CONFIG = json.load(f)

ARTICLES = []
COUNTRIES = ['US', 'GB', 'CA', 'AU', 'DE', 'FR', 'IT', 'ES', 'JP', 'IN', 'BR']
COUNTRY_NAMES = {
    'US': 'United States', 'GB': 'United Kingdom', 'CA': 'Canada',
    'AU': 'Australia', 'DE': 'Germany', 'FR': 'France',
    'IT': 'Italy', 'ES': 'Spain', 'JP': 'Japan', 'IN': 'India', 'BR': 'Brazil'
}

def categorize_article(title, source):
    text = (title + " " + source).lower()
    cat_map = {
        'Politics': ['trump', 'biden', 'election', 'vote', 'president', 'minister', 'govt'],
        'Sports': ['nba', 'nfl', 'soccer', 'football', 'tennis', 'cricket', 'olympics'],
        'Technology': ['ai', 'tech', 'google', 'apple', 'microsoft', 'cyber', 'software'],
        'Finance': ['stock', 'market', 'crypto', 'bitcoin', 'bank', 'economy', 'invest'],
        'Health': ['covid', 'doctor', 'hospital', 'vaccine', 'health', 'fitness'],
        'Entertainment': ['movie', 'film', 'music', 'celebrity', 'netflix', 'disney'],
        'Science': ['space', 'nasa', 'climate', 'science', 'research', 'quantum']
    }
    for cat, keywords in cat_map.items():
        if any(k in text for k in keywords):
            return cat
    return 'General'

def get_og_image(url):
    try:
        resp = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'lxml')
            og = soup.find('meta', property='og:image')
            if og and og.get('content'):
                return og.get('content')
    except:
        pass
    return 'https://placehold.co/400x200/1a73e8/white?text=News'

@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=5))
def fetch_feed(url):
    resp = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
    resp.raise_for_status()
    return feedparser.parse(resp.text)

def process_feed(country_code):
    print(f"Fetching {country_code}...")
    try:
        url = f"https://trends.google.com/trending/rss?geo={country_code}"
        feed = fetch_feed(url)
        count = 0
        for entry in feed.entries[:10]:
            if not any(a['title'] == entry.title for a in ARTICLES):
                ARTICLES.append({
                    'id': hashlib.md5(entry.title.encode()).hexdigest(),
                    'title': entry.title,
                    'link': entry.link,
                    'source': getattr(entry, 'source', {}).get('title', 'Google News'),
                    'published': getattr(entry, 'published', 'Just now'),
                    'country': country_code,
                    'summary': entry.title[:150] + "...",
                    'category': categorize_article(entry.title, ''),
                    'image': get_og_image(entry.link)
                })
                count += 1
        print(f"✅ {country_code} ({count} articles)")
    except Exception as e:
        print(f"❌ {country_code} failed: {e}")

def build_site():
    env = Environment(loader=FileSystemLoader('templates'))
    os.makedirs('dist', exist_ok=True)
    categories = sorted(set(a['category'] for a in ARTICLES))
    
    # If no articles, add demo articles so site works
    if not ARTICLES:
        for i in range(20):
            ARTICLES.append({
                'id': str(i),
                'title': f"Global News Sample Article {i+1}",
                'link': '#',
                'source': 'Google News',
                'published': 'Just now',
                'country': 'US',
                'summary': 'This is a sample article. Real articles will appear once the RSS feed works.',
                'category': ['Sports', 'Tech', 'Finance', 'Health', 'Entertainment'][i % 5],
                'image': 'https://placehold.co/400x200/1a73e8/white?text=Global+News'
            })
    
    context = {
        'articles': ARTICLES,
        'categories': categories if categories else ['General'],
        'countries': COUNTRIES,
        'country_names': COUNTRY_NAMES,
        'ga_header': CONFIG.get('google_analytics', {}).get('header', ''),
        'ga_body': CONFIG.get('google_analytics', {}).get('body', ''),
        'search_console': CONFIG.get('google_search_console', ''),
        'mondiad_meta': CONFIG.get('mondiad_meta', ''),
        'exit_link': CONFIG.get('exit_direct_link', '#'),
        'adsterra_728': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_728x90', ''),
        'adsterra_468': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_468x60', ''),
        'adsterra_320': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_320x50', ''),
        'adsterra_native': CONFIG.get('ads', {}).get('adsterra', {}).get('native_banner', ''),
        'adsterra_social': CONFIG.get('ads', {}).get('adsterra', {}).get('social_bar', ''),
        'aads_sticky': CONFIG.get('ads', {}).get('aads', {}).get('sticky_header', '')
    }
    with open('dist/index.html', 'w') as f:
        f.write(env.get_template('base.html').render(**context))
    with open('dist/about.html', 'w') as f:
        f.write("<h1>About</h1><p>Global Trends News</p>")
    with open('dist/privacy.html', 'w') as f:
        f.write("<h1>Privacy</h1><p>We use ads.</p>")
    with open('dist/robots.txt', 'w') as f:
        f.write("User-agent: *\nAllow: /")
    with open('dist/sitemap.xml', 'w') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>')
    print(f"✅ Site built! {len(ARTICLES)} articles")

if __name__ == "__main__":
    print("🚀 Starting...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(process_feed, COUNTRIES)
    build_site()
