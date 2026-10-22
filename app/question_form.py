"""题目表单解析：把 HTML 表单转成题目内容，并做配置合法性检查。

问卷编辑页与题库页共用这一份解析逻辑，保证「在问卷里建题」和「在题库里建题」
对同一份表单得到完全相同的结果。
"""

from __future__ import annotations

import string
from typing import Any

from .logic import QUESTION_TYPES
from .utils import parse_float, parse_int

OPTION_KEYS = list(string.ascii_uppercase)


def choice_options(raw: str | None) -> list[dict[str, str]]:
    lines = [line.strip() for line in (raw or "").splitlines()]
    labels = [line for line in lines if line]
    return [{"key": OPTION_KEYS[i], "label": label} for i, label in enumerate(labels)]


def build_question(form) -> tuple[dict[str, Any] | None, str | None]:
    """把表单转成题目内容，顺带做配置合法性检查。

    返回值只包含内容字段（type/title/options/rules/required），
    不含 survey_id / order / logic —— 那些属于「问卷内编排」，由调用方补。
    """
    qtype = form.get("type")
    if qtype not in QUESTION_TYPES:
        return None, "题型不合法"

    title = (form.get("title") or "").strip()
    if not title:
        return None, "请填写题干"
    if len(title) > 500:
        return None, "题干过长（最多 500 字）"

    rules: dict[str, Any] = {}
    options: list[dict[str, str]] = []

    if qtype in ("single", "multiple"):
        options = choice_options(form.get("options", ""))
        if len(options) < 2:
            return None, "单选题 / 多选题至少需要 2 个选项"
        if len(options) > 26:
            return None, "选项最多 26 个"

    if qtype == "multiple":
        minimum = parse_int(form.get("min_choices"), -1)
        maximum = parse_int(form.get("max_choices"), -1)
        exact = parse_int(form.get("exact_choices"), -1)
        if minimum >= 0:
            rules["min_choices"] = minimum
        if maximum >= 0:
            rules["max_choices"] = maximum
        if exact >= 0:
            rules["exact_choices"] = exact
        if "min_choices" in rules and "max_choices" in rules and rules["min_choices"] > rules["max_choices"]:
            return None, "最少选择数不能大于最多选择数"
        if "exact_choices" in rules and rules["exact_choices"] > len(options):
            return None, "固定选择数不能超过选项总数"

    if qtype == "text":
        minimum = parse_int(form.get("min_length"), -1)
        maximum = parse_int(form.get("max_length"), -1)
        if minimum >= 0:
            rules["min_length"] = minimum
        if maximum >= 0:
            rules["max_length"] = maximum
        if "min_length" in rules and "max_length" in rules and rules["min_length"] > rules["max_length"]:
            return None, "最少字数不能大于最多字数"

    if qtype == "number":
        minimum = parse_float(form.get("min_value"))
        maximum = parse_float(form.get("max_value"))
        if minimum is not None:
            rules["min_value"] = minimum
        if maximum is not None:
            rules["max_value"] = maximum
        if minimum is not None and maximum is not None and minimum > maximum:
            return None, "最小值不能大于最大值"
        rules["integer"] = form.get("integer") == "on"

    return {
        "type": qtype,
        "title": title,
        "options": options,
        "rules": rules,
        "required": form.get("required") == "on",
    }, None
