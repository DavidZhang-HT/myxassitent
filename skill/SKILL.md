---
name: sync-twitter-likes
description: Sync Twitter/X likes data to local SQLite database and query liked tweets. Use when the user asks to sync likes, fetch new likes, update Twitter data, check latest likes, search liked tweets, or manage the Twitter likes database.
---

# Sync Twitter Likes

从 Twitter API v2 增量同步用户点赞数据到 SQLite 数据库，支持查询和网页展示。

## 安装

```bash
git clone https://github.com/DavidZhang-HT/myxassitent.git
cd myxassitent
cp config.env.example config.env
# 编辑 config.env 填入你的 Twitter API 凭证
```

## 配置

编辑项目根目录的 `config.env`：

```env
TWITTER_API_KEY=your_key
TWITTER_API_SECRET=your_secret
TWITTER_ACCESS_TOKEN=your_token
TWITTER_ACCESS_SECRET=your_token_secret
```

也可通过环境变量指定自定义路径：
- `MYX_CONFIG` — config.env 文件路径
- `MYX_DB` — SQLite 数据库路径

## 同步

### 增量同步（默认，2 次 API 调用）

```bash
python3 sync.py
```

### 完整同步（首次使用或数据恢复）

```bash
python3 sync.py --full
```

### 从 JSON 文件导入

```bash
python3 sync.py /path/to/twitter_likes.json
```

**注意：Twitter API 按调用计费，默认增量模式仅请求最新一页。仅在用户明确要求时使用 `--full`。**

## 查询数据

```bash
sqlite3 twitter_likes.db
```

### 常用查询

```sql
-- 总数
SELECT COUNT(*) FROM tweets;

-- 搜索推文
SELECT author_screen_name, text, favorite_count
FROM tweets WHERE text LIKE '%关键词%'
ORDER BY favorite_count DESC LIMIT 20;

-- 全文搜索 (FTS5)
SELECT t.* FROM tweets t
JOIN tweets_fts fts ON fts.tweet_id = t.tweet_id
WHERE tweets_fts MATCH '"search term"' LIMIT 20;

-- 按分类查询
SELECT t.text, t.author_screen_name, c.name AS category
FROM tweets t
JOIN tweet_categories tc ON tc.tweet_id = t.tweet_id
JOIN categories c ON c.id = tc.category_id
WHERE c.name = 'AI/ML'
ORDER BY t.favorite_count DESC LIMIT 20;

-- 分类统计
SELECT c.name, COUNT(tc.tweet_id) AS cnt
FROM categories c
LEFT JOIN tweet_categories tc ON tc.category_id = c.id
GROUP BY c.id ORDER BY cnt DESC;

-- 热门作者
SELECT author_name, author_screen_name, COUNT(*) AS cnt
FROM tweets GROUP BY author_screen_name
ORDER BY cnt DESC LIMIT 20;

-- 同步记录
SELECT * FROM sync_log ORDER BY id DESC LIMIT 5;
```

## 网页界面

```bash
python3 app.py
# 访问 http://127.0.0.1:5000
```

支持列表展示、全文搜索、分类筛选、作者筛选、排序和在线同步。

## 数据库表

| 表 | 字段 |
|---|---|
| `tweets` | tweet_id, created_at, text, author_name, author_screen_name, author_id, retweet_count, favorite_count, tweet_url |
| `categories` | id, name |
| `tweet_categories` | tweet_id, category_id |
| `tweets_fts` | 全文搜索虚拟表 |
| `sync_log` | synced_at, new_count, total_fetched, status, message |
