#!/usr/bin/env python3
"""
Twitter Likes Sync — 直连 Twitter API v2，增量同步到 SQLite 数据库。

可以被以下方式调用：
  1. 命令行：python3 sync.py
  2. Flask 网页触发：POST /api/sync
  3. 外部系统通过 skill 安装后调用

配置文件查找顺序（取第一个存在的）：
  1. 环境变量 MYX_CONFIG 指定的路径
  2. 项目目录下的 config.env
数据库路径可通过环境变量 MYX_DB 指定，默认为项目目录下的 twitter_likes.db。
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths & Config
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).parent
DEFAULT_DB_PATH = PROJECT_DIR / "myxassistant.db"
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.env"

DB_PATH = Path(os.environ.get("MYX_DB", str(DEFAULT_DB_PATH)))


def _resolve_config_path() -> Path:
    """Resolve config.env path: env var > project dir."""
    env_path = os.environ.get("MYX_CONFIG")
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return p
    return DEFAULT_CONFIG_PATH


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# ---------------------------------------------------------------------------
# Category detection (same logic as import_data.py)
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
    text_lower = text.lower()
    cats = []
    for cat_name, patterns in CATEGORY_RULES:
        for pat in patterns:
            if re.search(pat, text_lower):
                cats.append(cat_name)
                break
    return cats if cats else ["Other"]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
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

        CREATE TABLE IF NOT EXISTS sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at   TEXT NOT NULL,
            new_count   INTEGER DEFAULT 0,
            total_fetched INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'success',
            message     TEXT
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


def insert_tweet(cur: sqlite3.Cursor, tweet: dict, cat_cache: dict[str, int]) -> bool:
    """Insert a single tweet + categories. Returns True if new, False if duplicate."""
    tid = tweet["tweet_id"]
    cur.execute("SELECT 1 FROM tweets WHERE tweet_id = ?", (tid,))
    if cur.fetchone():
        return False

    cur.execute(
        """INSERT INTO tweets
           (tweet_id, created_at, text, author_name, author_screen_name,
            author_id, retweet_count, favorite_count, tweet_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            tid,
            tweet["created_at"],
            tweet["text"],
            tweet.get("author_name", ""),
            tweet.get("author_screen_name", ""),
            tweet.get("author_id", ""),
            tweet.get("retweet_count", 0),
            tweet.get("favorite_count", 0),
            tweet.get("tweet_url", ""),
        ),
    )

    cats = detect_categories(tweet["text"])
    for cat in cats:
        if cat not in cat_cache:
            cur.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat,))
            cur.execute("SELECT id FROM categories WHERE name = ?", (cat,))
            cat_cache[cat] = cur.fetchone()[0]
        cur.execute(
            "INSERT OR IGNORE INTO tweet_categories (tweet_id, category_id) VALUES (?, ?)",
            (tid, cat_cache[cat]),
        )
    return True


