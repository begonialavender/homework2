"""MongoDB 连接、集合创建、校验器与索引定义。

数据库设计要点见 docs/02-数据库设计.md：
- 第一阶段：users / surveys / questions / responses 四个集合；
- 第二阶段新增 banks / question_bank 两个集合，把「题目」从问卷的附属物
  升级成可版本化、可共享、可复用的独立实体；
- question_bank 里一条文档 = 一个版本，版本内容不可变，靠 lineage_id 串成版本链；
- questions 仍然留在问卷里，但语义变成「内容快照 + 问卷内编排」，
  已发布问卷的题目被冻结，因此改题库不会影响旧问卷和旧回答；
- responses 的每条答案额外记录 lineage_id 与 version，跨问卷统计只需一次聚合；
- 每个集合都用 $jsonSchema 在数据库层兜底，避免脏数据写进来。
"""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

USER_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["username", "password_hash", "created_at"],
        "properties": {
            "username": {"bsonType": "string", "minLength": 3, "maxLength": 32},
            "password_hash": {"bsonType": "string", "minLength": 20},
            "created_at": {"bsonType": "date"},
        },
        "additionalProperties": True,
    }
}

SURVEY_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "slug", "owner_id", "title", "allow_anonymous",
            "allow_multiple", "status", "created_at", "question_count",
        ],
        "properties": {
            "slug": {"bsonType": "string", "minLength": 4, "maxLength": 32},
            "owner_id": {"bsonType": "objectId"},
            "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
            "description": {"bsonType": ["string", "null"], "maxLength": 2000},
            "allow_anonymous": {"bsonType": "bool"},
            "allow_multiple": {"bsonType": "bool"},
            "status": {"enum": ["draft", "published", "closed"]},
            "deadline": {"bsonType": ["date", "null"]},
            "created_at": {"bsonType": "date"},
            "published_at": {"bsonType": ["date", "null"]},
            "closed_at": {"bsonType": ["date", "null"]},
            "question_count": {"bsonType": "int", "minimum": 0},
        },
        "additionalProperties": True,
    }
}

# 题目内容部分在问卷快照与题库版本里结构一致，抽出来复用，避免两处定义漂移。
QUESTION_CONTENT_PROPERTIES: dict[str, Any] = {
    "type": {"enum": ["single", "multiple", "text", "number"]},
    "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
    "required": {"bsonType": "bool"},
    "options": {
        "bsonType": "array",
        "items": {
            "bsonType": "object",
            "required": ["key", "label"],
            "properties": {
                "key": {"bsonType": "string", "minLength": 1, "maxLength": 20},
                "label": {"bsonType": "string", "minLength": 1, "maxLength": 200},
            },
        },
    },
    "rules": {"bsonType": "object"},
}

QUESTION_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["survey_id", "order", "type", "title", "required", "logic"],
        "properties": {
            "survey_id": {"bsonType": "objectId"},
            "order": {"bsonType": "int", "minimum": 0},
            # 溯源字段：第一阶段的数据由迁移脚本补齐，因此这里不设为必填。
            "lineage_id": {"bsonType": "string", "minLength": 4, "maxLength": 32},
            "bank_version_id": {"bsonType": ["objectId", "null"]},
            "version": {"bsonType": "int", "minimum": 1},
            "frozen": {"bsonType": "bool"},
            "snapshot_at": {"bsonType": "date"},
            **QUESTION_CONTENT_PROPERTIES,
            "logic": {
                "bsonType": "object",
                "required": ["default_next"],
                "properties": {
                    "default_next": {"enum": ["next", "end"]},
                    "jumps": {"bsonType": "array"},
                },
            },
        },
        "additionalProperties": True,
    }
}

RESPONSE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["survey_id", "anonymous", "submitted_at", "path", "answers"],
        "properties": {
            "survey_id": {"bsonType": "objectId"},
            "respondent_id": {"bsonType": ["objectId", "null"]},
            "anonymous": {"bsonType": "bool"},
            "submitted_at": {"bsonType": "date"},
            "survey_version": {"bsonType": "int", "minimum": 1},
            "path": {"bsonType": "array", "items": {"bsonType": "string"}},
            "answers": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["question_id", "value"],
                    "properties": {
                        "question_id": {"bsonType": "string"},
                        # 跨问卷统计的聚合键；第一阶段旧数据由迁移脚本补齐。
                        "lineage_id": {"bsonType": "string", "minLength": 4, "maxLength": 32},
                        "version": {"bsonType": "int", "minimum": 1},
                        "value": {},
                    },
                },
            },
        },
        "additionalProperties": True,
    }
}

BANK_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["name", "owner_id", "visibility", "created_at"],
        "properties": {
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 100},
            "description": {"bsonType": ["string", "null"], "maxLength": 500},
            "owner_id": {"bsonType": "objectId"},
            "visibility": {"enum": ["private", "shared"]},
            "shared_with": {"bsonType": "array", "items": {"bsonType": "objectId"}},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
        "additionalProperties": True,
    }
}

