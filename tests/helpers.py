"""测试用的操作封装：尽量走真实的 HTTP 路由，而不是直接改数据库。"""

from __future__ import annotations

from bson import ObjectId

PASSWORD = "secret123"


def register(client, username="alice", password=PASSWORD):
    return client.post(
        "/register",
        data={"username": username, "password": password, "confirm": password},
        follow_redirects=True,
    )


def login(client, username="alice", password=PASSWORD):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def logout(client):
    return client.post("/logout", follow_redirects=True)


def create_survey(
    client,
    db,
    title="测试问卷",
    anonymous=False,
    multiple=True,
    deadline=None,
):
    data = {
        "title": title,
        "description": "系统生成的测试问卷",
    }
    if anonymous:
        data["allow_anonymous"] = "on"
    if multiple:
        data["allow_multiple"] = "on"
    if deadline:
        data["deadline"] = deadline
    client.post("/surveys/new", data=data, follow_redirects=True)
    return db.surveys.find_one({"title": title})


def add_single(client, db, survey, title="单选题", options="男\n女\n其他", required=False):
    return _add(
        client,
        db,
        survey,
        {"type": "single", "title": title, "options": options},
        required,
    )


def add_multiple(client, db, survey, title="多选题", options="苹果\n香蕉\n西瓜\n葡萄", required=False, **rules):
    form = {"type": "multiple", "title": title, "options": options}
    form.update({k: str(v) for k, v in rules.items()})
    return _add(client, db, survey, form, required)


def add_text(client, db, survey, title="填空题", required=False, **rules):
    form = {"type": "text", "title": title}
    form.update({k: str(v) for k, v in rules.items()})
    return _add(client, db, survey, form, required)


def add_number(client, db, survey, title="数字题", required=False, **rules):
    form = {"type": "number", "title": title}
    for key, value in rules.items():
        if key == "integer":
            if value:
                form["integer"] = "on"
        else:
            form[key] = str(value)
    return _add(client, db, survey, form, required)


def _add(client, db, survey, form, required):
    if required:
        form = dict(form, required="on")
    client.post(f"/surveys/{survey['_id']}/questions", data=form, follow_redirects=True)
    questions = list(db.questions.find({"survey_id": survey["_id"]}).sort("order", -1))
    return questions[0]


def publish(client, survey):
    return client.post(f"/surveys/{survey['_id']}/publish", follow_redirects=True)


def answer(client, slug, question_id, value, answers):
    return client.post(
        f"/api/surveys/{slug}/answer",
        json={
            "question_id": str(question_id),
            "value": value,
            "answers": {str(k): v for k, v in answers.items()},
        },
    )


def submit(client, slug, answers):
    return client.post(
        f"/api/surveys/{slug}/submit",
        json={"answers": {str(k): v for k, v in answers.items()}},
    )


def qid(question) -> str:
    return str(question["_id"] if isinstance(question, dict) else question)


def as_object_id(value):
    return value if isinstance(value, ObjectId) else ObjectId(str(value))
