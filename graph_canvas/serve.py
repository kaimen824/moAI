"""图结构画布开发服务器:静态前端 + /api/graph + 代码变更监视自动重导出。

用法(用带依赖的环境跑,如本项目的 conda novel-agent):
    D:/Anaconda/envs/novel-agent/python.exe serve.py [port]

    端口默认 8765,打开 http://127.0.0.1:8765

行为:
  - 启动先导出一次 graph.json;
  - 之后每秒轮询 app/graph/**/*.py 的 mtime,变了就在子进程重跑
    extract_graph.py(子进程 = 干净的模块状态,不怕 build.py 改动);
  - 导出失败(改坏代码)时 graph.json 保留上一版,error 字段带回前端标红;
  - 结构无变化时 version 哈希不变,前端不重绘(不闪不丢视口)。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
OUT = PUBLIC / "graph.json"
WATCH_DIRS = [PROJECT_DIR := ROOT.parent / "app" / "graph"]   # 想扩大监视范围就加目录

# 重放数据源:生产故事库(只读); GRAPH_DB 可换库
sys.path.insert(0, str(ROOT))
from replay import ReplayIndex          # noqa: E402

STORY_DB = Path(os.environ.get(
    "GRAPH_DB", ROOT.parent / "data" / "novel_agent.db"))
replay_index = ReplayIndex(STORY_DB)


def watch_fingerprint() -> dict[str, float]:
    files = [p for d in WATCH_DIRS for p in sorted(d.rglob("*.py"))]
    return {str(p): p.stat().st_mtime for p in files}


def dump() -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, str(ROOT / "extract_graph.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(PROJECT_DIR),
    )
    ok = r.returncode == 0
    return ok, (r.stdout + r.stderr).strip()


def watcher(interval: float = 1.0) -> None:
    last = watch_fingerprint()
    while True:
        time.sleep(interval)
        cur = watch_fingerprint()
        if cur != last:
            last = cur
            ok, log = dump()
            print(f"[watch] 变更已重导出 {'OK' if ok else '失败'}\n{log}",
                  file=sys.stderr)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/graph":
            fallback = '{"version": null, "error": "graph.json 未生成"}'
            body = OUT.read_bytes() if OUT.exists() else fallback.encode("utf-8")
        elif path == "/api/threads":
            replay_index.refresh_if_stale()
            body = json.dumps(replay_index.threads, ensure_ascii=False).encode("utf-8")
        elif path.startswith("/api/runs/"):
            tid = path[len("/api/runs/"):]
            replay_index.refresh_if_stale()
            run = replay_index.runs.get(tid)
            if run is None:
                self._send_json({"error": f"thread 不存在: {tid}"}, 404)
                return
            body = json.dumps(run, ensure_ascii=False).encode("utf-8")
        elif path.startswith("/api/state/"):
            _, _, _, tid, ckid = path.split("/")
            try:
                replay_index.refresh_if_stale()
                detail = replay_index.state_detail(tid, ckid)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, 500)
                return
            if detail is None:
                self._send_json({"error": "checkpoint 不存在"}, 404)
                return
            body = json.dumps(detail, ensure_ascii=False).encode("utf-8")
        else:
            super().do_GET()
            return
        self._send_bytes(body, "application/json; charset=utf-8")

    def _send_json(self, obj, code: int = 200) -> None:
        self._send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8", code)

    def _send_bytes(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # index.html 不缓存:重新构建后浏览器立刻拿到新 hash 的资源
        if self.path.endswith(".html") or self.path.rstrip("/").endswith((":8765", "")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):   # 安静模式:只留 watcher 的输出
        pass


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ok, log = dump()
    print(log or "首次导出未产生输出", file=sys.stderr if not ok else sys.stdout)
    threading.Thread(target=watcher, daemon=True).start()

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"graph canvas → http://127.0.0.1:{port}   (Ctrl+C 退出)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