# ---------------------------------------------------------------------------
# Twitter API v2 client
# ---------------------------------------------------------------------------
class TwitterAPI:
    """Minimal Twitter API v2 client using OAuth 1.0a (user-context)."""

    def __init__(self, credentials: dict[str, str] | None = None):
        if credentials is None:
            config_path = _resolve_config_path()
            credentials = _load_env_file(config_path)

        self.api_key = credentials.get("TWITTER_API_KEY", "")
        self.api_secret = credentials.get("TWITTER_API_SECRET", "")
        self.access_token = credentials.get("TWITTER_ACCESS_TOKEN", "")
        self.access_secret = credentials.get("TWITTER_ACCESS_SECRET", "")

        if not all([self.api_key, self.api_secret, self.access_token, self.access_secret]):
            config_path = _resolve_config_path()
            raise ValueError(
                "Missing Twitter API credentials.\n"
                f"  Config file: {config_path}\n"
                f"  Exists: {config_path.exists()}\n"
                "  Copy config.env.example to config.env and fill in your credentials."
            )

    # -- OAuth 1.0a helpers --------------------------------------------------
    def _oauth_sign(self, method: str, url: str, params: dict) -> str:
        param_str = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
            for k, v in sorted(params.items())
        )
        base_str = f"{method.upper()}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(param_str, safe='')}"
        signing_key = f"{urllib.parse.quote(self.api_secret, safe='')}&{urllib.parse.quote(self.access_secret, safe='')}"
        return base64.b64encode(
            hmac.new(signing_key.encode(), base_str.encode(), hashlib.sha1).digest()
        ).decode()

    def _auth_header(self, method: str, url: str, params: dict | None = None) -> str:
        if params is None:
            params = {}
        oauth = {
            "oauth_consumer_key": self.api_key,
            "oauth_nonce": hashlib.md5(str(time.time()).encode()).hexdigest(),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self.access_token,
            "oauth_version": "1.0",
        }
        oauth["oauth_signature"] = self._oauth_sign(method, url, {**oauth, **params})
        return "OAuth " + ", ".join(
            f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(str(v), safe="")}"'
            for k, v in oauth.items()
        )

    def _get(self, url: str, params: dict | None = None) -> dict:
        full_url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
        req = urllib.request.Request(
            full_url,
            headers={
                "Authorization": self._auth_header("GET", url, params),
                "User-Agent": "MyXAssistant/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    # -- API methods ---------------------------------------------------------
    def get_user_id(self) -> str:
        data = self._get("https://api.twitter.com/1.1/account/verify_credentials.json")
        return data["id_str"]

    def get_liked_tweets(self, user_id: str, max_results: int = 100,
                         pagination_token: str | None = None) -> dict:
        url = f"https://api.twitter.com/2/users/{user_id}/liked_tweets"
        params = {
            "max_results": max_results,
            "tweet.fields": "created_at,public_metrics,author_id",
            "user.fields": "username,name",
            "expansions": "author_id",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        return self._get(url, params)

    # -- Media upload helpers -------------------------------------------------
    UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
    CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB per chunk

    # media_type -> (max_bytes, media_category)
    MEDIA_LIMITS: dict[str, tuple[int, str]] = {
        "image/jpeg": (5 * 1024 * 1024, "tweet_image"),
        "image/png":  (5 * 1024 * 1024, "tweet_image"),
        "image/gif":  (15 * 1024 * 1024, "tweet_gif"),
        "image/webp": (5 * 1024 * 1024, "tweet_image"),
        "video/mp4":  (512 * 1024 * 1024, "tweet_video"),
    }

    def _post_form(self, url: str, fields: dict[str, str],
                   file_field: str | None = None,
                   file_data: bytes | None = None,
                   file_name: str | None = None,
                   file_content_type: str | None = None) -> dict:
        """POST multipart/form-data with OAuth 1.0a."""
        boundary = f"----WebKitFormBoundary{secrets.token_hex(16)}"
        body_parts: list[bytes] = []

        for k, v in fields.items():
            body_parts.append(
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                f"{v}\r\n".encode()
            )

        if file_field and file_data is not None:
            body_parts.append(
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"{file_field}\"; "
                f"filename=\"{file_name or 'media'}\"\r\n"
                f"Content-Type: {file_content_type or 'application/octet-stream'}\r\n"
                f"Content-Transfer-Encoding: binary\r\n\r\n".encode()
                + file_data + b"\r\n"
            )

        body_parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(body_parts)

        # OAuth signs against base URL without query params; form fields NOT included
        auth_header = self._auth_header("POST", url)
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Authorization": auth_header,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "MyXAssistant/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            raise Exception(f"Media upload error {e.code}: {error_body}")

    def upload_media_simple(self, file_data: bytes, media_type: str) -> str:
        """Upload an image via simple multipart upload. Returns media_id_string."""
        result = self._post_form(
            self.UPLOAD_URL, fields={},
            file_field="media_data",
            file_data=file_data,
            file_name="media",
            file_content_type=media_type,
        )
        return result["media_id_string"]

    def upload_media_chunked(self, file_data: bytes, media_type: str,
                             media_category: str = "tweet_video") -> str:
        """Upload video/large GIF via chunked upload. Returns media_id_string."""
        # INIT
        init_fields = {
            "command": "INIT",
            "total_bytes": str(len(file_data)),
            "media_type": media_type,
            "media_category": media_category,
        }
        init_resp = self._post_form(self.UPLOAD_URL, fields=init_fields)
        media_id = init_resp["media_id_string"]

        # APPEND (chunked)
        for i in range(0, len(file_data), self.CHUNK_SIZE):
            chunk = file_data[i:i + self.CHUNK_SIZE]
            segment = i // self.CHUNK_SIZE
            self._post_form(
                self.UPLOAD_URL,
                fields={"command": "APPEND", "media_id": media_id,
                         "segment_index": str(segment)},
                file_field="media_data",
                file_data=chunk,
                file_name="chunk",
                file_content_type="application/octet-stream",
            )

        # FINALIZE
        self._post_form(self.UPLOAD_URL,
                        fields={"command": "FINALIZE", "media_id": media_id})

        # STATUS — poll until processing completes (videos need async processing)
        if media_category == "tweet_video":
            self._wait_for_processing(media_id)

        return media_id

    def _wait_for_processing(self, media_id: str, max_wait: int = 120):
        """Poll media STATUS until processing completes."""
        url = f"{self.UPLOAD_URL}?command=STATUS&media_id={media_id}"
        deadline = time.time() + max_wait
        while time.time() < deadline:
            req = urllib.request.Request(
                url, method="GET",
                headers={
                    "Authorization": self._auth_header("GET",
                        self.UPLOAD_URL, {"command": "STATUS", "media_id": media_id}),
                    "User-Agent": "MyXAssistant/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            info = data.get("processing_info", {})
            state = info.get("state", "")
            if state == "succeeded":
                return
            if state == "failed":
                raise Exception(f"Media processing failed: {info.get('error', {})}")
            wait = info.get("check_after_secs", 5)
            time.sleep(min(wait, 10))
        raise Exception("Media processing timed out")

    def upload_media(self, file_data: bytes, media_type: str) -> str:
        """Smart upload: simple for images, chunked for video/large GIF."""
        limit_info = self.MEDIA_LIMITS.get(media_type)
        if not limit_info:
            raise ValueError(f"Unsupported media type: {media_type}. "
                             f"Supported: {', '.join(self.MEDIA_LIMITS.keys())}")
        max_bytes, category = limit_info
        if len(file_data) > max_bytes:
            raise ValueError(
                f"File too large: {len(file_data)} bytes "
                f"(max {max_bytes // 1024 // 1024} MB for {media_type})")

        if media_type == "video/mp4" or (media_type == "image/gif" and len(file_data) > 5 * 1024 * 1024):
            return self.upload_media_chunked(file_data, media_type, category)
        return self.upload_media_simple(file_data, media_type)

    # -- Tweet posting --------------------------------------------------------
    def post_tweet(self, text: str, media_ids: list[str] | None = None) -> dict:
        """Publish a tweet, optionally with media attachments."""
        url = "https://api.twitter.com/2/tweets"
        payload: dict = {"text": text}
        if media_ids:
            payload["media"] = {"media_ids": media_ids}
        data = json.dumps(payload).encode()
        auth_header = self._auth_header("POST", url)
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
                "User-Agent": "MyXAssistant/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            raise Exception(f"Twitter API error {e.code}: {error_body}")


# ---------------------------------------------------------------------------
# Core sync function
# ---------------------------------------------------------------------------
def sync_from_api(db_path: Path | None = None, on_progress=None,
                   max_pages: int | None = None) -> dict:
    """
    增量同步：从最新一条开始，逐条比对，遇已有数据即停。

    规则：
    1. API 用 max_results=1 逐条拉取（每条 1 次 API 调用）
    2. 与数据库「上次同步的最后一条」对比，一致 → 无更新，直接结束
    3. 不一致 → 查库，无则入库，继续拉下一条
    4. 直到某条已存在于库中，说明追上历史，停止

    最佳情况（无新点赞）：2 次 API 调用（user_id + 首条 likes）
    有新点赞时：2 + 新增条数 次调用。

    Args:
        db_path:    Path to SQLite database (default: project dir)
        on_progress: Optional callback(message: str)
        max_pages:  None=增量模式（逐条直到追上）；>0 时限制最大页数
    """
    if db_path is None:
        db_path = DB_PATH

    def log(msg: str):
        print(msg)
        if on_progress:
            on_progress(msg)

    result = {"new_count": 0, "total_fetched": 0, "status": "success",
              "message": "", "api_calls": 0}

    try:
        api = TwitterAPI()
        log("正在获取用户信息...")
        user_id = api.get_user_id()
        result["api_calls"] += 1
        log(f"用户ID: {user_id}")

        conn = sqlite3.connect(str(db_path))
        init_db(conn)
        cur = conn.cursor()
        cat_cache: dict[str, int] = {}
        for row in cur.execute("SELECT id, name FROM categories"):
            cat_cache[row[1]] = row[0]

        # 数据库中最新的 tweet_id（上次同步的最后一条）
        db_newest = cur.execute(
            "SELECT tweet_id FROM tweets ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        db_newest_id = db_newest[0] if db_newest else None
        log(f"数据库最新 tweet_id: {db_newest_id or '(空)'}")

        pagination_token = None
        page_count = 0
        max_pages = max_pages if max_pages is not None else 99999

        while page_count < max_pages:
            page_count += 1
            try:
                resp = api.get_liked_tweets(
                    user_id, max_results=10, pagination_token=pagination_token
                )
                result["api_calls"] += 1
            except Exception as e:
                log(f"API 请求失败: {e}")
                result["status"] = "partial"
                result["message"] = str(e)
                break

            tweets_data = resp.get("data", [])
            users = {u["id"]: u for u in resp.get("includes", {}).get("users", [])}

            if not tweets_data:
                log("没有更多数据。")
                break

            hit_existing = False
            for t in tweets_data:
                tid = t["id"]

                # 首条：与数据库最新对比，一致则无更新
                if result["total_fetched"] == 0 and tid == db_newest_id:
                    log("首条与数据库最新一致，无新数据。")
                    hit_existing = True
                    break

                cur.execute("SELECT 1 FROM tweets WHERE tweet_id = ?", (tid,))
                if cur.fetchone():
                    log(f"已存在 id={tid}，已追上历史。")
                    hit_existing = True
                    break

                author = users.get(t.get("author_id"), {})
                metrics = t.get("public_metrics", {})
                tweet = {
                    "tweet_id": tid,
                    "created_at": t.get("created_at", ""),
                    "text": t.get("text", ""),
                    "author_name": author.get("name", ""),
                    "author_screen_name": author.get("username", ""),
                    "author_id": t.get("author_id", ""),
                    "retweet_count": metrics.get("retweet_count", 0),
                    "favorite_count": metrics.get("like_count", 0),
                    "tweet_url": f"https://twitter.com/{author.get('username', '')}/status/{tid}",
                }
                if insert_tweet(cur, tweet, cat_cache):
                    result["new_count"] += 1
                result["total_fetched"] += 1
                log(f"  新增 id={tid}")

            conn.commit()
            if hit_existing:
                break

            pagination_token = resp.get("meta", {}).get("next_token")
            if not pagination_token:
                log("已到达最后一页。")
                break

            time.sleep(1)  # Rate limit

        conn.close()
        summary = (f"同步完成: 获取 {result['total_fetched']} 条, "
                   f"新增 {result['new_count']} 条 "
                   f"(API 调用 {result['api_calls']} 次)")
        log(summary)
        result["message"] = summary

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)
        log(f"同步失败: {e}")

    # Write sync log
    try:
        conn = sqlite3.connect(str(db_path))
        init_db(conn)
        conn.execute(
            "INSERT INTO sync_log (synced_at, new_count, total_fetched, status, message) VALUES (?, ?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), result["new_count"], result["total_fetched"],
             result["status"], result["message"]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Import from existing JSON file (for backward compatibility)
# ---------------------------------------------------------------------------
def sync_from_json(json_path: str, db_path: Path | None = None) -> dict:
    """Import from a JSON export file into the database."""
    if db_path is None:
        db_path = DB_PATH

    json_path = Path(json_path).expanduser()
    if not json_path.exists():
        return {"new_count": 0, "total_fetched": 0, "status": "error",
                "message": f"File not found: {json_path}"}

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    likes = data.get("likes", data) if isinstance(data, dict) else data

    conn = sqlite3.connect(str(db_path))
    init_db(conn)
    cur = conn.cursor()
    cat_cache: dict[str, int] = {}

    for row in cur.execute("SELECT id, name FROM categories"):
        cat_cache[row[1]] = row[0]

    new_count = 0
    for tweet in likes:
        if insert_tweet(cur, tweet, cat_cache):
            new_count += 1

    conn.commit()
    conn.close()

    return {
        "new_count": new_count,
        "total_fetched": len(likes),
        "status": "success",
        "message": f"从 JSON 导入完成: {new_count} 条新数据 (共 {len(likes)} 条)",
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1].endswith(".json"):
        # Import from JSON file
        print(f"从 JSON 文件导入: {sys.argv[1]}")
        result = sync_from_json(sys.argv[1])
    else:
        # Sync from Twitter API — 逐条同步直到追上历史
        print("从 Twitter API 增量同步（逐条比对，追上即停）...")
        print(f"数据库: {DB_PATH}")
        print(f"凭证: {_resolve_config_path()}")
        result = sync_from_api()

    print(f"\n结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    sys.exit(0 if result["status"] != "error" else 1)
