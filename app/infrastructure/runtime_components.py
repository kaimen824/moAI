"""引擎运行组件(ADR-0030 阶段2):Deps 上帝对象按职责拆解的实现。

每个组件对应 application/ports.py 的一个端口;方法体自原 Deps 逐字迁移
(含原注释与阈值),行为零变化。依赖注入两种形态:
- 直依赖(conn/run_lock)——组装时即确定;
- 持 Deps 弱引用(_deps)——repo/entities/embed_fn 在 build_engine 中
  延迟注入(既有装配顺序:Deps 先构造,repo 后补),运行时经 self._deps 读。
"""

from __future__ import annotations

import json
import queue
import re
import uuid
from datetime import datetime, timezone

from app.core.config import THREAD_LONG_CAP, THREAD_SHORT_CAP
from app.core.run_context import StopRequested, current_run, current_user_id
from app.memory.entity import EntityService
from app.memory.repository import AgentContext, new_id
from app.memory.retrieval import encode_embedding
from app.memory.schemas import EntityLink


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteEventBus:
    """SSE 事件总线:按 story(thread)隔离的广播 + 历史(端口 EventBus)。"""

    def __init__(self) -> None:
        self._subscribers: list = []   # [(thread_id, queue)]
        self._history: list = []       # [(kind, data, thread_id)] 全局环形,按 thread 过滤

    def emit(self, kind: str, data: dict, thread_id: str | None = None) -> None:
        """广播事件(按 thread 订阅者)并留存历史。

        thread_id 缺省时从运行上下文读取(ADR-0023)——多 story 并发时节点内
        emit 归属各自的 run,不再依赖"最近启动"的全局单值。事件体补 run_id。
        """
        ctx = current_run()
        tid = thread_id or ctx[0]
        run_id = ctx[1]
        if isinstance(data, dict) and "run_id" not in data:
            data = {**data, "run_id": run_id} if run_id else data
        self._history.append((kind, data, tid))
        if len(self._history) > 2000:
            del self._history[: len(self._history) - 2000]
        for sub_tid, q in list(self._subscribers):
            if sub_tid == tid:
                try:
                    q.put((kind, data))
                except Exception:
                    pass

    def subscribe(self, thread_id: str):
        """新增订阅者:先重放该 thread 历史,再接收后续事件。"""
        q: queue.Queue = queue.Queue()
        for k, d, tid in self._history:
            if tid == thread_id:
                q.put((k, d))
        self._subscribers.append((thread_id, q))
        return q

    def unsubscribe(self, q) -> None:
        self._subscribers = [(t, x) for t, x in self._subscribers if x is not q]

    def has_subscribers(self, thread_id: str) -> bool:
        """该 thread 是否有活跃订阅者(writer 决定流式/一次性下发)。"""
        return any(t == thread_id for t, _q in self._subscribers)

    def snapshot(self, thread_id: str) -> list:
        return [(k, d) for k, d, tid in self._history if tid == thread_id]

    def clear_events(self, thread_id: str) -> None:
        self._history = [(k, d, t) for k, d, t in self._history if t != thread_id]
        self._subscribers = [(t, q) for t, q in self._subscribers if t != thread_id]


class InMemoryStopController:
    """协作式停止(端口 StopController):用户中断按钮置位 -> 节点入口检查抛
    StopRequested(行业惯例:不硬杀线程,在步骤边界安全退出,checkpointer
    状态保留可续跑)。"""

    def __init__(self) -> None:
        self._stop_requests: set = set()

    def request_stop(self, story_id: str) -> None:
        self._stop_requests.add(story_id)

    def clear_stop(self, story_id: str) -> None:
        self._stop_requests.discard(story_id)

    def check_stop(self, story_id: str) -> None:
        """节点入口调用:置位即抛 StopRequested(由 _node 统一注入)。"""
        if story_id in self._stop_requests:
            raise StopRequested(story_id)


class SqliteRunStateStore:
    """运行状态持久化(端口 RunStateStore,ADR-0027,评审 6.4)。"""

    def __init__(self, conn, run_lock) -> None:
        self.conn = conn
        self.run_lock = run_lock

    def set_run_state(self, story_id: str, *, status: str, run_id: str | None = None,
                      interrupt_type: str | None = None,
                      interrupt_payload: str | None = None,
                      error_code: str | None = None,
                      error_stage: str | None = None,
                      target_chapters: int | None = None) -> None:
        """upsert 运行真相:running(启动)/waiting(中断卡)/idle(终态+错误码)。

        run_id 与 target_chapters 更新时 COALESCE 保留旧值(idle 转换后仍可
        关联最后一次 run);interrupt 与 error 字段随语义整体覆写。
        """
        started = _now() if status == "running" else None
        with self.run_lock:
            self.conn.execute(
                """
                INSERT INTO story_run_state (story_id, run_id, status, interrupt_type,
                    interrupt_payload, target_chapters, error_code, error_stage,
                    started_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(story_id) DO UPDATE SET
                    run_id=COALESCE(excluded.run_id, story_run_state.run_id),
                    status=excluded.status,
                    interrupt_type=excluded.interrupt_type,
                    interrupt_payload=excluded.interrupt_payload,
                    target_chapters=COALESCE(excluded.target_chapters,
                                             story_run_state.target_chapters),
                    error_code=excluded.error_code,
                    error_stage=excluded.error_stage,
                    updated_at=excluded.updated_at
                """,
                (story_id, run_id, status, interrupt_type, interrupt_payload,
                 target_chapters, error_code, error_stage, started, _now()),
            )
            self.conn.commit()

    def get_run_state(self, story_id: str) -> dict | None:
        with self.run_lock:
            row = self.conn.execute(
                "SELECT * FROM story_run_state WHERE story_id=?", (story_id,)).fetchone()
        return dict(row) if row else None


