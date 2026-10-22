"""填写与跳转的端到端测试：链接访问、逐步作答、提交、路径重算。"""

from __future__ import annotations

from tests.helpers import (
    add_multiple, add_number, add_single, add_text,
    answer, create_survey, login, publish, register, submit,
)


def build_survey(client, db, anonymous=False, multiple=True):
    register(client, "alice")
    login(client, "alice")
    survey = create_survey(client, db, anonymous=anonymous, multiple=multiple)
    q1 = add_single(client, db, survey, title="你平时运动吗？", options="经常\n偶尔\n从不")
    q2 = add_text(client, db, survey, title="每周大概几次？")
    q3 = add_number(client, db, survey, title="年龄", min_value=0, max_value=120, integer=True)
    q4 = add_text(client, db, survey, title="有什么建议？")
    return survey, q1, q2, q3, q4


def set_single_jump(client, survey, question, mapping, default_next="next"):
    """mapping: {"A": target_id, "B": "end" ...}"""
    data = {
        "type": question["type"],
        "title": question["title"],
        "options": "\n".join(opt["label"] for opt in question["options"]),
        "default_next": default_next,
    }
    if question.get("required"):
        data["required"] = "on"
    for key, target in mapping.items():
        data[f"jump_{key}"] = str(target)
    return client.post(
        f"/surveys/{survey['_id']}/questions/{question['_id']}",
        data=data,
        follow_redirects=True,
    )


class TestFillAccess:
    def test_fill_page_requires_login(self, client, db):
        survey, *_ = build_survey(client, db)
        publish(client, survey)
        client.post("/logout")
        resp = client.get(f"/survey/{survey['slug']}")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_fill_page_shows_first_question(self, client, db):
        survey, q1, *_ = build_survey(client, db)
        publish(client, survey)
        page = client.get(f"/survey/{survey['slug']}").get_data(as_text=True)
        assert q1["title"] in page

    def test_unknown_slug_404(self, client, db):
        register(client, "alice")
        login(client, "alice")
        assert client.get("/survey/does-not-exist").status_code == 404


class TestJumpSingle:
    def test_jump_when_option_matches(self, client, db):
        survey, q1, q2, q3, q4 = build_survey(client, db)
        set_single_jump(client, survey, q1, {"A": q4["_id"]})
        publish(client, survey)

        resp = answer(client, survey["slug"], q1["_id"], "A", {})
        body = resp.get_json()
        assert body["ok"] is True
        assert body["next"]["id"] == str(q4["_id"])

    def test_default_next_when_no_rule(self, client, db):
        survey, q1, q2, q3, q4 = build_survey(client, db)
        set_single_jump(client, survey, q1, {"A": q4["_id"]})
        publish(client, survey)

        resp = answer(client, survey["slug"], q1["_id"], "B", {})
        assert resp.get_json()["next"]["id"] == str(q2["_id"])

    def test_submit_skips_questions(self, client, db):
        survey, q1, q2, q3, q4 = build_survey(client, db)
        set_single_jump(client, survey, q1, {"A": q4["_id"]})
        publish(client, survey)

        resp = submit(client, survey["slug"], {str(q1["_id"]): "A", str(q4["_id"]): "希望多办活动"})
        assert resp.status_code == 200
        stored = db.responses.find_one({"survey_id": survey["_id"]})
        assert stored["path"] == [str(q1["_id"]), str(q4["_id"])]
        assert len(stored["answers"]) == 2

    def test_submit_rejects_answer_for_skipped_question(self, client, db):
        survey, q1, q2, q3, q4 = build_survey(client, db)
        set_single_jump(client, survey, q1, {"A": q4["_id"]})
        publish(client, survey)

        resp = submit(
            client,
            survey["slug"],
            {str(q1["_id"]): "A", str(q2["_id"]): "不该被提交", str(q4["_id"]): "建议"},
        )
        assert resp.status_code == 400
        assert "跳过" in resp.get_json()["error"]
        assert db.responses.count_documents({"survey_id": survey["_id"]}) == 0

    def test_jump_to_end(self, client, db):
        survey, q1, q2, q3, q4 = build_survey(client, db)
        set_single_jump(client, survey, q1, {"A": "end"})
        publish(client, survey)

        resp = answer(client, survey["slug"], q1["_id"], "A", {})
        body = resp.get_json()
        assert body["finished"] is True
        assert body["next"] is None

        assert submit(client, survey["slug"], {str(q1["_id"]): "A"}).status_code == 200


