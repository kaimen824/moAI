"""兼容转发(ADR-0030 阶段0):实现已迁 app/api/main.py。

经 sys.modules 别名保证 `import app.main` 与 `app.api.main` 是同一模块——
测试对 _engine/_active 等模块全局的重置语义不变,uvicorn app.main:app
启动命令保持有效。
"""

import sys

from app.api import main as _impl

sys.modules[__name__] = _impl