class SqliteDirectiveChannel:
    """用户指令通道(端口 DirectiveChannel,ADR-0024)。"""

    def __init__(self, conn, run_lock) -> None:
        self.conn = conn
        self.run_lock = run_lock

    def record_directive(self, story_id: str, content: str) -> str:
        """用户随时提交的指示;在下一次 build_context 时被主控消费。"""
        with self.run_lock:
            did = uuid.uuid4().hex
            self.conn.execute(
                "INSERT INTO user_directives (id, story_id, content, consumed_at, created_at)"
                " VALUES (?,?,?,?,?)",
                (did, story_id, content, None, _now()),
            )
            self.conn.commit()
            return did

    def peek_pending_directives(self, story_id: str) -> list[dict]:
        """查看未消费指令(只读;ADR-0024:消费标记延迟到定稿事务内执行)。

        此前"取走即标记"在生成失败/中断/重写时丢失用户指令(已标 consumed
        却未进定稿);现返回 [{id, content}],由 build_context 注入上下文,
        commit_finalize 在定稿事务内按 id 批量标记——指令生命周期与章节定稿原子。
        """
        with self.run_lock:
            rows = self.conn.execute(
                "SELECT id, content FROM user_directives"
                " WHERE story_id=? AND consumed_at IS NULL ORDER BY created_at",
                (story_id,),
            ).fetchall()
            return [{"id": r["id"], "content": r["content"]} for r in rows]

    def mark_directives_consumed(self, directive_ids: list[str]) -> None:
        """定稿事务内标记指令已消费(由 commit_finalize 调用,不单独 commit)。"""
        if not directive_ids:
            return
        marks = ",".join("?" * len(directive_ids))
        self.conn.execute(
            f"UPDATE user_directives SET consumed_at=? WHERE id IN ({marks})",
            [_now(), *directive_ids],
        )


class SqliteRecapBuilder:
    """记忆拼装(端口 RecapBuilder,ADR-0003 分层记忆)。"""

    def __init__(self, conn) -> None:
        self.conn = conn          # 与 repo.conn 同一连接(原经 repo.conn 访问)

    def recent_carryover(self, state: dict) -> str:
        """短期记忆:最近 2 章摘要 + 上一章结尾原文(ADR-0003)。"""
        story_id = state.get("story_id", "")
        chapter_no = state.get("chapter_no", 1)
        rows = self.conn.execute(
            "SELECT c.chapter_no, substr(c.content, -400) AS tail, s.content AS summary"
            " FROM chapters c LEFT JOIN chapter_summaries s"
            "   ON s.story_id = c.story_id AND s.chapter_no = c.chapter_no AND s.layer='chapter'"
            " WHERE c.story_id=? AND c.status='active' AND c.chapter_no < ?"
            " ORDER BY c.chapter_no DESC LIMIT 2",
            (story_id, chapter_no),
        ).fetchall()
        if not rows:
            return "(本书第一章)"
        parts = [f"上一章(ch{r['chapter_no']})摘要:{r['summary'] or ''}" for r in reversed(rows)]
        parts.append(f"上一章结尾原文:{rows[0]['tail'] or ''}")
        return "\n".join(parts)

    def stage_chapter_summaries(self, story_id: str, stage_start: int, *,
                                upto: int) -> list[tuple[int, str]]:
        """当前阶段内(>=stage_start, <=upto)的定稿章摘要序列。"""
        rows = self.conn.execute(
            "SELECT s.chapter_no, s.content FROM chapter_summaries s"
            " JOIN chapters c ON c.story_id = s.story_id AND c.chapter_no = s.chapter_no"
            "   AND c.status='active' AND c.branch_id = s.branch_id"
            " WHERE s.story_id=? AND s.layer='chapter'"
            "   AND s.chapter_no >= ? AND s.chapter_no <= ?"
            " ORDER BY s.chapter_no",
            (story_id, stage_start, upto),
        ).fetchall()
        return [(r["chapter_no"], r["content"] or "") for r in rows]

    def story_recap(self, state: dict) -> str:
        """已完成剧情回顾(分层记忆,防长篇上下文膨胀):

        - 更早阶段:各阶段聚合摘要(layer='stage',每条截 400 字)
        - 当前阶段:全量章摘要(细纲单元粒度,3-6 章,保真)
        - 细纲进度:当前细纲行中已写完/待写的章号(防重排锚点)
        stage_start 缺失(旧 checkpoint)时兜底为最近 10 章。
        """
        story_id = state.get("story_id", "")
        chapter_no = state.get("chapter_no", 1)
        stage_start = state.get("stage_start_chapter", 0)
        if stage_start <= 0:   # 旧状态兜底:最近 10 章视为"当前阶段"
            stage_start = max(chapter_no - 10, 1)

        stage_rows = self.conn.execute(
            "SELECT s.chapter_no, s.content FROM chapter_summaries s"
            " WHERE s.story_id=? AND s.layer='stage' AND s.chapter_no < ?"
            " ORDER BY s.chapter_no",
            (story_id, stage_start),
        ).fetchall()
        chapter_rows = self.stage_chapter_summaries(story_id, stage_start, upto=chapter_no - 1)

        if not stage_rows and not chapter_rows:
            return "(尚无已完成章节)"

        parts: list[str] = []
        if stage_rows:
            parts.append("[早期剧情(阶段聚合)]")
            parts.extend(f"- (至第{r['chapter_no']}章){(r['content'] or '')[:400]}"
                         for r in stage_rows)
        if chapter_rows:
            parts.append(f"[当前阶段(第{stage_start}章起,全量)]")
            parts.extend(f"- 第{no}章:{text[:200]}" for no, text in chapter_rows)

        # 细纲进度指针:已写完的细纲行 vs 待写(防重排的直接锚点)
        outline = state.get("stage_outline", "")
        if outline:
            done_lines, todo_lines = [], []
            for line in outline.splitlines():
                m = re.match(rf"\s*[-*]?\s*第?(\d+)[章|、|\s]", line)
                if not m:
                    continue
                (done_lines if int(m.group(1)) < chapter_no else todo_lines).append(line.strip())
            if done_lines or todo_lines:
                parts.append("[当前细纲进度(已完成行严禁重写)]")
                parts.extend(f"  已写完: {l}" for l in done_lines[:8])
                parts.extend(f"  待写:   {l}" for l in todo_lines[:8])
        return "\n".join(parts)

    def parse_stage_range(self, stage_outline: str, *, start: int) -> int:
        """解析细纲覆盖的末章章号(阶段边界);解析不出时保守取 start+2。"""
        nums = [int(m) for m in re.findall(r"第?(\d+)[章|、|\s]", stage_outline)]
        nums = [n for n in nums if n >= start]
        return max(nums) if nums else start + 2

    def extract_stage_line(self, stage_outline: str, chapter_no: int) -> str:
        """从阶段细纲提取本章行(确定性切片;兜底返回整份细纲)。"""
        for line in stage_outline.splitlines():
            if re.match(rf"\s*[-*]?\s*第?{chapter_no}[章|、|\s]", line):
                return line
        return stage_outline[:300]


