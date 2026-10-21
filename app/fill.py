"""填写问卷：页面 + 逐步作答 / 提交的 JSON 接口。

填写流程刻意做成「服务端驱动」：
前端每答完一题就把答案回传，由服务端用同一套跳转引擎算出下一题，
客户端拿不到也可以伪造跳转顺序；最终提交时服务端还会从头重走一遍路径。
"""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, render_template, request

from .logic import build_path, next_question
from .utils import (
    api_login_required, current_user, get_db, is_open, load_questions,
    login_required, normalise_answers, now,
)

bp = Blueprint("fill", __name__)


def _survey_by_slug(slug: str) -> dict:
    survey = get_db().surveys.find_one({"slug": slug})
    if survey is None:
        abort(404)
    return survey


def _public_question(question: dict | None, total: int) -> dict | None:
    if question is None:
        return None
    return {
        "id": str(question["_id"]),
        "index": question.get("order", 0) + 1,
        "total": total,
        "type": question["type"],
        "title": question["title"],
        "required": bool(question.get("required")),
        "options": question.get("options") or [],
        "rules": question.get("rules") or {},
    }


@bp.get("/survey/<slug>")
@login_required
def show(slug: str):
    survey = _survey_by_slug(slug)
    ok, reason = is_open(survey)
    if not ok:
        return render_template("survey_closed.html", survey=survey, reason=reason), 403

    user = current_user()
    questions = load_questions(survey["_id"])
    if not questions:
        return render_template("survey_closed.html", survey=survey, reason="该问卷还没有题目"), 403

    if not survey.get("allow_multiple") and not survey.get("allow_anonymous"):
        already = get_db().responses.find_one(
            {"survey_id": survey["_id"], "respondent_id": user["_id"]}, {"_id": 1}
        )
        if already:
            return render_template(
                "survey_closed.html", survey=survey, reason="你已经提交过这份问卷，感谢参与"
            )

    first, _, _ = next_question(questions, {})
    return render_template(
        "fill.html",
        survey=survey,
        first_question=_public_question(first, len(questions)),
        total=len(questions),
    )


@bp.post("/api/surveys/<slug>/answer")
@api_login_required
def answer(slug: str):
    survey = _survey_by_slug(slug)
    ok, reason = is_open(survey)
    if not ok:
        return {"ok": False, "error": reason}, 403

    payload = request.get_json(silent=True) or {}
    question_id = str(payload.get("question_id") or "")
    answers = normalise_answers(payload.get("answers"))
    if not question_id:
        return {"ok": False, "error": "缺少 question_id"}, 400

    questions = load_questions(survey["_id"])
    if question_id not in {str(q["_id"]) for q in questions}:
        return {"ok": False, "error": "题目不属于本问卷"}, 404

    answers[question_id] = payload.get("value")
    nxt, errors, finished = next_question(questions, answers)

    if errors:
        return {"ok": False, "errors": errors, "error": errors[0]["message"]}, 400

    return {
        "ok": True,
        "finished": finished,
        "next": _public_question(nxt, len(questions)),
        "answered": len([v for v in answers.values() if v not in (None, "", [])]),
    }


@bp.post("/api/surveys/<slug>/submit")
@api_login_required
def submit(slug: str):
    survey = _survey_by_slug(slug)
    ok, reason = is_open(survey)
    if not ok:
        return {"ok": False, "error": reason}, 403

    payload = request.get_json(silent=True) or {}
    answers = normalise_answers(payload.get("answers"))

    user = current_user()
    db = get_db()
    anonymous = bool(survey.get("allow_anonymous"))
    if not survey.get("allow_multiple") and not anonymous:
        exists = db.responses.find_one(
            {"survey_id": survey["_id"], "respondent_id": user["_id"]}, {"_id": 1}
        )
        if exists:
            return {"ok": False, "error": "你已经提交过这份问卷"}, 409

    questions = load_questions(survey["_id"])
    result = build_path(questions, answers)
    if not result.ok:
        return {
            "ok": False,
            "error": result.errors[0]["message"],
            "errors": result.errors,
        }, 400

    doc = {
        "survey_id": survey["_id"],
        "respondent_id": None if anonymous else user["_id"],
        "anonymous": anonymous,
        "submitted_at": now(),
        "survey_version": 1,
        "path": result.path,
        "answers": result.answer_docs(),
    }
    inserted = db.responses.insert_one(doc)
    return {"ok": True, "response_id": str(inserted.inserted_id)}


@bp.get("/survey/<slug>/done")
@login_required
def done(slug: str):
    survey = _survey_by_slug(slug)
    return render_template("survey_done.html", survey=survey)
