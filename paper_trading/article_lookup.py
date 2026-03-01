"""
Article Lookup for Cameron Pattern Enrichment

Checks if news articles exist for a symbol by querying
articles + article_entities tables (owned by FL3 V1 pipeline).

Pure data collection — NOT a filter. Graceful degradation on any error.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ArticleInfo:
    """Result of an article lookup for a symbol."""
    has_news: bool = False
    article_count: int = 0
    latest_title: Optional[str] = None
    latest_publish_time: Optional[datetime] = None


def check_articles_for_symbol(
    db_url: str,
    symbol: str,
    lookback_hours: int = 36,
) -> ArticleInfo:
    """
    Check if news articles exist for a symbol within the lookback window.

    Queries articles JOIN article_entities for ticker matches.
    Returns ArticleInfo with has_news=False on any error (graceful).
    """
    if not db_url:
        return ArticleInfo()

    try:
        import psycopg2
        conn = psycopg2.connect(db_url.strip())
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*), MAX(a.title), MAX(a.publish_time)
            FROM articles a
            JOIN article_entities ae ON ae.article_id = a.id
            WHERE ae.entity_type = 'ticker'
              AND ae.entity_value = %s
              AND a.publish_time > NOW() - make_interval(hours := %s)
        """, (symbol, lookback_hours))

        row = cur.fetchone()
        cur.close()
        conn.close()

        if row and row[0] > 0:
            return ArticleInfo(
                has_news=True,
                article_count=row[0],
                latest_title=row[1],
                latest_publish_time=row[2],
            )

        return ArticleInfo()

    except Exception as e:
        logger.debug(f"Article lookup failed for {symbol}: {e}")
        return ArticleInfo()