class SqliteStylePolicy:
    """叙述风格策略(端口 StylePolicy,ADR-0017/0019)。"""

    # 虚词边界(ADR-0017 修正):以这些字结尾/开头的短语是语法黏连而非口头禅
    # (如"林默没有""苏清歌说""审计部的")——写手无法稳定避开,禁了必成死循环
    _BAN_END_STOP = set("的了说是着在没吗呢吧被把和与或及")
    _BAN_START_STOP = set("的了不没也都就很被他她它")

    def __init__(self, conn, run_lock) -> None:
        self.conn = conn
        self.run_lock = run_lock

    def recent_phrase_blacklist(self, story_id: str, chapter_no: int, *,
                                window: int = 5, min_freq: int = 3,
                                min_chapters: int = 2, limit: int = 12,
                                exclude_texts: list[str] | None = None) -> list[str]:
        """动态句式自检(ADR-0017):近 K 章高频复现的中文短语 -> 本章禁用清单。

        题材无关、随书自适应(替代硬编码句式黑名单):4-gram 计数,仅统计
        跨 ≥min_chapters 章且总频次 ≥min_freq 的纯中文片段(跳过标点/数字/
        英文——数值滥用由写作规则约束,不在此列);相邻高频 gram 合并为最长短语。
        虚词边界过滤:以虚词开头/结尾的候选(语法黏连模式)不入清单——
        "人名+没有/说""名词+的"是自然汉语,禁用它们等于猎杀语法,写手
        无法稳定避开,会造成评审-重写死循环(ch12 实证)。
        实体指称排除(ADR-0019):与实体名/别名/角色名互为子串的短语不入清单——
        剧情连续章指称同一地点/机构是正常指称密度,不是复读口头禅
        (ch10"精神病院"12 次进黑名单、评审每轮开替换处方的实证)。
        剧情承载短语排除(ADR-0035):exclude_texts(本章要点/细纲/伏笔/衔接)
        交叠的短语不入清单,理由同上(《古真神》ch4"月圆之约"实证)。
        """
        with self.run_lock:
            rows = self.conn.execute(
                "SELECT content FROM chapters"
                " WHERE story_id=? AND status='active' AND chapter_no<?"
                " ORDER BY chapter_no DESC LIMIT ?",
                (story_id, chapter_no, window)).fetchall()
        if len(rows) < min_chapters:
            return []
        texts = [(r["content"] or "") for r in rows]
        gram_chapters: dict[str, set[int]] = {}
        for ci, text in enumerate(texts):
            for run in re.findall(r"[一-鿿]+", text):
                for i in range(len(run) - 3):
                    gram_chapters.setdefault(run[i:i + 4], set()).add(ci)
        # 频次阈值:出现于足够多章(跨章复现才是口头禅;单章内的重复多为有意排比)
        frequent = {g for g, chs in gram_chapters.items() if len(chs) >= min_chapters}
        if not frequent:
            return []
        counts: dict[str, int] = {}
        for text in texts:
            for run in re.findall(r"[一-鿿]+", text):
                for i in range(len(run) - 3):
                    g = run[i:i + 4]
                    if g in frequent:
                        counts[g] = counts.get(g, 0) + 1
        hot = {g for g, c in counts.items() if c >= min_freq}
        if not hot:
            return []
        # 合并为最长短语:连续段内所有 4-gram 均高频(末 gram 尾部 3 字计入跨度)
        phrases: dict[str, int] = {}
        for text in texts:
            for run in re.findall(r"[一-鿿]+", text):
                i = 0
                while i + 4 <= len(run):
                    j = i
                    while j + 4 <= len(run) and run[j:j + 4] in hot:
                        j += 1
                    if j > i:
                        phrases[run[i:j + 3]] = phrases.get(run[i:j + 3], 0) + 1
                        i = j          # 从首个非高频 gram 处继续扫描
                    else:
                        i += 1
        # 虚词边界修剪(语法黏连不入清单):掐掉首尾虚词(含"没有/的话"等双字尾缀),
        # 修剪后不足 4 字(只剩人名/名词骨架)则整条丢弃——"林默没有""苏清歌说"
        # "审计部的"这类模式写手无法稳定避开,禁了必成评审-重写死循环(ch12 实证)
        _END_TOKENS = ("没有", "的话", "似的", "一样", "一般")
        trimmed: dict[str, int] = {}
        for p, c in phrases.items():
            q = p
            changed = True
            while changed and q:
                changed = False
                for t in _END_TOKENS:
                    if q.endswith(t):
                        q = q[:-len(t)]
                        changed = True
                if q and q[-1] in self._BAN_END_STOP:
                    q = q[:-1]
                    changed = True
                if q and q[0] in self._BAN_START_STOP:
                    q = q[1:]
                    changed = True
            if len(q) >= 4:
                trimmed[q] = trimmed.get(q, 0) + c
        phrases = trimmed

        # 实体指称排除(ADR-0019):实体名/别名/角色名的双向子串剔除——
        # "精神病院""中央后勤部"这类专有名词跨章高频是叙事骨架,禁用它们
        # 逼写手规避式改写,只会稀释指代并重燃评审-重写循环
        with self.run_lock:
            protected = {r["name"] for r in self.conn.execute(
                "SELECT name FROM entities WHERE story_id=? AND status='active'",
                (story_id,))}
            protected |= {r["alias"] for r in self.conn.execute(
                "SELECT alias FROM entity_aliases WHERE story_id=?", (story_id,))}
            protected |= {r["name"] for r in self.conn.execute(
                "SELECT name FROM characters WHERE story_id=?", (story_id,))}
        if protected:
            phrases = {p: c for p, c in phrases.items()
                       if not any((p in n or n in p) for n in protected)}

        # 剧情承载短语排除(ADR-0035):与本章要点/阶段细纲/活跃伏笔/上期衔接
        # 有 ≥3 字交叠的短语不入清单——"月圆之约"这类剧情死线跨章高频是叙事
        # 骨架,进了清单即评审-重写死锁(《古真神》ch4 实证:style 三轮锁 6.0,
        # 同义变体全被抓,3 轮耗尽转人工)。与 ADR-0019 实体排除同族:
        # 口头禅是修饰层,剧情词是内容层,禁内容层等于禁剧情
        if exclude_texts:
            excl_grams: set[str] = set()
            for text in exclude_texts:
                for run in re.findall(r"[一-鿿]+", text or ""):
                    excl_grams |= {run[i:i + 3] for i in range(len(run) - 2)}
            phrases = {p: c for p, c in phrases.items()
                       if not any(p[i:i + 3] in excl_grams
                                  for i in range(len(p) - 2))}

        # 去包含:短语被更长高频短语覆盖时丢弃
        ordered = sorted(phrases, key=lambda p: (-len(p), p))
        kept: list[str] = []
        for p in ordered:
            if not any(p in q for q in kept):
                kept.append(p)
        kept.sort(key=lambda p: -phrases[p])
        return kept[:limit]

    def canonical_entity_registry(self, story_id: str, *, cap: int = 60) -> list[dict]:
        """规范名词典(ADR-0019):active 实体名 + 已注册别名 -> 叙述层统一用名。

        角色类实体不入册(角色名由[在场角色卡]注入,避免双份);
        返回 [{name, type, aliases:[...]}],按创建序,超量截断保 token。
        """
        with self.run_lock:
            ents = [dict(r) for r in self.conn.execute(
                "SELECT name, type FROM entities"
                " WHERE story_id=? AND status='active' AND type != 'character'"
                " ORDER BY created_at LIMIT ?", (story_id, cap))]
            alias_rows = self.conn.execute(
                "SELECT a.alias, e.name FROM entity_aliases a"
                " JOIN entities e ON e.id = a.entity_id WHERE a.story_id=?",
                (story_id,)).fetchall()
        by_name = {e["name"]: e for e in ents}
        for r in alias_rows:
            e = by_name.get(r["name"])
            if e:
                e.setdefault("aliases", []).append(r["alias"])
        return ents


