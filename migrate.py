"""第二阶段数据迁移：给第一阶段的数据补上「版本链」信息。

第一阶段的数据里，题目只是问卷的一部分，没有 lineage_id / version。
第二阶段把它们升级成：

- 每道旧题在 `question_bank` 里登记一个 v1 版本（所有者 = 问卷所有者）；
- 问卷里的题目补上 lineage_id / bank_version_id / version / frozen；
- 每条历史回答里的答案补上 lineage_id / version，跨问卷统计才能覆盖旧数据。

迁移是**幂等**的：已经有 lineage_id 的题目、已经补齐的答案会被跳过，
可以反复执行。

用法：
    python migrate.py --dry-run     # 只统计将要处理多少条，不写库
    python migrate.py               # 真正执行
    python migrate.py --db survey_system --uri mongodb://127.0.0.1:27017
"""

from __future__ import annotations

import argparse
import os
import sys

from bson import ObjectId

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import versioning  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.utils import now  # noqa: E402

URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("MONGO_DB", "survey_system")


def migrate(db, dry_run: bool = False) -> dict[str, int]:
    stats = {
        "questions": 0,
        "versions_created": 0,
        "frozen_updated": 0,
        "responses": 0,
        "answers_backfilled": 0,
        "skipped": 0,
    }

    surveys = {survey["_id"]: survey for survey in db.surveys.find({})}
    stamp = now()

    for question in db.questions.find({}):
        if question.get("lineage_id"):
            stats["skipped"] += 1
            continue
        survey = surveys.get(question.get("survey_id"))
        if survey is None:
            # 孤立的题目（问卷已删）也补一个版本，避免统计断链。
            owner_id = None
            frozen = False
            created_at = stamp
        else:
            owner_id = survey["owner_id"]
            frozen = survey.get("status") != "draft"
            created_at = survey.get("created_at") or stamp

        if owner_id is None:
            stats["skipped"] += 1
            continue

        stats["questions"] += 1
        if dry_run:
            continue

        version = versioning.create_version(
            db,
            {
                "type": question["type"],
                "title": question["title"],
                "options": question.get("options") or [],
                "rules": question.get("rules") or {},
                "required": bool(question.get("required")),
            },
            owner_id=owner_id,
            created_by=owner_id,
            change_note="第一阶段数据迁移：原始题目",
            now=created_at,
        )
        stats["versions_created"] += 1
        db.questions.update_one(
            {"_id": question["_id"]},
            {
                "$set": {
                    "lineage_id": version["lineage_id"],
                    "bank_version_id": version["_id"],
                    "version": 1,
                    "frozen": frozen,
                    "snapshot_at": created_at,
                }
            },
        )

    # 已发布 / 已关闭问卷的题目统一冻结；草稿里的题补 frozen=False。
    closed_ids = [sid for sid, survey in surveys.items() if survey.get("status") != "draft"]
    if closed_ids:
        stats["frozen_updated"] = db.questions.count_documents(
            {"survey_id": {"$in": closed_ids}, "frozen": {"$ne": True}}
        )
        if not dry_run:
            db.questions.update_many(
                {"survey_id": {"$in": closed_ids}, "frozen": {"$ne": True}},
                {"$set": {"frozen": True}},
            )
    if not dry_run:
        db.questions.update_many({"frozen": {"$exists": False}}, {"$set": {"frozen": False}})

    # 建立 question_id → (lineage, version) 映射，回填历史回答。
    mapping = {
        str(item["_id"]): (item.get("lineage_id"), item.get("version", 1))
        for item in db.questions.find({}, {"lineage_id": 1, "version": 1})
        if item.get("lineage_id")
    }

    for response in db.responses.find({}):
        answers = response.get("answers") or []
        if not answers or all(answer.get("lineage_id") for answer in answers):
            continue
        stats["responses"] += 1
        changed = False
        for answer in answers:
            if answer.get("lineage_id"):
                continue
            lineage_id, version = mapping.get(str(answer.get("question_id")), (None, None))
            if lineage_id:
                answer["lineage_id"] = lineage_id
                answer["version"] = int(version or 1)
                stats["answers_backfilled"] += 1
                changed = True
        if changed and not dry_run:
            db.responses.update_one({"_id": response["_id"]}, {"$set": {"answers": answers}})

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="第二阶段数据迁移：给旧数据补版本链信息")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument("--uri", default=URI, help="MongoDB 连接串")
    parser.add_argument("--db", default=DB_NAME, help="数据库名")
    parser.add_argument("--no-init", action="store_true", help="不执行 init_db（集合已存在时可用）")
    args = parser.parse_args()

    db = connect(args.uri, args.db)
    if not args.no_init:
        init_db(db)

    stats = migrate(db, dry_run=args.dry_run)
    mode = "预演（未写入）" if args.dry_run else "已完成"
    print(f"迁移{mode}：")
    print(f"  待迁移题目      {stats['questions']}")
    print(f"  新建题库版本    {stats['versions_created']}")
    print(f"  已补齐冻结标记  {stats['frozen_updated']}")
    print(f"  待回填回答      {stats['responses']}（答案 {stats['answers_backfilled']} 条）")
    print(f"  跳过（已迁移）  {stats['skipped']}")
    if args.dry_run:
        print("确认无误后去掉 --dry-run 再执行一次即可。")


if __name__ == "__main__":
    main()
