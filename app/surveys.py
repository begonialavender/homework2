"""问卷管理：列表、创建、设置、题目增删改、发布与关闭。"""

from __future__ import annotations

import string
from typing import Any

from bson import ObjectId
from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, url_for,
)

from .logic import QUESTION_TYPES
from .utils import (
    get_db, load_questions, login_required, new_slug, now,
    owned_survey_or_404, parse_datetime_local, parse_float, parse_int, to_object_id,
)

bp = Blueprint("surveys", __name__)

OPTION_KEYS = list(string.ascii_uppercase)


def _choice_options(raw: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in (raw or "").splitlines()]
    labels = [line for line in lines if line]
    return [{"key": OPTION_KEYS[i], "label": label} for i, label in enumerate(labels)]


def _build_question(form) -> tuple[dict[str, Any] | None, str | None]:
    """把表单转成题目文档，顺带做配置合法性检查。"""
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
        options = _choice_options(form.get("options", ""))
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
        "logic": {"default_next": "next", "jumps": []},
    }, None


def _jump_summaries(questions: list[dict]) -> dict[str, dict[str, Any]]:
    """把每题的跳转规则渲染成中文摘要，供编辑页展示。"""
    from .logic import describe_condition

    labels = {str(q["_id"]): f"第 {q.get('order', 0) + 1} 题" for q in questions}
    summaries: dict[str, dict[str, Any]] = {}
    for q in questions:
        logic = q.get("logic") or {}
        rows = []
        for jump in logic.get("jumps") or []:
            target = str(jump.get("target"))
            rows.append(
                {
                    "condition": describe_condition(q, jump.get("when") or {}),
                    "target": "结束问卷" if target == "end" else labels.get(target, "已删除的题目"),
                }
            )
        summaries[str(q["_id"])] = {
            "rows": rows,
            "default_end": logic.get("default_next") == "end",
        }
    return summaries


def _target_options(questions: list[dict], question_id: str) -> list[dict[str, str]]:
    """当前题目可以跳转到的目标列表。"""
    return [
        {"value": str(q["_id"]), "label": f"第 {q.get('order', 0) + 1} 题 · {q['title'][:24]}"}
        for q in questions
        if str(q["_id"]) != question_id
    ]


@bp.get("/dashboard")
@login_required
def dashboard():
    from .utils import current_user

    db = get_db()
    user = current_user()
    surveys = list(db.surveys.find({"owner_id": user["_id"]}).sort("created_at", -1))
    counts: dict[ObjectId, int] = {}
    if surveys:
        pipeline = [
            {"$match": {"survey_id": {"$in": [s["_id"] for s in surveys]}}},
            {"$group": {"_id": "$survey_id", "count": {"$sum": 1}}},
        ]
        counts = {row["_id"]: row["count"] for row in db.responses.aggregate(pipeline)}
    return render_template("dashboard.html", surveys=surveys, counts=counts)


@bp.route("/surveys/new", methods=["GET", "POST"])
@login_required
def create():
    from .utils import current_user

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        if not title:
            flash("请填写问卷标题", "error")
            return render_template("survey_new.html", title=title, description=description), 400

        db = get_db()
        doc = {
            "slug": new_slug(db),
            "owner_id": current_user()["_id"],
            "title": title,
            "description": description or None,
            "allow_anonymous": request.form.get("allow_anonymous") == "on",
            "allow_multiple": request.form.get("allow_multiple") == "on",
            "status": "draft",
            "deadline": parse_datetime_local(request.form.get("deadline")),
            "created_at": now(),
            "published_at": None,
            "closed_at": None,
            "question_count": 0,
        }
        result = db.surveys.insert_one(doc)
        return redirect(url_for("surveys.edit", sid=result.inserted_id))

    return render_template("survey_new.html", title="", description="")


@bp.get("/surveys/<sid>")
@login_required
def edit(sid: str):
    db = get_db()
    survey = owned_survey_or_404(sid)
    questions = load_questions(survey["_id"])
    response_count = db.responses.count_documents({"survey_id": survey["_id"]})
    return render_template(
        "survey_edit.html",
        survey=survey,
        questions=questions,
        response_count=response_count,
        jump_summaries=_jump_summaries(questions),
    )


