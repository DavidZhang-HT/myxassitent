---
name: MyXAssistant
description: Interact with the MyXAssistant service to sync X likes, search liked posts, get stats, and publish posts. Use when the user asks to sync likes, fetch new likes, search liked posts, query X data, check like stats, or post to X.
---

# MyXAssistant Service

MyXAssistant 是一个独立的 HTTP 服务，管理用户的 Twitter/X 点赞数据和发推。
所有操作通过 HTTP API 完成，不要直接访问数据库或运行脚本。

**服务地址：** `http://127.0.0.1:5000`

## API 参考

### 1. 健康检查

```
GET /api/health
```

返回服务状态、数据库推文总数、是否正在同步。用于确认服务是否在线。

---

### 2. 同步 likes 数据

通知服务从 Twitter API 拉取最新的点赞数据（服务自行下载，异步执行）。

**触发同步：**

```
POST /api/sync
Content-Type: application/json

{}
```

默认增量同步（仅最新 100 条，2 次 API 调用）。完整同步传 `{"full": true}`，但 Twitter API 是收费的，**仅在用户明确要求时使用**。

**查询同步状态：**

```
GET /api/sync/status
```

返回 `running`（是否进行中）、`progress`（进度消息）、`last_result`（上次结果）。

**同步历史：**

```
GET /api/sync/log
```

---

### 3. 查询推文

**列表/搜索/筛选：**

```
GET /api/tweets?q=关键词&category=1&author=ericosiu&sort=favorite_count&order=desc&page=1&per_page=20
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `q` | 全文搜索关键词 | - |
| `category` | 分类ID（可多次传） | - |
| `author` | 作者 screen_name | - |
| `sort` | `created_at` / `favorite_count` / `retweet_count` | `created_at` |
| `order` | `asc` / `desc` | `desc` |
| `page` | 页码 | 1 |
| `per_page` | 每页数量（最大100） | 20 |

**按 ID 查询单条：**

```
GET /api/tweets/{tweet_id}
```

---

### 4. 统计数据

```
GET /api/stats
```

返回：总数、作者数、分类列表及数量、时间范围、热门作者 Top 20。

---

### 5. 发布推文

```
POST /api/publish
Content-Type: application/json

{"text": "要发布的推文内容"}
```

限制 280 字符。返回 Twitter API 的响应。

---

## 使用示例

**同步最新数据：**
```bash
curl -X POST http://127.0.0.1:5000/api/sync
```

**等待完成并检查结果：**
```bash
curl http://127.0.0.1:5000/api/sync/status
```

**搜索 AI 相关推文：**
```bash
curl "http://127.0.0.1:5000/api/tweets?q=AI&sort=favorite_count&per_page=5"
```

**查看某个作者的所有点赞：**
```bash
curl "http://127.0.0.1:5000/api/tweets?author=kwindla"
```

**发一条推文：**
```bash
curl -X POST http://127.0.0.1:5000/api/publish \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello from MyXAssistant!"}'
```
