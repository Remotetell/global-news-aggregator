import os, json, hashlib, feedparser, requests, time, re
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from jinja2 import Environment, FileSystemLoader
from bs4 import BeautifulSoup
import concurrent.futures

# ==================== LOAD CONFIG ====================
with open('config.json', 'r') as f:
    CONFIG = json.load(f)

# ==================== LOAD API KEYS FROM ENVIRONMENT ====================
GEMINI_KEY = os.getenv('GEMINI_API_KEY', '')
ZENMUX_KEY = os.getenv('ZENMUX_API_KEY', '')
AI_NATIVE_KEY = os.getenv('AI_NATIVE_API_KEY', '')
BAZAAR_KEY = os.getenv('BAZAAR_API_KEY', '')
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY', '')

print(f"🔑 APIs loaded: Gemini={bool(GEMINI_KEY)}, ZenMux={bool(ZENMUX_KEY)}, "
      f"OpenRouter={bool(OPENROUTER_KEY)}, Bazaar={bool(BAZAAR_KEY)}, AI Native={bool(AI_NATIVE_KEY)}")

ARTICLES = []
COUNTRIES = ['US', 'GB', 'CA', 'AU', 'DE', 'FR', 'IT', 'ES', 'JP', 'IN', 'BR']
COUNTRY_NAMES = {
    'US': 'United States', 'GB': 'United Kingdom', 'CA': 'Canada',
    'AU': 'Australia', 'DE': 'Germany', 'FR': 'France',
    'IT': 'Italy', 'ES': 'Spain', 'JP': 'Japan', 'IN': 'India', 'BR': 'Brazil'
}

# ==================== USER-AGENTS FOR ROTATION ====================
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15'
]

def get_random_headers():
    return {'User-Agent': USER_AGENTS[hash(time.time()) % len(USER_AGENTS)]}

# ==================== CATEGORY MAPPING ====================
def categorize_article(title, source, content):
    text = (title + " " + source + " " + (content[:300] if content else "")).lower()
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

# ==================== IMPROVED ARTICLE CONTENT SCRAPING ====================
def fetch_article_content_and_image(url):
    """Fetch full article content with multiple fallback strategies"""
    
    # Try multiple user-agents
    for ua in USER_AGENTS[:3]:  # Try 3 different UAs
        try:
            headers = {'User-Agent': ua, 'Accept-Language': 'en-US,en;q=0.9'}
            resp = requests.get(url, timeout=15, headers=headers)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'lxml')
                
                # Remove junk
                for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript', 'iframe', 'form']):
                    tag.decompose()
                
                # === GET DESCRIPTION ===
                description = None
                og_desc = soup.find('meta', property='og:description')
                if og_desc and og_desc.get('content'):
                    description = og_desc.get('content')
                else:
                    meta_desc = soup.find('meta', attrs={'name': 'description'})
                    if meta_desc and meta_desc.get('content'):
                        description = meta_desc.get('content')
                
                # === GET CONTENT ===
                content = None
                # Try multiple selectors
                selectors = [
                    'article', 'main', '.article-body', '.story-body', '.post-content',
                    '.entry-content', '.content', '.post', '.article-content', '.body-text',
                    '.article__body', '.story__content', '.main-content', '.article-text'
                ]
                
                for selector in selectors:
                    try:
                        element = soup.select_one(selector)
                        if not element:
                            element = soup.find('div', class_=selector.replace('.', ''))
                        if element:
                            paragraphs = element.find_all('p')
                            raw = ' '.join([p.get_text(strip=True) for p in paragraphs])
                            if len(raw) > 200:
                                content = raw
                                break
                    except:
                        continue
                
                # === FALLBACK: ALL PARAGRAPHS ===
                if not content or len(content) < 100:
                    paragraphs = soup.find_all('p')
                    raw = ' '.join([p.get_text(strip=True) for p in paragraphs])
                    # Remove common junk phrases
                    junk_phrases = ['cookie', 'privacy', 'subscribe', 'newsletter', 'advertisement', 'sponsored']
                    if not any(junk in raw.lower() for junk in junk_phrases) and len(raw) > 200:
                        content = raw
                
                # === GET IMAGE ===
                image = None
                og_image = soup.find('meta', property='og:image')
                if og_image and og_image.get('content'):
                    image = og_image.get('content')
                else:
                    # Find first large image
                    for img in soup.find_all('img'):
                        src = img.get('src') or img.get('data-src')
                        if src and src.startswith('http'):
                            # Skip small icons/logos
                            width = img.get('width') or ''
                            height = img.get('height') or ''
                            if not width or (width.isdigit() and int(width) > 100):
                                if 'logo' not in src.lower() and 'icon' not in src.lower():
                                    image = src
                                    break
                
                # If we got content, return it
                if content and len(content) > 100:
                    return content, image, description
                
        except Exception as e:
            print(f"Scrape attempt with {ua[:30]}... failed: {e}")
            continue
    
    # === FINAL FALLBACK: If all scraping fails ===
    print(f"⚠️ All scraping attempts failed for {url[:50]}...")
    return None, None, None

