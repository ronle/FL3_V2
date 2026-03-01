"""
Cameron News Backfill — FMP + Arctic Shift (Reddit)

Backfills news articles for the Cameron scanner universe (~4,042 symbols) into
the FL3 articles table so sentiment correlation analysis can be re-run with
better coverage.

Two data sources:
  1. FMP: Formal news articles via Financial Modeling Prep API
  2. Arctic Shift: Reddit posts from wallstreetbets, pennystocks, etc.

Both insert into the shared `articles` table. After backfill, run the LLM
analysis pipeline to populate article_sentiment → sentiment_daily.

Usage:
    python -m scripts.cameron_news_backfill --phase fmp
    python -m scripts.cameron_news_backfill --phase reddit
    python -m scripts.cameron_news_backfill --phase both
    python -m scripts.cameron_news_backfill --phase report   # coverage check only
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

import duckdb
import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────
UNIVERSE_PATH = "E:/backtest_cache/cameron_daily_universe.parquet"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(BASE_DIR, "backtest_results")

# ── DB ────────────────────────────────────────────────────────────────
_LOCAL_DB = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"

def get_db_url():
    url = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "").strip()
    if not url or "/cloudsql/" in url:
        url = _LOCAL_DB
    return url

def get_conn():
    return psycopg2.connect(get_db_url())

# ── Cameron Symbols ──────────────────────────────────────────────────
GAP_PCT_MIN = 0.04
RVOL_MIN = 5.0
PRICE_MIN = 1.0
PRICE_MAX = 20.0

def load_cameron_symbols() -> List[str]:
    """Load unique Cameron-eligible symbols from universe parquet."""
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT DISTINCT symbol
        FROM read_parquet('{UNIVERSE_PATH}')
        WHERE gap_pct >= {GAP_PCT_MIN}
          AND rvol >= {RVOL_MIN} AND rvol < 1000000
          AND close_price >= {PRICE_MIN} AND close_price <= {PRICE_MAX}
        ORDER BY symbol
    """).fetchdf()
    con.close()
    symbols = df["symbol"].tolist()
    log.info(f"Loaded {len(symbols)} Cameron-eligible symbols")
    return symbols


# ═══════════════════════════════════════════════════════════════════════
# FMP BACKFILL
# ═══════════════════════════════════════════════════════════════════════

FMP_BASE_URL = "https://financialmodelingprep.com/stable/news/stock"
FMP_BACKFILL_START = date(2025, 1, 1)

def get_fmp_api_key() -> str:
    """Get FMP API key from env, GCP secrets, or hardcoded fallback."""
    key = os.environ.get("FMP_API_KEY")
    if key:
        return key
    # Try GCP Secret Manager
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = "projects/spartan-buckeye-474319-q8/secrets/FMP_API_KEY/versions/latest"
        resp = client.access_secret_version(request={"name": name})
        return resp.payload.data.decode("UTF-8").strip()
    except Exception:
        pass
    raise RuntimeError("FMP_API_KEY not found in environment or GCP secrets")


def seed_fmp_backfill_state(conn, symbols: List[str]) -> int:
    """Seed Cameron symbols into news_backfill_state_fmp table."""
    today = date.today()
    seeded = 0
    cur = conn.cursor()
    for sym in symbols:
        cur.execute("""
            INSERT INTO news_backfill_state_fmp (
                ticker, source, tier, start_date, end_date,
                cursor_from, cursor_to, last_page, done, priority
            )
            VALUES (%s, 'FMP', 'cameron', %s, %s, %s, %s, 0, false, 5)
            ON CONFLICT (ticker) DO UPDATE SET
                done = false,
                start_date = LEAST(news_backfill_state_fmp.start_date, EXCLUDED.start_date),
                end_date = GREATEST(news_backfill_state_fmp.end_date, EXCLUDED.end_date),
                tier = CASE WHEN news_backfill_state_fmp.tier = 'test' THEN 'cameron' ELSE news_backfill_state_fmp.tier END,
                updated_at = now()
            WHERE news_backfill_state_fmp.done = true
        """, (sym, FMP_BACKFILL_START, today, today.replace(day=1), today))
        if cur.rowcount > 0:
            seeded += 1

    conn.commit()
    log.info(f"Seeded {seeded} new/reset symbols into FMP backfill state")
    return seeded


