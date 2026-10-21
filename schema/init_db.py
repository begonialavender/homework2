"""初始化数据库：创建集合、$jsonSchema 校验器与索引。

用法：
    python schema/init_db.py                # 建库（可重复执行）
    python schema/init_db.py --drop         # 先删库再重建（会清空数据！）
    python schema/init_db.py --show         # 查看当前校验器与索引
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db import COLLECTIONS, INDEXES, connect, init_db  # noqa: E402

URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("MONGO_DB", "survey_system")


def show(db) -> None:
    print(f"数据库：{db.name}")
    for name in COLLECTIONS:
        if name not in db.list_collection_names():
            print(f"\n[{name}] 尚未创建")
            continue
        info = db.command("listCollections", filter={"name": name})["cursor"]["firstBatch"][0]
        validator = info.get("options", {}).get("validator", {})
        print(f"\n[{name}]")
        print(f"  校验器：{'已启用' if validator else '未设置'}")
        for index in db[name].list_indexes():
            keys = ", ".join(f"{k}:{v}" for k, v in index["key"].items())
            flags = []
            if index.get("unique"):
                flags.append("unique")
            print(f"  索引 {index['name']}: ({keys}) {' '.join(flags)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="问卷系统数据库初始化")
    parser.add_argument("--drop", action="store_true", help="先删除数据库再重建")
    parser.add_argument("--show", action="store_true", help="只查看，不修改")
    parser.add_argument("--uri", default=URI, help="MongoDB 连接串")
    parser.add_argument("--db", default=DB_NAME, help="数据库名")
    args = parser.parse_args()

    db = connect(args.uri, args.db)
    if args.show:
        show(db)
        return

    init_db(db, drop=args.drop)
    print(f"数据库 {db.name} 初始化完成。")
    show(db)


if __name__ == "__main__":
    main()
