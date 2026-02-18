#!/usr/bin/env python3
"""Check news pipeline status - articles and ready_for_analysis."""

import asyncio
import os
import sys

import asyncpg


async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)

    print("=" * 70)
    print("NEWS PIPELINE STATUS")
    print("=" * 70)

    # First check articles table columns
    columns = await pool.fetch("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'articles' AND table_schema = 'public'
        ORDER BY ordinal_position
    """)
    print("\nArticles table columns:")
    for c in columns:
        print(f"  {c['column_name']}: {c['data_type']}")

    # Check articles table - use correct column names
    articles_count = await pool.fetchval("SELECT COUNT(*) FROM articles")
    print(f"\nArticles table: {articles_count:,} total rows")

    # Get latest articles using created_at or whatever timestamp column exists
    timestamp_col = None
    for c in columns:
        if 'timestamp' in c['data_type'] or c['column_name'] in ['created_at', 'published', 'fetched_at']:
            timestamp_col = c['column_name']
            break

    if timestamp_col:
        recent_articles = await pool.fetchval(f"""
            SELECT COUNT(*) FROM articles
            WHERE {timestamp_col} > NOW() - INTERVAL '24 hours'
        """)
        print(f"Articles in last 24h: {recent_articles:,}")

        latest_article = await pool.fetch(f"""
            SELECT id, title, source, {timestamp_col} as ts
            FROM articles
            ORDER BY {timestamp_col} DESC
            LIMIT 5
        """)
        print("\nLatest 5 articles:")
        for a in latest_article:
            title = str(a['title'])[:60] + "..." if a['title'] and len(str(a['title'])) > 60 else a['title']
            print(f"  [{a['id']}] {a['ts']} - {a['source']}")
            print(f"       {title}")

    # Check ready_for_analysis queue
    queue_count = await pool.fetchval("SELECT COUNT(*) FROM ready_for_analysis")
    print(f"\nReady for analysis queue: {queue_count:,} articles")

    if queue_count > 0:
        queued = await pool.fetch("""
            SELECT r.article_id, r.queued_at, a.title
            FROM ready_for_analysis r
            JOIN articles a ON r.article_id = a.id
            ORDER BY r.queued_at DESC
            LIMIT 5
        """)
        print("\nRecently queued:")
        for q in queued:
            title = str(q['title'])[:50] + "..." if q['title'] and len(str(q['title'])) > 50 else q['title']
            print(f"  [{q['article_id']}] {q['queued_at']} - {title}")

    # Check article_sentiment (LLM analysis results)
    try:
        sentiment_count = await pool.fetchval("SELECT COUNT(*) FROM article_sentiment")
        print(f"\nArticle sentiment (LLM analyzed): {sentiment_count:,} total")

        if sentiment_count > 0:
            # Check sentiment columns first
            sent_cols = await pool.fetch("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'article_sentiment' AND table_schema = 'public'
            """)
            col_names = [c['column_name'] for c in sent_cols]
            print(f"  Columns: {', '.join(col_names)}")

            latest_sentiment = await pool.fetch("""
                SELECT * FROM article_sentiment
                ORDER BY article_id DESC
                LIMIT 3
            """)
            print("\nLatest sentiment records:")
            for s in latest_sentiment:
                print(f"  {dict(s)}")
    except Exception as e:
        print(f"\nArticle sentiment check error: {e}")

    await pool.close()
    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
