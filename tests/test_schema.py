"""数据库层测试：$jsonSchema 校验器、唯一索引、索引是否建全。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError, WriteError

from app.db import COLLECTIONS, INDEXES
from tests.helpers import login, register


def utcnow():
    return datetime.now(timezone.utc)


class TestValidators:
    def test_all_collections_have_validator(self, db):
        info = db.command("listCollections")["cursor"]["firstBatch"]
        by_name = {item["name"]: item for item in info}
        for name in COLLECTIONS:
            assert name in by_name, f"缺少集合 {name}"
            assert by_name[name]["options"].get("validator"), f"{name} 没有设置校验器"

    def test_survey_status_enum(self, db):
        with pytest.raises(WriteError):
            db.surveys.insert_one(
                {
                    "slug": "bad-status",
                    "owner_id": ObjectId(),
                    "title": "非法状态",
                    "allow_anonymous": False,
                    "allow_multiple": True,
                    "status": "not-a-status",
                    "created_at": utcnow(),
                    "question_count": 0,
                }
            )

    def test_survey_missing_required_field(self, db):
        with pytest.raises(WriteError):
            db.surveys.insert_one({"title": "只有标题"})

    def test_question_type_enum(self, db):
        with pytest.raises(WriteError):
            db.questions.insert_one(
                {
                    "survey_id": ObjectId(),
                    "order": 0,
                    "type": "rating",
                    "title": "非法题型",
                    "required": False,
                    "logic": {"default_next": "next", "jumps": []},
                }
            )

    def test_response_requires_answers(self, db):
        with pytest.raises(WriteError):
            db.responses.insert_one(
                {
                    "survey_id": ObjectId(),
                    "anonymous": True,
                    "submitted_at": utcnow(),
                    "path": [],
                }
            )


class TestIndexes:
    def test_indexes_exist(self, db):
        for name, specs in INDEXES.items():
            names = {idx["name"] for idx in db[name].list_indexes()}
            for _keys, options in specs:
                assert options["name"] in names, f"{name} 缺少索引 {options['name']}"

    def test_username_unique(self, client, db):
        register(client, "alice")
        with pytest.raises(DuplicateKeyError):
            db.users.insert_one(
                {"username": "alice", "password_hash": "x" * 40, "created_at": utcnow()}
            )

    def test_slug_unique(self, client, db):
        register(client, "alice")
        login(client, "alice")
        client.post("/surveys/new", data={"title": "第一份"}, follow_redirects=True)
        survey = db.surveys.find_one({"title": "第一份"})
        duplicate = {key: value for key, value in survey.items() if key != "_id"}
        with pytest.raises(DuplicateKeyError):
            db.surveys.insert_one(duplicate)

    def test_answer_question_id_index(self, db):
        names = {idx["name"] for idx in db.responses.list_indexes()}
        assert "ix_survey_answer_qid" in names
