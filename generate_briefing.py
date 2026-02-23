#!/usr/bin/env python3
"""
Daily Briefing Generator
Searches news via Brave API, generates HTML via AI API, sends via Resend API.
Designed to run in GitHub Actions (cloud, no Mac needed).

AI Provider options (set AI_PROVIDER env var):
  gemini   — Google Gemini 1.5 Flash (FREE, recommended)
  openai   — OpenAI GPT-4o-mini (~$0.004/day)
  anthropic — Claude 3.5 Sonnet (~$0.08/day)
"""
import os
import json
import time
import requests
from datetime import datetime, timedelta

# ── Config ──────────────────────────────────────────────────────────────
BRAVE_API_KEY  = os.environ['BRAVE_API_KEY']
RESEND_API_KEY = os.environ['RESEND_API_KEY']
RECIPIENT      = os.environ.get('RECIPIENT', 'ziyanjiang1116@gmail.com')
DEDUP_FILE     = 'sent_articles.json'

# AI provider selection — default: gemini (free)
AI_PROVIDER = os.environ.get('AI_PROVIDER', 'gemini').lower()

def make_ai_client():
    if AI_PROVIDER == 'gemini':
        import google.generativeai as genai
        genai.configure(api_key=os.environ['GEMINI_API_KEY'])
        return genai.GenerativeModel('gemini-2.0-flash')
    elif AI_PROVIDER == 'openai':
        from openai import OpenAI
        return OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    elif AI_PROVIDER == 'anthropic':
        import anthropic
        return anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
    else:
        raise ValueError(f"Unknown AI_PROVIDER: {AI_PROVIDER}")

