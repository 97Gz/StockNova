"""pytest 全局配置：测试隔离。

必须在导入任何 app 模块之前设置数据目录环境变量——
否则测试会和正在运行的开发服务器争抢同一个 DuckDB 文件
（DuckDB 单写者模型，文件被占用时第二个进程直接报错）。

conftest.py 由 pytest 最先加载，早于所有测试模块的 import，
所以在这里设置环境变量是安全的。
"""

import os
import tempfile

# 每次测试会话一个独立的临时数据目录（SQLite/DuckDB 都建在这里）
os.environ["STOCKNOVA_DATA_DIR"] = tempfile.mkdtemp(prefix="stocknova-test-")