def fetch_fmp_page(api_key: str, ticker: str, page: int = 0, limit: int = 50) -> Tuple[list, int]:
    """Fetch one page of FMP stock news. Returns (articles_list, response_bytes)."""
    import httpx

    url = FMP_BASE_URL
    params = {
        "symbols": ticker.upper(),
        "page": page,
        "limit": limit,
        "apikey": api_key,
    }

    for attempt in range(3):
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(url, params=params)
                resp_bytes = len(resp.content)

                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    log.warning(f"FMP rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue

                resp.raise_for_status()
                data = resp.json()
                return (data if isinstance(data, list) else []), resp_bytes
        except Exception as e:
            if attempt == 2:
                log.error(f"FMP fetch failed for {ticker}: {e}")
                return [], 0
            time.sleep(2 ** attempt)

    return [], 0


def insert_fmp_article(cur, article: dict, ticker: str) -> bool:
    """Insert one FMP article into articles table. Returns True if inserted."""
    url = article.get("url", "")
    if not url:
        return False

    # Normalize URL
    try:
        parsed = urllib.parse.urlparse(url)
        norm_url = urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc.lower(),
            parsed.path.rstrip("/"), "", "", ""
        ))
    except Exception:
        norm_url = url

    title = article.get("title", "")
    text = article.get("text", "")
    pub_date_str = article.get("publishedDate", "")
    site = article.get("site", "")

    # Parse publish time
    pub_time = None
    if pub_date_str:
        try:
            pub_time = datetime.fromisoformat(pub_date_str.replace(" ", "T"))
        except ValueError:
            pass

    content_hash = hashlib.sha256(f"{title}|{pub_date_str}|{site}".encode()).hexdigest()[:32]
    raw_json = json.dumps({"symbol": ticker, "site": site, "url": url, "image": article.get("image")})

    cur.execute("""
        INSERT INTO articles (source, canonical_url, title, summary, content,
                              authors, publish_time, lang, content_hash, raw)
        VALUES ('FMP', %s, %s, %s, %s, %s, %s, 'en', %s, %s)
        ON CONFLICT (canonical_url) DO NOTHING
        RETURNING id
    """, (norm_url, title, (text[:500] if text else None), text, site, pub_time, content_hash, raw_json))

    result = cur.fetchone()
    if result:
        article_id = result[0]
        # Insert entity mapping
        cur.execute("""
            INSERT INTO article_entities (article_id, entity_type, entity_value)
            VALUES (%s, 'ticker', %s)
            ON CONFLICT DO NOTHING
        """, (article_id, ticker.upper()))
        return True
    return False


