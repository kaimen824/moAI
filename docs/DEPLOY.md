# 墨澜 MoLan 公网部署指南

适用场景:单机 SQLite 部署,约 100 用户以内的公网服务。超出该规模需先
迁移 PostgreSQL + 多进程方案(见文末「边界」)。

## 1. 必设环境变量(公网安全底线)

```bash
# JWT 签名密钥(必须覆盖默认值,否则任何人可伪造 token)
NOVEL_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"

# 初始管理员口令(必须覆盖默认 admin123,启动时会打印警告直到覆盖)
NOVEL_ADMIN_USER=admin
NOVEL_ADMIN_PASSWORD="<强口令>"

# LLM API Key(阿里云百炼聚合,或智谱直连)
DASHSCOPE_API_KEY=sk-xxx
# GLM_API_KEY=sk-xxx          # 仅当直连智谱时
```

这些变量可写入项目根目录 `.env`(已设置的系统环境变量优先)。

## 2. 全部环境变量清单

| 变量 | 默认 | 说明 |
|---|---|---|
| `NOVEL_JWT_SECRET` | dev 默认值(**公网必改**) | JWT 签名密钥 |
| `NOVEL_JWT_EXPIRE_HOURS` | `2` | access token 有效期(小时) |
| `NOVEL_JWT_REFRESH_EXPIRE_HOURS` | `168` | refresh token 有效期(小时,7 天;活跃用户滑动续期) |
| `NOVEL_ADMIN_USER` | `admin` | 初始管理员用户名(仅首次 seed 生效) |
| `NOVEL_ADMIN_PASSWORD` | `admin123`(**公网必改**) | 初始管理员口令 |
| `NOVEL_DB_PATH` | `data/novel_agent.db` | SQLite 路径(WAL 自动开启) |
| `NOVEL_LLM_TIMEOUT_SECONDS` | `120` | 每次 LLM 调用超时 |
| `NOVEL_LLM_MAX_RETRIES` | `3` | SDK 内置 429/5xx 退避重试次数 |
| `NOVEL_STORY_TOKEN_BUDGET`… 见下 | `0`(不限) | 每日 token 预算(UTC 日,usage_log 聚合) |
| `NOVEL_STORY_DAILY_TOKEN_BUDGET` | `0` | 单 story 每日 token 上限,超限 429 |
| `NOVEL_GLOBAL_DAILY_TOKEN_BUDGET` | `0` | 全站每日 token 上限,超限 429 |
| `DASHSCOPE_API_KEY` / `GLM_API_KEY` | 空 | LLM Provider 密钥 |
| `MODEL__<ROLE>` | 见 `.env.example` | 按 Agent 角色覆盖模型(如 `MODEL__WRITER=glm-5`) |

公网建议至少设置 `NOVEL_GLOBAL_DAILY_TOKEN_BUDGET`(成本炸弹保险丝),
`NOVEL_STORY_DAILY_TOKEN_BUDGET` 按单用户日耗估一个量级。

## 3. 后端

```bash
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

**必须单 worker**:`_active` 运行集合与引擎单例在进程内存中,多 worker 会导致
互斥(409)失效、事件流串台。纵向扩容请加大机器,横向扩容前需先完成
PostgreSQL + Redis 迁移(未实现,勿直接上线多副本)。

进程启动时自动:版本化迁移(增量补列/补表)、收敛崩溃残留的 running 状态、
seed 初始管理员。**升级部署直接替换代码重启即可**,老库自动迁移。

## 4. 前端

```bash
cd frontend && npm ci && npm run build   # 产物在 frontend/dist
```

二选一:

**A. FastAPI 静态托管(最短路径)**:把 `dist` 挂给 FastAPI 后重启——
在 `app/main.py` 追加:

```python
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="spa")
```

**B. nginx 反代(前后端分离)**:静态走 nginx,`/api` 与 SSE 反代到 8000:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection "";     # SSE 必需:禁用连接复用截断
    proxy_read_timeout 3600s;           # SSE 长连接(单章生成可达数十分钟)
    proxy_buffering off;                # SSE 必需:关闭缓冲才有打字机效果
}
```

前端 API 地址见 `frontend/src/api.js` 的 base 配置。

## 5. HTTPS

最短路径用 Caddy(自动签发/续期证书):

```
your-domain.com {
    reverse_proxy 127.0.0.1:8000
}
```

nginx 方案则用 certbot 签发后配置 443。**公网必须 HTTPS**:Bearer token
明文传输在 HTTP 下可被链路窃听。

## 6. 备份与数据边界

- 数据全部在 `NOVEL_DB_PATH` 单文件(WAL 模式,另有 `-wal`/`-shm` 伴生文件)。
  备份用 `sqlite3 data/novel_agent.db ".backup backup.db"`,不要直接 cp 活库。
- 观测数据(usage_log/agent_traces/review_results/llm_failures)随业务库增长,
  可按 created_at 定期归档清理。

## 7. 单机部署边界(诚实口径)

- **并发上限**:SQLite 写锁 + 单进程引擎,实测语义上支持约 100 用户、
  10 级并发生成(每 story 同时刻只允许 1 个 active run,互斥在 409 挡住)。
- **无水平扩展**:见第 3 节,多 worker/多副本未经设计,不要上线。
- **事件流不持久化**:重启后前端凭 run-state(持久化中断卡)+ codex 重建
  视图;一般过程事件不回放。
- **运维观测**:`GET /admin/stats`(admin token)提供 active runs /
  24h 失败率 / token 成本(按日+story+owner);日志走 stderr
  (`novel.agent` logger,异常带全栈),交给 systemd/journald 采集即可。

## 8. systemd 示例

```ini
# /etc/systemd/system/molan.service
[Unit]
After=network.target

[Service]
User=deploy
WorkingDirectory=/srv/molan
EnvironmentFile=/srv/molan/.env.production
ExecStart=/srv/molan/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```
