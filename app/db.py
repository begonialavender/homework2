"""MongoDB 连接、集合创建、校验器与索引定义。

数据库设计要点见 docs/02-数据库设计.md：
- 分为 users / surveys / questions / responses 四个集合；
- 题目单独成集合，便于逐题修改与跳转引用；
- 答案以嵌入数组的形式放在 responses 文档内，提交是单文档原子写入；
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

QUESTION_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["survey_id", "order", "type", "title", "required", "logic"],
        "properties": {
            "survey_id": {"bsonType": "objectId"},
            "order": {"bsonType": "int", "minimum": 0},
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
            "path": {"bsonType": "array", "items": {"bsonType": "string"}},
            "answers": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["question_id", "value"],
                    "properties": {
                        "question_id": {"bsonType": "string"},
                        "value": {},
                    },
                },
            },
        },
        "additionalProperties": True,
    }
}

COLLECTIONS = {
    "users": USER_VALIDATOR,
    "surveys": SURVEY_VALIDATOR,
    "questions": QUESTION_VALIDATOR,
    "responses": RESPONSE_VALIDATOR,
}

INDEXES: dict[str, list[tuple[list[tuple[str, int]], dict[str, Any]]]] = {
    "users": [
        ([("username", ASCENDING)], {"unique": True, "name": "uq_username"}),
    ],
    "surveys": [
        ([("slug", ASCENDING)], {"unique": True, "name": "uq_slug"}),
        ([("owner_id", ASCENDING), ("created_at", DESCENDING)], {"name": "ix_owner_created"}),
    ],
    "questions": [
        ([("survey_id", ASCENDING), ("order", ASCENDING)], {"name": "ix_survey_order"}),
    ],
    "responses": [
        ([("survey_id", ASCENDING), ("submitted_at", DESCENDING)], {"name": "ix_survey_submitted"}),
        ([("survey_id", ASCENDING), ("respondent_id", ASCENDING)], {"name": "ix_survey_respondent"}),
        ([("survey_id", ASCENDING), ("answers.question_id", ASCENDING)], {"name": "ix_survey_answer_qid"}),
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
