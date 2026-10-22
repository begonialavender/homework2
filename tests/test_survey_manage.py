"""问卷创建与管理测试：四类题目、发布、关闭、截止时间。"""

from __future__ import annotations

from tests.helpers import (
    add_multiple,
    add_number,
    add_single,
    add_text,
    create_survey,
    login,
    publish,
    register,
)


def setup_user(client):
    register(client, "alice")
    login(client, "alice")


class TestCreate:
    def test_create_survey(self, client, db):
        setup_user(client)
        survey = create_survey(client, db, title="课程反馈")
        assert survey is not None
        assert survey["status"] == "draft"
        assert survey["allow_anonymous"] is False
        assert len(survey["slug"]) >= 6
        assert survey["question_count"] == 0

    def test_title_required(self, client):
        setup_user(client)
        resp = client.post("/surveys/new", data={"title": "   "}, follow_redirects=True)
        assert resp.status_code == 400

    def test_slug_is_unique(self, client, db):
        setup_user(client)
        a = create_survey(client, db, title="问卷 A")
        b = create_survey(client, db, title="问卷 B")
        assert a["slug"] != b["slug"]

    def test_update_settings(self, client, db):
        setup_user(client)
        survey = create_survey(client, db, title="旧标题")
        client.post(
            f"/surveys/{survey['_id']}/settings",
            data={
                "title": "新标题",
                "description": "新说明",
                "allow_anonymous": "on",
                "allow_multiple": "on",
                "deadline": "2030-01-01T12:00",
            },
            follow_redirects=True,
        )
        updated = db.surveys.find_one({"_id": survey["_id"]})
        assert updated["title"] == "新标题"
        assert updated["allow_anonymous"] is True
        assert updated["deadline"] is not None


class TestQuestions:
    def test_add_four_types(self, client, db):
        setup_user(client)
        survey = create_survey(client, db)
        single = add_single(client, db, survey)
        multiple = add_multiple(client, db, survey, min_choices=2, max_choices=3)
        text = add_text(client, db, survey, min_length=2, max_length=10)
        number = add_number(client, db, survey, min_value=0, max_value=120, integer=True)

        assert [q["order"] for q in db.questions.find({"survey_id": survey["_id"]}).sort("order", 1)] == [0, 1, 2, 3]
        assert single["options"][0] == {"key": "A", "label": "男"}
        assert multiple["rules"] == {"min_choices": 2, "max_choices": 3}
        assert text["rules"] == {"min_length": 2, "max_length": 10}
        assert number["rules"] == {"min_value": 0, "max_value": 120, "integer": True}

    def test_question_count_synced(self, client, db):
        setup_user(client)
        survey = create_survey(client, db)
        add_single(client, db, survey)
        add_text(client, db, survey)
        assert db.surveys.find_one({"_id": survey["_id"]})["question_count"] == 2

        question = db.questions.find_one({"survey_id": survey["_id"], "type": "text"})
        client.post(f"/surveys/{survey['_id']}/questions/{question['_id']}/delete", follow_redirects=True)
        assert db.surveys.find_one({"_id": survey["_id"]})["question_count"] == 1

    def test_invalid_question_config(self, client, db):
        setup_user(client)
        survey = create_survey(client, db)
        resp = client.post(
            f"/surveys/{survey['_id']}/questions",
            data={"type": "single", "title": "只有一个选项的题", "options": "唯一选项"},
            follow_redirects=True,
        )
        assert "至少需要 2 个选项" in resp.get_data(as_text=True)
        assert db.questions.count_documents({"survey_id": survey["_id"]}) == 0

    def test_move_question(self, client, db):
        setup_user(client)
        survey = create_survey(client, db)
        first = add_single(client, db, survey, title="第一题")
        second = add_text(client, db, survey, title="第二题")

        client.post(
            f"/surveys/{survey['_id']}/questions/{second['_id']}/move",
            data={"direction": "up"},
            follow_redirects=True,
        )
        texts = [q["title"] for q in db.questions.find({"survey_id": survey["_id"]}).sort("order", 1)]
        assert texts == ["第二题", "第一题"]

    def test_question_limit(self, client, db, app):
        setup_user(client)
        survey = create_survey(client, db)
        app.config["MAX_QUESTIONS_PER_SURVEY"] = 1
        try:
            add_single(client, db, survey, title="第一题")
            resp = client.post(
                f"/surveys/{survey['_id']}/questions",
                data={"type": "text", "title": "超出的题"},
                follow_redirects=True,
            )
            assert "最多 1 道题" in resp.get_data(as_text=True)
            assert db.questions.count_documents({"survey_id": survey["_id"]}) == 1
        finally:
            app.config["MAX_QUESTIONS_PER_SURVEY"] = 50

    def test_edit_pages_render(self, client, db):
        setup_user(client)
        survey = create_survey(client, db)
        question = add_single(client, db, survey)

        assert client.get("/surveys/new").status_code == 200
        assert client.get(f"/surveys/{survey['_id']}").status_code == 200
        page = client.get(f"/surveys/{survey['_id']}/questions/{question['_id']}")
        assert page.status_code == 200
        assert "跳转规则" in page.get_data(as_text=True)

    def test_jump_summary_on_edit_page(self, client, db):
        setup_user(client)
        survey = create_survey(client, db)
        q1 = add_single(client, db, survey, title="第一题")
        q2 = add_text(client, db, survey, title="第二题")
        client.post(
            f"/surveys/{survey['_id']}/questions/{q1['_id']}",
            data={
                "type": "single",
                "title": q1["title"],
                "options": "男\n女\n其他",
                "jump_A": str(q2["_id"]),
                "default_next": "next",
            },
            follow_redirects=True,
        )
        page = client.get(f"/surveys/{survey['_id']}").get_data(as_text=True)
        assert "跳转" in page
        assert "第 2 题" in page


