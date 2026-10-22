import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
TEST_DB = os.environ.get("MONGO_TEST_DB", "survey_system_test")
COLLECTIONS = ("users", "banks", "question_bank", "surveys", "questions", "responses")


@pytest.fixture(scope="session")
def mongo_server():
    """没有可用的 MongoDB 时，测试整体跳过并给出明确原因。"""
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    try:
        client.admin.command("ping")
    except PyMongoError as exc:  # pragma: no cover - 环境问题
        pytest.skip(f"无法连接 MongoDB（{MONGO_URI}）：{exc}")
    finally:
        client.close()
    return True


@pytest.fixture(scope="session")
def app(mongo_server):
    """整个测试会话只建一次库，避免反复建/删集合耗尽 MongoDB 的文件句柄。"""
    from app import create_app
    from app.db import init_db

    application = create_app(
        {
            "MONGO_URI": MONGO_URI,
            "MONGO_DB": TEST_DB,
            "SECRET_KEY": "test-secret",
            "TESTING": True,
            "AUTO_INIT_DB": False,
        }
    )
    db = application.extensions["mongo_db"]
    db.client.drop_database(TEST_DB)
    init_db(db)

    yield application

    db.client.drop_database(TEST_DB)
    db.client.close()


@pytest.fixture(autouse=True)
def clean_db(app):
    """每个用例开始前清空数据，集合与索引保持不变。"""
    db = app.extensions["mongo_db"]
    for name in COLLECTIONS:
        db[name].delete_many({})
    yield


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    return app.extensions["mongo_db"]