# ==================== AI SUMMARIZATION (Multi-API Fallback) ====================
def summarize_with_gemini(title, content, description):
    if not GEMINI_KEY:
        return None
    try:
        text_context = description if description else content[:500] if content else title
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_KEY}"
        payload = {"contents": [{"parts": [{"text": f"Summarize this news article in 2 short SEO sentences (max 60 words). Title: {title}. Context: {text_context[:600]}"}]}]}
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            return resp.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"Gemini error: {e}")
    return None

def summarize_with_zenmux(title, content, description):
    if not ZENMUX_KEY:
        return None
    try:
        text_context = description if description else content[:500] if content else title
        url = "https://zenmux.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {ZENMUX_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": f"Summarize this news headline in 2 short SEO sentences (max 60 words): {title}. Context: {text_context[:500]}"}],
            "max_tokens": 100
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"ZenMux error: {e}")
    return None

def summarize_with_openrouter(title, content, description):
    if not OPENROUTER_KEY:
        return None
    try:
        text_context = description if description else content[:500] if content else title
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": f"Summarize this news in 2 short sentences (max 60 words): {title}. Context: {text_context[:500]}"}],
            "max_tokens": 100
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"OpenRouter error: {e}")
    return None

def summarize_with_bazaar(title, content, description):
    if not BAZAAR_KEY:
        return None
    try:
        text_context = description if description else content[:500] if content else title
        url = "https://api.bazaarlink.ai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {BAZAAR_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": f"Summarize this news in 2 short sentences (max 60 words): {title}. Context: {text_context[:500]}"}],
            "max_tokens": 100
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"Bazaar error: {e}")
    return None

def summarize_with_ai_native(title, content, description):
    if not AI_NATIVE_KEY:
        return None
    try:
        text_context = description if description else content[:500] if content else title
        url = "https://api.ainative.studio/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {AI_NATIVE_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": f"Summarize this news in 2 short sentences (max 60 words): {title}. Context: {text_context[:500]}"}],
            "max_tokens": 100
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"AI Native error: {e}")
    return None

def get_summary_fallback(title, content, description):
    if description and len(description) > 50:
        return description[:200] + "..."
    elif content and len(content) > 50:
        return content[:200] + "..."
    else:
        return f"Breaking news on {title}."

def generate_summary(title, content, description):
    for summarizer in [summarize_with_gemini, summarize_with_zenmux, summarize_with_openrouter, 
                       summarize_with_bazaar, summarize_with_ai_native]:
        result = summarizer(title, content, description)
        if result and len(result) > 20:
            return result
    return get_summary_fallback(title, content, description)

# ==================== RSS FETCHING ====================
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_feed(url):
    resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
    resp.raise_for_status()
    return feedparser.parse(resp.text)