class TestPublishAndClose:
    def test_publish_requires_question(self, client, db):
        setup_user(client)
        survey = create_survey(client, db)
        resp = client.post(f"/surveys/{survey['_id']}/publish", follow_redirects=True)
        assert "至少添加一道题目" in resp.get_data(as_text=True)
        assert db.surveys.find_one({"_id": survey["_id"]})["status"] == "draft"

    def test_publish_and_close(self, client, db):
        setup_user(client)
        survey = create_survey(client, db)
        add_single(client, db, survey)
        publish(client, survey)
        published = db.surveys.find_one({"_id": survey["_id"]})
        assert published["status"] == "published"
        assert published["published_at"] is not None

        client.post(f"/surveys/{survey['_id']}/close", follow_redirects=True)
        closed = db.surveys.find_one({"_id": survey["_id"]})
        assert closed["status"] == "closed"
        assert closed["closed_at"] is not None

    def test_closed_survey_cannot_be_filled(self, client, db):
        setup_user(client)
        survey = create_survey(client, db)
        add_single(client, db, survey)
        publish(client, survey)
        assert client.get(f"/survey/{survey['slug']}").status_code == 200

        client.post(f"/surveys/{survey['_id']}/close", follow_redirects=True)
        resp = client.get(f"/survey/{survey['slug']}")
        assert resp.status_code == 403
        assert "关闭" in resp.get_data(as_text=True)

    def test_deadline_passed(self, client, db):
        setup_user(client)
        survey = create_survey(client, db, deadline="2020-01-01T09:00")
        add_single(client, db, survey)
        publish(client, survey)
        resp = client.get(f"/survey/{survey['slug']}")
        assert resp.status_code == 403
        assert "截止" in resp.get_data(as_text=True)

    def test_draft_cannot_be_filled(self, client, db):
        setup_user(client)
        survey = create_survey(client, db)
        add_single(client, db, survey)
        assert client.get(f"/survey/{survey['slug']}").status_code == 403
