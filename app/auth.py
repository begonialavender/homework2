"""注册 / 登录 / 退出。"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from .utils import current_user, get_db, now

bp = Blueprint("auth", __name__)


@bp.get("/")
def index():
    if current_user():
        return redirect(url_for("surveys.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        error = None
        if not 3 <= len(username) <= 32:
            error = "用户名长度需在 3 ~ 32 个字符之间"
        elif len(password) < 6:
            error = "密码至少 6 位"
        elif password != confirm:
            error = "两次输入的密码不一致"

        if error:
            flash(error, "error")
            return render_template("register.html", username=username), 400

        try:
            get_db().users.insert_one(
                {
                    "username": username,
                    "password_hash": generate_password_hash(password),
                    "created_at": now(),
                }
            )
        except DuplicateKeyError:
            flash("该用户名已被注册", "error")
            return render_template("register.html", username=username), 400

        flash("注册成功，请登录", "ok")
        return redirect(url_for("auth.login"))

    return render_template("register.html", username="")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = get_db().users.find_one({"username": username})
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("用户名或密码错误", "error")
            return render_template("login.html", username=username), 401

        session.clear()
        session["uid"] = str(user["_id"])
        session.permanent = True
        target = request.args.get("next") or url_for("surveys.dashboard")
        if not target.startswith("/"):
            target = url_for("surveys.dashboard")
        return redirect(target)

    return render_template("login.html", username="")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