def run_fmp_backfill(symbols: List[str]):
    """Run FMP news backfill for Cameron symbols."""
    api_key = get_fmp_api_key()
    conn = get_conn()

    # Seed state table
    seed_fmp_backfill_state(conn, symbols)

    # Get pending tickers
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, last_page FROM news_backfill_state_fmp
        WHERE done = false AND tier = 'cameron'
        ORDER BY priority DESC, updated_at ASC
    """)
    pending = cur.fetchall()
    log.info(f"FMP backfill: {len(pending)} tickers pending")

    stats = {"api_calls": 0, "articles_inserted": 0, "articles_skipped": 0,
             "tickers_done": 0, "errors": 0}
    start_time = time.time()

    for i, (ticker, last_page) in enumerate(pending):
        page = last_page or 0
        ticker_articles = 0

        while True:
            articles, resp_bytes = fetch_fmp_page(api_key, ticker, page)
            stats["api_calls"] += 1

            if not articles:
                break

            for art in articles:
                # Date filter
                pub_str = art.get("publishedDate", "")
                if pub_str:
                    try:
                        pub_date = datetime.fromisoformat(pub_str.replace(" ", "T")).date()
                        if pub_date < FMP_BACKFILL_START:
                            continue
                    except ValueError:
                        pass

                was_inserted = insert_fmp_article(cur, art, ticker)
                if was_inserted:
                    stats["articles_inserted"] += 1
                    ticker_articles += 1
                else:
                    stats["articles_skipped"] += 1

            # Check if we've gone past our date boundary
            if len(articles) < 50:
                break
            page += 1
            if page >= 200:  # safety limit
                break

        # Mark ticker done
        cur.execute("""
            UPDATE news_backfill_state_fmp
            SET done = true, last_page = %s, last_successful_fetch_ts = now(), updated_at = now()
            WHERE ticker = %s
        """, (page, ticker))
        conn.commit()
        stats["tickers_done"] += 1

        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = stats["api_calls"] / elapsed * 60
            log.info(
                f"[FMP] {i+1}/{len(pending)} tickers | "
                f"{stats['articles_inserted']} articles | "
                f"{stats['api_calls']} API calls | "
                f"{rate:.0f} calls/min"
            )

    elapsed = time.time() - start_time
    conn.close()

    log.info("=" * 60)
    log.info("FMP BACKFILL COMPLETE")
    log.info(f"  Tickers processed: {stats['tickers_done']}")
    log.info(f"  API calls: {stats['api_calls']}")
    log.info(f"  Articles inserted: {stats['articles_inserted']}")
    log.info(f"  Articles skipped (dup): {stats['articles_skipped']}")
    log.info(f"  Elapsed: {elapsed:.0f}s ({elapsed/60:.1f}m)")
    log.info("=" * 60)
    return stats


# ═══════════════════════════════════════════════════════════════════════
# ARCTIC SHIFT (REDDIT) BACKFILL
# ═══════════════════════════════════════════════════════════════════════

ARCTIC_SHIFT_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
REDDIT_SUBS = ["wallstreetbets", "stocks", "investing", "options", "pennystocks",
               "Shortsqueeze", "smallstreetbets"]
REDDIT_START = date(2025, 1, 1)
REDDIT_END = date(2026, 2, 27)

# Common English words to exclude from bare ticker matching
COMMON_WORDS = {
    "A","B","C","D","E","F","G","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z",
    "AI","AM","AN","AS","AT","BE","BY","DO","GO","HE","IF","IN","IS","IT","ME","MY","NO","OF","OK","ON","OR","SO","TO","UP","US","WE",
    "ALL","AND","ANY","ARE","BIG","BUT","BUY","CAN","DAY","DID","FOR","GET","GOT","GUY","HAS","HAD","HER","HIM","HIS","HOT","HOW",
    "ITS","JOB","LET","LOT","LOW","MAN","MAY","NEW","NOT","NOW","OLD","ONE","OUR","OUT","OWN","PUT","RAN","RUN","SAY","SEE","SET",
    "SHE","THE","TOO","TOP","TRY","TWO","USE","VIA","WAS","WAY","WHO","WHY","WIN","WON","YES","YET","YOU",
    "ALSO","BACK","BEEN","BEST","BOTH","CALL","CAME","COME","COST","DOWN","EACH","EVEN","FAST","FEEL","FIND","FREE",
    "FROM","FULL","GAIN","GAVE","GOOD","GROW","HALF","HAND","HAVE","HEAR","HELP","HERE","HIGH","HOLD","HOME","HOPE",
    "HUGE","IDEA","INTO","JUST","KEEP","KNOW","LAST","LEFT","LESS","LIFE","LIKE","LINE","LIST","LIVE","LONG","LOOK",
    "LOSS","LOST","LOVE","MADE","MAIN","MAKE","MANY","MORE","MOST","MOVE","MUCH","MUST","NAME","NEED","NEXT","ONLY",
    "OPEN","OVER","PAID","PART","PAST","PICK","PLAY","PLUS","POST","PULL","PUSH","REAL","REST","RIDE","RISK","SAFE",
    "SAID","SAME","SAVE","SELL","SELF","SEND","SHOT","SHOW","SIDE","SOLD","SOME","SOON","STAY","STOP","SUCH","SURE",
    "TAKE","TALK","TELL","THAN","THAT","THEM","THEN","THEY","THIS","TIME","TOOK","TRUE","TURN","VERY","WAIT","WANT",
    "WEEK","WELL","WENT","WERE","WHAT","WHEN","WILL","WITH","WORD","WORK","YEAR","YOUR","ZERO",
    "APE","BAG","BET","DIP","FUD","GEM","HIT","IMO","LOL","MAX","MIN","MOM","OTC","POP","RIP","SOS","TIP","WOW","YOY",
}

import re

def extract_tickers(text: str, valid_symbols: set) -> List[str]:
    """Extract ticker mentions from text. Returns list of matched symbols."""
    if not text:
        return []
    found = set()
    # $TICKER — high confidence
    for m in re.findall(r"\$([A-Z]{1,5})\b", text):
        if m.upper() in valid_symbols:
            found.add(m.upper())
    # Bare TICKER — exclude common words
    for m in re.findall(r"\b([A-Z]{1,5})\b", text):
        s = m.upper()
        if s in valid_symbols and s not in COMMON_WORDS:
            found.add(s)
    return list(found)


def fetch_arctic_shift_page(sub: str, after_ts: int, before_ts: int) -> list:
    """Fetch one page from Arctic Shift API."""
    params = urllib.parse.urlencode({
        "subreddit": sub,
        "after": after_ts,
        "before": before_ts,
        "limit": "auto",
        "sort": "asc",
    })
    url = f"{ARCTIC_SHIFT_URL}?{params}"

    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cameron_backfill/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
        except Exception as e:
            if attempt == 3:
                log.error(f"Arctic Shift fetch failed: {e}")
                return []
            time.sleep(2 ** attempt)
    return []


def insert_reddit_article(cur, post: dict, tickers: List[str]) -> bool:
    """Insert a Reddit post into articles table with entity mappings."""
    post_id = post.get("id", "")
    title = post.get("title", "")
    selftext = post.get("selftext", "") or ""
    author = post.get("author", "[deleted]") or "[deleted]"
    score = int(post.get("score", 0) or 0)
    upvote_ratio = post.get("upvote_ratio")
    num_comments = int(post.get("num_comments", 0) or 0)
    created_utc = int(post.get("created_utc", 0))
    permalink = post.get("permalink", "")
    subreddit = post.get("subreddit", "")

    if not post_id:
        return False

    canonical_url = f"https://reddit.com{permalink}" if permalink else f"reddit://{post_id}"
    pub_time = datetime.fromtimestamp(created_utc, tz=timezone.utc) if created_utc else None
    content = f"{title}\n\n{selftext}".strip()
    content_hash = hashlib.sha256(f"reddit|{post_id}".encode()).hexdigest()[:32]
    raw_json = json.dumps({
        "subreddit": subreddit, "author": author,
        "permalink": permalink, "post_id": post_id,
    })

    cur.execute("""
        INSERT INTO articles (source, external_id, canonical_url, title, summary, content,
                              authors, publish_time, lang, content_hash, raw,
                              score, upvote_ratio, num_comments)
        VALUES ('reddit', %s, %s, %s, %s, %s, %s, %s, 'en', %s, %s, %s, %s, %s)
        ON CONFLICT (canonical_url) DO NOTHING
        RETURNING id
    """, (post_id, canonical_url, title, (selftext[:500] if selftext else None),
          content, author, pub_time, content_hash, raw_json,
          score, upvote_ratio, num_comments))

    result = cur.fetchone()
    if result:
        article_id = result[0]
        # Insert entity mappings for all matched tickers
        for ticker in tickers:
            cur.execute("""
                INSERT INTO article_entities (article_id, entity_type, entity_value)
                VALUES (%s, 'ticker', %s) ON CONFLICT DO NOTHING
            """, (article_id, ticker))
        return True
    return False


def run_reddit_backfill(symbols: List[str]):
    """Run Arctic Shift Reddit backfill for Cameron symbols."""
    conn = get_conn()
    cur = conn.cursor()
    symbol_set = set(s.upper() for s in symbols)

    start_ts = int(datetime(REDDIT_START.year, REDDIT_START.month, REDDIT_START.day,
                            tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime(REDDIT_END.year, REDDIT_END.month, REDDIT_END.day, 23, 59, 59,
                          tzinfo=timezone.utc).timestamp())

    stats = {"posts_scanned": 0, "posts_with_mentions": 0, "articles_inserted": 0,
             "articles_skipped": 0, "entity_mappings": 0}
    start_time = time.time()

    for sub in REDDIT_SUBS:
        log.info(f"[Arctic Shift] Fetching r/{sub} ({REDDIT_START} to {REDDIT_END})...")
        cursor_ts = start_ts
        sub_posts = 0
        sub_mentions = 0

        while cursor_ts < end_ts:
            posts = fetch_arctic_shift_page(sub, cursor_ts, end_ts)
            if not posts:
                break

            for p in posts:
                created = int(p.get("created_utc", 0))
                if created < start_ts or created > end_ts:
                    continue

                stats["posts_scanned"] += 1
                sub_posts += 1

                # Extract ticker mentions from title + selftext
                text = f"{p.get('title', '')}\n{p.get('selftext', '') or ''}"
                tickers = extract_tickers(text, symbol_set)

                if tickers:
                    stats["posts_with_mentions"] += 1
                    sub_mentions += 1
                    was_inserted = insert_reddit_article(cur, p, tickers)
                    if was_inserted:
                        stats["articles_inserted"] += 1
                        stats["entity_mappings"] += len(tickers)
                    else:
                        stats["articles_skipped"] += 1

            # Advance cursor
            last_ts = int(posts[-1].get("created_utc", 0))
            if last_ts <= cursor_ts:
                break
            cursor_ts = last_ts + 1

            # Commit periodically
            if stats["posts_scanned"] % 5000 == 0:
                conn.commit()
                elapsed = time.time() - start_time
                log.info(
                    f"  [r/{sub}] {sub_posts} posts scanned, {sub_mentions} with Cameron mentions | "
                    f"Total: {stats['articles_inserted']} inserted | {elapsed:.0f}s"
                )

            time.sleep(0.2)  # Arctic Shift rate limit

        conn.commit()
        log.info(f"  [r/{sub}] Done: {sub_posts} posts, {sub_mentions} with Cameron ticker mentions")

    elapsed = time.time() - start_time
    conn.close()

    log.info("=" * 60)
    log.info("REDDIT ARCTIC SHIFT BACKFILL COMPLETE")
    log.info(f"  Posts scanned: {stats['posts_scanned']}")
    log.info(f"  Posts with Cameron mentions: {stats['posts_with_mentions']}")
    log.info(f"  Articles inserted: {stats['articles_inserted']}")
    log.info(f"  Articles skipped (dup): {stats['articles_skipped']}")
    log.info(f"  Entity mappings created: {stats['entity_mappings']}")
    log.info(f"  Elapsed: {elapsed:.0f}s ({elapsed/60:.1f}m)")
    log.info("=" * 60)
    return stats


# ═══════════════════════════════════════════════════════════════════════
# COVERAGE REPORT
# ═══════════════════════════════════════════════════════════════════════

def run_coverage_report(symbols: List[str]):
    """Check current news coverage for Cameron symbols."""
    conn = get_conn()
    cur = conn.cursor()

    sym_list = tuple(symbols)

    # Total articles per source for Cameron tickers
    cur.execute("""
        SELECT a.source, COUNT(DISTINCT a.id), COUNT(DISTINCT ae.entity_value)
        FROM article_entities ae
        JOIN articles a ON a.id = ae.article_id
        WHERE ae.entity_type = 'ticker' AND ae.entity_value = ANY(%s)
          AND a.publish_time >= '2025-01-01'
        GROUP BY a.source
        ORDER BY COUNT(DISTINCT a.id) DESC
    """, (list(sym_list),))
    rows = cur.fetchall()

    log.info("=" * 60)
    log.info(f"NEWS COVERAGE REPORT — {len(symbols)} Cameron symbols")
    log.info("=" * 60)
    log.info(f"{'Source':<20} {'Articles':>10} {'Symbols':>10}")
    log.info("-" * 42)
    total_articles = 0
    covered_symbols = set()
    for source, art_count, sym_count in rows:
        log.info(f"{source:<20} {art_count:>10,} {sym_count:>10,}")
        total_articles += art_count

    # Unique covered symbols
    cur.execute("""
        SELECT COUNT(DISTINCT ae.entity_value)
        FROM article_entities ae
        JOIN articles a ON a.id = ae.article_id
        WHERE ae.entity_type = 'ticker' AND ae.entity_value = ANY(%s)
          AND a.publish_time >= '2025-01-01'
    """, (list(sym_list),))
    covered = cur.fetchone()[0]

    log.info("-" * 42)
    log.info(f"{'TOTAL':<20} {total_articles:>10,} {covered:>10,}")
    log.info(f"\nCoverage: {covered}/{len(symbols)} symbols ({covered/len(symbols)*100:.1f}%)")

    # Check sentiment_daily coverage
    cur.execute("""
        SELECT COUNT(DISTINCT ticker)
        FROM sentiment_daily
        WHERE ticker = ANY(%s) AND asof_date >= '2025-01-01'
    """, (list(sym_list),))
    sentiment_covered = cur.fetchone()[0]
    log.info(f"Sentiment daily coverage: {sentiment_covered}/{len(symbols)} ({sentiment_covered/len(symbols)*100:.1f}%)")

    # Symbols with NO coverage at all
    cur.execute("""
        SELECT ae.entity_value
        FROM article_entities ae
        JOIN articles a ON a.id = ae.article_id
        WHERE ae.entity_type = 'ticker' AND ae.entity_value = ANY(%s)
          AND a.publish_time >= '2025-01-01'
    """, (list(sym_list),))
    covered_set = {r[0] for r in cur.fetchall()}
    uncovered = [s for s in symbols if s not in covered_set]
    log.info(f"Symbols with ZERO articles: {len(uncovered)}")
    if uncovered[:20]:
        log.info(f"  Examples: {', '.join(uncovered[:20])}")

    conn.close()
    log.info("=" * 60)


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Cameron News Backfill")
    ap.add_argument("--phase", choices=["fmp", "reddit", "both", "report"],
                    default="both", help="Which backfill to run")
    args = ap.parse_args()

    symbols = load_cameron_symbols()

    if args.phase == "report":
        run_coverage_report(symbols)
        return

    if args.phase in ("fmp", "both"):
        log.info("=" * 60)
        log.info("PHASE 1: FMP NEWS BACKFILL")
        log.info("=" * 60)
        run_fmp_backfill(symbols)

    if args.phase in ("reddit", "both"):
        log.info("=" * 60)
        log.info("PHASE 2: ARCTIC SHIFT (REDDIT) BACKFILL")
        log.info("=" * 60)
        run_reddit_backfill(symbols)

    # Final coverage report
    log.info("\n\n")
    run_coverage_report(symbols)


if __name__ == "__main__":
    main()
