"""题库版本链：建版、改题生成新版本、题型不可变、恢复、唯一索引。"""

from __future__ import annotations

import pytest
from pymongo.errors import DuplicateKeyError

from tests.helpers import (
    add_single, create_survey, login, publish, register,
)


def _setup(client, db, title="你的年龄是？"):
    register(client)
    login(client)
    survey = create_survey(client, db, title="人口统计")
    question = add_single(
        client, db, survey, title=title, options="18 岁以下\n18-25 岁\n26 岁以上"
    )
    return survey, question


def _question_form(title, options="18 岁以下\n18-25 岁\n26 岁以上", **extra):
    form = {"type": "single", "title": title, "options": options}
    form.update(extra)
    return form


class TestAutoRegister:
    def test_creating_question_registers_v1(self, client, db):
        _survey, question = _setup(client, db)

        assert question["lineage_id"].startswith("Q")
        assert question["version"] == 1
        assert question["frozen"] is False

        version = db.question_bank.find_one({"_id": question["bank_version_id"]})
        assert version is not None
        assert version["lineage_id"] == question["lineage_id"]
        assert version["version"] == 1
        assert version["title"] == "你的年龄是？"
        assert version["root_id"] == version["_id"]
        assert version["parent_id"] is None
        assert version["visibility"] == "private"

    def test_bank_page_lists_question(self, client, db):
        _setup(client, db)
        page = client.get("/bank")
        assert page.status_code == 200
        assert "你的年龄是？" in page.get_data(as_text=True)


class TestNewVersion:
    def test_editing_draft_creates_new_version_and_keeps_old(self, client, db):
        survey, question = _setup(client, db)
        lineage = question["lineage_id"]

        client.post(
            f"/surveys/{survey['_id']}/questions/{question['_id']}",
            data=_question_form("你的年龄段是？"),
            follow_redirects=True,
        )

        chain = list(db.question_bank.find({"lineage_id": lineage}).sort("version", 1))
        assert [item["version"] for item in chain] == [1, 2]
        assert chain[0]["title"] == "你的年龄是？"  # 旧版本不被覆盖
        assert chain[1]["title"] == "你的年龄段是？"
        assert chain[1]["parent_id"] == chain[0]["_id"]
        assert chain[1]["root_id"] == chain[0]["_id"]
        assert "修改" in (chain[1]["change_note"] or "")

        refreshed = db.questions.find_one({"_id": question["_id"]})
        assert refreshed["version"] == 2
        assert refreshed["bank_version_id"] == chain[1]["_id"]

    def test_saving_without_content_change_does_not_fork(self, client, db):
        survey, question = _setup(client, db)
        client.post(
            f"/surveys/{survey['_id']}/questions/{question['_id']}",
            data=_question_form("你的年龄是？", options="18 岁以下\n18-25 岁\n26 岁以上"),
            follow_redirects=True,
        )
        assert db.question_bank.count_documents({"lineage_id": question["lineage_id"]}) == 1

    def test_type_cannot_change_between_versions(self, client, db):
        _survey, question = _setup(client, db)
        lineage = question["lineage_id"]
        page = client.post(
            f"/bank/questions/{lineage}/versions",
            data={"type": "text", "title": "换个题型", "min_length": "1"},
            follow_redirects=True,
        )
        assert page.status_code == 200
        assert db.question_bank.count_documents({"lineage_id": lineage}) == 1

    def test_new_version_from_bank_page(self, client, db):
        _survey, question = _setup(client, db)
        lineage = question["lineage_id"]
        client.post(
            f"/bank/questions/{lineage}/versions",
            data=_question_form("你今年多大？", change_note="缩短题干"),
            follow_redirects=True,
        )
        chain = list(db.question_bank.find({"lineage_id": lineage}).sort("version", 1))
        assert len(chain) == 2
        assert chain[1]["change_note"] == "缩短题干"
        # 已经进入旧问卷的那份快照不受影响
        survey_question = db.questions.find_one({"_id": question["_id"]})
        assert survey_question["title"] == "你的年龄是？"
        assert survey_question["version"] == 1


class TestRestore:
    def test_restore_creates_new_version_with_old_content(self, client, db):
        _survey, question = _setup(client, db)
        lineage = question["lineage_id"]
        client.post(
            f"/bank/questions/{lineage}/versions",
            data=_question_form("改坏了的新题干"),
            follow_redirects=True,
        )
        v1 = db.question_bank.find_one({"lineage_id": lineage, "version": 1})

        client.post(
            f"/bank/questions/{lineage}/versions/1/restore",
            data={"change_note": "还是原来的好"},
            follow_redirects=True,
        )

        chain = list(db.question_bank.find({"lineage_id": lineage}).sort("version", 1))
        assert [item["version"] for item in chain] == [1, 2, 3]
        assert chain[2]["title"] == "你的年龄是？"
        assert chain[2]["restored_from"] == v1["_id"]
        assert chain[2]["parent_id"] == chain[1]["_id"]
        assert chain[1]["title"] == "改坏了的新题干"  # 历史不被改写

    def test_restore_unknown_version_404(self, client, db):
        _survey, question = _setup(client, db)
        response = client.post(f"/bank/questions/{question['lineage_id']}/versions/99/restore")
        assert response.status_code == 404


class TestImmutability:
    def test_lineage_version_unique(self, client, db):
        _survey, question = _setup(client, db)
        version = db.question_bank.find_one({"lineage_id": question["lineage_id"]})
        duplicate = dict(version)
        duplicate.pop("_id")
        with pytest.raises(DuplicateKeyError):
            db.question_bank.insert_one(duplicate)

    def test_published_survey_question_is_frozen(self, client, db):
        survey, question = _setup(client, db)
        publish(client, survey)
        refreshed = db.questions.find_one({"_id": question["_id"]})
        assert refreshed["frozen"] is True
