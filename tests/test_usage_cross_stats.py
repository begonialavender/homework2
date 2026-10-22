"""使用情况与跨问卷统计：同一道题在多个问卷里的回答合并统计。"""

from __future__ import annotations

from tests.helpers import (
    add_number, add_single, create_survey, login, publish, register, submit,
)
from app import versioning


def _publish_and_submit(client, db, survey, question_id, value):
    publish(client, survey)
    refreshed = db.surveys.find_one({"_id": survey["_id"]})
    response = submit(client, refreshed["slug"], {str(question_id): value})
    assert response.status_code == 200, response.get_data(as_text=True)
    return db.responses.find_one({"survey_id": survey["_id"]})


class TestResponseProvenance:
    def test_answer_records_lineage_and_version(self, client, db):
        register(client)
        login(client)
        survey = create_survey(client, db, title="问卷一")
        question = add_single(client, db, survey, title="你的年龄是？")
        response = _publish_and_submit(client, db, survey, question["_id"], "B")

        answer = response["answers"][0]
        assert answer["lineage_id"] == question["lineage_id"]
        assert answer["version"] == 1


class TestUsage:
    def test_lists_every_survey_using_the_question(self, client, db):
        register(client)
        login(client)
        first = create_survey(client, db, title="问卷一")
        question = add_single(client, db, first, title="你的年龄是？")
        version = db.question_bank.find_one({"lineage_id": question["lineage_id"]})

        second = create_survey(client, db, title="问卷二")
        client.post(
            f"/surveys/{second['_id']}/questions/from-bank",
            data={"version_id": str(version["_id"])},
            follow_redirects=True,
        )

        rows = versioning.usage(db, question["lineage_id"])
        titles = {row["survey"]["title"] for row in rows}
        assert titles == {"问卷一", "问卷二"}

        page = client.get(f"/bank/questions/{question['lineage_id']}/usage")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "问卷一" in html and "问卷二" in html


class TestCrossSurveyStats:
    def test_merges_answers_from_multiple_surveys(self, client, db):
        register(client)
        login(client)

        first = create_survey(client, db, title="问卷一")
        question = add_single(client, db, first, title="你的年龄是？", options="18 以下\n18-25\n26 以上")
        version = db.question_bank.find_one({"lineage_id": question["lineage_id"]})
        _publish_and_submit(client, db, first, question["_id"], "A")

        second = create_survey(client, db, title="问卷二")
        client.post(
            f"/surveys/{second['_id']}/questions/from-bank",
            data={"version_id": str(version["_id"])},
            follow_redirects=True,
        )
        second_question = db.questions.find_one({"survey_id": second["_id"]})
        _publish_and_submit(client, db, second, second_question["_id"], "B")

        stats = versioning.cross_survey_stats(db, question["lineage_id"])
        assert stats["answered"] == 2
        assert stats["survey_count"] == 2
        counts = {opt["key"]: opt["count"] for opt in stats["options"]}
        assert counts["A"] == 1
        assert counts["B"] == 1
        assert counts["C"] == 0

        page = client.get(f"/stats/questions/{question['lineage_id']}")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "跨问卷统计" in html
        assert "问卷一" in html and "问卷二" in html

    def test_stats_merge_different_versions(self, client, db):
        register(client)
        login(client)
        first = create_survey(client, db, title="问卷一")
        question = add_single(client, db, first, title="你的年龄是？", options="18 以下\n18-25\n26 以上")
        lineage = question["lineage_id"]
        version_v1 = db.question_bank.find_one({"lineage_id": lineage})
        _publish_and_submit(client, db, first, question["_id"], "A")

        # 题目在题库里升级成 v2，新问卷用 v2
        client.post(
            f"/bank/questions/{lineage}/versions",
            data={"type": "single", "title": "你的年龄段是？", "options": "18 以下\n18-25\n26 以上"},
            follow_redirects=True,
        )
        version_v2 = db.question_bank.find_one({"lineage_id": lineage, "version": 2})
        assert version_v2["_id"] != version_v1["_id"]

        second = create_survey(client, db, title="问卷二")
        client.post(
            f"/surveys/{second['_id']}/questions/from-bank",
            data={"version_id": str(version_v2["_id"])},
            follow_redirects=True,
        )
        second_question = db.questions.find_one({"survey_id": second["_id"]})
        assert second_question["version"] == 2
        _publish_and_submit(client, db, second, second_question["_id"], "C")

        stats = versioning.cross_survey_stats(db, lineage)
        assert stats["answered"] == 2
        assert len(stats["versions"]) == 2
        assert {row["version"] for row in stats["version_totals"]} == {1, 2}
        counts = {opt["key"]: opt["count"] for opt in stats["options"]}
        assert counts == {"A": 1, "B": 0, "C": 1}

    def test_number_question_stats(self, client, db):
        register(client)
        login(client)
        survey = create_survey(client, db, title="问卷一")
        question = add_number(client, db, survey, title="每周运动几次？")
        _publish_and_submit(client, db, survey, question["_id"], 3)

        stats = versioning.cross_survey_stats(db, question["lineage_id"])
        assert stats["type"] == "number"
        assert stats["average"] == 3.0
        assert stats["count"] == 1

    def test_private_question_stats_404_for_others(self, app, client, db):
        register(client, "alice")
        login(client, "alice")
        survey = create_survey(client, db, title="alice 的问卷")
        question = add_single(client, db, survey, title="你的年龄是？")

        bob = app.test_client()
        register(bob, "bob")
        login(bob, "bob")
        assert bob.get(f"/stats/questions/{question['lineage_id']}").status_code == 404
