"""Run: python -m unittest discover -s tests -v (no production database access)."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

_database_dir = tempfile.TemporaryDirectory(prefix="legacy-trainee-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + _database_dir.name.replace("\\", "/") + "/test.db"
os.environ["SECRET_KEY"] = "isolated-trainee-test-secret"

import main
import database
import models
from jose import jwt
from sqlalchemy import create_engine, inspect


async def asgi_request(method, path, body=None, token=None):
    """Exercise the real ASGI app without an extra HTTP client dependency."""
    messages = []
    payload = json.dumps(body).encode() if body is not None else b""
    headers = [(b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": headers,
        "client": ("127.0.0.1", 12345), "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        messages.append(message)

    await main.app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    response = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(response) if response else None


class TraineeAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = main.hash_password("test-password")

    @classmethod
    def tearDownClass(cls):
        database.engine.dispose()
        _database_dir.cleanup()

    def setUp(self):
        models.Base.metadata.drop_all(database.engine)
        models.Base.metadata.create_all(database.engine)
        self.db = database.SessionLocal()
        self.tokens = {}
        names = ["admin", "админ", "председатель", "председатель студсовета",
                 "заместитель председателя по IT", "зам. председателя", "секретарь",
                 "руководитель отдела", "заместитель руководителя отдела", "участник"]
        for index, name in enumerate(names):
            position = models.Position(name=name, can_edit_cards=True, can_manage_positions=True)
            self.db.add(position)
            self.db.flush()
            login = f"user{index}"
            user = models.User(login=login, first_name="Тест", last_name="Участник",
                               password_hash=self.password_hash, position_id=position.id)
            self.db.add(user)
            self.tokens[name] = main.create_access_token({"sub": login})
        category = models.Category(name="Вводные материалы")
        hidden_category = models.Category(name="Закрытая тема")
        self.db.add_all([category, hidden_category])
        self.db.flush()
        self.category_id = category.id
        self.db.add_all([
            models.Card(title="Открытая сотрудникам", category_id=category.id),
            models.Card(title="Закрытая", category_id=hidden_category.id),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def request(self, method, path, body=None, token=None):
        return asyncio.run(asgi_request(method, path, body, token))

    def rotate(self):
        status, result = self.request("POST", "/api/trainee-code", token=self.tokens["admin"])
        self.assertEqual(status, 200, result)
        return result["code"]

    def trainee_token(self):
        code = self.rotate()
        status, result = self.request("POST", "/api/trainee/login", {"code": code})
        self.assertEqual(status, 200, result)
        return result["access_token"]

    def shared_card(self):
        status, result = self.request("POST", "/api/cards", {
            "title": "Для стажёра", "content": "# Привет\n:::users IT",
            "category_id": self.category_id, "trainee_access": True,
            "access_logins": "private-member", "access_positions": "секретарь",
        }, self.tokens["admin"])
        self.assertEqual(status, 201, result)
        self.assertTrue(result.get("trainee_access"), result)
        return result["id"]

    def test_code_requires_real_authorized_manager_role(self):
        for name, token in self.tokens.items():
            allowed = name in {"admin", "админ", "председатель", "председатель студсовета",
                               "заместитель председателя по IT", "зам. председателя"}
            for method in ["GET", "POST"]:
                with self.subTest(role=name, method=method):
                    status, _ = self.request(method, "/api/trainee-code", token=token)
                    self.assertEqual(status, 200 if allowed else 403)

    def test_no_code_until_first_generation_and_invalid_code_denied(self):
        status, result = self.request("GET", "/api/trainee-code", token=self.tokens["admin"])
        self.assertEqual(status, 200)
        self.assertIsNone(result["code"])
        self.assertEqual(self.request("POST", "/api/trainee/login", {"code": "wrong"})[0], 401)
        code = self.rotate()
        self.assertGreaterEqual(len(code), 16)
        self.assertEqual(self.request("POST", "/api/trainee/login", {"code": "wrong"})[0], 401)

    def test_session_is_30_days_without_creating_a_user(self):
        count = self.db.query(models.User).count()
        code = self.rotate()
        status, result = self.request("POST", "/api/trainee/login", {"code": f" {code}\n"})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["expires_in"], 30 * 24 * 60 * 60)
        payload = jwt.decode(result["access_token"], main.SECRET_KEY, algorithms=["HS256"])
        self.assertEqual(payload["type"], "trainee")
        self.assertAlmostEqual(payload["exp"] - datetime.now(timezone.utc).timestamp(), 2592000, delta=5)
        self.assertEqual(self.request("GET", "/api/trainee/session", token=result["access_token"])[0], 200)
        self.assertEqual(self.db.query(models.User).count(), count)

    def test_rotation_revokes_all_old_tokens_and_old_code(self):
        old_code = self.rotate()
        _, old = self.request("POST", "/api/trainee/login", {"code": old_code})
        new_code = self.rotate()
        self.assertNotEqual(new_code, old_code)
        self.assertEqual(self.request("POST", "/api/trainee/login", {"code": old_code})[0], 401)
        for path in ["session", "cards", "cards/1", "categories"]:
            self.assertEqual(self.request("GET", f"/api/trainee/{path}", token=old["access_token"])[0], 401)
        self.assertEqual(self.request("POST", "/api/trainee/login", {"code": new_code})[0], 200)

    def test_expired_and_wrong_type_tokens_are_rejected(self):
        token = self.trainee_token()
        payload = jwt.decode(token, main.SECRET_KEY, algorithms=["HS256"])
        payload["exp"] = datetime.utcnow() - timedelta(seconds=1)
        expired = jwt.encode(payload, main.SECRET_KEY, algorithm="HS256")
        for bad_token in [expired, self.tokens["admin"], "not-a-jwt"]:
            self.assertEqual(self.request("GET", "/api/trainee/cards", token=bad_token)[0], 401)

    def test_trainee_cannot_use_any_member_endpoints_or_manage_code(self):
        token = self.trainee_token()
        for method, path in [
            ("GET", "/api/user/me"), ("GET", "/api/users"), ("GET", "/api/departments"),
            ("GET", "/api/positions"), ("GET", "/api/cards"), ("GET", "/api/categories"),
            ("GET", "/api/cards/1/history"), ("GET", "/api/cards/1/comments"),
            ("POST", "/api/cards"), ("PUT", "/api/cards/1"), ("DELETE", "/api/cards/1"),
            ("GET", "/api/trainee-code"), ("POST", "/api/trainee-code"),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, {}, token)[0], 401)

    def test_guest_cards_filtering_no_member_access_lists_or_visits(self):
        card_id = self.shared_card()
        token = self.trainee_token()
        status, cards = self.request("GET", "/api/trainee/cards", token=token)
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in cards], [card_id])
        for card in cards:
            self.assertNotIn("access_positions", card)
            self.assertNotIn("access_logins", card)
        status, categories = self.request("GET", "/api/trainee/categories", token=token)
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in categories], [self.category_id])
        status, card = self.request("GET", f"/api/trainee/cards/{card_id}", token=token)
        self.assertEqual(status, 200)
        self.assertNotIn("access_logins", card)
        self.assertEqual(self.db.query(models.CardVisit).count(), 0)
        for hidden in [1, 2, 9999]:
            self.assertEqual(self.request("GET", f"/api/trainee/cards/{hidden}", token=token)[0], 404)

    def test_revoking_card_access_takes_effect_without_new_token(self):
        card_id = self.shared_card()
        token = self.trainee_token()
        status, result = self.request("PUT", f"/api/cards/{card_id}", {"trainee_access": False}, self.tokens["admin"])
        self.assertEqual(status, 200, result)
        self.assertEqual(self.request("GET", f"/api/trainee/cards/{card_id}", token=token)[0], 404)
        self.assertEqual(self.request("GET", "/api/trainee/categories", token=token)[1], [])
        self.db.expire_all()
        self.assertIn("стаж", self.db.query(models.ActivityLog).filter_by(action="update").one().details)

    def test_new_cards_are_private_by_default_and_null_flag_is_invalid(self):
        status, card = self.request("POST", "/api/cards", {
            "title": "Private", "category_id": self.category_id,
        }, self.tokens["admin"])
        self.assertEqual(status, 201)
        self.assertIs(card.get("trainee_access"), False)
        self.assertEqual(self.request("PUT", f"/api/cards/{card['id']}", {"trainee_access": None}, self.tokens["admin"])[0], 422)

    def test_code_persists_in_database_across_sessions(self):
        code = self.rotate()
        database.engine.dispose()
        status, result = self.request("GET", "/api/trainee-code", token=self.tokens["admin"])
        self.assertEqual(status, 200)
        self.assertEqual(result["code"], code)

    def test_simultaneous_first_generation_and_rotations_leave_one_current_code(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            codes = list(pool.map(lambda _: self.rotate(), range(6)))
        self.assertEqual(len(set(codes)), 6)
        _, current = self.request("GET", "/api/trainee-code", token=self.tokens["admin"])
        self.assertIn(current["code"], codes)
        for code in codes:
            self.assertEqual(self.request("POST", "/api/trainee/login", {"code": code})[0],
                             200 if code == current["code"] else 401)

    def test_missing_expiry_or_missing_credentials_are_rejected(self):
        token = self.trainee_token()
        payload = jwt.decode(token, main.SECRET_KEY, algorithms=["HS256"])
        del payload["exp"]
        invalid = jwt.encode(payload, main.SECRET_KEY, algorithm="HS256")
        for path in ["session", "cards", "categories"]:
            for credential in [invalid, None]:
                self.assertEqual(self.request("GET", f"/api/trainee/{path}", token=credential)[0], 401)

    def test_member_login_and_member_card_access_still_work(self):
        status, result = self.request("POST", "/api/login", {"login": "user0", "password": "test-password"})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/user/me", token=result["access_token"])[0], 200)
        self.assertEqual(len(self.request("GET", "/api/cards", token=result["access_token"])[1]), 2)

    def test_trainee_type_cannot_impersonate_existing_member(self):
        token = main.create_access_token({"sub": "user0", "type": "trainee"})
        self.assertEqual(self.request("GET", "/api/users", token=token)[0], 401)

    def test_deactivated_manager_cannot_rotate(self):
        user = self.db.query(models.User).filter_by(login="user0").one()
        user.is_deactivated = True
        self.db.commit()
        self.assertEqual(self.request("POST", "/api/trainee-code", token=self.tokens["admin"])[0], 403)

    def test_existing_card_schema_migration_preserves_data_and_is_idempotent(self):
        old_engine = create_engine("sqlite://")
        with old_engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE positions (id INTEGER PRIMARY KEY, name TEXT)")
            conn.exec_driver_sql("CREATE TABLE cards (id INTEGER PRIMARY KEY, title TEXT)")
            conn.exec_driver_sql("INSERT INTO cards (title) VALUES ('existing')")
        with patch.object(database, "engine", old_engine):
            database.ensure_schema_upgrades()
            database.ensure_schema_upgrades()
        self.assertIn("trainee_access", {c["name"] for c in inspect(old_engine).get_columns("cards")})
        with old_engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT title, trainee_access FROM cards").one(), ("existing", 0))
        old_engine.dispose()


if __name__ == "__main__":
    unittest.main()
