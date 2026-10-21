"""账号功能测试：注册、登录、未登录跳转、数据隔离。"""

from __future__ import annotations

from tests.helpers import PASSWORD, create_survey, login, logout, register


class TestRegister:
    def test_register_success(self, client, db):
        resp = register(client, "alice")
        assert resp.status_code == 200
        user = db.users.find_one({"username": "alice"})
        assert user is not None
        assert user["password_hash"] != PASSWORD  # 不能明文存储
        assert user["password_hash"].startswith(("pbkdf2:", "scrypt:"))

    def test_duplicate_username(self, client):
        register(client, "alice")
        resp = register(client, "alice")
        assert resp.status_code == 400
        assert "已被注册" in resp.get_data(as_text=True)

    def test_short_password_rejected(self, client, db):
        resp = client.post(
            "/register",
            data={"username": "bob", "password": "123", "confirm": "123"},
            follow_redirects=True,
        )
        assert resp.status_code == 400
        assert db.users.count_documents({"username": "bob"}) == 0

    def test_password_mismatch(self, client, db):
        resp = client.post(
            "/register",
            data={"username": "bob", "password": "secret123", "confirm": "secret999"},
            follow_redirects=True,
        )
        assert resp.status_code == 400
        assert db.users.count_documents({"username": "bob"}) == 0


class TestLogin:
    def test_login_success(self, client):
        register(client, "alice")
        logout(client)
        resp = login(client, "alice")
        assert resp.status_code == 200
        assert "我的问卷" in resp.get_data(as_text=True)

    def test_wrong_password(self, client):
        register(client, "alice")
        logout(client)
        resp = client.post(
            "/login",
            data={"username": "alice", "password": "wrong"},
            follow_redirects=True,
        )
        assert resp.status_code == 401

    def test_dashboard_requires_login(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_logout(self, client):
        register(client, "alice")
        logout(client)
        assert client.get("/dashboard").status_code == 302


class TestIsolation:
    def test_only_own_surveys_on_dashboard(self, client, db):
        register(client, "alice")
        login(client, "alice")
        create_survey(client, db, title="Alice 的问卷")
        logout(client)

        register(client, "bob")
        login(client, "bob")
        create_survey(client, db, title="Bob 的问卷")
        page = client.get("/dashboard").get_data(as_text=True)
        assert "Bob 的问卷" in page
        assert "Alice 的问卷" not in page

    def test_cannot_open_others_survey(self, client, db):
        register(client, "alice")
        login(client, "alice")
        survey = create_survey(client, db, title="Alice 的问卷")
        logout(client)

        register(client, "bob")
        login(client, "bob")
        assert client.get(f"/surveys/{survey['_id']}").status_code == 404
        assert client.get(f"/surveys/{survey['_id']}/stats").status_code == 404
        resp = client.post(f"/surveys/{survey['_id']}/delete", follow_redirects=True)
        assert resp.status_code == 404
        assert db.surveys.count_documents({"_id": survey["_id"]}) == 1
