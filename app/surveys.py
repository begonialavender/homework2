"""问卷管理：列表、创建、设置、题目增删改、发布与关闭。

第二阶段起，题目不再只是问卷的附属物：
- 每建一道题，同时在题库 `question_bank` 里生成它的 v1；
- 从题库选题时，把某个版本的内容**快照**进问卷；
- 问卷一旦发布，题目被冻结，改题只能「基于原版本生成新版本」，
  因此旧问卷、旧回答永远对得上；
- 想改一份已发布的问卷，用「复制为新草稿」另开一份，旧问卷原样保留。
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, url_for,
)

from . import versioning
from .logic import QUESTION_TYPES
from .question_form import build_question, choice_options
from .utils import (
    current_user, get_db, load_questions, login_required, new_slug, now,
    owned_survey_or_404, parse_datetime_local, parse_float, parse_int, to_object_id,
)

bp = Blueprint("surveys", __name__)

# 兼容第一阶段模块里的私有名字，旧代码/测试仍可照常调用。
_choice_options = choice_options
_build_question = build_question


def _is_frozen(survey: dict) -> bool:
    """已发布 / 已关闭的问卷，题目结构冻结。"""
    return survey.get("status") != "draft"


def _reject_frozen(survey: dict, sid: str, action: str):
    flash(
        f"问卷已发布，题目已冻结，不能{action}。"
        "如需改动，请用「复制为新草稿」另开一份问卷。",
        "error",
    )
    return redirect(url_for("surveys.edit", sid=sid))


def _content_equal(a: dict, b: dict) -> bool:
    return all((a.get(key) or None) == (b.get(key) or None) for key in versioning.CONTENT_FIELDS)


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
        frozen=_is_frozen(survey),
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
    # 冻结：题目内容与结构从此固定，题库里再改也影响不到这份问卷。
    db.questions.update_many({"survey_id": survey["_id"]}, {"$set": {"frozen": True}})
    flash("问卷已发布，题目已冻结；填写链接可以发出去了", "ok")
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
    if _is_frozen(survey):
        return _reject_frozen(survey, sid, "再添加题目")

    doc, error = _build_question(request.form)
    if error:
        flash(error, "error")
        return redirect(url_for("surveys.edit", sid=sid))

    db = get_db()
    limit = current_app.config.get("MAX_QUESTIONS_PER_SURVEY", 50)
    if db.questions.count_documents({"survey_id": survey["_id"]}) >= limit:
        flash(f"单份问卷最多 {limit} 道题", "error")
        return redirect(url_for("surveys.edit", sid=sid))

    # 新建题同时进题库：内容是 v1，问卷持有它的快照。
    version = versioning.create_version(
        db,
        doc,
        owner_id=current_user()["_id"],
        change_note=f"在问卷《{survey['title']}》中新建",
    )

    last = db.questions.find_one({"survey_id": survey["_id"]}, sort=[("order", -1)])
    order = (last["order"] + 1) if last else 0
    doc.update(versioning.snapshot_fields(version))
    doc.update(
        {
            "survey_id": survey["_id"],
            "order": order,
            "logic": {"default_next": "next", "jumps": []},
        }
    )
    db.questions.insert_one(doc)
    db.surveys.update_one({"_id": survey["_id"]}, {"$inc": {"question_count": 1}})
    flash(f"题目已添加（题库版本 v{version['version']}）", "ok")
    return redirect(url_for("surveys.edit", sid=sid))


@bp.get("/surveys/<sid>/from-bank")
@login_required
def from_bank(sid: str):
    """从题库选题：我的题 + 别人共享给我的题。"""
    survey = owned_survey_or_404(sid)
    if _is_frozen(survey):
        return _reject_frozen(survey, sid, "再从题库选题")

    db = get_db()
    user = current_user()
    heads = versioning.visible_versions(db, user)
    chains = {head_doc["lineage_id"]: versioning.versions(db, head_doc["lineage_id"]) for head_doc in heads}
    owners = {
        item["owner_id"]
        for chain in chains.values()
        for item in chain
        if item.get("owner_id")
    }
    names = {
        item["_id"]: item["username"]
        for item in db.users.find({"_id": {"$in": list(owners)}}, {"username": 1})
    } if owners else {}
    banks = list(
        db.banks.find(
            {
                "$or": [
                    {"owner_id": user["_id"]},
                    {"visibility": "shared", "shared_with": []},
                    {"shared_with": user["_id"]},
                ]
            }
        ).sort("created_at", -1)
    )
    bank_names = {bank["_id"]: bank["name"] for bank in banks}
    return render_template(
        "survey_from_bank.html",
        survey=survey,
        heads=heads,
        chains=chains,
        bank_names=bank_names,
        owners=names,
        qtype_labels=None,
    )


@bp.post("/surveys/<sid>/questions/from-bank")
@login_required
def add_from_bank(sid: str):
    survey = owned_survey_or_404(sid)
    if _is_frozen(survey):
        return _reject_frozen(survey, sid, "从题库选题")

    db = get_db()
    user = current_user()
    version = versioning.by_id(db, request.form.get("version_id"))
    if version is None or not versioning.can_view(version, user):
        abort(404)

    limit = current_app.config.get("MAX_QUESTIONS_PER_SURVEY", 50)
    if db.questions.count_documents({"survey_id": survey["_id"]}) >= limit:
        flash(f"单份问卷最多 {limit} 道题", "error")
        return redirect(url_for("surveys.from_bank", sid=sid))

    last = db.questions.find_one({"survey_id": survey["_id"]}, sort=[("order", -1)])
    order = (last["order"] + 1) if last else 0
    doc = versioning.content_of(version)
    doc.pop("tags", None)
    doc.update(versioning.snapshot_fields(version))
    doc.update(
        {
            "survey_id": survey["_id"],
            "order": order,
            "logic": {"default_next": "next", "jumps": []},
        }
    )
    db.questions.insert_one(doc)
    db.surveys.update_one({"_id": survey["_id"]}, {"$inc": {"question_count": 1}})
    flash(f"已从题库选用「{version['title'][:24]}」v{version['version']}", "ok")
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
        if _is_frozen(survey):
            return _reject_frozen(survey, sid, "修改题目")

        doc, error = _build_question(request.form)
        if error:
            flash(error, "error")
            return redirect(url_for("surveys.edit_question", sid=sid, qid=qid))

        content = versioning.content_of(doc)
        base_version = versioning.by_id(db, question.get("bank_version_id"))
        changed = False
        if base_version is None:
            # 迁移未覆盖到的旧数据：先按当前内容补一个 v1，再接受这次修改。
            base_version = versioning.create_version(
                db,
                content,
                owner_id=survey["owner_id"],
                change_note="迁移补齐：问卷内原始题目",
            )
        elif not _content_equal(content, base_version):
            # 关键动作：改题不覆盖旧版本，而是生成一个新版本。
            new_version = versioning.fork_version(
                db,
                base_version,
                content,
                created_by=current_user()["_id"],
                change_note=(request.form.get("change_note") or "").strip()
                or f"在问卷《{survey['title']}》中修改",
            )
            base_version = new_version
            changed = True

        doc.update(versioning.snapshot_fields(base_version))
        doc["logic"] = _build_logic(request.form, question, questions)
        db.questions.update_one({"_id": question["_id"]}, {"$set": doc})
        if changed:
            flash(
                f"题目已更新，并生成题库版本 v{base_version['version']}；旧版本仍保留在题库里",
                "ok",
            )
        else:
            flash("题目已更新（内容没有变化）", "ok")
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
        frozen=_is_frozen(survey),
        current_version=versioning.by_id(db, question.get("bank_version_id")),
        lineage_versions=(
            versioning.versions(db, question["lineage_id"]) if question.get("lineage_id") else []
        ),
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
    if _is_frozen(survey):
        return _reject_frozen(survey, sid, "删除题目")
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
    if _is_frozen(survey):
        return _reject_frozen(survey, sid, "调整题目顺序")
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


@bp.post("/surveys/<sid>/duplicate")
@login_required
def duplicate(sid: str):
    """把一份问卷复制成新草稿。旧问卷（含已发布、已收集的回答）原样不动。"""
    survey = owned_survey_or_404(sid)
    db = get_db()
    user = current_user()

    new_doc = {
        "slug": new_slug(db),
        "owner_id": user["_id"],
        "title": f"{survey['title']}（副本）"[:200],
        "description": survey.get("description"),
        "allow_anonymous": bool(survey.get("allow_anonymous")),
        "allow_multiple": bool(survey.get("allow_multiple")),
        "status": "draft",
        "deadline": survey.get("deadline"),
        "created_at": now(),
        "published_at": None,
        "closed_at": None,
        "question_count": 0,
        "copied_from": survey["_id"],
    }
    new_id = db.surveys.insert_one(new_doc).inserted_id

    mapping: dict[str, str] = {}
    created: list[tuple[ObjectId, dict]] = []
    for question in load_questions(survey["_id"]):
        copy = {key: question.get(key) for key in versioning.CONTENT_FIELDS}
        for key in ("lineage_id", "bank_version_id", "version", "snapshot_at"):
            if question.get(key) is not None:
                copy[key] = question[key]
        copy.update(
            {
                "survey_id": new_id,
                "order": question.get("order", 0),
                "frozen": False,
                "logic": {"default_next": "next", "jumps": []},
            }
        )
        new_qid = db.questions.insert_one(copy).inserted_id
        mapping[str(question["_id"])] = str(new_qid)
        created.append((new_qid, question.get("logic") or {}))

    # 跳转目标指向的是旧问卷里的题目 id，必须重映射到副本里的新 id。
    for new_qid, logic in created:
        jumps = []
        for jump in logic.get("jumps") or []:
            target = str(jump.get("target"))
            if target == "end":
                jumps.append({"when": jump.get("when") or {}, "target": "end"})
            elif target in mapping:
                jumps.append({"when": jump.get("when") or {}, "target": mapping[target]})
        db.questions.update_one(
            {"_id": new_qid},
            {
                "$set": {
                    "logic": {
                        "default_next": "end" if logic.get("default_next") == "end" else "next",
                        "jumps": jumps,
                    }
                }
            },
        )

    db.surveys.update_one({"_id": new_id}, {"$set": {"question_count": len(created)}})
    flash(f"已复制为草稿《{new_doc['title']}》，原问卷与其回答不受影响", "ok")
    return redirect(url_for("surveys.edit", sid=new_id))


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
