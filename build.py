import os
import json
import hashlib
import feedparser
import requests
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from jinja2 import Environment, FileSystemLoader
import concurrent.futures

with open('config.json', 'r') as f:
    CONFIG = json.load(f)

GEMINI_KEY = os.getenv('GEMINI_API_KEY')
ARTICLES = []
COUNTRY_NAMES = {
    'US': 'United States', 'GB': 'United Kingdom', 'CA': 'Canada',
    'AU': 'Australia', 'DE': 'Germany', 'FR': 'France',
    'IT': 'Italy', 'ES': 'Spain', 'JP': 'Japan', 'IN': 'India', 'BR': 'Brazil'
}
COUNTRIES = list(COUNTRY_NAMES.keys())

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type(requests.exceptions.RequestException))
def fetch_feed(url):
    resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
    resp.raise_for_status()
    return feedparser.parse(resp.text)

def get_gemini_summary(title):
    if not GEMINI_KEY:
        return f"Latest updates on {title}."
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_KEY}"
        payload = {"contents": [{"parts": [{"text": f"Summarize this headline in 2 short SEO sentences: {title}"}]}]}
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"Gemini error: {e}")
    return f"Breaking news on {title}."

def process_feed(country_code):
    print(f"Fetching {country_code}...")
    try:
        url = f"https://trends.google.com/trending/rss?geo={country_code}"
        feed = fetch_feed(url)
        for entry in feed.entries[:10]:
            article_id = hashlib.md5(entry.title.encode()).hexdigest()
            if not any(a['id'] == article_id for a in ARTICLES):
                summary = get_gemini_summary(entry.title)
                category = 'General'
                for cat in ['Sports', 'Business', 'Technology', 'Finance', 'Health', 'Entertainment']:
                    if cat.lower() in entry.title.lower():
                        category = cat
                        break
                ARTICLES.append({
                    'id': article_id,
                    'title': entry.title,
                    'link': entry.link,
                    'published': entry.published,
                    'country': country_code,
                    'summary': summary,
                    'category': category
                })
        print(f"✅ {country_code} done")
    except Exception as e:
        # GRACEFUL FAILURE: logs error but continues building
        print(f"❌ FAILED {country_code}: {str(e)}")

def build_site():
    env = Environment(loader=FileSystemLoader('templates'))
    os.makedirs('dist', exist_ok=True)
    context = {
        'articles': ARTICLES,
        'countries': COUNTRIES,
        'country_names': COUNTRY_NAMES,
        'ad_header': CONFIG.get('ad_header', ''),
        'ad_body': CONFIG.get('ad_body', ''),
        'ad_footer': CONFIG.get('ad_footer', ''),
        'ga_tag': CONFIG.get('google_analytics', ''),
        'search_console': CONFIG.get('google_search_console', ''),
        'exit_link': CONFIG.get('exit_direct_link', '#')
    }
    with open('dist/index.html', 'w') as f:
        f.write(env.get_template('base.html').render(**context))
    for page in ['about', 'privacy']:
        with open(f'dist/{page}.html', 'w') as f:
            f.write(env.get_template(f'{page}.html').render(**context))
    
    # Sitemap
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    base_url = 'https://your-site.pages.dev'
    for a in ARTICLES:
        sitemap += f'<url><loc>{base_url}/?id={a["id"]}</loc><lastmod>{datetime.now().strftime("%Y-%m-%d")}</lastmod></url>\n'
    sitemap += '</urlset>'
    with open('dist/sitemap.xml', 'w') as f:
        f.write(sitemap)
    with open('dist/robots.txt', 'w') as f:
        f.write("User-agent: *\nAllow: /\nSitemap: https://your-site.pages.dev/sitemap.xml")
    print(f"✅ Site built! {len(ARTICLES)} articles")

if __name__ == "__main__":
    print("🚀 Starting...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(process_feed, COUNTRIES)
    build_site()