def call_ai(client, prompt):
    """Unified AI call — returns generated text string."""
    if AI_PROVIDER == 'gemini':
        response = client.generate_content(prompt)
        return response.text
    elif AI_PROVIDER == 'openai':
        response = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=8000,
        )
        return response.choices[0].message.content
    elif AI_PROVIDER == 'anthropic':
        response = client.messages.create(
            model='claude-3-5-sonnet-20241022',
            max_tokens=8096,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return response.content[0].text

# ── Deduplication ────────────────────────────────────────────────────────
def load_sent_articles():
    if os.path.exists(DEDUP_FILE):
        with open(DEDUP_FILE, 'r') as f:
            data = json.load(f)
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        return {url: ts for url, ts in data.items() if ts > cutoff}
    return {}

def save_sent_articles(sent):
    with open(DEDUP_FILE, 'w') as f:
        json.dump(sent, f, indent=2, ensure_ascii=False)

# ── Brave Search ─────────────────────────────────────────────────────────
def search_news(query, count=6):
    headers = {
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip',
        'X-Subscription-Token': BRAVE_API_KEY,
    }
    params = {
        'q': query,
        'count': count,
        'freshness': 'pd',         # past day
        'text_decorations': False,
        'search_lang': 'en',
    }
    try:
        r = requests.get(
            'https://api.search.brave.com/res/v1/news/search',
            headers=headers, params=params, timeout=15
        )
        if r.status_code == 200:
            return r.json().get('results', [])
        elif r.status_code == 429:
            print(f"  Brave 429 rate limit, waiting 5s...")
            time.sleep(5)
            # retry once
            r2 = requests.get(
                'https://api.search.brave.com/res/v1/news/search',
                headers=headers, params=params, timeout=15
            )
            if r2.status_code == 200:
                return r2.json().get('results', [])
            elif r2.status_code == 429:
                print(f"  Brave 429 again, skipping query.")
                return []
        else:
            print(f"  Brave API {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  Search error for '{query}': {e}")
    return []

def collect_news(sent_urls):
    categories = {
        'AI': [
            'artificial intelligence AI news today 2026',
            'OpenAI Google DeepMind Anthropic news',
            'AI startup funding model release 2026',
        ],
        'VC': [
            'venture capital startup funding raised million 2026',
            'Series A B C D funding round announced 2026',
            'unicorn IPO acquisition tech startup 2026',
        ],
        'Crypto': [
            'bitcoin ethereum crypto news today 2026',
            'DeFi blockchain Web3 stablecoin news 2026',
            'crypto regulation SEC market 2026',
        ],
        'Macro': [
            'Federal Reserve interest rates inflation US economy 2026',
            'US GDP employment tariff trade macro economy 2026',
            'China Europe economy geopolitics market 2026',
        ],
    }

    all_articles = {}
    for category, queries in categories.items():
        articles = []
        seen_urls = set(sent_urls.keys())
        for query in queries:
            time.sleep(2)     # Brave free tier: max 1 req/sec, 2s buffer
            results = search_news(query, count=5)
            for r in results:
                url = r.get('url', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    articles.append({
                        'title':       r.get('title', ''),
                        'url':         url,
                        'description': r.get('description', ''),
                        'source':      r.get('meta_url', {}).get('hostname', ''),
                        'age':         r.get('age', ''),
                    })
        # Keep max 6 per category
        all_articles[category] = articles[:6]
        print(f"  {category}: {len(all_articles[category])} articles")

    return all_articles

# ── Claude HTML Generation ───────────────────────────────────────────────
def generate_html(client, articles_by_category, today):
    articles_json = json.dumps(articles_by_category, ensure_ascii=False, indent=2)

    prompt = f"""Today is {today}. Below are raw news articles in 4 categories collected via Brave Search.
Generate a beautiful Chinese-language daily briefing as a COMPLETE, SELF-CONTAINED HTML email.

RAW ARTICLES:
{articles_json}

REQUIREMENTS:
1. Write a 2-3 sentence Chinese summary for each article. Translate the title to Chinese.
2. Importance levels:
   - 🔥 高 (HIGH): funding ≥$50M, major Fed/policy action, large market move (±5%+)
   - ⭐ 中 (MEDIUM): notable news, moderate funding, regular updates
   - 📰 低 (LOW): minor news
3. EVERY article must have a "🔗 阅读原文" hyperlink using the article's URL.
4. Sections: 🤖 人工智能 | 💰 投融资/VC | ₿ 加密货币/Crypto | 📈 宏观经济
5. Skip articles that are clearly older than 48 hours.
6. NO footer, NO "Powered by", NO contact info, NO config paths.

HTML DESIGN SPEC:
- Dark gradient header: background linear-gradient(135deg,#1a1a2e,#0f3460); white text; "📰 Daily Briefing" h1; date subtitle
- Stats bar: light gray bg, shows total count + HIGH/MEDIUM/LOW counts
- Section headers: emoji + bold title + bottom border
- Article cards: border-radius:10px; colored left border (red=#e53935 HIGH, orange=#fb8c00 MEDIUM, green=#43a047 LOW)
- Importance tag: small colored pill chip above title
- Chinese title: 15px bold; English title: 11px italic gray below it
- Summary: 13px, color #444
- Footer of each card: source name (left) + 🔗 阅读原文 blue pill button (right)
- Max-width: 680px, centered, white bg, subtle box-shadow, border-radius:16px
- Mobile responsive

Return ONLY the complete HTML document (starting with <!DOCTYPE html>), no markdown, no explanation."""

    return call_ai(client, prompt)

# ── Send Email via Resend ─────────────────────────────────────────────────
def send_email(html_content, date_str):
    headers = {
        'Authorization': f'Bearer {RESEND_API_KEY}',
        'Content-Type': 'application/json',
    }
    payload = {
        "from":    "Daily Briefing <briefing@resend.dev>",
        "to":      [RECIPIENT],
        "subject": f"📰 Daily Briefing · {date_str}",
        "html":    html_content,
    }
    r = requests.post('https://api.resend.com/emails',
                      json=payload, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()

# ── Main ─────────────────────────────────────────────────────────────────
def main():
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"\n{'='*50}")
    print(f"Daily Briefing — {today}")
    print('='*50)

    # Load deduplication history
    sent_urls = load_sent_articles()
    print(f"Dedup: {len(sent_urls)} previously sent URLs loaded")

    # Collect news
    print("\nSearching news...")
    articles = collect_news(sent_urls)
    total = sum(len(v) for v in articles.values())
    print(f"Total new articles: {total}")

    if total == 0:
        print("No new articles — skipping send.")
        return

    # Generate HTML with AI
    print(f"\nGenerating HTML with {AI_PROVIDER.upper()} API...")
    ai_client = make_ai_client()
    html = generate_html(ai_client, articles, today)
    print(f"HTML generated: {len(html):,} chars")

    # Save archive
    os.makedirs('archives', exist_ok=True)
    archive_path = f'archives/briefing-{today}.html'
    with open(archive_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Archived: {archive_path}")

    # Send email
    print(f"\nSending to {RECIPIENT}...")
    result = send_email(html, today)
    print(f"✅ Email sent! ID: {result.get('id')}")

    # Update dedup history
    new_urls = {
        article['url']: datetime.now().isoformat()
        for cat_articles in articles.values()
        for article in cat_articles
        if article.get('url')
    }
    sent_urls.update(new_urls)
    save_sent_articles(sent_urls)
    print(f"Dedup: saved {len(new_urls)} new URLs")

if __name__ == '__main__':
    main()