BANK_VERSION_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "lineage_id", "version", "type", "title", "required",
            "owner_id", "visibility", "created_at",
        ],
        "properties": {
            # 同一条版本链共用一个 lineage_id，跨问卷统计也认它。
            "lineage_id": {"bsonType": "string", "minLength": 4, "maxLength": 32},
            "version": {"bsonType": "int", "minimum": 1},
            "parent_id": {"bsonType": ["objectId", "null"]},
            "root_id": {"bsonType": ["objectId", "null"]},
            "restored_from": {"bsonType": ["objectId", "null"]},
            "change_note": {"bsonType": ["string", "null"], "maxLength": 200},
            "tags": {"bsonType": "array", "items": {"bsonType": "string", "maxLength": 20}},
            "owner_id": {"bsonType": "objectId"},
            "created_by": {"bsonType": ["objectId", "null"]},
            "visibility": {"enum": ["private", "shared"]},
            "shared_with": {"bsonType": "array", "items": {"bsonType": "objectId"}},
            "bank_ids": {"bsonType": "array", "items": {"bsonType": "objectId"}},
            "archived": {"bsonType": "bool"},
            "created_at": {"bsonType": "date"},
            **QUESTION_CONTENT_PROPERTIES,
        },
        "additionalProperties": True,
    }
}

COLLECTIONS = {
    "users": USER_VALIDATOR,
    "banks": BANK_VALIDATOR,
    "question_bank": BANK_VERSION_VALIDATOR,
    "surveys": SURVEY_VALIDATOR,
    "questions": QUESTION_VALIDATOR,
    "responses": RESPONSE_VALIDATOR,
}

INDEXES: dict[str, list[tuple[list[tuple[str, int]], dict[str, Any]]]] = {
    "users": [
        ([("username", ASCENDING)], {"unique": True, "name": "uq_username"}),
    ],
    "banks": [
        ([("owner_id", ASCENDING), ("created_at", DESCENDING)], {"name": "ix_bank_owner"}),
        ([("visibility", ASCENDING), ("updated_at", DESCENDING)], {"name": "ix_bank_visibility"}),
    ],
    "question_bank": [
        # 同一 lineage 下版本号唯一：将来若并发建版，数据库层直接拒绝重复号。
        ([("lineage_id", ASCENDING), ("version", ASCENDING)], {"unique": True, "name": "uq_lineage_version"}),
        ([("owner_id", ASCENDING), ("created_at", DESCENDING)], {"name": "ix_qb_owner"}),
        ([("created_at", DESCENDING)], {"name": "ix_qb_created"}),
        ([("bank_ids", ASCENDING)], {"name": "ix_qb_banks"}),
        ([("visibility", ASCENDING)], {"name": "ix_qb_visibility"}),
        ([("title", ASCENDING)], {"name": "ix_qb_title"}),
    ],
    "surveys": [
        ([("slug", ASCENDING)], {"unique": True, "name": "uq_slug"}),
        ([("owner_id", ASCENDING), ("created_at", DESCENDING)], {"name": "ix_owner_created"}),
    ],
    "questions": [
        ([("survey_id", ASCENDING), ("order", ASCENDING)], {"name": "ix_survey_order"}),
        # 需求 6「查某题被哪些问卷用」与需求 8「跨问卷统计」都走这个索引。
        ([("lineage_id", ASCENDING), ("survey_id", ASCENDING)], {"name": "ix_question_lineage"}),
    ],
    "responses": [
        ([("survey_id", ASCENDING), ("submitted_at", DESCENDING)], {"name": "ix_survey_submitted"}),
        ([("survey_id", ASCENDING), ("respondent_id", ASCENDING)], {"name": "ix_survey_respondent"}),
        ([("survey_id", ASCENDING), ("answers.question_id", ASCENDING)], {"name": "ix_survey_answer_qid"}),
        ([("answers.lineage_id", ASCENDING)], {"name": "ix_answer_lineage"}),
    ],
}


def connect(uri: str, db_name: str) -> Database:
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=5000,
        tz_aware=True,
        maxPoolSize=20,
        appname="survey-system",
    )
    return client[db_name]


def init_db(db: Database, drop: bool = False) -> Database:
    """创建集合、校验器和索引。可重复执行。"""
    if drop:
        db.client.drop_database(db.name)

    existing = set(db.list_collection_names())
    for name, validator in COLLECTIONS.items():
        if name not in existing:
            db.create_collection(name, validator=validator, validationLevel="strict")
        else:
            db.command(
                {
                    "collMod": name,
                    "validator": validator,
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )

    for name, specs in INDEXES.items():
        for keys, options in specs:
            db[name].create_index(keys, **options)

    return db
