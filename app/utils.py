"""通用工具：当前用户、登录校验、ObjectId 解析、slug 生成。"""

from __future__ import annotations

import functools
import secrets
from datetime import datetime, timezone
from typing import Any, Callable

from bson import ObjectId
from flask import abort, g, redirect, request, session, url_for

from . import logic


def now() -> datetime:
    return datetime.now(timezone.utc)


def get_db():
    from flask import current_app

    return current_app.extensions["mongo_db"]


def to_object_id(value: Any) -> ObjectId | None:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def load_current_user() -> dict | None:
    uid = session.get("uid")
    if not uid:
        return None
    oid = to_object_id(uid)
    if oid is None:
        session.clear()
        return None
    user = get_db().users.find_one({"_id": oid})
    if user is None:
        session.clear()
    return user


def current_user() -> dict | None:
    if "user" not in g:
        g.user = load_current_user()
    return g.user


def login_required(view: Callable) -> Callable:
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def api_login_required(view: Callable) -> Callable:
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return {"ok": False, "error": "请先登录后再填写问卷"}, 401
        return view(*args, **kwargs)

    return wrapped


def parse_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def parse_float(raw: Any, default: float | None = None) -> float | None:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def parse_datetime_local(raw: str | None) -> datetime | None:
    """解析 <input type="datetime-local"> 的值，按服务器本地时区转成 UTC。"""
    if not raw:
        return None
    try:
        naive = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    local = naive.astimezone()
    return local.astimezone(timezone.utc)


def new_slug(db) -> str:
    while True:
        slug = secrets.token_urlsafe(6).replace("-", "").replace("_", "")
        if not db.surveys.find_one({"slug": slug}, {"_id": 1}):
            return slug


def owned_survey_or_404(sid: str) -> dict:
    """取出属于当前用户的问卷，否则 404。"""
    db = get_db()
    oid = to_object_id(sid)
    if oid is None:
        abort(404)
    survey = db.surveys.find_one({"_id": oid})
    if survey is None:
        abort(404)
    user = current_user()
    if user is None or survey["owner_id"] != user["_id"]:
        abort(404)
    return survey


def load_questions(survey_id: ObjectId) -> list[dict]:
    return list(get_db().questions.find({"survey_id": survey_id}).sort("order", 1))


def is_open(survey: dict) -> tuple[bool, str]:
    """问卷是否可填写，返回 (可填写, 原因)。"""
    if survey.get("status") != "published":
        return False, "该问卷尚未发布或已关闭"
    deadline = survey.get("deadline")
    if deadline is not None and deadline <= now():
        return False, "该问卷已过截止时间"
    return True, ""


def normalise_answers(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items()}


__all__ = [
    "api_login_required",
    "current_user",
    "get_db",
    "is_open",
    "load_current_user",
    "load_questions",
    "login_required",
    "new_slug",
    "normalise_answers",
    "now",
    "owned_survey_or_404",
    "parse_datetime_local",
    "parse_float",
    "parse_int",
    "to_object_id",
    "logic",
]