@bp.post("/surveys/<sid>/settings")
@login_required
def update_settings(sid: str):
    survey = owned_survey_or_404(sid)
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("问卷标题不能为空", "error")
        return redirect(url_for("surveys.edit", sid=sid))

    get_db().surveys.update_one(
        {"_id": survey["_id"]},
        {
            "$set": {
                "title": title,
                "description": (request.form.get("description") or "").strip() or None,
                "allow_anonymous": request.form.get("allow_anonymous") == "on",
                "allow_multiple": request.form.get("allow_multiple") == "on",
                "deadline": parse_datetime_local(request.form.get("deadline")),
            }
        },
    )
    flash("问卷设置已保存", "ok")
    return redirect(url_for("surveys.edit", sid=sid))


@bp.post("/surveys/<sid>/publish")
@login_required
def publish(sid: str):
    survey = owned_survey_or_404(sid)
    db = get_db()
    if db.questions.count_documents({"survey_id": survey["_id"]}) == 0:
        flash("至少添加一道题目后才能发布", "error")
        return redirect(url_for("surveys.edit", sid=sid))

    db.surveys.update_one(
        {"_id": survey["_id"]},
        {"$set": {"status": "published", "published_at": survey.get("published_at") or now(), "closed_at": None}},
    )
    flash("问卷已发布，可以把链接发给填写者了", "ok")
    return redirect(url_for("surveys.edit", sid=sid))


@bp.post("/surveys/<sid>/close")
@login_required
def close(sid: str):
    survey = owned_survey_or_404(sid)
    get_db().surveys.update_one(
        {"_id": survey["_id"]},
        {"$set": {"status": "closed", "closed_at": now()}},
    )
    flash("问卷已关闭，填写链接不再接受新的回答", "ok")
    return redirect(url_for("surveys.edit", sid=sid))


@bp.post("/surveys/<sid>/delete")
@login_required
def delete(sid: str):
    survey = owned_survey_or_404(sid)
    db = get_db()
    db.questions.delete_many({"survey_id": survey["_id"]})
    db.responses.delete_many({"survey_id": survey["_id"]})
    db.surveys.delete_one({"_id": survey["_id"]})
    flash("问卷及其题目、回答已删除", "ok")
    return redirect(url_for("surveys.dashboard"))


@bp.post("/surveys/<sid>/questions")
@login_required
def add_question(sid: str):
    survey = owned_survey_or_404(sid)
    doc, error = _build_question(request.form)
    if error:
        flash(error, "error")
        return redirect(url_for("surveys.edit", sid=sid))

    db = get_db()
    limit = current_app.config.get("MAX_QUESTIONS_PER_SURVEY", 50)
    if db.questions.count_documents({"survey_id": survey["_id"]}) >= limit:
        flash(f"单份问卷最多 {limit} 道题", "error")
        return redirect(url_for("surveys.edit", sid=sid))

    last = db.questions.find_one({"survey_id": survey["_id"]}, sort=[("order", -1)])
    order = (last["order"] + 1) if last else 0
    doc.update({"survey_id": survey["_id"], "order": order})
    db.questions.insert_one(doc)
    db.surveys.update_one({"_id": survey["_id"]}, {"$inc": {"question_count": 1}})
    flash("题目已添加", "ok")
    return redirect(url_for("surveys.edit", sid=sid))


@bp.route("/surveys/<sid>/questions/<qid>", methods=["GET", "POST"])
@login_required
def edit_question(sid: str, qid: str):
    survey = owned_survey_or_404(sid)
    db = get_db()
    qid_oid = to_object_id(qid)
    question = db.questions.find_one({"_id": qid_oid, "survey_id": survey["_id"]}) if qid_oid else None
    if question is None:
        abort(404)

    questions = load_questions(survey["_id"])

    if request.method == "POST":
        doc, error = _build_question(request.form)
        if error:
            flash(error, "error")
            return redirect(url_for("surveys.edit_question", sid=sid, qid=qid))

        doc["logic"] = _build_logic(request.form, question, questions)
        db.questions.update_one({"_id": question["_id"]}, {"$set": doc})
        flash("题目已更新", "ok")
        return redirect(url_for("surveys.edit", sid=sid))

    logic = question.get("logic") or {}
    jumps_by_option: dict[str, str] = {}
    number_jump: dict[str, Any] | None = None
    text_jump: dict[str, Any] | None = None
    for jump in logic.get("jumps") or []:
        condition = jump.get("when") or {}
        if "option" in condition:
            jumps_by_option[str(condition["option"])] = str(jump.get("target"))
        elif condition.get("op"):
            number_jump = {"condition": condition, "target": str(jump.get("target"))}
        elif "equals" in condition:
            text_jump = {"condition": condition, "target": str(jump.get("target"))}

    return render_template(
        "question_edit.html",
        survey=survey,
        question=question,
        questions=questions,
        options_text="\n".join(opt["label"] for opt in question.get("options") or []),
        target_options=_target_options(questions, str(question["_id"])),
        jumps_by_option=jumps_by_option,
        number_jump=number_jump,
        text_jump=text_jump,
        default_next=logic.get("default_next") or "next",
    )


