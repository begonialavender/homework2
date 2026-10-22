"""题库页面与分组：新建题库、建题、加入题库、改名、解散、页面可渲染。"""

from __future__ import annotations

from tests.helpers import login, register


def _login(client):
    register(client)
    login(client)


def _create_bank(client, name):
    client.post("/bank/new", data={"name": name, "description": "常用题"}, follow_redirects=True)


class TestBankPages:
    def test_index_and_new_question_page_render(self, client, db):
        _login(client)
        assert client.get("/bank").status_code == 200
        page = client.get("/bank/questions/new")
        assert page.status_code == 200
        assert "保存到题库" in page.get_data(as_text=True)

    def test_create_question_directly_in_bank(self, client, db):
        _login(client)
        _create_bank(client, "课程题")
        bank = db.banks.find_one({"name": "课程题"})

        client.post(
            "/bank/questions/new",
            data={
                "type": "single",
                "title": "你所在的年级？",
                "options": "大一\n大二\n大三\n大四",
                "tags": "人口统计, 常用",
                "bank_id": str(bank["_id"]),
                "change_note": "课程问卷常用",
            },
            follow_redirects=True,
        )
        version = db.question_bank.find_one({"title": "你所在的年级？"})
        assert version is not None
        assert version["version"] == 1
        assert version["bank_ids"] == [bank["_id"]]
        assert version["tags"] == ["人口统计", "常用"]

        page = client.get(f"/bank/questions/{version['lineage_id']}")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "版本历史" in html
        assert "生成 v2" in html

        filtered = client.get(f"/bank?bank={bank['_id']}")
        assert "你所在的年级？" in filtered.get_data(as_text=True)

    def test_invalid_question_form_rejected(self, client, db):
        _login(client)
        page = client.post(
            "/bank/questions/new",
            data={"type": "single", "title": "", "options": "只有一个选项"},
            follow_redirects=True,
        )
        assert page.status_code == 400
        assert db.question_bank.count_documents({}) == 0

    def test_add_question_to_bank_and_remove(self, client, db):
        _login(client)
        _create_bank(client, "课程题")
        bank = db.banks.find_one({"name": "课程题"})
        client.post(
            "/bank/questions/new",
            data={"type": "text", "title": "还有什么建议？", "min_length": "0"},
            follow_redirects=True,
        )
        version = db.question_bank.find_one({"title": "还有什么建议？"})

        client.post(
            f"/bank/{bank['_id']}/add",
            data={"version_id": str(version["_id"])},
            follow_redirects=True,
        )
        assert bank["_id"] in db.question_bank.find_one({"_id": version["_id"]})["bank_ids"]

        client.post(
            f"/bank/{bank['_id']}/remove",
            data={"version_id": str(version["_id"])},
            follow_redirects=True,
        )
        assert bank["_id"] not in db.question_bank.find_one({"_id": version["_id"]})["bank_ids"]

    def test_rename_and_delete_bank_keeps_questions(self, client, db):
        _login(client)
        _create_bank(client, "旧名字")
        bank = db.banks.find_one({"name": "旧名字"})
        client.post(
            "/bank/questions/new",
            data={"type": "text", "title": "独立题目", "bank_id": str(bank["_id"])},
            follow_redirects=True,
        )
        version = db.question_bank.find_one({"title": "独立题目"})

        client.post(
            f"/bank/{bank['_id']}/rename",
            data={"name": "新名字", "description": "改过了"},
            follow_redirects=True,
        )
        assert db.banks.find_one({"_id": bank["_id"]})["name"] == "新名字"

        client.post(f"/bank/{bank['_id']}/delete", follow_redirects=True)
        assert db.banks.find_one({"_id": bank["_id"]}) is None
        remaining = db.question_bank.find_one({"_id": version["_id"]})
        assert remaining is not None
        assert remaining["bank_ids"] == []

    def test_share_bank_with_user(self, app, client, db):
        _login(client)
        _create_bank(client, "团队题库")
        bank = db.banks.find_one({"name": "团队题库"})

        bob = app.test_client()
        register(bob, "bob")
        login(bob, "bob")

        client.post(
            f"/bank/{bank['_id']}/share",
            data={"visibility": "shared", "shared_with": "bob"},
            follow_redirects=True,
        )
        bob_user = db.users.find_one({"username": "bob"})
        updated = db.banks.find_one({"_id": bank["_id"]})
        assert updated["visibility"] == "shared"
        assert updated["shared_with"] == [bob_user["_id"]]

        page = bob.get("/bank").get_data(as_text=True)
        assert "团队题库" in page

        # 所有者打开单个题库时，共享设置面板要能正常渲染
        detail = client.get(f"/bank?bank={bank['_id']}")
        assert detail.status_code == 200
        assert "题库共享" in detail.get_data(as_text=True)