def process_feed(country_code):
    print(f"Fetching {country_code}...")
    try:
        feed_urls = [
            f"https://news.google.com/rss?hl=en-{country_code}&gl={country_code}&ceid={country_code}:en",
            f"https://news.google.com/rss?hl={country_code.lower()}&gl={country_code}&ceid={country_code}:en"
        ]
        feed = None
        for url in feed_urls:
            try:
                feed = fetch_feed(url)
                if feed and len(feed.entries) > 0:
                    break
            except:
                continue
        
        if not feed or len(feed.entries) == 0:
            print(f"⚠️ No feed for {country_code}, trying trends fallback...")
            trends_url = f"https://trends.google.com/trending/rss?geo={country_code}"
            feed = fetch_feed(trends_url)
        
        count = 0
        for entry in feed.entries[:10]:
            article_id = hashlib.md5(entry.link.encode()).hexdigest()
            if any(a['id'] == article_id for a in ARTICLES):
                continue
            
            # Try to scrape content
            content, image, description = fetch_article_content_and_image(entry.link)
            
            # If scraping failed, use RSS fields
            if not content:
                if hasattr(entry, 'summary'):
                    content = entry.summary
                else:
                    content = entry.title
            if not description:
                description = content[:200] + "..." if content else entry.title
            
            # Generate AI summary
            summary = generate_summary(entry.title, content, description)
            
            category = categorize_article(entry.title, getattr(entry, 'source', {}).get('title', ''), content)
            
            ARTICLES.append({
                'id': article_id,
                'title': entry.title,
                'link': entry.link,
                'source': getattr(entry, 'source', {}).get('title', 'Google News'),
                'published': getattr(entry, 'published', 'Just now'),
                'country': country_code,
                'summary': summary,
                'content': content if content else entry.title,
                'description': description,
                'category': category,
                'image': image or ''
            })
            count += 1
        print(f"✅ {country_code} ({count} articles)")
    except Exception as e:
        print(f"❌ {country_code} failed: {e}")

# ==================== SITE BUILDER ====================
def build_site():
    env = Environment(loader=FileSystemLoader('templates'))
    os.makedirs('dist', exist_ok=True)
    categories = sorted(set(a['category'] for a in ARTICLES)) if ARTICLES else ['General']
    
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
        'adsterra_social': CONFIG.get('ads', {}).get('adsterra', {}).get('social_bar', ''),
        'adsterra_728': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_728x90', ''),
        'adsterra_468': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_468x60', ''),
        'adsterra_320': CONFIG.get('ads', {}).get('adsterra', {}).get('banner_320x50', ''),
        'adsterra_native': CONFIG.get('ads', {}).get('adsterra', {}).get('native_banner', ''),
        'aads_sticky': CONFIG.get('ads', {}).get('aads', {}).get('sticky_header', '')
    }

    with open('dist/index.html', 'w') as f:
        f.write(env.get_template('base.html').render(**context))

    os.makedirs('dist/article', exist_ok=True)
    for a in ARTICLES:
        article_context = context.copy()
        article_context['article'] = a
        with open(f'dist/article/{a["id"]}.html', 'w') as f:
            f.write(env.get_template('article.html').render(**article_context))

    for page in ['about', 'privacy']:
        with open(f'dist/{page}.html', 'w') as f:
            f.write(env.get_template(f'{page}.html').render(**context))

    with open('dist/robots.txt', 'w') as f:
        f.write("User-agent: *\nAllow: /\nSitemap: https://global-news-aggregator.pages.dev/sitemap.xml")

    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    base_url = 'https://global-news-aggregator.pages.dev'
    for a in ARTICLES:
        sitemap += f'<url><loc>{base_url}/article/{a["id"]}.html</loc><lastmod>{datetime.now().strftime("%Y-%m-%d")}</lastmod></url>\n'
    sitemap += '</urlset>'
    with open('dist/sitemap.xml', 'w') as f:
        f.write(sitemap)

    print(f"✅ Site built! {len(ARTICLES)} articles across {len(categories)} categories")

if __name__ == "__main__":
    print("🚀 Starting Global News Pipeline with Multi-API Fallback...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        executor.map(process_feed, COUNTRIES)
    build_site()
