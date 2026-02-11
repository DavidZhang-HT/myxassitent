#!/usr/bin/env python3
"""
MyXAssistant — X 数据服务。

作为独立 HTTP 服务运行，对外提供统一的 REST API：
  - 数据同步（从 X API 拉取 likes）
  - 数据查询（搜索、筛选、统计）
  - 发布推文
  - 网页展示

外部系统（如 OpenClaw）通过 HTTP API 与本服务交互，不直接访问数据库或脚本。
"""

import sqlite3
import threading
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request

app = Flask(__name__)
DB_PATH = Path(__file__).parent / "myxassistant.db"

# Sync state (shared across requests)
_sync_lock = threading.Lock()
_sync_status = {"running": False, "last_result": None, "progress": []}

SERVICE_VERSION = "1.0.0"


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
# Service info
# ---------------------------------------------------------------------------
@app.route("/api/health")
def api_health():
    """Health check endpoint."""
    db = get_db()
    try:
        total = db.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
        db_ok = True
    except Exception:
        total = 0
        db_ok = False
    return jsonify({
        "status": "ok" if db_ok else "degraded",
        "service": "myxassistant",
        "version": SERVICE_VERSION,
        "db_tweets": total,
        "sync_running": _sync_status["running"],
    })


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API — Stats
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
    date_range = db.execute(
        "SELECT MIN(created_at) AS min_date, MAX(created_at) AS max_date FROM tweets"
    ).fetchone()
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


# ---------------------------------------------------------------------------
# API — Tweets (list / search / filter)
# ---------------------------------------------------------------------------
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

    if q:
        where_clauses.append("t.tweet_id IN (SELECT tweet_id FROM tweets_fts WHERE tweets_fts MATCH ?)")
        fts_query = " ".join(f'"{w}"' for w in q.split() if w)
        params.append(fts_query)

    if cat_ids:
        placeholders = ",".join("?" * len(cat_ids))
        joins.append("JOIN tweet_categories tc ON tc.tweet_id = t.tweet_id")
        where_clauses.append(f"tc.category_id IN ({placeholders})")
        params.extend(cat_ids)

    if author:
        where_clauses.append("t.author_screen_name = ?")
        params.append(author)

    join_sql = " ".join(joins)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    count_sql = f"SELECT COUNT(DISTINCT t.tweet_id) FROM tweets t {join_sql} {where_sql}"
    total = db.execute(count_sql, params).fetchone()[0]

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

    tweets = [_row_to_tweet(r) for r in rows]

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "tweets": tweets,
    })


def _row_to_tweet(r) -> dict:
    return {
        "tweet_id": r["tweet_id"],
        "created_at": r["created_at"],
        "text": r["text"],
        "author_name": r["author_name"],
        "author_screen_name": r["author_screen_name"],
        "retweet_count": r["retweet_count"],
        "favorite_count": r["favorite_count"],
        "tweet_url": r["tweet_url"],
        "categories": r["categories"] or "Other",
    }


# ---------------------------------------------------------------------------
# API — Single tweet by ID
# ---------------------------------------------------------------------------
@app.route("/api/tweets/<tweet_id>")
def api_tweet_detail(tweet_id):
    """Get a single tweet by tweet_id."""
    db = get_db()
    r = db.execute("""
        SELECT t.*, GROUP_CONCAT(c.name, ', ') AS categories
        FROM tweets t
        LEFT JOIN tweet_categories tc ON tc.tweet_id = t.tweet_id
        LEFT JOIN categories c ON c.id = tc.category_id
        WHERE t.tweet_id = ?
        GROUP BY t.tweet_id
    """, (tweet_id,)).fetchone()
    if not r:
        return jsonify({"error": "Tweet not found"}), 404
    return jsonify(_row_to_tweet(r))


# ---------------------------------------------------------------------------
# API — Sync (trigger download from Twitter API)
# ---------------------------------------------------------------------------
@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Trigger a sync from Twitter API.

    Non-blocking — runs in background.
    Optional JSON body: {"full": true} for full historical sync.
    """
    if _sync_status["running"]:
        return jsonify({"status": "already_running", "message": "同步正在进行中..."}), 409

    body = request.get_json(silent=True) or {}
    max_pages = 999 if body.get("full") else 1

    def run_sync():
        from sync import sync_from_api
        _sync_status["running"] = True
        _sync_status["progress"] = []
        try:
            result = sync_from_api(
                db_path=DB_PATH,
                on_progress=lambda msg: _sync_status["progress"].append(msg),
                max_pages=max_pages,
            )
            _sync_status["last_result"] = result
        except Exception as e:
            _sync_status["last_result"] = {"status": "error", "message": str(e)}
        finally:
            _sync_status["running"] = False

    thread = threading.Thread(target=run_sync, daemon=True)
    thread.start()
    return jsonify({"status": "started", "message": "同步已启动"})


@app.route("/api/sync/status")
def api_sync_status():
    """Get current sync status."""
    return jsonify({
        "running": _sync_status["running"],
        "progress": _sync_status["progress"][-10:],
        "last_result": _sync_status["last_result"],
    })


@app.route("/api/sync/log")
def api_sync_log():
    """Get sync history."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT 20"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception:
        return jsonify([])


# ---------------------------------------------------------------------------
# API — Publish tweet
# ---------------------------------------------------------------------------
@app.route("/api/publish", methods=["POST"])
def api_publish():
    """Publish a new tweet.

    JSON body: {"text": "tweet content"}
    Returns the created tweet data from Twitter API.
    """
    body = request.get_json(silent=True)
    if not body or not body.get("text"):
        return jsonify({"error": "Missing 'text' in request body"}), 400

    text = body["text"].strip()
    if not text:
        return jsonify({"error": "Tweet text cannot be empty"}), 400
    if len(text) > 280:
        return jsonify({"error": f"Tweet too long: {len(text)}/280 characters"}), 400

    try:
        from sync import TwitterAPI
        api = TwitterAPI()
        result = api.post_tweet(text)
        return jsonify({"status": "success", "tweet": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
