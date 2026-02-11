#!/usr/bin/env python3
"""Flask application for MyXAssistant."""

import sqlite3
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request

app = Flask(__name__)
DB_PATH = Path(__file__).parent / "twitter_likes.db"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
@app.route("/api/stats")
def api_stats():
    """Return overall statistics."""
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    authors = db.execute("SELECT COUNT(DISTINCT author_screen_name) FROM tweets").fetchone()[0]
    categories = [
        {"id": r["id"], "name": r["name"], "count": r["cnt"]}
        for r in db.execute("""
            SELECT c.id, c.name, COUNT(tc.tweet_id) AS cnt
            FROM categories c
            LEFT JOIN tweet_categories tc ON tc.category_id = c.id
            GROUP BY c.id
            ORDER BY cnt DESC
        """)
    ]
    # Date range
    date_range = db.execute(
        "SELECT MIN(created_at) AS min_date, MAX(created_at) AS max_date FROM tweets"
    ).fetchone()
    # Top authors
    top_authors = [
        {"name": r["author_name"], "screen_name": r["author_screen_name"], "count": r["cnt"]}
        for r in db.execute("""
            SELECT author_name, author_screen_name, COUNT(*) AS cnt
            FROM tweets GROUP BY author_screen_name
            ORDER BY cnt DESC LIMIT 20
        """)
    ]
    return jsonify({
        "total": total,
        "authors": authors,
        "categories": categories,
        "date_range": {
            "min": date_range["min_date"],
            "max": date_range["max_date"],
        },
        "top_authors": top_authors,
    })


@app.route("/api/tweets")
def api_tweets():
    """Return paginated, searchable, filterable tweets.

    Query params:
      - q: full‑text search query
      - category: category ID (can repeat)
      - author: author_screen_name
      - sort: field to sort by (created_at, favorite_count, retweet_count)
      - order: asc / desc (default desc)
      - page: page number (1‑based, default 1)
      - per_page: items per page (default 20, max 100)
    """
    db = get_db()
    q = request.args.get("q", "").strip()
    cat_ids = request.args.getlist("category", type=int)
    author = request.args.get("author", "").strip()
    sort = request.args.get("sort", "created_at")
    order = request.args.get("order", "desc").upper()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))

    if sort not in ("created_at", "favorite_count", "retweet_count"):
        sort = "created_at"
    if order not in ("ASC", "DESC"):
        order = "DESC"

    params: list = []
    where_clauses: list[str] = []
    joins: list[str] = []

    # Full-text search
    if q:
        # Use FTS5 match
        where_clauses.append("t.tweet_id IN (SELECT tweet_id FROM tweets_fts WHERE tweets_fts MATCH ?)")
        # Escape special chars for FTS5 and wrap each word in quotes
        fts_query = " ".join(f'"{w}"' for w in q.split() if w)
        params.append(fts_query)

    # Category filter
    if cat_ids:
        placeholders = ",".join("?" * len(cat_ids))
        joins.append("JOIN tweet_categories tc ON tc.tweet_id = t.tweet_id")
        where_clauses.append(f"tc.category_id IN ({placeholders})")
        params.extend(cat_ids)

    # Author filter
    if author:
        where_clauses.append("t.author_screen_name = ?")
        params.append(author)

    join_sql = " ".join(joins)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Count
    count_sql = f"SELECT COUNT(DISTINCT t.tweet_id) FROM tweets t {join_sql} {where_sql}"
    total = db.execute(count_sql, params).fetchone()[0]

    # Fetch page
    offset = (page - 1) * per_page
    data_sql = f"""
        SELECT DISTINCT t.*, GROUP_CONCAT(c.name, ', ') AS categories
        FROM tweets t
        LEFT JOIN tweet_categories tc2 ON tc2.tweet_id = t.tweet_id
        LEFT JOIN categories c ON c.id = tc2.category_id
        {join_sql}
        {where_sql}
        GROUP BY t.tweet_id
        ORDER BY t.{sort} {order}
        LIMIT ? OFFSET ?
    """
    params.extend([per_page, offset])
    rows = db.execute(data_sql, params).fetchall()

    tweets = []
    for r in rows:
        tweets.append({
            "tweet_id": r["tweet_id"],
            "created_at": r["created_at"],
            "text": r["text"],
            "author_name": r["author_name"],
            "author_screen_name": r["author_screen_name"],
            "retweet_count": r["retweet_count"],
            "favorite_count": r["favorite_count"],
            "tweet_url": r["tweet_url"],
            "categories": r["categories"] or "Other",
        })

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "tweets": tweets,
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