def _build_logic(form, question: dict, questions: list[dict]) -> dict[str, Any]:
    """解析跳转规则表单：每行一个条件 → 目标题目。"""
    valid_targets = {str(q["_id"]) for q in questions}
    jumps: list[dict[str, Any]] = []
    qtype = question.get("type")

    def add(condition: dict, raw_target: str | None) -> None:
        target = (raw_target or "").strip()
        if not target or target == "next":
            return
        if target != "end" and target not in valid_targets:
            return
        if target == str(question["_id"]):
            return
        jumps.append({"when": condition, "target": target})

    if qtype in ("single", "multiple"):
        for option in question.get("options") or []:
            if qtype == "single":
                add({"option": option["key"]}, form.get(f"jump_{option['key']}"))
            else:
                add({"contains_any": [option["key"]]}, form.get(f"jump_{option['key']}"))

    if qtype == "number":
        op = form.get("number_op")
        value = parse_float(form.get("number_value"))
        if op and value is not None:
            condition: dict[str, Any] = {"op": op, "value": value}
            if op == "between":
                value2 = parse_float(form.get("number_value2"))
                if value2 is not None:
                    condition["value2"] = value2
                    add(condition, form.get("number_target"))
            else:
                add(condition, form.get("number_target"))

    if qtype == "text":
        text_equals = (form.get("text_equals") or "").strip()
        if text_equals:
            add({"equals": text_equals}, form.get("text_target"))

    default_next = form.get("default_next") or "next"
    return {"default_next": "end" if default_next == "end" else "next", "jumps": jumps}


@bp.post("/surveys/<sid>/questions/<qid>/delete")
@login_required
def delete_question(sid: str, qid: str):
    survey = owned_survey_or_404(sid)
    db = get_db()
    qid_oid = to_object_id(qid)
    result = db.questions.delete_one({"_id": qid_oid, "survey_id": survey["_id"]})
    if result.deleted_count:
        db.surveys.update_one({"_id": survey["_id"]}, {"$inc": {"question_count": -1}})
        # 清掉指向已删除题目的跳转，避免留下悬空目标
        survivors = load_questions(survey["_id"])
        valid = {str(q["_id"]) for q in survivors}
        for q in survivors:
            jumps = [j for j in (q.get("logic") or {}).get("jumps", []) if j.get("target") in valid or j.get("target") == "end"]
            if len(jumps) != len((q.get("logic") or {}).get("jumps", [])):
                db.questions.update_one({"_id": q["_id"]}, {"$set": {"logic.jumps": jumps}})
        flash("题目已删除", "ok")
    return redirect(url_for("surveys.edit", sid=sid))


@bp.post("/surveys/<sid>/questions/<qid>/move")
@login_required
def move_question(sid: str, qid: str):
    """上移 / 下移：与相邻题目交换 order，保证顺序连续。"""
    survey = owned_survey_or_404(sid)
    db = get_db()
    direction = request.form.get("direction")
    questions = load_questions(survey["_id"])
    index = next((i for i, q in enumerate(questions) if str(q["_id"]) == qid), None)
    if index is None:
        abort(404)
    swap = index - 1 if direction == "up" else index + 1
    if swap < 0 or swap >= len(questions):
        return redirect(url_for("surveys.edit", sid=sid))

    a, b = questions[index], questions[swap]
    db.questions.update_one({"_id": a["_id"]}, {"$set": {"order": swap}})
    db.questions.update_one({"_id": b["_id"]}, {"$set": {"order": index}})
    return redirect(url_for("surveys.edit", sid=sid))


@bp.get("/api/surveys/<sid>/questions")
@login_required
def api_questions(sid: str):
    """编辑页调试用：以 JSON 返回题目与跳转规则。"""
    survey = owned_survey_or_404(sid)
    questions = load_questions(survey["_id"])
    return jsonify(
        {
            "ok": True,
            "questions": [
                {
                    "id": str(q["_id"]),
                    "order": q["order"],
                    "type": q["type"],
                    "title": q["title"],
                    "required": q["required"],
                    "options": q.get("options", []),
                    "rules": q.get("rules", {}),
                    "logic": q.get("logic", {}),
                }
                for q in questions
            ],
        }
    )
