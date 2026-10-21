"""纯函数单元测试：校验规则、条件匹配、路径重算。

这一组不连数据库，跑得很快，适合在改跳转逻辑时反复执行。
"""

from __future__ import annotations

import pytest
from bson import ObjectId

from app.logic import (
    END,
    ValidationError,
    build_path,
    match_condition,
    next_question,
    resolve_target,
    validate_answer,
)


def make_question(order: int, qtype: str, **kwargs) -> dict:
    base = {
        "_id": ObjectId(),
        "order": order,
        "type": qtype,
        "title": f"第 {order + 1} 题",
        "required": False,
        "options": [],
        "rules": {},
        "logic": {"default_next": "next", "jumps": []},
    }
    base.update(kwargs)
    return base


SINGLE = make_question(
    0,
    "single",
    options=[{"key": "A", "label": "男"}, {"key": "B", "label": "女"}, {"key": "C", "label": "其他"}],
)


class TestValidateAnswer:
    def test_single_ok(self):
        assert validate_answer(SINGLE, "A") == "A"

    def test_single_not_in_options(self):
        with pytest.raises(ValidationError):
            validate_answer(SINGLE, "Z")

    def test_single_wrong_type(self):
        with pytest.raises(ValidationError):
            validate_answer(SINGLE, ["A"])

    def test_required_empty(self):
        question = make_question(0, "text", required=True)
        with pytest.raises(ValidationError, match="必答"):
            validate_answer(question, "")

    def test_optional_empty_returns_none(self):
        question = make_question(0, "text")
        assert validate_answer(question, "") is None

    def test_multiple_rule_min(self):
        question = make_question(
            0,
            "multiple",
            options=[{"key": k, "label": k} for k in "ABCD"],
            rules={"min_choices": 2},
        )
        assert validate_answer(question, ["A", "B"]) == ["A", "B"]
        with pytest.raises(ValidationError, match="至少选择 2 项"):
            validate_answer(question, ["A"])

    def test_multiple_rule_max(self):
        question = make_question(
            0,
            "multiple",
            options=[{"key": k, "label": k} for k in "ABCD"],
            rules={"max_choices": 2},
        )
        with pytest.raises(ValidationError, match="最多选择 2 项"):
            validate_answer(question, ["A", "B", "C"])

    def test_multiple_rule_exact(self):
        question = make_question(
            0,
            "multiple",
            options=[{"key": k, "label": k} for k in "ABCD"],
            rules={"exact_choices": 2},
        )
        assert validate_answer(question, ["A", "C"]) == ["A", "C"]
        with pytest.raises(ValidationError, match="必须选择 2 项"):
            validate_answer(question, ["A", "B", "C"])

    def test_multiple_duplicate_and_unknown(self):
        question = make_question(0, "multiple", options=[{"key": "A", "label": "A"}])
        with pytest.raises(ValidationError, match="重复"):
            validate_answer(question, ["A", "A"])
        with pytest.raises(ValidationError, match="不存在"):
            validate_answer(question, ["B"])

    def test_text_length(self):
        question = make_question(0, "text", rules={"min_length": 2, "max_length": 4})
        assert validate_answer(question, "你好") == "你好"
        with pytest.raises(ValidationError, match="至少输入 2"):
            validate_answer(question, "你")
        with pytest.raises(ValidationError, match="最多输入 4"):
            validate_answer(question, "一二三四五")

    def test_number_integer_and_range(self):
        question = make_question(0, "number", rules={"integer": True, "min_value": 0, "max_value": 120})
        assert validate_answer(question, "18") == 18
        assert validate_answer(question, 120) == 120
        with pytest.raises(ValidationError, match="整数"):
            validate_answer(question, "18.5")
        with pytest.raises(ValidationError, match="不能大于"):
            validate_answer(question, "121")
        with pytest.raises(ValidationError, match="不能小于"):
            validate_answer(question, "-1")

    def test_number_float_allowed(self):
        question = make_question(0, "number", rules={"min_value": 0})
        assert validate_answer(question, 3.5) == 3.5

    def test_number_garbage(self):
        question = make_question(0, "number")
        with pytest.raises(ValidationError):
            validate_answer(question, "abc")


class TestConditions:
    def test_single_option(self):
        assert match_condition({"option": "A"}, "A", "single")
        assert not match_condition({"option": "A"}, "B", "single")

    def test_multiple_any_all(self):
        assert match_condition({"contains_any": ["A", "Z"]}, ["A", "B"], "multiple")
        assert not match_condition({"contains_all": ["A", "Z"]}, ["A", "B"], "multiple")
        assert match_condition({"contains_all": ["A", "B"]}, ["A", "B"], "multiple")

    def test_multiple_count(self):
        condition = {"count": {"op": "gte", "value": 2}}
        assert match_condition(condition, ["A", "B"], "multiple")
        assert not match_condition(condition, ["A"], "multiple")

    def test_number_compare(self):
        assert match_condition({"op": "gte", "value": 60}, 60, "number")
        assert not match_condition({"op": "gte", "value": 60}, 59, "number")
        assert match_condition({"op": "between", "value": 18, "value2": 30}, 25, "number")
        assert not match_condition({"op": "between", "value": 18, "value2": 30}, 31, "number")

    def test_empty_value_never_matches(self):
        assert not match_condition({"option": "A"}, None, "single")


