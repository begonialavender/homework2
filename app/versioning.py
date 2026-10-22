"""题目版本化服务：建版、分叉、恢复、可见性判断、使用情况与跨问卷统计。

设计原则（详见 docs/02-数据库设计.md 与 docs/04-关键逻辑说明.md）：

1. **版本不可变**。`question_bank` 里一条文档就是一个版本，写入后再也不改内容；
   要改题只能「基于某版生成新版本」，因此历史永远可回溯。
2. **一条版本链共用一个 `lineage_id`**。版本之间用 `parent_id` 串起来，
   分叉也是合法的（同一题的不同版本可以同时被不同问卷使用）。
3. **问卷用快照，不引用活文档**。题目进入问卷时把内容复制进 `questions`，
   已发布问卷的题目冻结，此后题库怎么改都不会动到旧问卷和旧回答。
4. **跨问卷统计认 lineage_id**。提交回答时把 lineage_id / version 一并写进
   `responses.answers`，统计只需一次聚合，不必回表拼接问卷。

本模块只依赖 pymongo 的 Database 对象，不依赖 Flask，便于直接单测。
"""

from __future__ import annotations

import secrets
from typing import Any, Iterable

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

# 题目「内容」字段：只有这些字段参与版本复制，问卷内的 order / 跳转规则不算内容。
CONTENT_FIELDS = ("type", "title", "options", "rules", "required")
MAX_NOTE = 200
MAX_VERSION_ATTEMPTS = 8


# --------------------------------------------------------------------------- #
# 基础取值
# --------------------------------------------------------------------------- #

def new_lineage_id(db) -> str:
    """生成一个不与现有版本链冲突的 lineage_id。"""
    while True:
        candidate = "Q" + secrets.token_hex(4).upper()
        if db.question_bank.find_one({"lineage_id": candidate}, {"_id": 1}) is None:
            return candidate


def content_of(doc: dict) -> dict[str, Any]:
    """从版本或问卷题目里取出内容字段。"""
    content: dict[str, Any] = {
        "type": doc.get("type"),
        "title": doc.get("title"),
        "options": list(doc.get("options") or []),
        "rules": dict(doc.get("rules") or {}),
        "required": bool(doc.get("required")),
    }
    if doc.get("tags") is not None:
        content["tags"] = list(doc.get("tags") or [])
    return content


def head(db, lineage_id: str) -> dict | None:
    """版本号最大的那一版（当前版本）。"""
    return db.question_bank.find_one(
        {"lineage_id": lineage_id}, sort=[("version", -1)]
    )


def versions(db, lineage_id: str) -> list[dict]:
    """整条版本链，按版本号升序。"""
    return list(db.question_bank.find({"lineage_id": lineage_id}).sort("version", 1))


def root(db, lineage_id: str) -> dict | None:
    return db.question_bank.find_one({"lineage_id": lineage_id}, sort=[("version", 1)])


def by_id(db, version_id: Any) -> dict | None:
    oid = version_id if isinstance(version_id, ObjectId) else _maybe_oid(version_id)
    if oid is None:
        return None
    return db.question_bank.find_one({"_id": oid})


def _maybe_oid(value: Any) -> ObjectId | None:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 建版
# --------------------------------------------------------------------------- #

