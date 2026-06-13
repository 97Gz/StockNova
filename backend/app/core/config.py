"""应用配置。

设计说明：
- 这里只放"启动前就必须确定"的少量配置（端口、数据目录等）。
- 业务配置（AI 接入、数据源选择、定时任务时间等）全部存在 SQLite 的
  settings 表里，由设置中心页面修改 —— 这是访谈确认的"零配置文件"原则。
- BaseSettings 会自动读取环境变量（如 STOCKNOVA_PORT），没有则用默认值。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """启动配置：环境变量前缀 STOCKNOVA_，例如 STOCKNOVA_PORT=9000。"""

    model_config = SettingsConfigDict(env_prefix="STOCKNOVA_")

    # 服务监听地址：默认只绑定本机回环地址，保证数据不暴露到局域网
    host: str = "127.0.0.1"
    port: int = 8000

    # 数据目录：SQLite / DuckDB 文件的存放位置。
    # 开发期放在仓库的 backend/data/ 下；桌面端打包后会改为 %APPDATA%/StockNova，
    # Docker 部署用 STOCKNOVA_DATA_DIR=/data 挂载卷持久化。
    data_dir: Path = Path(__file__).resolve().parents[2] / "data"

    # 前端静态目录：生产部署（Docker/桌面端）时由 FastAPI 托管已构建的前端，
    # 实现「单端口、同源」访问（无需另起 nginx）。为空则按仓库默认 frontend/dist。
    # 开发期不设置此项，前端走 vite dev server（5173）+ 代理。
    static_dir: Path | None = None

    @property
    def sqlite_path(self) -> Path:
        """业务库文件路径（自选股/持仓/策略配置等）。"""
        return self.data_dir / "stocknova.db"

    @property
    def duckdb_path(self) -> Path:
        """行情分析库文件路径（日线/快照/板块等大体量数据）。"""
        return self.data_dir / "market.duckdb"

    @property
    def resolved_static_dir(self) -> Path:
        """前端构建产物目录：优先用显式配置，否则回退仓库内 frontend/dist。"""
        if self.static_dir is not None:
            return self.static_dir
        return Path(__file__).resolve().parents[3] / "frontend" / "dist"


# 全局唯一的配置实例：其他模块直接 `from app.core.config import config` 使用
config = AppConfig()