class TestJump:
    def _chain(self):
        q1 = make_question(0, "single", options=[{"key": "A", "label": "A"}, {"key": "B", "label": "B"}])
        q2 = make_question(1, "text")
        q3 = make_question(2, "text")
        q4 = make_question(3, "text")
        q1["logic"]["jumps"] = [
            {"when": {"option": "A"}, "target": str(q4["_id"])},
            {"when": {"option": "B"}, "target": str(q3["_id"])},
        ]
        return q1, q2, q3, q4

    def test_resolve_target(self):
        q1, q2, q3, q4 = self._chain()
        assert resolve_target(q1, "A", str(q2["_id"])) == str(q4["_id"])
        assert resolve_target(q1, "B", str(q2["_id"])) == str(q3["_id"])

    def test_default_next(self):
        q1, q2, _, _ = self._chain()
        assert resolve_target(q1, None, str(q2["_id"])) == str(q2["_id"])

    def test_default_end(self):
        question = make_question(0, "text", logic={"default_next": "end", "jumps": []})
        assert resolve_target(question, "随便", None) == END

    def test_build_path_skips_questions(self):
        q1, q2, q3, q4 = self._chain()
        result = build_path([q1, q2, q3, q4], {str(q1["_id"]): "A", str(q4["_id"]): "结尾"})
        assert result.ok, result.errors
        assert result.path == [str(q1["_id"]), str(q4["_id"])]
        assert result.values[str(q4["_id"])] == "结尾"

    def test_build_path_rejects_answer_for_skipped_question(self):
        q1, q2, q3, q4 = self._chain()
        result = build_path(
            [q1, q2, q3, q4],
            {str(q1["_id"]): "A", str(q2["_id"]): "不该出现", str(q4["_id"]): "结尾"},
        )
        assert not result.ok
        assert "跳过" in result.errors[0]["message"]

    def test_build_path_required_missing(self):
        q1, q2, q3, q4 = self._chain()
        q2["required"] = True
        # q1 选空缺省（跳转不命中），默认走到 q2，而 q2 是必答题却没有答案
        result = build_path([q1, q2, q3, q4], {str(q1["_id"]): None})
        assert not result.ok
        assert result.errors[0]["question_id"] == str(q2["_id"])

    def test_build_path_unknown_question(self):
        q1, _, _, _ = self._chain()
        result = build_path([q1], {str(ObjectId()): "x"})
        assert not result.ok
        assert "不属于本问卷" in result.errors[0]["message"]

    def test_build_path_detects_loop(self):
        q1 = make_question(0, "text")
        q2 = make_question(1, "text")
        q1["logic"]["jumps"] = [{"when": {"equals": "x"}, "target": str(q2["_id"])}]
        q2["logic"]["jumps"] = [{"when": {"equals": "y"}, "target": str(q1["_id"])}]
        result = build_path([q1, q2], {str(q1["_id"]): "x", str(q2["_id"]): "y"})
        assert not result.ok
        assert "环路" in result.errors[0]["message"]

    def test_build_path_bad_jump_target(self):
        q1 = make_question(0, "text")
        q1["logic"]["jumps"] = [{"when": {"equals": "x"}, "target": str(ObjectId())}]
        result = build_path([q1], {str(q1["_id"]): "x"})
        assert not result.ok
        assert "跳转目标题目不存在" in result.errors[0]["message"]


class TestNextQuestion:
    def test_returns_first_unanswered(self):
        q1, q2, q3, q4 = TestJump()._chain()
        nxt, errors, finished = next_question([q1, q2, q3, q4], {})
        assert errors == [] and not finished
        assert str(nxt["_id"]) == str(q1["_id"])

    def test_follows_jump(self):
        q1, q2, q3, q4 = TestJump()._chain()
        nxt, errors, finished = next_question(
            [q1, q2, q3, q4], {str(q1["_id"]): "A"}
        )
        assert not errors and not finished
        assert str(nxt["_id"]) == str(q4["_id"])

    def test_finished(self):
        q1, q2, q3, q4 = TestJump()._chain()
        nxt, errors, finished = next_question(
            [q1, q2, q3, q4], {str(q1["_id"]): "A", str(q4["_id"]): "完"}
        )
        assert nxt is None and not errors and finished

    def test_invalid_answer(self):
        q1, q2, q3, q4 = TestJump()._chain()
        nxt, errors, finished = next_question([q1, q2, q3, q4], {str(q1["_id"]): "Z"})
        assert errors and not finished