def create_version(
    db,
    content: dict[str, Any],
    *,
    owner_id: ObjectId,
    created_by: ObjectId | None = None,
    lineage_id: str | None = None,
    parent_id: ObjectId | None = None,
    change_note: str | None = None,
    restored_from: ObjectId | None = None,
    visibility: str | None = None,
    shared_with: Iterable[ObjectId] | None = None,
    bank_ids: Iterable[ObjectId] | None = None,
    tags: Iterable[str] | None = None,
    archived: bool = False,
    now: Any = None,
) -> dict[str, Any]:
    """写入一个新版本。

    - `lineage_id` 为空表示开一条新链（v1）；
    - 否则版本号取当前最大值 +1，并用唯一索引兜底并发：撞号就重算重试。
    """
    from datetime import datetime, timezone

    stamp = now or datetime.now(timezone.utc)
    payload = content_of(content)
    if not payload["title"] or not payload["type"]:
        raise ValueError("题目内容不完整：缺少题型或题干")

    if lineage_id is None:
        lineage_id = new_lineage_id(db)
    else:
        base = root(db, lineage_id)
        if base is None:
            raise ValueError(f"版本链 {lineage_id} 不存在")
        # 题型是「这道题是什么」的身份，不许在版本间悄悄改掉。
        if base["type"] != payload["type"]:
            raise ValueError(
                f"同一道题的各版本题型必须一致（原为 {base['type']}，新为 {payload['type']}）"
            )

    for _attempt in range(MAX_VERSION_ATTEMPTS):
        version_no = _next_version_no(db, lineage_id)
        doc: dict[str, Any] = {
            "lineage_id": lineage_id,
            "version": version_no,
            "parent_id": parent_id,
            "root_id": None,
            "restored_from": restored_from,
            "change_note": (change_note or None) and str(change_note)[:MAX_NOTE],
            "owner_id": owner_id,
            "created_by": created_by or owner_id,
            "visibility": visibility or "private",
            "shared_with": list(shared_with or []),
            "bank_ids": list(bank_ids or []),
            "tags": [str(t)[:20] for t in (tags or [])][:10],
            "archived": bool(archived),
            "created_at": stamp,
            **payload,
        }
        try:
            inserted = db.question_bank.insert_one(doc)
        except DuplicateKeyError:
            continue  # 并发下被别人抢先占了版本号，重算
        doc["_id"] = inserted.inserted_id
        if version_no == 1:
            db.question_bank.update_one(
                {"_id": inserted.inserted_id}, {"$set": {"root_id": inserted.inserted_id}}
            )
            doc["root_id"] = inserted.inserted_id
        else:
            base = root(db, lineage_id)
            doc["root_id"] = base["_id"] if base else doc["_id"]
            db.question_bank.update_one(
                {"_id": inserted.inserted_id}, {"$set": {"root_id": doc["root_id"]}}
            )
        return doc

    raise RuntimeError("版本号分配冲突过多，请稍后重试")


def _next_version_no(db, lineage_id: str) -> int:
    current = db.question_bank.find_one(
        {"lineage_id": lineage_id}, sort=[("version", -1)], projection={"version": 1}
    )
    return (current["version"] + 1) if current else 1


def fork_version(
    db,
    parent: dict,
    content: dict[str, Any],
    *,
    created_by: ObjectId,
    change_note: str | None = None,
    inherit: bool = True,
) -> dict[str, Any]:
    """基于某一版生成新版本，默认继承链的所有者、共享设置与所属题库。"""
    return create_version(
        db,
        content,
        owner_id=parent["owner_id"],
        created_by=created_by,
        lineage_id=parent["lineage_id"],
        parent_id=parent["_id"],
        change_note=change_note,
        visibility=parent.get("visibility", "private") if inherit else "private",
        shared_with=parent.get("shared_with") or [] if inherit else [],
        bank_ids=parent.get("bank_ids") or [] if inherit else [],
        tags=parent.get("tags") or [] if inherit else [],
    )


def restore_version(
    db,
    source: dict,
    *,
    created_by: ObjectId,
    change_note: str | None = None,
) -> dict[str, Any]:
    """把旧版本的内容恢复成一个**新版本**（历史不回头改）。

    新版本接在链的末尾（parent = 当前最新版），内容取自 `source`，
    并记录 `restored_from`，这样既保持线性历史，又能看出「从哪一版恢复的」。
    """
    note = change_note or f"从 v{source['version']} 恢复"
    latest = head(db, source["lineage_id"]) or source
    return create_version(
        db,
        content_of(source),
        owner_id=source["owner_id"],
        created_by=created_by,
        lineage_id=source["lineage_id"],
        parent_id=latest["_id"],
        change_note=note,
        restored_from=source["_id"],
        visibility=source.get("visibility", "private"),
        shared_with=source.get("shared_with") or [],
        bank_ids=source.get("bank_ids") or [],
        tags=source.get("tags") or [],
    )


# --------------------------------------------------------------------------- #
# 可见性与权限
# --------------------------------------------------------------------------- #

def visible_filter(user: dict | None) -> dict[str, Any]:
    """当前用户能看到的版本。

    - 自己拥有的；
    - 标记为共享且没有指定名单的（相当于全站公开）；
    - 共享名单里包含自己的。
    """
    if user is None:
        return {"_id": None}
    return {
        "$or": [
            {"owner_id": user["_id"]},
            {"visibility": "shared", "shared_with": []},
            {"shared_with": user["_id"]},
        ]
    }


