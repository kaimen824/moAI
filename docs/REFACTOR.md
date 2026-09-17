# 后端解耦重构方案(ADR-0030 配套方案稿)

状态:施工中(2026-09-17 拍板"先重构",功能线——数据飞轮 P0、分卷大纲
②③ 方案——排队等待)。本文档随各阶段完成更新进度标记。

## 一、诊断:六笔解耦债(2026-09-16 实测口径)

后端 6,669 行 Python。已有 importlinter 分层契约(api→graph→memory→db→core),
但 `app/api/` 是空包,813 行的 main.py 游离在契约之外——契约对最大的债零约束。

| # | 债 | 实测证据 |
|---|---|---|
| 1 | Deps 上帝对象 | `graph/runtime.py` 的 `Deps` 一类混 7 职责(事件总线/SQL 直访/记忆拼装/风格策略/定稿大事务/运行状态机/失败台账);build.py 36 处 `deps.*` |
| 2 | SQL 散落,仓储名存实亡 | `conn.execute/commit` 直写:runtime.py 57 处、main.py 39 处、repository.py 29 处;调用方经 `repo.conn` 穿透 |
| 3 | main.py 巨石 | 路由+引擎生命周期+SSE worker+预算闸门+认证+admin 单文件,业务逻辑嵌在端点函数体 |
| 4 | 编排与用例混杂 | `graph/build.py` 490 行 = 图结构 + 路由谓词 + 节点业务(confirm 事务落库等) |
| 5 | 横切关注点手工闭包接线 | usage/trace sink、ContextVar、SSE emit 在 build_engine 里闭包互指 |
| 6 | 测试注入面窄 | 节点级单测须手拼 SimpleNamespace 假 deps(test_supervisor_feedback 实证) |

## 二、目标架构

依赖方向单向化 + 端口隔离 + 显式构造注入。**不拆进程、不引框架**。

```
app/
├── api/                # 表现层(薄):DTO + 端点,只调 application
│   ├── routes/         #   stories / auth / admin / facts / entities / config
│   ├── sse.py          #   SSE 流式响应 + worker 线程管理
│   ├── deps.py         #   FastAPI Depends 装配(引擎单例/当前用户)
│   └── main.py         #   create_app() 工厂
├── application/        # 用例层:业务动作服务化,依赖显式声明
│   ├── ports.py        #   端口:EventBus/RunStateStore/DirectiveChannel/
│   │                   #   RecapBuilder/StylePolicy/CharacterRegistry/
│   │                   #   FailureLedger/FinalizeStore/StopController
│   └── run_service.py  #   预算闸门(用量聚合经 ObservabilityQueries)
├── graph/              # 编排层:只留 LangGraph
│   ├── wiring.py       #   纯图结构
│   ├── routes.py       #   路由谓词(route_* 纯函数)
│   └── nodes.py        #   节点薄壳(图内业务函数)
├── infrastructure/     # 适配器(Deps 协作器 + UI 读模型的 Sqlite 实现归此语义)
│   ├── runtime_components.py  # 九个 Deps 协作组件(阶段2)
│   └── queries.py             # StoryQueries/ReviewQueues/UserStore/
│                               # ObservabilityQueries(UI 读模型,阶段3)
├── memory/ db/ core/   # 现有模块,保持
└── main.py             # 兼容转发 → app.api.main(保 uvicorn app.main:app)
```

依赖方向:`api → application ← infrastructure`;graph 编排挂 application 之上;
core 垫底。Deps 拆为 6 个显式协作者(EventBus/RunStateStore/RecapBuilder/
StylePolicy/FailureLedger/FinalizeUoW),全项目仅装配点(composition root)
知道具体实现。

## 三、明确不做(解耦深度裁剪)

- **多进程/微服务**:进程内互斥与单事务是有意设计(ADR-0023/0027);
  水平扩展前置 PostgreSQL+Redis 迁移,属独立项目。
- **DI 框架**:装配点唯一(build_engine),手工构造注入足够。
- **ORM**:586 行手写 DDL + 精确 SQL 是资产;仓储收敛即可。
- **domain 独立目录**:纯规则(章号推进/评审合并/阶段边界)量小,
  三次法则——留在 application,第三次重复时再抽。

## 四、五阶段迁移(绞杀者模式,146+ 测试做安全网)

每阶段:全量测试绿 → 当场 commit → 可独立停止。

| 阶段 | 内容 | 量级 | 状态 |
|---|---|---|---|
| 0 契约硬化 | main.py 迁入 app/api/(旧路径兼容转发),app.api 层真实生效 | 半天 | ✅ 完成 |
| 1 main.py 拆分 | routes/ 域模块 + sse.py + api/deps.py;预算闸门下沉 RunService | 1-2 天 | ✅ 完成 |
| 2 Deps 拆解 | ports 先立,Deps 变兼容门面,组件落 app/infrastructure | 2-3 天 | ✅ 完成 |
| 3 SQL 收敛 | api 层内联 SQL → infrastructure/queries.py 四域读模型;定稿 UoW 由阶段2 FinalizeStore 达成(端口+单事务,落位 infrastructure 而非 application——纯 DB 事务无业务规则,不再建转发层) | 2-3 天 | ✅ 完成 |
| 4 build.py 三分 | wiring / routes / nodes 分离 | 1-2 天 | 待开工 |

锚点测试:阶段1 test_api/test_run_state;阶段2 全量+test_supervisor_feedback;
阶段3 test_data_integrity/test_db;阶段4 test_graph_e2e。

## 五、完成口径

- 后端行为零变化(146+ 测试全程绿,无删除无放松)。
- importlinter 契约覆盖全部后端代码,api 层不再是空包。
- 每阶段独立 commit,可 revert 到任一稳定中间态。
