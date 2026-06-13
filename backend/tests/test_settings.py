"""设置服务单元测试：用独立的内存 SQLite，不碰真实数据。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import BizError
from app.models.orm import OrmBase
from app.services import settings_service
from app.services.settings_service import MASKED


@pytest.fixture()
def db() -> Session:
    """每个测试一个全新的内存数据库会话。"""
    engine = create_engine("sqlite:///:memory:")
    OrmBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_default_value_when_unset(db: Session):
    """未保存过的配置返回注册表默认值。"""
    assert settings_service.get_value(db, "data.history_years") == 10
    assert settings_service.get_value(db, "quotes.source") == "tencent"


def test_update_and_read_back(db: Session):
    settings_service.update_values(db, {"data.request_delay_ms": 300})
    assert settings_service.get_value(db, "data.request_delay_ms") == 300


def test_unknown_key_rejected(db: Session):
    """不在注册表里的 key 一律拒绝（防任意写入）。"""
    with pytest.raises(BizError):
        settings_service.update_values(db, {"hacker.key": 1})
    with pytest.raises(BizError):
        settings_service.get_value(db, "no.such.key")


def test_type_validation(db: Session):
    """类型不符拒绝；JSON 数字的 int/float 互转例外。"""
    with pytest.raises(BizError):
        settings_service.update_values(db, {"data.history_years": "十年"})
    # float 整数 → int 配置：允许（前端 JSON 序列化可能产生 10.0）
    settings_service.update_values(db, {"data.history_years": 8.0})
    assert settings_service.get_value(db, "data.history_years") == 8


def test_secret_masking(db: Session):
    """敏感项读取打码；提交打码占位符不覆盖原值。"""
    settings_service.update_values(db, {"ai.api_key": "sk-real-key"})

    items = {i["key"]: i for i in settings_service.get_all(db)}
    assert items["ai.api_key"]["value"] == MASKED  # 已配置 → 显示打码
    assert settings_service.get_value(db, "ai.api_key") == "sk-real-key"  # 内部读取拿真值

    # 前端原样提交打码占位符 → 保持原值
    settings_service.update_values(db, {"ai.api_key": MASKED})
    assert settings_service.get_value(db, "ai.api_key") == "sk-real-key"


def test_unset_secret_shows_empty(db: Session):
    items = {i["key"]: i for i in settings_service.get_all(db)}
    assert items["ai.api_key"]["value"] == ""
