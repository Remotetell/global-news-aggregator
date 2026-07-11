import os, json, hashlib, feedparser, requests, time, re
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from jinja2 import Environment, FileSystemLoader
from bs4 import BeautifulSoup
import concurrent.futures
from newspaper import Article
import tldextract
import openai
from gnews import GNews

# ==================== LOAD CONFIG ====================
with open('config.json', 'r') as f:
    CONFIG = json.load(f)

# ==================== LOAD API KEYS ====================
GEMINI_KEY = os.getenv('GEMINI_API_KEY', '')
ZENMUX_KEY = os.getenv('ZENMUX_API_KEY', '')
AI_NATIVE_KEY = os.getenv('AI_NATIVE_API_KEY', '')
BAZAAR_KEY = os.getenv('BAZAAR_API_KEY', '')
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY', '')
OPENAI_KEY = os.getenv('OPENAI_API_KEY', '')

print(f"🔑 APIs loaded: Gemini={bool(GEMINI_KEY)}, ZenMux={bool(ZENMUX_KEY)}, "
      f"OpenRouter={bool(OPENROUTER_KEY)}, Bazaar={bool(BAZAAR_KEY)}, AI Native={bool(AI_NATIVE_KEY)}, OpenAI={bool(OPENAI_KEY)}")

# Get cloaking link from config
CLOAK_LINK = CONFIG.get('cloak_direct_link', CONFIG.get('exit_direct_link', '#'))

ARTICLES = []
COUNTRIES = ['US', 'GB', 'CA', 'AU', 'DE', 'FR', 'IT', 'ES', 'JP', 'IN', 'BR']
COUNTRY_NAMES = {
    'US': 'United States', 'GB': 'United Kingdom', 'CA': 'Canada',
    'AU': 'Australia', 'DE': 'Germany', 'FR': 'France',
    'IT': 'Italy', 'ES': 'Spain', 'JP': 'Japan', 'IN': 'India', 'BR': 'Brazil'
}

# ==================== ARTICLE EXTRACTION ====================
def fetch_with_newspaper(url):
    try:
        article = Article(url, language='en', 
                         headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        article.download()
        article.parse()
        article.nlp()
        content = article.text
        image = article.top_image
        description = article.meta_description
        if not description and content:
            description = content[:200] + "..."
        return content, image, description
    except Exception as e:
        print(f"Newspaper extraction failed: {e}")
        return None, None, None

def fetch_with_bs4_fallback(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(url, timeout=15, headers=headers)
        if resp.status_code != 200:
            return None, None, None
        soup = BeautifulSoup(resp.text, 'lxml')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript', 'iframe']):
            tag.decompose()
        description = None
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            description = og_desc.get('content')
        else:
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                description = meta_desc.get('content')
        content = None
        selectors = ['article', 'main', '.article-body', '.story-body', '.post-content', '.entry-content', '.content']
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                paragraphs = element.find_all('p')
                raw = ' '.join([p.get_text(strip=True) for p in paragraphs])
                if len(raw) > 200:
                    content = raw
                    break
        if not content:
            paragraphs = soup.find_all('p')
            raw = ' '.join([p.get_text(strip=True) for p in paragraphs])
            if len(raw) > 200:
                content = raw
        image = None
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            image = og_image.get('content')
        else:
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src')
                if src and src.startswith('http') and 'logo' not in src.lower() and 'icon' not in src.lower():
                    image = src
                    break
        return content, image, description
    except Exception as e:
        print(f"BS4 fallback failed: {e}")
        return None, None, None

def fetch_article_content_and_image(url):
    print(f"📰 Extracting: {url[:60]}...")
    content, image, description = fetch_with_newspaper(url)
    if not content or len(content) < 100:
        print(f"   Newspaper returned empty, trying BS4 fallback...")
        content, image, description = fetch_with_bs4_fallback(url)
    if content and len(content) < 50:
        print(f"   Content too short ({len(content)} chars)")
    return content, image, description

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

# ==================== AI CONTENT GENERATION (not just summary) ====================
def generate_ai_content(title, content, description, url):
    """Generate unique, well-optimized article content from URL context"""
    if not content or len(content) < 100:
        # If no content, ask AI to write based on title and description
        text_context = f"Title: {title}. Description: {description if description else 'No description'}"
    else:
        text_context = f"Title: {title}. Content preview: {content[:800]}"
    
    # Try all APIs to generate unique content
    for api_call in [summarize_with_gemini, summarize_with_zenmux, summarize_with_openrouter,
                     summarize_with_bazaar, summarize_with_ai_native, summarize_with_openai]:
        result = api_call(title, content, description)
        if result and len(result) > 50:
            return result
    
    # Fallback: use description or content
    if description and len(description) > 50:
        return f"{title}\n\n{description}\n\nRead the full article at the source."
    elif content and len(content) > 50:
        return content
    else:
        return f"{title}\n\nThis article covers the latest developments on this topic. Visit the original source for more details."

# ==================== AI SUMMARIZATION ====================
def summarize_with_gemini(title, content, description):
    if not GEMINI_KEY:
        return None
    try:
        text_context = description if description else content[:500] if content else title
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_KEY}"
        payload = {"contents": [{"parts": [{"text": f"Write a well-structured news article (2-3 paragraphs) based on this: Title: {title}. Context: {text_context[:600]}"}]}]}
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
            "messages": [{"role": "user", "content": f"Write a well-structured news article (2-3 paragraphs) based on this: Title: {title}. Context: {text_context[:500]}"}],
            "max_tokens": 300
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
            "messages": [{"role": "user", "content": f"Write a well-structured news article (2-3 paragraphs) based on this: Title: {title}. Context: {text_context[:500]}"}],
            "max_tokens": 300
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
            "messages": [{"role": "user", "content": f"Write a well-structured news article (2-3 paragraphs) based on this: Title: {title}. Context: {text_context[:500]}"}],
            "max_tokens": 300
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
            "messages": [{"role": "user", "content": f"Write a well-structured news article (2-3 paragraphs) based on this: Title: {title}. Context: {text_context[:500]}"}],
            "max_tokens": 300
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"AI Native error: {e}")
    return None

