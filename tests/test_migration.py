"""迁移脚本：第一阶段旧数据补齐版本链，且可以重复执行。"""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId

from migrate import migrate as run_migration


def _legacy_dataset(db, status="published"):
    user_id = ObjectId()
    db.users.insert_one(
        {
            "_id": user_id,
            "username": "legacy",
            "password_hash": "x" * 40,
            "created_at": datetime.now(timezone.utc),
        }
    )
    survey_id = ObjectId()
    db.surveys.insert_one(
        {
            "_id": survey_id,
            "slug": "legacy01",
            "owner_id": user_id,
            "title": "去年的问卷",
            "description": None,
            "allow_anonymous": True,
            "allow_multiple": True,
            "status": status,
            "deadline": None,
            "created_at": datetime.now(timezone.utc),
            "published_at": datetime.now(timezone.utc),
            "closed_at": None,
            "question_count": 1,
        }
    )
    question_id = db.questions.insert_one(
        {
            "survey_id": survey_id,
            "order": 0,
            "type": "single",
            "title": "你的年龄是？",
            "options": [
                {"key": "A", "label": "18 以下"},
                {"key": "B", "label": "18-25"},
            ],
            "rules": {},
            "required": False,
            "logic": {"default_next": "next", "jumps": []},
        }
    ).inserted_id
    db.responses.insert_one(
        {
            "survey_id": survey_id,
            "respondent_id": None,
            "anonymous": True,
            "submitted_at": datetime.now(timezone.utc),
            "survey_version": 1,
            "path": [str(question_id)],
            "answers": [{"question_id": str(question_id), "value": "A"}],
        }
    )
    return survey_id, question_id


def test_dry_run_writes_nothing(db):
    _legacy_dataset(db)
    stats = run_migration(db, dry_run=True)
    assert stats["questions"] == 1
    assert db.question_bank.count_documents({}) == 0
    assert "lineage_id" not in db.questions.find_one({})


def test_migrates_questions_and_answers(db):
    _survey_id, question_id = _legacy_dataset(db)

    stats = run_migration(db)
    assert stats["questions"] == 1
    assert stats["versions_created"] == 1
    assert stats["responses"] == 1
    assert stats["answers_backfilled"] == 1

    question = db.questions.find_one({"_id": question_id})
    assert question["lineage_id"].startswith("Q")
    assert question["version"] == 1
    assert question["frozen"] is True
    assert question["bank_version_id"] is not None

    version = db.question_bank.find_one({"_id": question["bank_version_id"]})
    assert version["lineage_id"] == question["lineage_id"]
    assert version["version"] == 1
    assert version["title"] == "你的年龄是？"
    assert version["visibility"] == "private"

    response = db.responses.find_one({})
    answer = response["answers"][0]
    assert answer["lineage_id"] == question["lineage_id"]
    assert answer["version"] == 1


def test_migration_is_idempotent(db):
    _legacy_dataset(db)
    first = run_migration(db)
    second = run_migration(db)

    assert first["questions"] == 1
    assert second["questions"] == 0
    assert second["versions_created"] == 0
    assert db.question_bank.count_documents({}) == 1


def test_draft_survey_questions_not_frozen(db):
    _survey_id, question_id = _legacy_dataset(db, status="draft")
    run_migration(db)
    assert db.questions.find_one({"_id": question_id})["frozen"] is False


def test_migrated_question_usable_in_cross_stats(db, client):
    _survey_id, question_id = _legacy_dataset(db)
    run_migration(db)

    question = db.questions.find_one({"_id": question_id})
    from app import versioning

    stats = versioning.cross_survey_stats(db, question["lineage_id"])
    assert stats["answered"] == 1
    assert {opt["key"]: opt["count"] for opt in stats["options"]}["A"] == 1