def visible_versions(db, user: dict | None) -> list[dict]:
    """按 lineage 取当前用户可见的「当前版本」，用于题库列表。"""
    pipeline = [
        {"$match": visible_filter(user)},
        {"$sort": {"lineage_id": 1, "version": -1}},
        {"$group": {"_id": "$lineage_id", "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
        {"$sort": {"created_at": -1}},
    ]
    return list(db.question_bank.aggregate(pipeline))


def is_owner(version: dict, user: dict | None) -> bool:
    return bool(user) and version.get("owner_id") == user["_id"]


def can_view(version: dict, user: dict | None) -> bool:
    if user is None:
        return False
    if is_owner(version, user):
        return True
    if version.get("visibility") != "shared":
        return False
    shared = version.get("shared_with") or []
    return not shared or user["_id"] in shared


def can_fork(version: dict, user: dict | None) -> bool:
    """共享题的接收方也可以基于它建新版本（团队协作）。"""
    return can_view(version, user)


def can_manage(version: dict, user: dict | None) -> bool:
    """共享设置、加题库、归档这些只能链的所有者做。"""
    return is_owner(version, user)


# --------------------------------------------------------------------------- #
# 问卷侧：快照
# --------------------------------------------------------------------------- #

def snapshot_fields(version: dict) -> dict[str, Any]:
    """把题库版本变成问卷题目文档的溯源字段（内容由调用方合并）。"""
    return {
        "lineage_id": version["lineage_id"],
        "bank_version_id": version["_id"],
        "version": version["version"],
        "frozen": False,
        "snapshot_at": version.get("created_at"),
    }


# --------------------------------------------------------------------------- #
# 使用情况与跨问卷统计
# --------------------------------------------------------------------------- #

def usage(db, lineage_id: str) -> list[dict[str, Any]]:
    """这道题被哪些问卷使用：问卷、所有者、状态、用到的版本、回答数。"""
    rows = list(
        db.questions.aggregate(
            [
                {"$match": {"lineage_id": lineage_id}},
                {
                    "$group": {
                        "_id": "$survey_id",
                        "versions": {"$addToSet": "$version"},
                        "orders": {"$push": "$order"},
                    }
                },
                {
                    "$lookup": {
                        "from": "surveys",
                        "localField": "_id",
                        "foreignField": "_id",
                        "as": "survey",
                    }
                },
                {"$unwind": "$survey"},
                {"$sort": {"survey.created_at": -1}},
            ]
        )
    )
    survey_ids = [row["_id"] for row in rows]
    counts: dict[Any, int] = {}
    if survey_ids:
        counts = {
            item["_id"]: item["count"]
            for item in db.responses.aggregate(
                [
                    {"$match": {"survey_id": {"$in": survey_ids}}},
                    {"$group": {"_id": "$survey_id", "count": {"$sum": 1}}},
                ]
            )
        }
    owner_ids = [row["survey"]["owner_id"] for row in rows]
    names = {}
    if owner_ids:
        names = {
            user["_id"]: user["username"]
            for user in db.users.find({"_id": {"$in": owner_ids}}, {"username": 1})
        }
    result = []
    for row in rows:
        survey = row["survey"]
        result.append(
            {
                "survey": survey,
                "owner_name": names.get(survey["owner_id"], "—"),
                "versions": sorted(row["versions"]),
                "order": min(row["orders"]) if row["orders"] else 0,
                "responses": counts.get(row["_id"], 0),
            }
        )
    return result


def cross_survey_stats(db, lineage_id: str) -> dict[str, Any]:
    """需求 8：同一道题（同一 lineage）在所有问卷里的回答情况。

    只看 `answers.lineage_id`，因此同一题的 v1 与 v2 会被合并统计；
    同时给出按版本、按问卷的拆分，便于看出口径差异。
    """
    chain = versions(db, lineage_id)
    if not chain:
        return {"lineage_id": lineage_id, "type": None, "answered": 0, "surveys": [], "versions": []}

    head_doc = chain[-1]
    qtype = head_doc["type"]
    used = usage(db, lineage_id)
    answered = sum(
        _answered_for_survey(db, lineage_id, item["survey"]["_id"]) for item in used
    )
    survey_ids = [item["survey"]["_id"] for item in used]

    data: dict[str, Any] = {
        "lineage_id": lineage_id,
        "type": qtype,
        "title": head_doc["title"],
        "answered": answered,
        "survey_count": len(used),
        "surveys": used,
        "versions": chain,
        "options": [],
    }

    base = [
        {"$match": {"answers.lineage_id": lineage_id}},
        {"$unwind": "$answers"},
        {"$match": {"answers.lineage_id": lineage_id}},
    ]

    if qtype in ("single", "multiple"):
        if qtype == "multiple":
            base.append({"$unwind": "$answers.value"})
        rows = list(
            db.responses.aggregate(
                base
                + [
                    {
                        "$group": {
                            "_id": {
                                "survey": "$survey_id",
                                "version": "$answers.version",
                                "value": "$answers.value",
                            },
                            "count": {"$sum": 1},
                        }
                    },
                    {"$sort": {"count": -1}},
                ]
            )
        )
        totals: dict[str, int] = {}
        per_version: dict[int, dict[str, int]] = {}
        per_survey: dict[Any, int] = {}
        for row in rows:
            key = row["_id"]
            value = str(key.get("value"))
            totals[value] = totals.get(value, 0) + row["count"]
            version = key.get("version") or 1
            per_version.setdefault(version, {})
            per_version[version][value] = per_version[version].get(value, 0) + row["count"]
            per_survey[key["survey"]] = per_survey.get(key["survey"], 0) + row["count"]

        labels = _option_labels(chain)
        ordered_keys = list(labels.keys())
        for key in sorted(totals):
            if key not in ordered_keys:
                ordered_keys.append(key)
        data["options"] = [
            {
                "key": key,
                "label": labels.get(key, {}).get("label", key),
                "labels_by_version": labels.get(key, {}).get("versions", {}),
                "count": totals.get(key, 0),
                "percent": round(totals.get(key, 0) * 100 / answered, 1) if answered else 0.0,
                "by_version": {
                    version: counts.get(key, 0) for version, counts in sorted(per_version.items())
                },
            }
            for key in ordered_keys
        ]
        data["version_totals"] = [
            {
                "version": version,
                "count": sum(per_version[version].values()),
                "counts": per_version[version],
            }
            for version in sorted(per_version)
        ]

    elif qtype == "number":
        rows = list(
            db.responses.aggregate(
                base
                + [
                    {
                        "$group": {
                            "_id": "$survey_id",
                            "avg": {"$avg": "$answers.value"},
                            "min": {"$min": "$answers.value"},
                            "max": {"$max": "$answers.value"},
                            "count": {"$sum": 1},
                        }
                    }
                ]
            )
        )
        values = [row["count"] for row in rows]
        overall = list(
            db.responses.aggregate(
                base
                + [
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
            )
        )
        stat = overall[0] if overall else {}
        data.update(
            {
                "average": round(stat["avg"], 2) if stat.get("avg") is not None else None,
                "minimum": stat.get("min"),
                "maximum": stat.get("max"),
                "by_survey": {
                    str(row["_id"]): {
                        "average": round(row["avg"], 2) if row.get("avg") is not None else None,
                        "minimum": row.get("min"),
                        "maximum": row.get("max"),
                        "count": row["count"],
                    }
                    for row in rows
                },
                "count": sum(values),
            }
        )

    else:  # text
        docs = db.responses.find(
            {"answers.lineage_id": lineage_id},
            {"answers": 1, "submitted_at": 1, "survey_id": 1},
        ).sort("submitted_at", -1)
        entries = []
        for doc in docs:
            for answer in doc.get("answers", []):
                if answer.get("lineage_id") != lineage_id:
                    continue
                entries.append(
                    {
                        "value": answer.get("value"),
                        "version": answer.get("version") or 1,
                        "survey_id": doc["survey_id"],
                        "submitted_at": doc.get("submitted_at"),
                    }
                )
        data["entries"] = entries[:300]
        data["entries_total"] = len(entries)
        data["average_length"] = (
            round(sum(len(str(e["value"])) for e in entries) / len(entries), 1) if entries else None
        )

    data["survey_ids"] = survey_ids
    return data


def _answered_for_survey(db, lineage_id: str, survey_id: ObjectId) -> int:
    return db.responses.count_documents(
        {"survey_id": survey_id, "answers.lineage_id": lineage_id}
    )


def _option_labels(chain: list[dict]) -> dict[str, dict[str, Any]]:
    """选项 key → 标签。以最新版本为准，同时保留每个版本自己的叫法。"""
    labels: dict[str, dict[str, Any]] = {}
    for version in chain:
        for opt in version.get("options") or []:
            key = str(opt.get("key"))
            entry = labels.setdefault(key, {"label": opt.get("label", key), "versions": {}})
            entry["versions"][version["version"]] = opt.get("label", key)
            entry["label"] = opt.get("label", key)  # 后面（更新的版本）覆盖前面的
    return labels
