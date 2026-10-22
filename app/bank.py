"""题库：题目版本、共享、题库分组、使用情况。

这一层是第二阶段新增的「题目的家」。三道防线保证需求里那句
「改题不要影响已经做好的问卷」：

1. 版本只增不改（`question_bank` 写入后内容字段不再变动）；
2. 问卷保存的是快照，发布后冻结；
3. 共享只影响「能不能被选」，不会改到别人问卷里的内容。
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for,
)

from . import versioning
from .question_form import build_question
from .utils import current_user, get_db, login_required, now, to_object_id

bp = Blueprint("bank", __name__)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _visible_banks(db, user: dict) -> list[dict]:
    return list(
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


def _chain_or_404(lineage: str) -> list[dict]:
    db = get_db()
    chain = versioning.versions(db, lineage)
    if not chain or not versioning.can_view(chain[-1], current_user()):
        abort(404)
    return chain


def _owner_or_404(lineage: str) -> dict:
    chain = _chain_or_404(lineage)
    if not versioning.can_manage(chain[-1], current_user()):
        abort(403)
    return chain[-1]


def _tag_list(raw: Any) -> list[str]:
    parts = [item.strip() for item in str(raw or "").replace("，", ",").split(",")]
    return [part[:20] for part in parts if part][:10]


def _resolve_users(db, raw: str) -> tuple[list[ObjectId], list[str]]:
    names = [name.strip() for name in str(raw or "").replace("，", ",").split(",") if name.strip()]
    if not names:
        return [], []
    found = list(db.users.find({"username": {"$in": names}}, {"username": 1}))
    ids = [user["_id"] for user in found]
    missing = sorted(set(names) - {user["username"] for user in found})
    return ids, missing


def _version_label(version: dict) -> str:
    return f"v{version['version']}"


# --------------------------------------------------------------------------- #
# 题库总览
# --------------------------------------------------------------------------- #

@bp.get("/bank")
@login_required
def index():
    db = get_db()
    user = current_user()
    banks = _visible_banks(db, user)
    bank_id = to_object_id(request.args.get("bank"))
    bank = next((item for item in banks if item["_id"] == bank_id), None)
    if bank_id and bank is None:
        abort(404)

    heads = versioning.visible_versions(db, user)
    if bank is not None:
        heads = [item for item in heads if bank["_id"] in (item.get("bank_ids") or [])]

    lineage_ids = [item["lineage_id"] for item in heads]
    version_counts: dict[str, int] = {}
    usage_counts: dict[str, int] = {}
    if lineage_ids:
        version_counts = {
            row["_id"]: row["count"]
            for row in db.question_bank.aggregate(
                [
                    {"$match": {"lineage_id": {"$in": lineage_ids}}},
                    {"$group": {"_id": "$lineage_id", "count": {"$sum": 1}}},
                ]
            )
        }
        usage_counts = {
            row["_id"]: row["count"]
            for row in db.questions.aggregate(
                [
                    {"$match": {"lineage_id": {"$in": lineage_ids}}},
                    {"$group": {"_id": "$lineage_id", "count": {"$sum": 1}}},
                ]
            )
        }

    owner_ids = {item["owner_id"] for item in heads}
    names = {
        item["_id"]: item["username"]
        for item in db.users.find({"_id": {"$in": list(owner_ids)}}, {"username": 1})
    } if owner_ids else {}

    bank_names = {item["_id"]: item["name"] for item in banks}
    # 每个题库里有多少道题（按 lineage 去重，不数版本）
    bank_counts = {
        row["_id"]: row["count"]
        for row in db.question_bank.aggregate(
            [
                {"$match": {"bank_ids": {"$ne": []}}},
                {"$unwind": "$bank_ids"},
                {"$group": {"_id": "$bank_ids", "lineages": {"$addToSet": "$lineage_id"}}},
                {"$project": {"count": {"$size": "$lineages"}}},
            ]
        )
    }
    shared_bank_names: list[str] = []
    if bank is not None and bank.get("shared_with"):
        shared_bank_names = [
            item["username"]
            for item in db.users.find({"_id": {"$in": bank["shared_with"]}}, {"username": 1})
        ]
    return render_template(
        "bank.html",
        banks=banks,
        bank=bank,
        heads=heads,
        version_counts=version_counts,
        usage_counts=usage_counts,
        bank_names=bank_names,
        bank_counts=bank_counts,
        shared_bank_names=shared_bank_names,
        owner_names=names,
        is_owner=lambda item: item["owner_id"] == user["_id"],
    )


@bp.post("/bank/new")
@login_required
def create_bank():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("请填写题库名称", "error")
        return redirect(url_for("bank.index"))
    if len(name) > 100:
        flash("题库名称过长（最多 100 字）", "error")
        return redirect(url_for("bank.index"))

    db = get_db()
    stamp = now()
    db.banks.insert_one(
        {
            "name": name,
            "description": (request.form.get("description") or "").strip() or None,
            "owner_id": current_user()["_id"],
            "visibility": "private",
            "shared_with": [],
            "created_at": stamp,
            "updated_at": stamp,
        }
    )
    flash(f"题库「{name}」已创建", "ok")
    return redirect(url_for("bank.index"))


@bp.post("/bank/<bid>/rename")
@login_required
def rename_bank(bid: str):
    db = get_db()
    bank = db.banks.find_one({"_id": to_object_id(bid)})
    if bank is None or bank["owner_id"] != current_user()["_id"]:
        abort(404)
    name = (request.form.get("name") or "").strip()
    if not name or len(name) > 100:
        flash("题库名称不能为空且不超过 100 字", "error")
        return redirect(url_for("bank.index", bank=bid))
    db.banks.update_one(
        {"_id": bank["_id"]},
        {
            "$set": {
                "name": name,
                "description": (request.form.get("description") or "").strip() or None,
                "updated_at": now(),
            }
        },
    )
    flash("题库信息已更新", "ok")
    return redirect(url_for("bank.index", bank=bid))


@bp.post("/bank/<bid>/delete")
@login_required
def delete_bank(bid: str):
    db = get_db()
    bank = db.banks.find_one({"_id": to_object_id(bid)})
    if bank is None or bank["owner_id"] != current_user()["_id"]:
        abort(404)
    # 只解散题库这个「分组」，题目本身留在 versioning 里，不删。
    db.banks.delete_one({"_id": bank["_id"]})
    db.question_bank.update_many(
        {"bank_ids": bank["_id"]}, {"$pull": {"bank_ids": bank["_id"]}}
    )
    flash(f"题库「{bank['name']}」已解散，题目本身仍然保留在题库列表里", "ok")
    return redirect(url_for("bank.index"))


@bp.post("/bank/<bid>/share")
@login_required
def share_bank(bid: str):
    db = get_db()
    bank = db.banks.find_one({"_id": to_object_id(bid)})
    if bank is None or bank["owner_id"] != current_user()["_id"]:
        abort(404)
    visibility = "shared" if request.form.get("visibility") == "shared" else "private"
    ids, missing = _resolve_users(db, request.form.get("shared_with", ""))
    db.banks.update_one(
        {"_id": bank["_id"]},
        {"$set": {"visibility": visibility, "shared_with": ids, "updated_at": now()}},
    )
    if missing:
        flash(f"以下用户不存在，已忽略：{'、'.join(missing)}", "error")
    else:
        flash("题库共享设置已保存", "ok")
    return redirect(url_for("bank.index", bank=bid))


@bp.post("/bank/<bid>/add")
@login_required
def add_to_bank(bid: str):
    db = get_db()
    bank = db.banks.find_one({"_id": to_object_id(bid)})
    if bank is None:
        abort(404)
    version = versioning.by_id(db, request.form.get("version_id"))
    if version is None or not versioning.can_view(version, current_user()):
        abort(404)
    db.question_bank.update_many(
        {"lineage_id": version["lineage_id"]}, {"$addToSet": {"bank_ids": bank["_id"]}}
    )
    flash(f"已把「{version['title'][:24]}」加入题库「{bank['name']}」", "ok")
    return redirect(request.form.get("next_url") or url_for("bank.question", lineage=version["lineage_id"]))


@bp.post("/bank/<bid>/remove")
@login_required
def remove_from_bank(bid: str):
    db = get_db()
    bank = db.banks.find_one({"_id": to_object_id(bid)})
    if bank is None:
        abort(404)
    version = versioning.by_id(db, request.form.get("version_id"))
    if version is None or not versioning.can_view(version, current_user()):
        abort(404)
    db.question_bank.update_many(
        {"lineage_id": version["lineage_id"]}, {"$pull": {"bank_ids": bank["_id"]}}
    )
    flash("已从题库移除（题目本身仍然保留）", "ok")
    return redirect(url_for("bank.question", lineage=version["lineage_id"]))


# --------------------------------------------------------------------------- #
# 题目：新建 / 详情 / 新版本 / 恢复 / 共享
# --------------------------------------------------------------------------- #

@bp.route("/bank/questions/new", methods=["GET", "POST"])
@login_required
def new_question():
    db = get_db()
    banks = _visible_banks(db, current_user())
    bank_id = to_object_id(request.args.get("bank") or request.form.get("bank_id"))

    if request.method == "POST":
        content, error = build_question(request.form)
        if error:
            flash(error, "error")
            return render_template(
                "bank_new_question.html", banks=banks, bank_id=bank_id, form=request.form
            ), 400

        content["tags"] = _tag_list(request.form.get("tags"))
        version = versioning.create_version(
            db,
            content,
            owner_id=current_user()["_id"],
            change_note=(request.form.get("change_note") or "").strip() or "创建题目",
            bank_ids=[bank_id] if bank_id else [],
        )
        flash(f"题目已保存到题库（v{version['version']}）", "ok")
        return redirect(url_for("bank.question", lineage=version["lineage_id"]))

    return render_template("bank_new_question.html", banks=banks, bank_id=bank_id, form={})


@bp.get("/bank/questions/<lineage>")
@login_required
def question(lineage: str):
    db = get_db()
    user = current_user()
    chain = _chain_or_404(lineage)
    head = chain[-1]
    version_no = request.args.get("version", type=int) or head["version"]
    current = next((item for item in chain if item["version"] == version_no), head)

    used = versioning.usage(db, lineage)
    banks = {item["_id"]: item for item in _visible_banks(db, user)}
    my_banks = [item for item in banks.values() if item["owner_id"] == user["_id"]]
    memberships = [banks[item] for item in current.get("bank_ids") or [] if item in banks]

    shared_names = [
        item["username"]
        for item in db.users.find({"_id": {"$in": current.get("shared_with") or []}}, {"username": 1})
    ]
    creator_names = {
        item["_id"]: item["username"]
        for item in db.users.find(
            {"_id": {"$in": [doc.get("created_by") for doc in chain if doc.get("created_by")]}},
            {"username": 1},
        )
    }
    owner = db.users.find_one({"_id": head["owner_id"]}, {"username": 1})
    return render_template(
        "bank_question.html",
        chain=chain,
        head=head,
        current=current,
        usage=used,
        memberships=memberships,
        my_banks=my_banks,
        shared_names=shared_names,
        creator_names=creator_names,
        owner_name=owner["username"] if owner else "—",
        can_manage=versioning.can_manage(head, user),
        is_shared_with=not versioning.is_owner(head, user),
    )


@bp.post("/bank/questions/<lineage>/versions")
@login_required
def new_version(lineage: str):
    db = get_db()
    user = current_user()
    head = _chain_or_404(lineage)[-1]
    content, error = build_question(request.form)
    if error:
        flash(error, "error")
        return redirect(url_for("bank.question", lineage=lineage))

    content["tags"] = _tag_list(request.form.get("tags")) or head.get("tags") or []
    try:
        version = versioning.fork_version(
            db,
            head,
            content,
            created_by=user["_id"],
            change_note=(request.form.get("change_note") or "").strip() or None,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("bank.question", lineage=lineage))

    flash(
        f"已生成 v{version['version']}；旧版本仍完整保留，使用旧版本的问卷不受影响",
        "ok",
    )
    return redirect(url_for("bank.question", lineage=lineage))


@bp.post("/bank/questions/<lineage>/versions/<int:version_no>/restore")
@login_required
def restore(lineage: str, version_no: int):
    db = get_db()
    user = current_user()
    chain = _chain_or_404(lineage)
    source = next((item for item in chain if item["version"] == version_no), None)
    if source is None:
        abort(404)
    restored = versioning.restore_version(
        db,
        source,
        created_by=user["_id"],
        change_note=(request.form.get("change_note") or "").strip() or None,
    )
    flash(
        f"已按 v{version_no} 的内容生成 v{restored['version']}（历史版本一律不改动）",
        "ok",
    )
    return redirect(url_for("bank.question", lineage=lineage))


@bp.post("/bank/questions/<lineage>/share")
@login_required
def share(lineage: str):
    db = get_db()
    head = _owner_or_404(lineage)
    visibility = "shared" if request.form.get("visibility") == "shared" else "private"
    ids, missing = _resolve_users(db, request.form.get("shared_with", ""))

    # 共享是「题」的属性，不是某一个版本的属性：整条链一起改。
    db.question_bank.update_many(
        {"lineage_id": lineage},
        {"$set": {"visibility": visibility, "shared_with": ids}},
    )
    if missing:
        flash(f"以下用户不存在，已忽略：{'、'.join(missing)}", "error")
    elif visibility == "shared" and ids:
        flash(f"已共享给 {len(ids)} 位用户；他们可以在自己的问卷里选用这道题", "ok")
    elif visibility == "shared":
        flash("已设为公开：所有登录用户都能在题库里看到并选用这道题", "ok")
    else:
        flash("已取消共享，题目恢复为仅自己可见", "ok")
    return redirect(url_for("bank.question", lineage=lineage))


@bp.get("/bank/questions/<lineage>/usage")
@login_required
def usage(lineage: str):
    db = get_db()
    chain = _chain_or_404(lineage)
    head = chain[-1]
    return render_template(
        "bank_usage.html",
        chain=chain,
        head=head,
        usage=versioning.usage(db, lineage),
    )
