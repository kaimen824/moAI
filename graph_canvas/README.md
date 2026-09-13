# graph_canvas · 生产图结构画布 v2

LangGraph 生产图的三层可视化:**结构(代码即图)× 节点流转(checkpoint 重放)× 状态 diff**。
对标 LangGraph Studio 的回放体验,但直读本项目 SqliteSaver 的历史 checkpoint——
官方 Studio 做不到的历史回放(平台强制接管持久化),这里可以。

## 启动

```bash
# 1) 前端已预构建(public/assets);改了前端源码才需要重新构建:
cd frontend && npm install --registry=https://registry.npmmirror.com && npm run build

# 2) 服务(结构与重放 API + 静态托管),用带依赖的环境:
D:/Anaconda/envs/novel-agent/python.exe serve.py          # http://127.0.0.1:8765
GRAPH_DB=路径/到/别的库 python serve.py                    # 换重放数据源(默认生产库只读)
```

## 交互

- **左栏**:运行线程列表(章数/步数/停住的中断点),点击载入回放
- **画布**:滚轮缩放、拖拽平移、minimap、节点可拖动;点击节点看执行履历(次数、每步写入了哪些状态)
- **时间轴**(底部):拖动/步进按 super-step 回放,`▶` 播放,`◀◉/◉▶` 在中断点与异常之间跳转
- **右侧抽屉**:当前步骤的 state diff(绿=新增,黄=变更,红=删除;含 branch 路由决策)
- 快捷键:`←/→` 步进,`空格` 播放/暂停
- **代码自动更新**:`app/graph/**/*.py` 变更 → 结构重导出 → 画布自动重绘(保留视口)

## 三层数据流

```
结构层  app/graph/build.py ─(子进程 extract_graph.py)→ public/graph.json
        前端轮询 version(结构内容哈希),变了才重布局

流转层  data/novel_agent.db(只读)
        checkpoints(时序/updated_channels) + writes(branch:to:*/__interrupt__/__resume__/__error__)
        → replay.py 重建 super-step 序列:执行了哪些节点、走了哪些边、中断在哪
        → /api/threads /api/runs/:tid

状态层  checkpoint 载荷(msgpack,JsonPlusSerializer 解码)
        → 与父 checkpoint 的 channel_values diff
        → /api/state/:tid/:checkpoint_id
```

节点语义:`branch:to:X` 写入 = 实际走了到 X 的边;`__interrupt__` = HITL 暂停;
`__resume__` = Command(resume) 输入;线程末尾未消化的 `__interrupt__` = 当前停住的中断点。

## 文件

| 文件 | 作用 |
|---|---|
| `serve.py` | HTTP 服务 + 代码监视 + 重放 API |
| `extract_graph.py` | 从 `build_graph()` 导出结构 JSON |
| `replay.py` | checkpoint 重放索引(只读生产库,按 mtime 缓存) |
| `frontend/` | React 18 + @xyflow/react + dagre(npm build → ../public) |
| `langgraph.json` + `app/graph/studio.py` | LangGraph Studio 直连入口(验证用,跑副本库) |

## LangGraph Studio(官方,验证交互用)

```bash
D:/Anaconda/envs/novel-agent/Scripts/langgraph.exe dev --no-browser --no-reload --port 2027
# 打开 https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2027
```

限制:langgraph dev 拒绝自定义 checkpointer(平台接管持久化),**历史 checkpoint 不可见**,
只能对 Studio 内新开的 run 做 time-travel/interrupt 表单;数据写 `data/studio_replay.db` 副本。
