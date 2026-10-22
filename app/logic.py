"""问卷校验与跳转引擎。

本模块是纯函数实现：不依赖 Flask，也不依赖 MongoDB。
只要传入题目列表和答案字典就能算出「实际作答路径」，
因此可以被单元测试直接调用，也方便在第二阶段扩展新题型。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

END = "end"
QUESTION_TYPES = ("single", "multiple", "text", "number")

INT_RE = re.compile(r"^[+-]?\d+$")


class ValidationError(ValueError):
    """单题答案不合法。"""


@dataclass
class PathResult:
    path: list[str] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def answer_docs(self, meta: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """转换成 responses.answers 的嵌入文档结构。

        `meta` 提供每道题的 lineage_id / version，写进答案里供跨问卷统计使用；
        不传时退化为第一阶段的结构，保持向后兼容。
        """
        docs: list[dict[str, Any]] = []
        for qid, value in self.values.items():
            if value is None:
                continue
            doc: dict[str, Any] = {"question_id": qid, "value": value}
            extra = (meta or {}).get(qid) or {}
            if extra.get("lineage_id"):
                doc["lineage_id"] = str(extra["lineage_id"])
            if extra.get("version"):
                doc["version"] = int(extra["version"])
            docs.append(doc)
        return docs


def is_empty(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, str):
        return raw.strip() == ""
    if isinstance(raw, (list, tuple, dict)):
        return len(raw) == 0
    return False


def _option_keys(question: dict) -> set[str]:
    return {str(opt.get("key")) for opt in question.get("options") or []}


def validate_answer(question: dict, raw: Any) -> Any:
    """校验单题答案，返回规范化后的值；不合法时抛 ValidationError。"""
    qtype = question.get("type")
    rules = question.get("rules") or {}

    if is_empty(raw):
        if question.get("required"):
            raise ValidationError("本题为必答题")
        return None

    if qtype == "single":
        if not isinstance(raw, str):
            raise ValidationError("单选题只能提交一个选项")
        if raw not in _option_keys(question):
            raise ValidationError(f"选项 {raw!r} 不存在")
        return raw

    if qtype == "multiple":
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            raise ValidationError("多选题必须提交选项列表")
        keys = _option_keys(question)
        picked: list[str] = []
        for item in raw:
            if not isinstance(item, str) or item not in keys:
                raise ValidationError(f"选项 {item!r} 不存在")
            if item in picked:
                raise ValidationError(f"选项 {item} 重复提交")
            picked.append(item)

        count = len(picked)
        exact = rules.get("exact_choices")
        low = rules.get("min_choices")
        high = rules.get("max_choices")
        if exact is not None and count != exact:
            raise ValidationError(f"必须选择 {exact} 项，当前选了 {count} 项")
        if low is not None and count < low:
            raise ValidationError(f"至少选择 {low} 项，当前选了 {count} 项")
        if high is not None and count > high:
            raise ValidationError(f"最多选择 {high} 项，当前选了 {count} 项")
        return picked

    if qtype == "text":
        if not isinstance(raw, str):
            raise ValidationError("填空题必须提交文本")
        length = len(raw)
        low = rules.get("min_length")
        high = rules.get("max_length")
        if low is not None and length < low:
            raise ValidationError(f"至少输入 {low} 个字，当前 {length} 个字")
        if high is not None and length > high:
            raise ValidationError(f"最多输入 {high} 个字，当前 {length} 个字")
        return raw

    if qtype == "number":
        number = _to_number(raw, bool(rules.get("integer")))
        low = rules.get("min_value")
        high = rules.get("max_value")
        if low is not None and number < low:
            raise ValidationError(f"不能小于 {low}，当前为 {number}")
        if high is not None and number > high:
            raise ValidationError(f"不能大于 {high}，当前为 {number}")
        return number

    raise ValidationError(f"未知题型 {qtype!r}")


def _to_number(raw: Any, integer: bool) -> float | int:
    if isinstance(raw, bool):
        raise ValidationError("必须为数字")
    if isinstance(raw, str):
        text = raw.strip()
        if integer:
            if not INT_RE.match(text):
                raise ValidationError("必须为整数")
            return int(text)
        try:
            number = float(text)
        except ValueError as exc:
            raise ValidationError("必须为数字") from exc
    elif isinstance(raw, (int, float)):
        number = raw
    else:
        raise ValidationError("必须为数字")

    if isinstance(number, float) and not math.isfinite(number):
        raise ValidationError("必须为有限数字")
    if integer:
        if isinstance(number, float):
            if not number.is_integer():
                raise ValidationError("必须为整数")
            return int(number)
        return int(number)
    return float(number)


def _compare(left: float, op: str | None, right: float | None) -> bool:
    if op is None or right is None:
        return False
    if op == "eq":
        return left == right
    if op == "neq":
        return left != right
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    return False


def match_condition(condition: dict, value: Any, qtype: str) -> bool:
    """判断某个答案是否满足一条跳转条件。"""
    if value is None or not condition:
        return False

    if qtype == "single":
        return condition.get("option") == value

    if qtype == "multiple":
        picked = set(value or [])
        if "contains_any" in condition:
            return bool(picked & set(condition["contains_any"]))
        if "contains_all" in condition:
            return set(condition["contains_all"]).issubset(picked)
        if "count" in condition:
            spec = condition["count"] or {}
            return _compare(len(picked), spec.get("op"), spec.get("value"))
        return False

    if qtype == "number":
        op = condition.get("op")
        if op == "between":
            low = condition.get("value")
            high = condition.get("value2")
            return low is not None and high is not None and low <= value <= high
        return _compare(value, op, condition.get("value"))

    if qtype == "text":
        if "equals" in condition:
            return value == condition["equals"]
        return False

    return False


def resolve_target(question: dict, value: Any, next_question_id: str | None) -> str:
    """按跳转规则算出下一题的 id（或 END）。"""
    logic = question.get("logic") or {}
    for jump in logic.get("jumps") or []:
        target = jump.get("target")
        if not target:
            continue
        if match_condition(jump.get("when") or {}, value, question.get("type")):
            return str(target)
    if logic.get("default_next") == "end":
        return END
    return str(next_question_id) if next_question_id else END


def build_path(questions: list[dict], answers: dict[str, Any]) -> PathResult:
    """按题目顺序 + 跳转规则走一遍问卷，返回实际路径、规范化答案和错误。

    这是服务端的唯一真相来源：
    - 逐题判断「这题该不该出现」，未出现的题不允许有答案；
    - 每次提交都重新走一遍，因此客户端无法通过伪造顺序绕过必答或跳过校验。
    """
    result = PathResult()
    ordered = sorted(questions, key=lambda q: q.get("order", 0))
    if not ordered:
        return result

    by_id = {str(q["_id"]): q for q in ordered}
    index_of = {str(q["_id"]): i for i, q in enumerate(ordered)}
    submitted = {str(k): v for k, v in (answers or {}).items()}

    unknown = [qid for qid in submitted if qid not in by_id]
    for qid in unknown:
        result.errors.append({"question_id": qid, "message": "题目不存在或不属于本问卷"})
    if result.errors:
        return result

    current: str | None = str(ordered[0]["_id"])
    visited: set[str] = set()

    while current and current != END:
        if current in visited:
            result.errors.append({"question_id": current, "message": "跳转规则形成环路，无法继续"})
            break
        question = by_id.get(current)
        if question is None:
            result.errors.append({"question_id": current, "message": "跳转目标题目不存在"})
            break

        visited.add(current)
        result.path.append(current)

        value: Any = None
        if current in submitted:
            try:
                value = validate_answer(question, submitted[current])
            except ValidationError as exc:
                result.errors.append({"question_id": current, "message": str(exc)})
                break
        elif question.get("required"):
            result.errors.append({"question_id": current, "message": "本题为必答题"})
            break

        result.values[current] = value

        position = index_of[current]
        following = (
            str(ordered[position + 1]["_id"]) if position + 1 < len(ordered) else None
        )
        target = resolve_target(question, value, following)
        if target != END and target not in by_id:
            result.errors.append({"question_id": current, "message": "跳转目标题目不存在"})
            break
        current = None if target == END else target

        if len(result.path) > len(ordered) + 1:
            result.errors.append({"question_id": current or "", "message": "跳转规则形成环路，无法继续"})
            break

    if result.errors:
        return result

    skipped = [qid for qid in submitted if qid not in result.path]
    for qid in skipped:
        result.errors.append(
            {"question_id": qid, "message": "该题已被跳转规则跳过，不应提交答案"}
        )

    return result


def next_question(
    questions: list[dict], answers: dict[str, Any]
) -> tuple[dict | None, list[dict[str, str]], bool]:
    """填写过程中的「下一题」计算。

    与 build_path 的区别：遇到第一道尚未作答的题就停下来并把它返回，
    供前端渲染；只有走完整条路径才认为问卷已答完。
    返回 (下一题, 错误列表, 是否已答完)。
    """
    ordered = sorted(questions, key=lambda q: q.get("order", 0))
    if not ordered:
        return None, [], True

    by_id = {str(q["_id"]): q for q in ordered}
    index_of = {str(q["_id"]): i for i, q in enumerate(ordered)}
    submitted = {str(k): v for k, v in (answers or {}).items()}

    for qid in submitted:
        if qid not in by_id:
            return None, [{"question_id": qid, "message": "题目不存在或不属于本问卷"}], False

    current: str | None = str(ordered[0]["_id"])
    visited: set[str] = set()

    while current and current != END:
        if current in visited:
            return None, [{"question_id": current, "message": "跳转规则形成环路，无法继续"}], False
        question = by_id.get(current)
        if question is None:
            return None, [{"question_id": current, "message": "跳转目标题目不存在"}], False

        visited.add(current)
        if current not in submitted:
            return question, [], False

        try:
            value = validate_answer(question, submitted[current])
        except ValidationError as exc:
            return None, [{"question_id": current, "message": str(exc)}], False

        position = index_of[current]
        following = str(ordered[position + 1]["_id"]) if position + 1 < len(ordered) else None
        target = resolve_target(question, value, following)
        if target != END and target not in by_id:
            return None, [{"question_id": current, "message": "跳转目标题目不存在"}], False
        current = None if target == END else target

        if len(visited) > len(ordered) + 1:
            return None, [{"question_id": current or "", "message": "跳转规则形成环路，无法继续"}], False

    return None, [], True


def describe_condition(question: dict, condition: dict) -> str:
    """把跳转条件渲染成中文，用于界面展示与文档。"""
    qtype = question.get("type")
    labels = {str(o.get("key")): o.get("label", str(o.get("key"))) for o in question.get("options") or []}

    if qtype == "single":
        key = condition.get("option")
        return f"选择「{labels.get(str(key), key)}」"
    if qtype == "multiple":
        if "contains_any" in condition:
            names = "、".join(labels.get(str(k), str(k)) for k in condition["contains_any"])
            return f"选中任意一项：{names}"
        if "contains_all" in condition:
            names = "、".join(labels.get(str(k), str(k)) for k in condition["contains_all"])
            return f"同时选中：{names}"
        if "count" in condition:
            spec = condition["count"] or {}
            op = {"eq": "等于", "neq": "不等于", "gt": "大于", "gte": "不少于",
                  "lt": "小于", "lte": "不多于"}.get(spec.get("op"), spec.get("op"))
            return f"选中数量{op} {spec.get('value')} 项"
        return "多选题条件"
    if qtype == "number":
        op = {"eq": "等于", "neq": "不等于", "gt": "大于", "gte": "不小于",
              "lt": "小于", "lte": "不大于"}.get(condition.get("op"), condition.get("op"))
        if condition.get("op") == "between":
            return f"数值在 {condition.get('value')} ~ {condition.get('value2')} 之间"
        return f"数值{op} {condition.get('value')}"
    if qtype == "text":
        return f"内容等于「{condition.get('equals')}」"
    return "条件"
