#!/usr/bin/env python3
"""Import Twitter likes data from JSON into MyXAssistant database."""

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "myxassistant.db"

# ---------------------------------------------------------------------------
# Category detection – keyword‑based heuristic
# ---------------------------------------------------------------------------
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("AI/ML", [
        r"\bai\b", r"\bllm\b", r"\bgpt\b", r"\bclaude\b", r"\bopenai\b",
        r"\bmachine.?learning\b", r"\bdeep.?learning\b", r"\bneural\b",
        r"\btransformer\b", r"\bmodel\b", r"\binference\b", r"\btraining\b",
        r"\bfine.?tun", r"\bprompt\b", r"\brag\b", r"\bagent\b",
        r"\bembedding", r"\btoken", r"\blangchain\b", r"\bvector\b",
        r"\bchatbot\b", r"\bgemini\b", r"\bllama\b", r"\bmistral\b",
        r"\bgrok\b", r"\bperception\b", r"\bconversational ai\b",
    ]),
    ("Voice/Audio", [
        r"\bvoice\b", r"\baudio\b", r"\btts\b", r"\bspeech\b",
        r"\bspeaker\b", r"\bwhisper\b", r"\bpipecat\b", r"\bwebrtc\b",
        r"\brealtime\b", r"\breal.?time\b", r"\bstreaming\b",
        r"\b语音\b", r"\blivekit\b", r"\bdaily\b",
    ]),
    ("Web Development", [
        r"\breact\b", r"\bnext\.?js\b", r"\bvue\b", r"\bsvelte\b",
        r"\bhtml\b", r"\bcss\b", r"\btailwind\b", r"\bjavascript\b",
        r"\btypescript\b", r"\bnode\b", r"\bnpm\b", r"\bwebpack\b",
        r"\bvite\b", r"\bfrontend\b", r"\bbackend\b", r"\bfullstack\b",
        r"\bweb\s?app\b", r"\bapi\b", r"\brest\b", r"\bgraphql\b",
        r"\bframework\b",
    ]),
    ("DevTools", [
        r"\bgit\b", r"\bdocker\b", r"\bkubernetes\b", r"\bk8s\b",
        r"\bci.?cd\b", r"\bdevops\b", r"\bcli\b", r"\bterminal\b",
        r"\beditor\b", r"\bide\b", r"\bvscode\b", r"\bcursor\b",
        r"\brust\b", r"\bgo\b", r"\bpython\b", r"\bswift\b",
        r"\bcompiler\b", r"\bdebug", r"\bopen.?source\b",
        r"\bsdk\b", r"\blibrary\b", r"\bpackage\b",
    ]),
    ("Design/UI", [
        r"\bdesign\b", r"\bui\b", r"\bux\b", r"\bfigma\b",
        r"\bavatar\b", r"\banimation\b", r"\bshader\b", r"\b3d\b",
        r"\bvisual\b", r"\bgraphic\b", r"\bicon\b", r"\blogo\b",
        r"\billustrat", r"\bmotion\b", r"\bcss\b",
    ]),
    ("Crypto/Web3", [
        r"\bcrypto\b", r"\bblockchain\b", r"\bweb3\b", r"\bdefi\b",
        r"\bnft\b", r"\bethereum\b", r"\bsolana\b", r"\bbitcoin\b",
        r"\btoken\b", r"\bwallet\b", r"\bsmart.?contract\b",
    ]),
    ("Business/Startup", [
        r"\bstartup\b", r"\bfunding\b", r"\braised\b", r"\bseries\b",
        r"\bvc\b", r"\byc\b", r"\bfounder\b", r"\brevenue\b",
        r"\bgrowth\b", r"\blaunch\b", r"\bproduct\b", r"\bmarket\b",
    ]),
    ("Data/Infra", [
        r"\bdatabase\b", r"\bsql\b", r"\bpostgres\b", r"\bredis\b",
        r"\belastic\b", r"\bkafka\b", r"\binfra\b", r"\bcloud\b",
        r"\baws\b", r"\bgcp\b", r"\bazure\b", r"\bserverless\b",
        r"\bdata\b", r"\banalytics\b",
    ]),
]


def detect_categories(text: str) -> list[str]:
    """Return a list of matching category names for a tweet text."""
    text_lower = text.lower()
    cats = []
    for cat_name, patterns in CATEGORY_RULES:
        for pat in patterns:
            if re.search(pat, text_lower):
                cats.append(cat_name)
                break
    return cats if cats else ["Other"]


def init_db(conn: sqlite3.Connection):
    """Create tables if they don't already exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tweets (
            tweet_id        TEXT PRIMARY KEY,
            created_at      TEXT NOT NULL,
            text            TEXT NOT NULL,
            author_name     TEXT NOT NULL,
            author_screen_name TEXT NOT NULL,
            author_id       TEXT NOT NULL,
            retweet_count   INTEGER DEFAULT 0,
            favorite_count  INTEGER DEFAULT 0,
            tweet_url       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS categories (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tweet_categories (
            tweet_id    TEXT NOT NULL REFERENCES tweets(tweet_id),
            category_id INTEGER NOT NULL REFERENCES categories(id),
            PRIMARY KEY (tweet_id, category_id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS tweets_fts USING fts5(
            tweet_id,
            text,
            author_name,
            author_screen_name,
            content='tweets',
            content_rowid='rowid'
        );

        CREATE TRIGGER IF NOT EXISTS tweets_ai AFTER INSERT ON tweets BEGIN
            INSERT INTO tweets_fts(rowid, tweet_id, text, author_name, author_screen_name)
            VALUES (new.rowid, new.tweet_id, new.text, new.author_name, new.author_screen_name);
        END;
    """)


def import_json(json_path: str):
    """Read JSON file and insert into database."""
    json_path = Path(json_path).expanduser()
    if not json_path.exists():
        print(f"Error: file not found: {json_path}")
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    likes = data.get("likes", data) if isinstance(data, dict) else data
    print(f"Found {len(likes)} tweets to import")

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    cur = conn.cursor()

    # Pre‑populate category lookup
    cat_cache: dict[str, int] = {}

    imported = 0
    skipped = 0
    for tweet in likes:
        tid = tweet["tweet_id"]
        # skip duplicates
        cur.execute("SELECT 1 FROM tweets WHERE tweet_id = ?", (tid,))
        if cur.fetchone():
            skipped += 1
            continue

        cur.execute(
            """INSERT INTO tweets
               (tweet_id, created_at, text, author_name, author_screen_name,
                author_id, retweet_count, favorite_count, tweet_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tid,
                tweet["created_at"],
                tweet["text"],
                tweet["author_name"],
                tweet["author_screen_name"],
                tweet["author_id"],
                tweet.get("retweet_count", 0),
                tweet.get("favorite_count", 0),
                tweet["tweet_url"],
            ),
        )

        # Assign categories
        cats = detect_categories(tweet["text"])
        for cat in cats:
            if cat not in cat_cache:
                cur.execute(
                    "INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat,)
                )
                cur.execute("SELECT id FROM categories WHERE name = ?", (cat,))
                cat_cache[cat] = cur.fetchone()[0]
            cur.execute(
                "INSERT OR IGNORE INTO tweet_categories (tweet_id, category_id) VALUES (?, ?)",
                (tid, cat_cache[cat]),
            )

        imported += 1

    conn.commit()
    conn.close()
    print(f"Import complete: {imported} imported, {skipped} skipped (duplicates)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_data.py <path_to_json>")
        sys.exit(1)
    import_json(sys.argv[1])