def summarize_with_openai(title, content, description):
    if not OPENAI_KEY:
        return None
    try:
        openai.api_key = OPENAI_KEY
        text_context = description if description else content[:500] if content else title
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"Write a well-structured news article (2-3 paragraphs) based on this: Title: {title}. Context: {text_context[:500]}"}],
            max_tokens=300
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"OpenAI error: {e}")
    return None

def get_short_summary(title, content, description):
    """Short summary for the card preview"""
    if description and len(description) > 50:
        return description[:200] + "..."
    elif content and len(content) > 50:
        return content[:200] + "..."
    else:
        return f"Latest news on {title}."

# ==================== RSS FETCHING ====================
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_feed(url):
    resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
    resp.raise_for_status()
    return feedparser.parse(resp.text)

def fetch_with_gnews(country_code):
    try:
        gn = GNews(language='en', country=country_code, max_results=15)
        articles = gn.get_news(country_code)
        entries = []
        for item in articles:
            entries.append({
                'title': item.get('title', ''),
                'link': item.get('url', ''),
                'summary': item.get('description', ''),
                'published': item.get('published date', 'Just now'),
                'source': {'title': item.get('source', {}).get('title', 'GNews')}
            })
        class Feed:
            entries = entries
        return Feed() if entries else None
    except Exception as e:
        print(f"GNews fallback failed: {e}")
        return None

def cloak_url(url):
    """Cloak any URL with the Adsterra direct link"""
    return CLOAK_LINK + "&url=" + requests.utils.quote(url, safe='')

def process_feed(country_code):
    print(f"🌍 Fetching {country_code}...")
    feed = None
    try:
        feed_urls = [
            f"https://news.google.com/rss?hl=en-{country_code}&gl={country_code}&ceid={country_code}:en",
            f"https://news.google.com/rss?hl={country_code.lower()}&gl={country_code}&ceid={country_code}:en"
        ]
        for url in feed_urls:
            try:
                feed = fetch_feed(url)
                if feed and len(feed.entries) > 0:
                    break
            except:
                continue
        
        if not feed or len(feed.entries) == 0:
            print(f"   ⚠️ RSS feed failed for {country_code}, trying GNews fallback...")
            feed = fetch_with_gnews(country_code)
        
        if not feed or len(feed.entries) == 0:
            print(f"   ⚠️ No feed for {country_code}, trying trends fallback...")
            trends_url = f"https://trends.google.com/trending/rss?geo={country_code}"
            feed = fetch_feed(trends_url)
        
        if not feed or len(feed.entries) == 0:
            print(f"❌ {country_code} failed completely")
            return
        
        count = 0
        for entry in feed.entries[:12]:
            if hasattr(entry, 'link'):
                link = entry.link
                title = entry.title
                source = getattr(entry, 'source', {}).get('title', 'Google News')
                published = getattr(entry, 'published', 'Just now')
                summary_field = entry.summary if hasattr(entry, 'summary') else ''
            else:
                link = entry.get('link', '')
                title = entry.get('title', '')
                source = entry.get('source', {}).get('title', 'GNews')
                published = entry.get('published', 'Just now')
                summary_field = entry.get('summary', '')
            
            if not link:
                continue
            
            article_id = hashlib.md5(link.encode()).hexdigest()
            if any(a['id'] == article_id for a in ARTICLES):
                continue
            
            # Extract content
            content, image, description = fetch_article_content_and_image(link)
            
            # Generate AI content (unique article)
            ai_content = generate_ai_content(title, content, description, link)
            
            # Get short summary for card preview
            short_summary = get_short_summary(title, content, description)
            
            # If AI content is too short, use description or content
            if len(ai_content) < 100:
                if description and len(description) > 50:
                    ai_content = description + "\n\nContinue reading at the source."
                elif content and len(content) > 50:
                    ai_content = content
                else:
                    ai_content = f"{title}\n\nRead the full article at the source."
            
            category = categorize_article(title, source, content or ai_content)
            
            # Cloak the original link
            cloaked_url = cloak_url(link)
            
            ARTICLES.append({
                'id': article_id,
                'title': title,
                'link': link,
                'cloaked_link': cloaked_url,
                'source': source,
                'published': published,
                'country': country_code,
                'summary': short_summary,
                'content': ai_content,
                'description': description or short_summary,
                'category': category,
                'image': image or ''
            })
            count += 1
            print(f"   ✅ Article: {title[:50]}... (Content: {len(ai_content)} chars)")
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
        'cloak_link': CLOAK_LINK,
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
    print("🚀 Starting Global News Pipeline with Newspaper3k + AI Content Generation + Cloaking...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        executor.map(process_feed, COUNTRIES)
    build_site()
