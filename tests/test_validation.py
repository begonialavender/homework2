"""校验测试：必答、单选越界、多选数量、字数、数字范围与整数。"""

from __future__ import annotations

from tests.helpers import (
    add_multiple, add_number, add_single, add_text,
    answer, create_survey, login, publish, register, submit,
)


def make_survey(client, db):
    register(client, "alice")
    login(client, "alice")
    survey = create_survey(client, db)
    return survey


def error_of(resp) -> str:
    return (resp.get_json() or {}).get("error", "")


class TestSingleChoice:
    def test_invalid_option_rejected(self, client, db):
        survey = make_survey(client, db)
        q1 = add_single(client, db, survey)
        publish(client, survey)
        resp = submit(client, survey["slug"], {str(q1["_id"]): "Z"})
        assert resp.status_code == 400
        assert "不存在" in error_of(resp)

    def test_valid_option_accepted(self, client, db):
        survey = make_survey(client, db)
        q1 = add_single(client, db, survey)
        publish(client, survey)
        assert submit(client, survey["slug"], {str(q1["_id"]): "B"}).status_code == 200


class TestMultipleChoice:
    def test_min_choices(self, client, db):
        survey = make_survey(client, db)
        q1 = add_multiple(client, db, survey, min_choices=2)
        publish(client, survey)
        resp = submit(client, survey["slug"], {str(q1["_id"]): ["A"]})
        assert resp.status_code == 400
        assert "至少选择 2 项" in error_of(resp)

    def test_max_choices(self, client, db):
        survey = make_survey(client, db)
        q1 = add_multiple(client, db, survey, max_choices=2)
        publish(client, survey)
        resp = submit(client, survey["slug"], {str(q1["_id"]): ["A", "B", "C"]})
        assert resp.status_code == 400
        assert "最多选择 2 项" in error_of(resp)

    def test_exact_choices(self, client, db):
        survey = make_survey(client, db)
        q1 = add_multiple(client, db, survey, exact_choices=2)
        publish(client, survey)
        assert submit(client, survey["slug"], {str(q1["_id"]): ["A"]}).status_code == 400
        assert submit(client, survey["slug"], {str(q1["_id"]): ["A", "C"]}).status_code == 200

    def test_unknown_option(self, client, db):
        survey = make_survey(client, db)
        q1 = add_multiple(client, db, survey)
        publish(client, survey)
        assert submit(client, survey["slug"], {str(q1["_id"]): ["A", "Q"]}).status_code == 400


class TestText:
    def test_min_length(self, client, db):
        survey = make_survey(client, db)
        q1 = add_text(client, db, survey, min_length=3)
        publish(client, survey)
        resp = submit(client, survey["slug"], {str(q1["_id"]): "ab"})
        assert resp.status_code == 400
        assert "至少输入 3" in error_of(resp)

    def test_max_length(self, client, db):
        survey = make_survey(client, db)
        q1 = add_text(client, db, survey, max_length=4)
        publish(client, survey)
        resp = submit(client, survey["slug"], {str(q1["_id"]): "一二三四五"})
        assert resp.status_code == 400
        assert "最多输入 4" in error_of(resp)

    def test_ok(self, client, db):
        survey = make_survey(client, db)
        q1 = add_text(client, db, survey, min_length=2, max_length=4)
        publish(client, survey)
        assert submit(client, survey["slug"], {str(q1["_id"]): "正好"}).status_code == 200


class TestNumber:
    def test_integer_required(self, client, db):
        survey = make_survey(client, db)
        q1 = add_number(client, db, survey, integer=True, min_value=0, max_value=120)
        publish(client, survey)
        resp = submit(client, survey["slug"], {str(q1["_id"]): "18.5"})
        assert resp.status_code == 400
        assert "整数" in error_of(resp)

    def test_range(self, client, db):
        survey = make_survey(client, db)
        q1 = add_number(client, db, survey, integer=True, min_value=0, max_value=120)
        publish(client, survey)
        assert submit(client, survey["slug"], {str(q1["_id"]): "121"}).status_code == 400
        assert submit(client, survey["slug"], {str(q1["_id"]): "-1"}).status_code == 400

    def test_not_a_number(self, client, db):
        survey = make_survey(client, db)
        q1 = add_number(client, db, survey)
        publish(client, survey)
        resp = submit(client, survey["slug"], {str(q1["_id"]): "十八"})
        assert resp.status_code == 400
        assert "数字" in error_of(resp)

    def test_valid_boundaries(self, client, db):
        survey = make_survey(client, db)
        q1 = add_number(client, db, survey, integer=True, min_value=0, max_value=120)
        publish(client, survey)
        assert submit(client, survey["slug"], {str(q1["_id"]): "0"}).status_code == 200
        assert submit(client, survey["slug"], {str(q1["_id"]): "120"}).status_code == 200


class TestRequired:
    def test_missing_required(self, client, db):
        survey = make_survey(client, db)
        q1 = add_single(client, db, survey, required=True)
        publish(client, survey)
        resp = submit(client, survey["slug"], {})
        assert resp.status_code == 400
        assert "必答" in error_of(resp)

    def test_step_api_blocks_missing_required(self, client, db):
        survey = make_survey(client, db)
        q1 = add_single(client, db, survey, required=True)
        publish(client, survey)
        resp = answer(client, survey["slug"], q1["_id"], None, {})
        assert resp.status_code == 400
        assert "必答" in error_of(resp)

    def test_optional_can_be_empty(self, client, db):
        survey = make_survey(client, db)
        q1 = add_text(client, db, survey, required=False)
        publish(client, survey)
        assert submit(client, survey["slug"], {}).status_code == 200
