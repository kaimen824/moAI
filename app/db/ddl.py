"""全表 DDL(DESIGN_FINAL §3.2 数据模型,15 张设计表 + 实现载体表)。

表清单与设计文档一一对应;实现期补充的载体表(stories / outlines / paragraphs)
是既有设计概念的存储落点(多篇管理 / 总大纲常驻 / 段落级 embedding),见变更日志。

时间戳统一 TEXT(ISO-8601 UTC);主键 TEXT(uuid4.hex,应用层生成)。
"""

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

-- ========== 基础(实现期补充的载体表)==========

CREATE TABLE IF NOT EXISTS stories (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  premise     TEXT,
  status      TEXT NOT NULL DEFAULT 'draft',   -- draft|creating|active|completed
  main_branch_id TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outlines (
  id          TEXT PRIMARY KEY,
  story_id    TEXT NOT NULL REFERENCES stories(id),
  version_no  INTEGER NOT NULL,
  content     TEXT NOT NULL,                   -- 总大纲(markdown)
  status      TEXT NOT NULL DEFAULT 'draft',   -- draft|confirmed
  created_at  TEXT NOT NULL,
  UNIQUE (story_id, version_no)
);

-- ========== 分支簿记(ADR-0004/0007)==========

CREATE TABLE IF NOT EXISTS branches (
  id            TEXT PRIMARY KEY,
  story_id      TEXT NOT NULL REFERENCES stories(id),
  kind          TEXT NOT NULL DEFAULT 'main',  -- main|if_line
  fork_chapter_no INTEGER,                     -- IF 线 fork 点
  parent_branch_id TEXT,
  status        TEXT NOT NULL DEFAULT 'active',-- active|archived
  created_at    TEXT NOT NULL
);

-- ========== 长期记忆:facts / beliefs(分表,E2 终裁)==========

CREATE TABLE IF NOT EXISTS facts (
  id            TEXT PRIMARY KEY,
  story_id      TEXT NOT NULL REFERENCES stories(id),
  type          TEXT NOT NULL,                 -- event|state|setting|relation
  content       TEXT NOT NULL,
  chapter_established INTEGER,
  branch_id     TEXT NOT NULL REFERENCES branches(id),
  prev_version_id TEXT,                        -- 版本链:被本行推翻的上一版
  confidence    TEXT NOT NULL DEFAULT 'high',  -- high|low(E3 置信度分层)
  status        TEXT NOT NULL DEFAULT 'confirmed', -- confirmed|pending_review
  embedding     BLOB,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_story_branch ON facts(story_id, branch_id);
CREATE INDEX IF NOT EXISTS idx_facts_version ON facts(prev_version_id);

CREATE TABLE IF NOT EXISTS beliefs (
  id            TEXT PRIMARY KEY,
  story_id      TEXT NOT NULL REFERENCES stories(id),
  character_id  TEXT NOT NULL,
  content       TEXT NOT NULL,
  source_fact_id TEXT,                         -- 关联客观事实(误信 = belief 与 fact 冲突)
  status        TEXT NOT NULL DEFAULT 'believed', -- believed|dispelled
  established_chapter INTEGER,
  dispelled_chapter INTEGER,
  branch_id     TEXT NOT NULL REFERENCES branches(id),
  prev_version_id TEXT,                        -- 认知演化链:误信A -> 误信B -> 真相
  embedding     BLOB,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_beliefs_char ON beliefs(story_id, character_id, status);

CREATE TABLE IF NOT EXISTS fact_visibility (
  fact_id       TEXT NOT NULL REFERENCES facts(id),
  character_id  TEXT NOT NULL,
  knowledge_level TEXT NOT NULL,               -- known_full|known_partial(R3:缺省即未知,不落 unknown 行)
  detail        TEXT,                          -- R4:partial 时记录"知晓的部分"
  learned_chapter INTEGER,
  branch_id     TEXT NOT NULL,
  PRIMARY KEY (fact_id, character_id)
);

-- ========== 角色 / 章节 ==========

CREATE TABLE IF NOT EXISTS characters (
  id          TEXT PRIMARY KEY,
  story_id    TEXT NOT NULL REFERENCES stories(id),
  name        TEXT NOT NULL,
  profile     TEXT,                            -- 身份/外貌/性格/目标(markdown)
  entity_id   TEXT,                            -- 关联 wiki 条目
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
  id            TEXT PRIMARY KEY,
  story_id      TEXT NOT NULL REFERENCES stories(id),
  chapter_no    INTEGER NOT NULL,
  version_no    INTEGER NOT NULL DEFAULT 1,    -- R2 章节版本化
  prev_version_id TEXT,
  title         TEXT,
  content       TEXT,                          -- 原文全量
  status        TEXT NOT NULL DEFAULT 'draft', -- draft|active|stale|archived
  branch_id     TEXT NOT NULL REFERENCES branches(id),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chapters_story ON chapters(story_id, chapter_no, status);

CREATE TABLE IF NOT EXISTS paragraphs (
  id            TEXT PRIMARY KEY,
  chapter_id    TEXT NOT NULL REFERENCES chapters(id),
  para_no       INTEGER NOT NULL,
  text          TEXT NOT NULL,
  embedding     BLOB,                          -- 段落级 embedding(检索下钻层)
  UNIQUE (chapter_id, para_no)
);

CREATE TABLE IF NOT EXISTS chapter_summaries (
  id          TEXT PRIMARY KEY,
  story_id    TEXT NOT NULL REFERENCES stories(id),
  chapter_no  INTEGER,                         -- layer=book 时为 NULL
  layer       TEXT NOT NULL,                   -- chapter|volume|book(分层检索索引)
  content     TEXT NOT NULL,
  branch_id   TEXT NOT NULL,
  embedding   BLOB,
  created_at  TEXT NOT NULL
);

-- ========== 伏笔 / 时间线 ==========

CREATE TABLE IF NOT EXISTS plot_threads (
  id              TEXT PRIMARY KEY,
  story_id        TEXT NOT NULL REFERENCES stories(id),
  description     TEXT NOT NULL,
  planted_chapter INTEGER,
  resolved_chapter INTEGER,
  status          TEXT NOT NULL DEFAULT 'open', -- open|resolved|dropped
  branch_id       TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS temporal_relations (
  id          TEXT PRIMARY KEY,
  story_id    TEXT NOT NULL REFERENCES stories(id),
  event_a     TEXT NOT NULL,                   -- facts.id
  event_b     TEXT NOT NULL,                   -- facts.id
  relation    TEXT NOT NULL,                   -- before|after|during|parallel
  branch_id   TEXT NOT NULL
);

-- ========== Wiki 实体与链接图(ADR-0002;ADR-0015 激活)==========

CREATE TABLE IF NOT EXISTS entities (
  id          TEXT PRIMARY KEY,
  story_id    TEXT NOT NULL REFERENCES stories(id),
  type        TEXT NOT NULL,                   -- character|faction|location|item|technique|concept
  name        TEXT NOT NULL,
  content     TEXT,                            -- wiki 条目正文(阶段末滚动摘要维护)
  embedding   BLOB,
  chapter_no  INTEGER,                         -- 首次出现章(NULL=共创种子)
  status      TEXT NOT NULL DEFAULT 'active',  -- active|merged(被合并保留审计)
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_links (
  id          TEXT PRIMARY KEY,
  story_id    TEXT NOT NULL REFERENCES stories(id),
  from_entity TEXT NOT NULL REFERENCES entities(id),
  to_entity   TEXT NOT NULL REFERENCES entities(id),
  relation    TEXT,                            -- 自由文本(ADR-0015 裁决④)
  chapter_no  INTEGER                          -- 关系确立章(NULL=共创种子;阶段滚动锚点)
);
CREATE INDEX IF NOT EXISTS idx_links_story ON entity_links(story_id);

-- 实体别名(ADR-0015 去重确定性层:道号/俗称/尊称 -> 实体)
CREATE TABLE IF NOT EXISTS entity_aliases (
  alias       TEXT NOT NULL,
  story_id    TEXT NOT NULL REFERENCES stories(id),
  entity_id   TEXT NOT NULL REFERENCES entities(id),
  created_at  TEXT NOT NULL,
  PRIMARY KEY (story_id, alias)                -- 同书别名唯一:先注册者得
);

-- 合并提案(ADR-0015:LLM 裁决 uncertain 才入队;人工裁决持久生效)
CREATE TABLE IF NOT EXISTS entity_merge_proposals (
  id            TEXT PRIMARY KEY,
  story_id      TEXT NOT NULL REFERENCES stories(id),
  candidate_name TEXT NOT NULL,
  candidate_entity_id TEXT REFERENCES entities(id),  -- 先写后合并的候选条目
  target_entity_id TEXT NOT NULL REFERENCES entities(id),
  similarity    REAL,
  evidence      TEXT,                          -- 相似度+共现+关系重合等裁决证据
  status        TEXT NOT NULL DEFAULT 'pending', -- pending|merged|new|ignored
  chapter_no    INTEGER,
  created_at    TEXT NOT NULL,
  decided_at    TEXT
);

-- ========== 权限(ADR-0003/0006:表级 + story 隔离)==========

CREATE TABLE IF NOT EXISTS agent_acl (
  agent_name  TEXT NOT NULL,
  data_domain TEXT NOT NULL,                   -- 表名
  can_read    INTEGER NOT NULL DEFAULT 0,
  can_write   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (agent_name, data_domain)
);

-- ========== 可观测性(ADR-0010)==========

CREATE TABLE IF NOT EXISTS review_results (
  id          TEXT PRIMARY KEY,
  story_id    TEXT NOT NULL,
  chapter_id  TEXT,
  chapter_no  INTEGER,
  round_no    INTEGER NOT NULL,
  reviewer    TEXT NOT NULL,                   -- outline|reviewer
  verdict     TEXT NOT NULL,                   -- pass|revise|block
  scores      TEXT,                            -- json:各维度分数
  feedback    TEXT,
  forced_pass INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
  id          TEXT PRIMARY KEY,
  story_id    TEXT,
  agent       TEXT NOT NULL,
  model       TEXT NOT NULL,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  latency_ms  INTEGER,
  trace_id    TEXT,
  stage       TEXT,                            -- 调用环节(细纲/初稿/评审/抽取/摘要...)
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_traces (      -- 全节点可观测:LLM 输入/输出快照
  id          TEXT PRIMARY KEY,
  story_id    TEXT,
  agent       TEXT NOT NULL,
  model       TEXT NOT NULL,
  stage       TEXT,
  input_text  TEXT,                            -- 截断快照(role: content 序列化)
  output_text TEXT,                            -- 截断快照
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  latency_ms  INTEGER,
  trace_id    TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_story ON agent_traces(story_id, created_at);

CREATE TABLE IF NOT EXISTS retrieval_audit (
  id            TEXT PRIMARY KEY,
  story_id      TEXT,
  caller        TEXT NOT NULL,
  query         TEXT,
  returned_count INTEGER,
  latency_ms    INTEGER,
  created_at    TEXT NOT NULL
);

-- ========== 用户指令通道(任意时刻输入,生成时消费)==========
CREATE TABLE IF NOT EXISTS user_directives (
  id          TEXT PRIMARY KEY,
  story_id    TEXT NOT NULL REFERENCES stories(id),
  content     TEXT NOT NULL,
  consumed_at TEXT,                            -- NULL = 待消费
  created_at  TEXT NOT NULL
);
"""

ALL_TABLES = [
    "stories", "outlines", "branches",
    "facts", "beliefs", "fact_visibility",
    "characters", "chapters", "paragraphs", "chapter_summaries",
    "plot_threads", "temporal_relations",
    "entities", "entity_links", "entity_aliases", "entity_merge_proposals",
    "agent_acl",
    "review_results", "usage_log", "agent_traces", "retrieval_audit",
    "user_directives",
]

# 轻量迁移:既有库补列/补索引(CREATE IF NOT EXISTS 不覆盖已存在的表)。
# 引用新增列的索引必须在这里、ALTER 之后建——放在 SCHEMA_SQL 里会在老库上
# 因"表已存在被跳过、列还不存在"而失败(no such column)。
_MIGRATIONS = [
    ("entities", "chapter_no", "ALTER TABLE entities ADD COLUMN chapter_no INTEGER"),
    ("entities", "status", "ALTER TABLE entities ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"),
    ("entity_links", "chapter_no", "ALTER TABLE entity_links ADD COLUMN chapter_no INTEGER"),
    # 索引(table, 列校验放宽为表存在即建,IF NOT EXISTS 幂等)
    ("entities", "__idx_entities_story__",
     "CREATE INDEX IF NOT EXISTS idx_entities_story ON entities(story_id, status)"),
]


def migrate(conn) -> None:
    existing_tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table, col, sql in _MIGRATIONS:
        if table not in existing_tables:
            continue   # 全新建库:SCHEMA_SQL 已含新列,无需迁移
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(sql)
