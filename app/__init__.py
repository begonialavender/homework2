"""应用工厂：把 MongoDB 连接、蓝图、模板过滤器装配起来。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from flask import Flask

from .db import connect, init_db

TYPE_LABELS = {
    "single": "单选题",
    "multiple": "多选题",
    "text": "文本填空题",
    "number": "数字填空题",
}

STATUS_LABELS = {
    "draft": "草稿",
    "published": "已发布",
    "closed": "已关闭",
}


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object("config.Config")
    if config:
        app.config.update(config)

    # 中文直接以 UTF-8 输出，方便接口调试与页面检查
    app.json.ensure_ascii = False

    db = connect(app.config["MONGO_URI"], app.config["MONGO_DB"])
    if app.config.get("AUTO_INIT_DB", True):
        init_db(db)
    app.extensions["mongo_db"] = db

    from . import auth, export, fill, stats, surveys

    app.register_blueprint(auth.bp)
    app.register_blueprint(surveys.bp)
    app.register_blueprint(fill.bp)
    app.register_blueprint(stats.bp)
    app.register_blueprint(export.bp)

    @app.template_filter("dt")
    def format_dt(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
        if not value:
            return "—"
        if value.tzinfo:
            value = value.astimezone()
        return value.strftime(fmt)

    @app.template_filter("type_label")
    def type_label(value: str) -> str:
        return TYPE_LABELS.get(value, value)

    @app.template_filter("status_label")
    def status_label(value: str) -> str:
        return STATUS_LABELS.get(value, value)

    @app.template_filter("oid")
    def oid(value: Any) -> str:
        return str(value)

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        from .utils import current_user

        return {
            "TYPE_LABELS": TYPE_LABELS,
            "STATUS_LABELS": STATUS_LABELS,
            "now": datetime.now(timezone.utc),
            "object_id": ObjectId,
            "current_user": current_user(),
        }

    return app