class SqliteCharacterRegistry:
    """角色/实体簿记辅助(端口 CharacterRegistry,ADR-0015)。

    持 Deps 弱引用:repo/entities/embed_fn 由 build_engine 延迟注入。
    """

    def __init__(self, deps) -> None:
        self._deps = deps

    def character_name_map(self, state: dict) -> dict[str, str]:
        """名字 -> 角色 id(ADR-0015 升级:并入实体别名表,修别称盲区)。

        本名精确优先;别名(道号/俗称/尊称)经 characters.entity_id 关联补入。
        消歧正确性由别名注册时的实体层保证(同书别名唯一,先注册者得)。
        """
        story_id = state.get("story_id", "")
        ctx = AgentContext("supervisor", story_id)
        mapping = {c.name: c.id for c in self._deps.repo.get_characters(ctx)}
        rows = self._deps.conn.execute(
            "SELECT a.alias, c.id FROM entity_aliases a"
            " JOIN characters c ON c.entity_id = a.entity_id AND c.story_id = a.story_id"
            " WHERE a.story_id=?", (story_id,)).fetchall()
        for r in rows:
            mapping.setdefault(r["alias"], r["id"])
        return mapping

    def prepare_character_seeds(self, state: dict) -> dict:
        """共创落库准备阶段(评审 6.8 / ADR-0024,纯计算零 DB 写):
        解析角色/实体/别名/链接草稿为待写行(预生成 id),embedding 在事务外
        批量计算(失败降级 None)。与 commit_character_seeds 配对——准备失败
        在此抛出,调用方(confirm_master_outline)尚未进入事务,零残留。

        角色实体由代码确定性生成(与角色卡同名同文,零对齐风险),entity_id
        回写 character_drafts——检索链接扩展与别名识别从第一章即生效。
        """
        story_id = state["story_id"]
        seeds = state.get("entity_drafts") or {}
        char_aliases = {a.get("name"): (a.get("aliases") or [])
                        for a in seeds.get("character_aliases", [])}
        chars: list[dict] = []
        ents: list[dict] = []
        aliases: list[tuple[str, str]] = []
        name_to_eid: dict[str, str] = {}
        ids: list[str] = []
        # 1) 角色卡 + 角色实体(确定性同名同文)
        for d in state.get("character_drafts", []):
            name = d.get("name", "未命名")
            cid, eid = new_id(), new_id()
            chars.append({"id": cid, "name": name, "profile": d.get("profile", ""),
                          "entity_id": eid})
            ents.append({"id": eid, "type": "character", "name": name,
                         "content": d.get("profile", "")})
            d["id"] = cid
            d["entity_id"] = eid
            ids.append(cid)
            name_to_eid[name] = eid
            for a in char_aliases.get(name, []):
                aliases.append(((a or "").strip(), eid))
        # 2) 种子实体(势力/地点/物品/功法/概念)
        for e in seeds.get("entities", []):
            name = (e.get("name") or "").strip()
            if not name or name in name_to_eid:
                continue
            eid = new_id()
            ents.append({"id": eid, "type": e.get("type", "concept"), "name": name,
                         "content": e.get("content", "")})
            name_to_eid[name] = eid
            for a in e.get("aliases", []) or []:
                aliases.append(((a or "").strip(), eid))
        # 3) 初始链接(from/to 名字对;解析不了的丢弃)
        links = [EntityLink(id=new_id(), story_id=story_id,
                            from_entity=name_to_eid[(l.get("from") or "").strip()],
                            to_entity=name_to_eid[(l.get("to") or "").strip()],
                            relation=(l.get("relation") or "")[:120])
                 for l in seeds.get("links", [])
                 if (l.get("from") or "").strip() in name_to_eid
                 and (l.get("to") or "").strip() in name_to_eid]
        # 4) 种子实体 embedding(消歧向量层;事务外批量,失败降级 None 不阻塞)
        for e in ents:
            e["embedding"] = None
        if self._deps.embed_fn is not None and ents:
            try:
                vectors = self._deps.embed_fn(
                    [f"{e['name']}:{(e['content'] or '')[:200]}" for e in ents])
                for e, v in zip(ents, vectors):
                    if v:
                        e["embedding"] = encode_embedding(v)
            except Exception:
                pass
        return {"story_id": story_id, "chars": chars, "ents": ents,
                "aliases": aliases, "links": links, "ids": ids}

    def commit_character_seeds(self, plan: dict, *, commit: bool = True) -> list[str]:
        """共创落库写入阶段(纯 DB,评审 6.8 / ADR-0024):写角色卡+实体+别名+链接。

        commit=False 时不开自己的事务、不 commit,由调用方事务统一提交
        (confirm_master_outline 把总大纲与角色卡并入同一事务,中途失败整体
        回滚——不再出现"大纲已确认但角色卡缺失"的半成品)。身份走 agent_acl
        校验(character_manager/entity_manager,单写者不变式不变)。
        """
        story_id = plan["story_id"]
        ctx = AgentContext("character_manager", story_id)
        ectx = AgentContext("entity_manager", story_id)
        ts = _now()
        conn = self._deps.conn
        repo = self._deps.repo
        for c in plan["chars"]:
            repo.check_access(ctx, "characters", "write")
            conn.execute(
                "INSERT INTO characters (id, story_id, name, profile, entity_id,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (c["id"], story_id, c["name"], c["profile"], c["entity_id"], ts, ts))
        for e in plan["ents"]:
            repo.check_access(ectx, "entities", "write")
            conn.execute(
                "INSERT INTO entities (id, story_id, type, name, content, embedding,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (e["id"], story_id, e["type"], e["name"], e["content"],
                 e["embedding"], ts, ts))
        for alias, eid in plan["aliases"]:
            if alias:
                conn.execute(
                    "INSERT OR REPLACE INTO entity_aliases"
                    " (alias, story_id, entity_id, created_at) VALUES (?,?,?,?)",
                    (alias, story_id, eid, ts))
        if plan["links"]:
            repo.check_access(ectx, "entity_links", "write")
            conn.executemany(
                "INSERT INTO entity_links (id, story_id, from_entity, to_entity, relation)"
                " VALUES (?,?,?,?,?)",
                [(l.id, story_id, l.from_entity, l.to_entity, l.relation)
                 for l in plan["links"]])
        if commit:
            conn.commit()
        return plan["ids"]

    def resolve_entity_proposal(self, proposal_id: str, action: str) -> dict:
        """裁决合并提案:merge=执行归一(链接重定向+别名吸收);new/ignore=登记裁决。

        裁决持久生效(ADR-0015):后续同名候选按此规则自动处理,不再重复入队。
        """
        conn = self._deps.conn
        entities: EntityService | None = self._deps.entities
        with self._deps.run_lock:
            row = conn.execute(
                "SELECT * FROM entity_merge_proposals WHERE id=? AND status='pending'",
                (proposal_id,)).fetchone()
            if not row:
                raise LookupError(f"pending proposal not found: {proposal_id}")
            p = dict(row)
            if action == "merge":
                entities.execute_merge(conn, p)   # entities 未装配时 AttributeError(原行为)
            else:
                conn.execute(
                    "UPDATE entity_merge_proposals SET status=?, decided_at=? WHERE id=?",
                    ("new" if action == "new" else "ignored", _now(), proposal_id))
            conn.commit()
            return p


