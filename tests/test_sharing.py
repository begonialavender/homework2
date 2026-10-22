"""题目共享：可见性、指定用户共享、公开、共享者选用与改题。"""

from __future__ import annotations

from tests.helpers import add_single, create_survey, login, register


def _alice_and_question(app, client, db):
    register(client, "alice")
    login(client, "alice")
    survey = create_survey(client, db, title="alice 的问卷")
    question = add_single(client, db, survey, title="你的年龄是？")
    return survey, question


def _share(client, lineage, visibility, users=""):
    return client.post(
        f"/bank/questions/{lineage}/share",
        data={"visibility": visibility, "shared_with": users},
        follow_redirects=True,
    )


class TestVisibility:
    def test_private_question_hidden_from_others(self, app, client, db):
        _survey, question = _alice_and_question(app, client, db)

        bob = app.test_client()
        register(bob, "bob")
        login(bob, "bob")
        bob_survey = create_survey(bob, db, title="bob 的问卷")

        bank_page = bob.get("/bank").get_data(as_text=True)
        assert "你的年龄是？" not in bank_page

        picker = bob.get(f"/surveys/{bob_survey['_id']}/from-bank").get_data(as_text=True)
        assert "你的年龄是？" not in picker

        assert bob.get(f"/bank/questions/{question['lineage_id']}").status_code == 404

    def test_share_to_named_user(self, app, client, db):
        _survey, question = _alice_and_question(app, client, db)
        bob = app.test_client()
        register(bob, "bob")
        login(bob, "bob")
        bob_survey = create_survey(bob, db, title="bob 的问卷")

        _share(client, question["lineage_id"], "shared", "bob")

        picker = bob.get(f"/surveys/{bob_survey['_id']}/from-bank")
        assert picker.status_code == 200
        assert "你的年龄是？" in picker.get_data(as_text=True)
        assert "共享自 alice" in picker.get_data(as_text=True)

    def test_public_share_visible_to_everyone(self, app, client, db):
        _survey, question = _alice_and_question(app, client, db)
        _share(client, question["lineage_id"], "shared", "")

        carol = app.test_client()
        register(carol, "carol")
        login(carol, "carol")
        carol_survey = create_survey(carol, db, title="carol 的问卷")

        picker = carol.get(f"/surveys/{carol_survey['_id']}/from-bank").get_data(as_text=True)
        assert "你的年龄是？" in picker

    def test_unshare_hides_again(self, app, client, db):
        _survey, question = _alice_and_question(app, client, db)
        bob = app.test_client()
        register(bob, "bob")
        login(bob, "bob")
        bob_survey = create_survey(bob, db, title="bob 的问卷")

        _share(client, question["lineage_id"], "shared", "bob")
        _share(client, question["lineage_id"], "private", "")

        picker = bob.get(f"/surveys/{bob_survey['_id']}/from-bank").get_data(as_text=True)
        assert "你的年龄是？" not in picker

    def test_only_owner_can_change_sharing(self, app, client, db):
        _survey, question = _alice_and_question(app, client, db)
        bob = app.test_client()
        register(bob, "bob")
        login(bob, "bob")
        _share(client, question["lineage_id"], "shared", "bob")

        response = _share(bob, question["lineage_id"], "shared", "bob")
        assert response.status_code == 403


class TestUsingSharedQuestion:
    def test_shared_question_keeps_same_lineage(self, app, client, db):
        _survey, question = _alice_and_question(app, client, db)
        version = db.question_bank.find_one({"lineage_id": question["lineage_id"]})

        bob = app.test_client()
        register(bob, "bob")
        login(bob, "bob")
        bob_survey = create_survey(bob, db, title="bob 的问卷")
        _share(client, question["lineage_id"], "shared", "bob")

        bob.post(
            f"/surveys/{bob_survey['_id']}/questions/from-bank",
            data={"version_id": str(version["_id"])},
            follow_redirects=True,
        )
        copied = db.questions.find_one({"survey_id": bob_survey["_id"]})
        assert copied is not None
        assert copied["lineage_id"] == question["lineage_id"]
        assert copied["version"] == 1
        assert copied["title"] == "你的年龄是？"

        # 题库版本的所有者仍然是 alice，选择不会改变归属
        still = db.question_bank.find_one({"_id": version["_id"]})
        alice = db.users.find_one({"username": "alice"})
        assert still["owner_id"] == alice["_id"]

    def test_shared_user_fork_does_not_touch_alice_survey(self, app, client, db):
        survey, question = _alice_and_question(app, client, db)
        version = db.question_bank.find_one({"lineage_id": question["lineage_id"]})

        bob = app.test_client()
        register(bob, "bob")
        login(bob, "bob")
        bob_survey = create_survey(bob, db, title="bob 的问卷")
        _share(client, question["lineage_id"], "shared", "bob")
        bob.post(
            f"/surveys/{bob_survey['_id']}/questions/from-bank",
            data={"version_id": str(version["_id"])},
            follow_redirects=True,
        )
        copied = db.questions.find_one({"survey_id": bob_survey["_id"]})

        bob.post(
            f"/surveys/{bob_survey['_id']}/questions/{copied['_id']}",
            data={"type": "single", "title": "bob 改过的题干", "options": "A1\nB1"},
            follow_redirects=True,
        )

        chain = list(db.question_bank.find({"lineage_id": question["lineage_id"]}).sort("version", 1))
        assert len(chain) == 2
        assert chain[1]["title"] == "bob 改过的题干"
        assert chain[1]["owner_id"] == chain[0]["owner_id"]  # 链的所有者不变
        assert chain[1]["created_by"] != chain[0]["created_by"]

        # alice 自己问卷里的快照保持在 v1
        alice_question = db.questions.find_one({"_id": question["_id"]})
        assert alice_question["title"] == "你的年龄是？"
        assert alice_question["version"] == 1
        assert alice_question["frozen"] is False
