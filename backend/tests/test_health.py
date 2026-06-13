"""M0 冒烟测试：验证应用能启动、健康检查返回统一响应包、数据库文件生成。"""

from fastapi.testclient import TestClient

from app.core.config import config
from app.main import app


def test_health_returns_unified_envelope():
    """健康检查应返回 { code:0, message:'ok', data:{...} } 统一包装。"""
    # TestClient 的 with 语法会触发 lifespan（即执行数据库初始化）
    with TestClient(app) as client:
        resp = client.get("/api/v1/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "ok"
    assert body["data"]["status"] == "up"
    assert body["data"]["app"] == "StockNova"


def test_databases_created_on_startup():
    """应用启动后，SQLite 与 DuckDB 数据文件应已生成。"""
    with TestClient(app):
        assert config.sqlite_path.exists(), "SQLite 业务库文件未创建"
        assert config.duckdb_path.exists(), "DuckDB 行情库文件未创建"