class SqliteFailureLedger:
    """审计台账(端口 FailureLedger,评审 6.9/ADR-0026)。"""

    def __init__(self, conn, run_lock) -> None:
        self.conn = conn
        self.run_lock = run_lock

    def log_review(self, state: dict, *, reviewer: str, verdict: dict, round_no: int,
                   forced: bool | None = None) -> None:
        # forced=None:读 state 中的 rewrite_exhausted(评审节点);
        # 显式传入:merge 等在标志写入前落审计的调用方。
        if forced is None:
            forced = bool(state.get("rewrite_exhausted"))
        with self.run_lock:
            self.conn.execute(
                "INSERT INTO review_results (id, story_id, chapter_no, round_no, reviewer,"
                " verdict, scores, feedback, forced_pass, run_id, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, state.get("story_id", ""), state.get("chapter_no"),
                 round_no, reviewer, verdict.get("verdict", "revise"),
                 json.dumps(verdict.get("scores", {}), ensure_ascii=False),
                 verdict.get("feedback", ""),
                 1 if forced else 0, current_run()[1] or None, _now()),
            )
            self.conn.commit()

    def log_llm_failure(self, *, story_id: str, stage: str, node: str,
                        exc: Exception) -> None:
        """LLM 输出失败台账(评审 6.9 / ADR-0026):坏 JSON/校验失败截断留痕,
        trace_id 与 SSE error 事件关联归因。台账写失败不掩盖主错误。"""
        raw = getattr(exc, "raw_output", "")
        trace_id = getattr(exc, "trace_id", "")
        status_code = getattr(exc, "status_code", None)      # provider 异常自带(ADR-0028)
        retry_count = getattr(exc, "retry_count", None)      # 应用内重试(ask_json 自纠=1)
        try:
            with self.run_lock:
                self.conn.execute(
                    "INSERT INTO llm_failures (id, story_id, run_id, stage, node,"
                    " trace_id, raw_output, error, provider_status_code, retry_count,"
                    " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, story_id or None, current_run()[1] or None,
                     stage, node, trace_id, (raw or "")[:4000], str(exc)[:500],
                     status_code, retry_count, _now()),
                )
                self.conn.commit()
        except Exception:   # noqa: BLE001 — 台账失败不影响主错误传播
            pass


