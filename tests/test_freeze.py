"""冻结与复制：已发布问卷的题目不可改，旧问卷内容不随题库变化。"""

from __future__ import annotations

from tests.helpers import add_single, create_survey, login, publish, register


def _form(title, options="甲\n乙\n丙"):
    return {"type": "single", "title": title, "options": options}


def _setup(client, db):
    register(client)
    login(client)
    survey = create_survey(client, db, title="年度调查")
    first = add_single(client, db, survey, title="你的年龄是？", options="18 以下\n18-25\n26 以上")
    second = add_single(client, db, survey, title="你的性别是？", options="男\n女")
    return survey, first, second


class TestFrozen:
    def test_cannot_edit_question_after_publish(self, client, db):
        survey, question, _second = _setup(client, db)
        publish(client, survey)

        page = client.post(
            f"/surveys/{survey['_id']}/questions/{question['_id']}",
            data=_form("偷偷改掉的题干"),
            follow_redirects=True,
        )
        assert "冻结" in page.get_data(as_text=True)

        refreshed = db.questions.find_one({"_id": question["_id"]})
        assert refreshed["title"] == "你的年龄是？"
        assert db.question_bank.count_documents({"lineage_id": question["lineage_id"]}) == 1

    def test_cannot_add_question_after_publish(self, client, db):
        survey, _first, _second = _setup(client, db)
        publish(client, survey)
        before = db.questions.count_documents({"survey_id": survey["_id"]})

        client.post(
            f"/surveys/{survey['_id']}/questions",
            data=_form("发布后加的题"),
            follow_redirects=True,
        )
        assert db.questions.count_documents({"survey_id": survey["_id"]}) == before

    def test_cannot_delete_or_move_after_publish(self, client, db):
        survey, first, second = _setup(client, db)
        publish(client, survey)

        client.post(
            f"/surveys/{survey['_id']}/questions/{first['_id']}/delete",
            follow_redirects=True,
        )
        assert db.questions.find_one({"_id": first["_id"]}) is not None

        client.post(
            f"/surveys/{survey['_id']}/questions/{second['_id']}/move",
            data={"direction": "up"},
            follow_redirects=True,
        )
        assert db.questions.find_one({"_id": second["_id"]})["order"] == 1

    def test_bank_new_version_does_not_change_published_survey(self, client, db):
        survey, question, _second = _setup(client, db)
        publish(client, survey)
        lineage = question["lineage_id"]

        client.post(
            f"/bank/questions/{lineage}/versions",
            data={"type": "single", "title": "题库里的新版题干", "options": "18 以下\n18-25\n26 以上"},
            follow_redirects=True,
        )

        snapshot = db.questions.find_one({"_id": question["_id"]})
        assert snapshot["title"] == "你的年龄是？"
        assert snapshot["version"] == 1
        assert db.question_bank.count_documents({"lineage_id": lineage}) == 2


class TestDuplicate:
    def test_duplicate_creates_independent_draft(self, client, db):
        survey, first, second = _setup(client, db)
        client.post(
            f"/surveys/{survey['_id']}/questions/{first['_id']}",
            data={
                **_form("你的年龄是？", "18 以下\n18-25\n26 以上"),
                "jump_A": str(second["_id"]),
            },
            follow_redirects=True,
        )
        publish(client, survey)

        client.post(f"/surveys/{survey['_id']}/duplicate", follow_redirects=True)

        copy = db.surveys.find_one({"title": "年度调查（副本）"})
        assert copy is not None
        assert copy["status"] == "draft"
        assert copy["_id"] != survey["_id"]
        assert db.questions.count_documents({"survey_id": copy["_id"]}) == 2

        copied = list(db.questions.find({"survey_id": copy["_id"]}).sort("order", 1))
        assert all(item["frozen"] is False for item in copied)
        assert all(item["lineage_id"] for item in copied)

        # 跳转目标必须指向副本里的题目，而不是旧问卷的题目
        jump = copied[0]["logic"]["jumps"][0]
        assert jump["target"] == str(copied[1]["_id"])
        assert jump["target"] != str(second["_id"])

    def test_duplicate_can_be_edited_while_original_stays(self, client, db):
        survey, first, _second = _setup(client, db)
        publish(client, survey)
        client.post(f"/surveys/{survey['_id']}/duplicate", follow_redirects=True)

        copy = db.surveys.find_one({"title": "年度调查（副本）"})
        copied = db.questions.find_one({"survey_id": copy["_id"]}, sort=[("order", 1)])

        client.post(
            f"/surveys/{copy['_id']}/questions/{copied['_id']}",
            data=_form("今年改成年龄段"),
            follow_redirects=True,
        )

        assert db.questions.find_one({"_id": copied["_id"]})["title"] == "今年改成年龄段"
        # 原问卷原封不动
        original = db.questions.find_one({"_id": first["_id"]})
        assert original["title"] == "你的年龄是？"
        assert original["frozen"] is True
