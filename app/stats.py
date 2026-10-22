"""统计：用聚合管道在数据库侧完成分组，Python 只做展示层的补零与排序。

第二阶段新增「跨问卷单题统计」：同一道题（同一 lineage_id）在多个问卷里被使用时，
把它们的回答合并统计。聚合键是落在 `responses.answers` 里的 lineage_id，
所以不需要先把各问卷的题目拼起来。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, render_template

from . import versioning
from .utils import (
    current_user, get_db, load_questions, login_required, owned_survey_or_404, to_object_id,
)

bp = Blueprint("stats", __name__)


def _base_match(survey_id, question_id: str) -> list[dict[str, Any]]:
    return [
        {"$match": {"survey_id": survey_id}},
        {"$unwind": "$answers"},
        {"$match": {"answers.question_id": question_id}},
    ]


def question_stats(survey: dict, question: dict) -> dict[str, Any]:
    """单题统计。返回的结构按题型不同，模板按 type 分支渲染。"""
    db = get_db()
    qid = str(question["_id"])
    survey_id = survey["_id"]
    qtype = question["type"]

    answered = db.responses.count_documents(
        {"survey_id": survey_id, "answers.question_id": qid}
    )
    data: dict[str, Any] = {"answered": answered, "type": qtype}

    if qtype == "single":
        pipeline = _base_match(survey_id, qid) + [
            {"$group": {"_id": "$answers.value", "count": {"$sum": 1}}},
        ]
        rows = {row["_id"]: row["count"] for row in db.responses.aggregate(pipeline)}
        options = [
            {
                "key": opt["key"],
                "label": opt["label"],
                "count": rows.get(opt["key"], 0),
                "percent": round(rows.get(opt["key"], 0) * 100 / answered, 1) if answered else 0.0,
            }
            for opt in question.get("options") or []
        ]
        data["options"] = options

    elif qtype == "multiple":
        pipeline = _base_match(survey_id, qid) + [
            {"$unwind": "$answers.value"},
            {"$group": {"_id": "$answers.value", "count": {"$sum": 1}}},
        ]
        rows = {row["_id"]: row["count"] for row in db.responses.aggregate(pipeline)}
        options = [
            {
                "key": opt["key"],
                "label": opt["label"],
                "count": rows.get(opt["key"], 0),
                "percent": round(rows.get(opt["key"], 0) * 100 / answered, 1) if answered else 0.0,
            }
            for opt in question.get("options") or []
        ]
        data["options"] = options

    elif qtype == "number":
        pipeline = _base_match(survey_id, qid) + [
            {
                "$group": {
                    "_id": None,
                    "avg": {"$avg": "$answers.value"},
                    "min": {"$min": "$answers.value"},
                    "max": {"$max": "$answers.value"},
                    "count": {"$sum": 1},
                }
            }
        ]
        rows = list(db.responses.aggregate(pipeline))
        stats = rows[0] if rows else {}
        values: list[Any] = []
        for doc in db.responses.find(
            {"survey_id": survey_id, "answers.question_id": qid},
            {"answers": 1},
        ):
            value = next(
                (a["value"] for a in doc.get("answers", []) if a["question_id"] == qid), None
            )
            if isinstance(value, (int, float)):
                values.append(value)
        values.sort()
        data.update(
            {
                "average": round(stats["avg"], 2) if stats.get("avg") is not None else None,
                "minimum": stats.get("min"),
                "maximum": stats.get("max"),
                "values": values,
            }
        )

    else:  # text
        docs = db.responses.find(
            {"survey_id": survey_id, "answers.question_id": qid},
            {"answers": 1, "submitted_at": 1},
        ).sort("submitted_at", -1)
        entries = []
        for doc in docs:
            value = next((a["value"] for a in doc["answers"] if a["question_id"] == qid), None)
            if value is not None:
                entries.append({"value": value, "submitted_at": doc.get("submitted_at")})
        data["entries"] = entries
        data["lengths"] = [len(str(e["value"])) for e in entries]
        data["average_length"] = (
            round(sum(data["lengths"]) / len(data["lengths"]), 1) if data["lengths"] else None
        )

    return data


@bp.get("/surveys/<sid>/stats")
@login_required
def overview(sid: str):
    survey = owned_survey_or_404(sid)
    db = get_db()
    questions = load_questions(survey["_id"])
    rows = [(q, question_stats(survey, q)) for q in questions]
    total = db.responses.count_documents({"survey_id": survey["_id"]})
    return render_template(
        "stats.html",
        survey=survey,
        rows=rows,
        total_responses=total,
        single_question=None,
    )


@bp.get("/surveys/<sid>/stats/<qid>")
@login_required
def single(sid: str, qid: str):
    survey = owned_survey_or_404(sid)
    db = get_db()
    oid = to_object_id(qid)
    question = db.questions.find_one({"_id": oid, "survey_id": survey["_id"]}) if oid else None
    if question is None:
        abort(404)
    total = db.responses.count_documents({"survey_id": survey["_id"]})
    return render_template(
        "stats.html",
        survey=survey,
        rows=[(question, question_stats(survey, question))],
        total_responses=total,
        single_question=question,
    )


@bp.get("/stats/questions/<lineage>")
@login_required
def question_overview(lineage: str):
    """需求 8：一道题在所有问卷里的回答情况。

    能看到哪道题的统计，取决于题库里的可见性：自己的题、别人共享给
    自己的题、以及公开题。合并统计时不同版本会分开标注。
    """
    db = get_db()
    head = versioning.head(db, lineage)
    if head is None or not versioning.can_view(head, current_user()):
        abort(404)
    data = versioning.cross_survey_stats(db, lineage)
    return render_template("stats_question.html", data=data, head=head)