class SqliteFinalizeStore:
    """定稿原子落库(端口 FinalizeStore,ADR-0003 写链路,单事务)。

    持 Deps 弱引用:embed_fn 延迟注入;指令消费经 DirectiveChannel。
    """

    def __init__(self, deps, directives) -> None:
        self._deps = deps
        self._directives = directives

    def _fact_supersede_target(self, story_id: str, branch: str, f: dict,
                               chapter_no: int) -> str | None:
        """求新事实的 prev_version_id(推翻链):
        ① LLM 显式 supersedes(模糊匹配已知事实,取最近一条);
        ② setting 类兜底:接在当前仍有效的最新 setting 链尾之后,
           保证任一时点只有最新场景环境生效(时间线不回漂)。
        """
        conn = self._deps.conn
        sup = (f.get("supersedes") or "").strip()
        if sup:
            row = conn.execute(
                "SELECT id FROM facts WHERE story_id=? AND branch_id=?"
                " AND chapter_established<? AND content LIKE ?"
                " AND status!='rejected' ORDER BY chapter_established DESC LIMIT 1",
                (story_id, branch, chapter_no, f"%{sup[:24]}%"),
            ).fetchone()
            if row:
                return row["id"]
        if f.get("type") == "setting":
            row = conn.execute(
                # 仍有效 = 没有任何后续版本指向它(NOT EXISTS,与 world 回放口径一致)
                "SELECT f.id, f.chapter_established FROM facts f"
                " LEFT JOIN facts g ON g.prev_version_id = f.id"
                " WHERE f.story_id=? AND f.branch_id=? AND f.type='setting'"
                "   AND f.status!='rejected' AND g.id IS NULL"
                " ORDER BY f.chapter_established DESC LIMIT 1",
                (story_id, branch),
            ).fetchone()
            if row:
                return row["id"]
        return None

    def _match_open_thread(self, story_id: str, thread_id: str | None,
                           description: str, *, escalate: bool = False):
        """定位活跃伏笔(ADR-0025):优先评审回传的 thread_id 精确命中;
        id 缺失或失配(模型未按契约/清单已被同章其他变更改写)才降级为
        描述 LIKE 匹配,并在 retrieval_audit 留痕(caller='thread_fallback')
        便于追查契约失守。escalate=True 附加"未升格且为 short"条件(仅一次)。"""
        conn = self._deps.conn
        extra = (" AND escalated_chapter IS NULL"
                 " AND COALESCE(tier,'short')='short'") if escalate else ""
        if thread_id:
            row = conn.execute(
                f"SELECT id FROM plot_threads WHERE id=? AND story_id=?"
                f" AND status='open'{extra}",
                (thread_id, story_id),
            ).fetchone()
            if row:
                return row
        row = conn.execute(
            f"SELECT id FROM plot_threads WHERE story_id=? AND status='open'{extra}"
            " AND description LIKE ? ORDER BY planted_chapter DESC LIMIT 1",
            (story_id, f"%{description[:12]}%"),
        ).fetchone()
        if row:
            conn.execute(
                "INSERT INTO retrieval_audit (id, story_id, caller, query,"
                " returned_count, latency_ms, created_at) VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, story_id, "thread_fallback",
                 f"thread_id={thread_id or '(缺失)'} desc={description[:40]}",
                 1, 0, _now()),
            )
        return row

    def commit_finalize(self, state: dict) -> str:
        """定稿 DB 事务:章节 + 事实/认知/可见性 + 伏笔 + 摘要,一次提交。
        失败整体回滚,世界状态零污染。"""
        conn = self._deps.conn
        embed_fn = self._deps.embed_fn
        story_id = state["story_id"]
        branch = state["branch_id"]
        chapter_no = state["chapter_no"]
        changes = state.get("fact_changes", {})
        chapter_id = uuid.uuid4().hex

        # 事务外预计算 embedding(facts 去重后的内容 + 章摘要 + 新实体条目);
        # 失败降级为 None——不阻塞定稿,仅损失向量检索能力
        emb_contents: list[str] = [f.get("content", "").strip()
                                   for f in changes.get("facts", [])]
        emb_contents.append(state.get("chapter_summary", ""))
        entity_changes = state.get("entity_changes") or {}
        ent_emb_start = len(emb_contents)
        emb_contents.extend(
            f"{e['name']}:{e.get('content', '')}" for e in entity_changes.get("new_entities", []))
        emb_vecs: list[bytes | None] = [None] * len(emb_contents)
        if embed_fn is not None:
            try:
                texts = [t for t in emb_contents if t]
                if texts:
                    vectors = embed_fn(texts) or []
                    vi = 0
                    for i, t in enumerate(emb_contents):
                        if t and vi < len(vectors) and vectors[vi]:
                            emb_vecs[i] = encode_embedding(vectors[vi])
                            vi += 1
            except Exception:
                emb_vecs = [None] * len(emb_contents)

        try:
            self._deps.run_lock.acquire()   # 显式事务:跨越整个 BEGIN..COMMIT 的临界区
            conn.execute("BEGIN")
            # 1) 章节(active);revamp 轮(ADR-0031 P1):旧版归档,新版
            #    INSERT(version_no+1、prev_version_id 挂链)——R2 章节版本化
            #    天然承载"重构历史章",旧版可回溯
            if state.get("revamp_pending"):
                old = conn.execute(
                    "SELECT id, version_no FROM chapters"
                    " WHERE story_id=? AND chapter_no=? AND status='active'"
                    " ORDER BY version_no DESC LIMIT 1",
                    (story_id, chapter_no)).fetchone()
                if old is None:
                    raise ValueError(
                        f"revamp 目标章不存在:story={story_id} chapter={chapter_no}")
                conn.execute(
                    "UPDATE chapters SET status='archived', updated_at=? WHERE id=?",
                    (_now(), old["id"]))
                chapter_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO chapters (id, story_id, chapter_no, version_no,"
                    " prev_version_id, title, content, status, branch_id, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (chapter_id, story_id, chapter_no, old["version_no"] + 1, old["id"],
                     f"第{chapter_no}章", state.get("draft", ""), "active", branch,
                     _now(), _now()))
            else:
                conn.execute(
                    "INSERT INTO chapters (id, story_id, chapter_no, version_no, title, content,"
                    " status, branch_id, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (chapter_id, story_id, chapter_no, 1,
                     f"第{chapter_no}章", state.get("draft", ""), "active", branch, _now(), _now()),
                )
            # 2) facts + visibility(精确去重:同分支同内容已存在则跳过)
            fact_ids: list[str] = []
            for i, f in enumerate(changes.get("facts", [])):
                content = (f.get("content") or "").strip()
                if not content:
                    continue
                dup = conn.execute(
                    "SELECT id FROM facts WHERE story_id=? AND branch_id=?"
                    " AND content=? AND status!='rejected' LIMIT 1",
                    (story_id, branch, content),
                ).fetchone()
                if dup:
                    continue     # 语义去重由抽取层负责;此处拦精确重复
                fid = uuid.uuid4().hex
                fact_ids.append(fid)
                conn.execute(
                    "INSERT INTO facts (id, story_id, type, content, chapter_established,"
                    " branch_id, prev_version_id, confidence, status, embedding, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, story_id, f.get("type", "event"), content,
                     chapter_no, branch,
                     self._fact_supersede_target(story_id, branch, f, chapter_no),
                     f.get("confidence", "high"),
                     "pending_review" if f.get("confidence") == "low" else "confirmed",
                     emb_vecs[i] if i < len(emb_vecs) else None, _now()),
                )
                for cid in f.get("visible_ids", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO fact_visibility"
                        " (fact_id, character_id, knowledge_level, detail, learned_chapter, branch_id)"
                        " VALUES (?,?,?,?,?,?)",
                        (fid, cid, "known_full", None, chapter_no, branch),
                    )
            # 3) beliefs
            for b in changes.get("beliefs", []):
                if not b.get("character_id"):
                    continue
                conn.execute(
                    "INSERT INTO beliefs (id, story_id, character_id, content, source_fact_id,"
                    " status, established_chapter, dispelled_chapter, branch_id,"
                    " prev_version_id, embedding, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, story_id, b["character_id"], b.get("content", ""),
                     None, "believed", chapter_no, None, branch, None, None, _now()),
                )
            # 4) 角色卡更新
            for u in state.get("character_changes", []):
                if not u.get("character_id"):
                    continue
                conn.execute(
                    "UPDATE characters SET profile = COALESCE(profile,'') || char(10) || ?,"
                    " updated_at=? WHERE id=?",
                    (u.get("profile_append", ""), _now(), u["character_id"]),
                )
            # 5) 伏笔(人工已确认的 thread_changes;ADR-0020:伏笔评审产出,
            #    plant 必带 tier+basis;容量超限硬校验拒绝——契约已告知模型,
            #    此处兜底防漏)
            for t in state.get("thread_changes", []):
                action = t.get("action", "plant")
                if action == "plant":
                    tier = t.get("tier") if t.get("tier") in ("short", "long") else "short"
                    cap = THREAD_LONG_CAP if tier == "long" else THREAD_SHORT_CAP
                    open_n = conn.execute(
                        "SELECT COUNT(*) c FROM plot_threads"
                        " WHERE story_id=? AND status='open' AND COALESCE(tier,'short')=?",
                        (story_id, tier)).fetchone()["c"]
                    if open_n >= cap:
                        continue
                    conn.execute(
                        "INSERT INTO plot_threads (id, story_id, description, planted_chapter,"
                        " resolved_chapter, status, tier, basis, branch_id,"
                        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex, story_id, t.get("description", ""), chapter_no,
                         None, "open", tier, t.get("basis", ""), branch, _now(), _now()),
                    )
                else:
                    # advance/resolve/drop:ADR-0025 优先按评审回传的 thread_id
                    # 精确命中,失配才降级描述 LIKE(留痕 retrieval_audit)
                    row = self._match_open_thread(story_id, t.get("thread_id"),
                                                  t.get("description", ""))
                    if row:
                        status = {"advance": "open", "resolve": "resolved", "drop": "dropped"}[action]
                        conn.execute(
                            "UPDATE plot_threads SET status=?, resolved_chapter=?, updated_at=?"
                            " WHERE id=?",
                            (status, chapter_no if action != "advance" else None, _now(), row["id"]),
                        )
            # 6) 伏笔复核裁决生效(ADR-0020):escalate 升格为账本管理动作,
            #    不进剧情确认链,定稿时直接落库;仅允许一次(已 long/已升格的跳过)
            for r in (state.get("thread_review", {}) or {}).get("reviews", []):
                if r.get("verdict") != "escalate":
                    continue
                row = self._match_open_thread(story_id, r.get("thread_id"),
                                              r.get("description", ""), escalate=True)
                if row:
                    conn.execute(
                        "UPDATE plot_threads SET tier='long', escalated_chapter=?,"
                        " updated_at=? WHERE id=?",
                        (chapter_no, _now(), row["id"]),
                    )
            # 6) 章摘要(检索索引)+ 阶段聚合摘要(分层记忆)
            summary_vec = emb_vecs[-1] if emb_vecs else None
            conn.execute(
                "INSERT INTO chapter_summaries (id, story_id, chapter_no, layer, content,"
                " branch_id, embedding, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, story_id, chapter_no, "chapter",
                 state.get("chapter_summary", ""), branch, summary_vec, _now()),
            )
            if state.get("stage_summary"):
                conn.execute(
                    "INSERT INTO chapter_summaries (id, story_id, chapter_no, layer, content,"
                    " branch_id, embedding, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, story_id, chapter_no, "stage",
                     state["stage_summary"], branch, None, _now()),
                )
            # 7) 实体族(ADR-0015):新实体/别名/链接/合并提案 + 阶段末条目滚动
            for i, e in enumerate(entity_changes.get("new_entities", [])):
                conn.execute(
                    "INSERT INTO entities (id, story_id, type, name, content, embedding,"
                    " chapter_no, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (e["id"], story_id, e.get("type", "concept"), e["name"],
                     e.get("content", ""),
                     emb_vecs[ent_emb_start + i] if ent_emb_start + i < len(emb_vecs) else None,
                     chapter_no, "active", _now(), _now()),
                )
            for a in entity_changes.get("aliases", []):
                conn.execute(
                    "INSERT OR REPLACE INTO entity_aliases"
                    " (alias, story_id, entity_id, created_at) VALUES (?,?,?,?)",
                    (a["alias"], story_id, a["entity_id"], _now()),
                )
            for l in entity_changes.get("links", []):
                conn.execute(
                    "INSERT INTO entity_links (id, story_id, from_entity, to_entity,"
                    " relation, chapter_no) VALUES (?,?,?,?,?,?)",
                    (l["id"], story_id, l["from_entity"], l["to_entity"],
                     l.get("relation", ""), l.get("chapter_no")),
                )
            for p in entity_changes.get("proposals", []):
                conn.execute(
                    "INSERT INTO entity_merge_proposals (id, story_id, candidate_name,"
                    " candidate_entity_id, target_entity_id, similarity, evidence,"
                    " status, chapter_no, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, story_id, p["candidate_name"],
                     p.get("candidate_entity_id"), p.get("target_entity_id"),
                     p.get("similarity"), p.get("evidence", ""),
                     "pending", p.get("chapter_no"), _now()),
                )
            for u in state.get("entity_content_updates") or []:
                if u.get("entity_id") and u.get("content"):
                    conn.execute(
                        "UPDATE entities SET content=?, updated_at=? WHERE id=? AND story_id=?",
                        (u["content"], _now(), u["entity_id"], story_id),
                    )
            # 8) 用户指令消费(评审 6.12 / ADR-0024):build_context 只 peek 只读,
            #    消费标记延迟到本定稿事务内——事务回滚则指令仍 pending,
            #    本章重跑依旧生效,不再"取走即标、失败即丢"
            self._directives.mark_directives_consumed(
                (state.get("context_bundle") or {}).get("user_directive_ids") or [])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._deps.run_lock.release()
        return chapter_id