class TestJumpMultiple:
    def test_contains_any(self, client, db):
        register(client, "alice")
        login(client, "alice")
        survey = create_survey(client, db)
        q1 = add_multiple(client, db, survey, options="读代码\n写代码\n跑步")
        q2 = add_text(client, db, survey, title="第二题")
        q3 = add_text(client, db, survey, title="第三题")

        client.post(
            f"/surveys/{survey['_id']}/questions/{q1['_id']}",
            data={
                "type": "multiple",
                "title": q1["title"],
                "options": "读代码\n写代码\n跑步",
                "jump_A": str(q3["_id"]),
                "jump_B": str(q3["_id"]),
                "default_next": "next",
            },
            follow_redirects=True,
        )
        publish(client, survey)

        body = answer(client, survey["slug"], q1["_id"], ["A", "C"], {}).get_json()
        assert body["next"]["id"] == str(q3["_id"])

        body = answer(client, survey["slug"], q1["_id"], ["C"], {}).get_json()
        assert body["next"]["id"] == str(q2["_id"])


class TestJumpNumber:
    def test_threshold_jump(self, client, db):
        register(client, "alice")
        login(client, "alice")
        survey = create_survey(client, db)
        q1 = add_number(client, db, survey, title="年龄", min_value=0, max_value=120, integer=True)
        q2 = add_text(client, db, survey, title="第二题")
        q3 = add_text(client, db, survey, title="第三题")

        client.post(
            f"/surveys/{survey['_id']}/questions/{q1['_id']}",
            data={
                "type": "number",
                "title": q1["title"],
                "min_value": "0",
                "max_value": "120",
                "integer": "on",
                "number_op": "gte",
                "number_value": "60",
                "number_target": str(q3["_id"]),
                "default_next": "next",
            },
            follow_redirects=True,
        )
        publish(client, survey)

        assert answer(client, survey["slug"], q1["_id"], "60", {}).get_json()["next"]["id"] == str(q3["_id"])
        assert answer(client, survey["slug"], q1["_id"], "59", {}).get_json()["next"]["id"] == str(q2["_id"])


class TestSubmitRules:
    def test_required_question_must_be_answered(self, client, db):
        register(client, "alice")
        login(client, "alice")
        survey = create_survey(client, db)
        q1 = add_single(client, db, survey, required=True)
        publish(client, survey)

        resp = submit(client, survey["slug"], {})
        assert resp.status_code == 400
        assert "必答" in resp.get_json()["error"]

    def test_duplicate_submission_blocked(self, client, db):
        register(client, "alice")
        login(client, "alice")
        survey = create_survey(client, db, multiple=False)
        q1 = add_text(client, db, survey, title="意见")
        publish(client, survey)

        assert submit(client, survey["slug"], {str(q1["_id"]): "第一次"}).status_code == 200
        resp = submit(client, survey["slug"], {str(q1["_id"]): "第二次"})
        assert resp.status_code == 409
        assert db.responses.count_documents({"survey_id": survey["_id"]}) == 1

    def test_unknown_question_rejected(self, client, db):
        register(client, "alice")
        login(client, "alice")
        survey = create_survey(client, db)
        add_text(client, db, survey, title="意见")
        publish(client, survey)

        resp = submit(client, survey["slug"], {"5f0000000000000000000000": "x"})
        assert resp.status_code == 400

    def test_anonymous_response_has_no_respondent(self, client, db):
        survey, q1, q2, q3, q4 = build_survey(client, db, anonymous=True)
        publish(client, survey)
        assert submit(client, survey["slug"], {str(q1["_id"]): "A", str(q2["_id"]): "x"}).status_code == 200
        stored = db.responses.find_one({"survey_id": survey["_id"]})
        assert stored["anonymous"] is True
        assert stored["respondent_id"] is None

    def test_named_response_records_respondent(self, client, db):
        survey, q1, q2, q3, q4 = build_survey(client, db, anonymous=False)
        publish(client, survey)
        assert submit(client, survey["slug"], {str(q1["_id"]): "A", str(q2["_id"]): "x"}).status_code == 200
        stored = db.responses.find_one({"survey_id": survey["_id"]})
        assert stored["respondent_id"] is not None
