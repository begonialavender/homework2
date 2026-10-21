"""统计测试：单选计数、多选计数、数字平均、文本列表。"""

from __future__ import annotations

from app.stats import question_stats
from tests.helpers import (
    add_multiple, add_number, add_single, add_text,
    create_survey, login, publish, register, submit,
)


def build(client, db):
    register(client, "alice")
    login(client, "alice")
    survey = create_survey(client, db)
    q1 = add_single(client, db, survey, title="性别", options="男\n女\n其他")
    q2 = add_multiple(client, db, survey, title="水果", options="苹果\n香蕉\n西瓜\n葡萄")
    q3 = add_number(client, db, survey, title="年龄", min_value=0, max_value=120, integer=True)
    q4 = add_text(client, db, survey, title="建议")
    publish(client, survey)

    rows = [
        {str(q1["_id"]): "A", str(q2["_id"]): ["A", "B"], str(q3["_id"]): "10", str(q4["_id"]): "hi"},
        {str(q1["_id"]): "A", str(q2["_id"]): ["A"], str(q3["_id"]): "20", str(q4["_id"]): "hello"},
        {str(q1["_id"]): "B", str(q2["_id"]): ["B", "C"], str(q3["_id"]): "30", str(q4["_id"]): "abc"},
    ]
    for row in rows:
        assert submit(client, survey["slug"], row).status_code == 200
    return survey, q1, q2, q3, q4


class TestAggregation:
    def test_single_counts(self, app, client, db):
        survey, q1, *_ = build(client, db)
        with app.app_context():
            data = question_stats(survey, q1)
        counts = {opt["key"]: opt["count"] for opt in data["options"]}
        assert counts == {"A": 2, "B": 1, "C": 0}
        assert data["answered"] == 3
        assert {opt["key"]: opt["percent"] for opt in data["options"]}["A"] == 66.7

    def test_multiple_counts(self, app, client, db):
        survey, _, q2, _, _ = build(client, db)
        with app.app_context():
            data = question_stats(survey, q2)
        counts = {opt["key"]: opt["count"] for opt in data["options"]}
        assert counts == {"A": 2, "B": 2, "C": 1, "D": 0}
        assert data["answered"] == 3

    def test_number_average(self, app, client, db):
        survey, _, _, q3, _ = build(client, db)
        with app.app_context():
            data = question_stats(survey, q3)
        assert data["average"] == 20.0
        assert data["minimum"] == 10
        assert data["maximum"] == 30
        assert data["values"] == [10, 20, 30]

    def test_text_entries(self, app, client, db):
        survey, _, _, _, q4 = build(client, db)
        with app.app_context():
            data = question_stats(survey, q4)
        texts = [entry["value"] for entry in data["entries"]]
        assert sorted(texts) == sorted(["hi", "hello", "abc"])
        assert data["average_length"] == 3.3

    def test_no_responses(self, app, client, db):
        register(client, "alice")
        login(client, "alice")
        survey = create_survey(client, db)
        q1 = add_single(client, db, survey)
        with app.app_context():
            data = question_stats(survey, q1)
        assert data["answered"] == 0
        assert data["options"][0]["percent"] == 0.0


class TestStatsPage:
    def test_overview_page_renders(self, client, db):
        survey, *_ = build(client, db)
        resp = client.get(f"/surveys/{survey['_id']}/stats")
        assert resp.status_code == 200
        page = resp.get_data(as_text=True)
        assert "统计结果" in page
        assert "66.7%" in page

    def test_single_question_page(self, client, db):
        survey, q1, *_ = build(client, db)
        resp = client.get(f"/surveys/{survey['_id']}/stats/{q1['_id']}")
        assert resp.status_code == 200
        assert "整卷统计" in resp.get_data(as_text=True)

    def test_stats_of_other_user_is_404(self, client, db):
        survey, *_ = build(client, db)
        client.post("/logout")
        register(client, "bob")
        login(client, "bob")
        assert client.get(f"/surveys/{survey['_id']}/stats").status_code == 404
