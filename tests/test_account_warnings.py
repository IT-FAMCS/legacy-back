import asyncio
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, inspect
from test_trainee_access import asgi_request
import database
import main
import models


class AccountWarningTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        database.engine.dispose()

    def setUp(self):
        models.Base.metadata.drop_all(database.engine)
        models.Base.metadata.create_all(database.engine)
        self.db = database.SessionLocal()
        self.tokens = {}
        self.ids = {}
        names = ["admin", "админ", "председатель", "заместитель председателя", "заместитель председателя по IT",
                 "председатель студсовета", "руководитель отдела", "заместитель руководителя отдела", "секретарь", "участник", "заместитель руководителя проекта"]
        for i, name in enumerate(names):
            pos = models.Position(name=name, hierarchy_level=0 if name == "admin" else 6,
                can_register_users=name == "admin", can_edit_any_user=True)
            self.db.add(pos)
            self.db.flush()
            user = models.User(login=f"warn{i}", first_name="Тест", last_name=name,
                               password_hash="unused", position_id=pos.id)
            self.db.add(user)
            self.db.flush()
            self.ids[name] = user.id
            self.tokens[name] = main.create_access_token({"sub": user.login})
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def request(self, method, path, data=None, role="admin"):
        return asyncio.run(asgi_request(method, path, data, self.tokens.get(role) if role else None))

    def issue(self, reason="Не вернул оборудование", role="admin"):
        status, data = self.request("POST", f"/api/users/{self.ids['участник']}/warnings", {"reason": reason}, role)
        self.assertEqual(status, 201, data)
        return data

    def test_warning_permissions_separate_viewers_and_managers(self):
        managers = {"admin", "админ", "председатель", "заместитель председателя", "заместитель председателя по IT"}
        viewers = managers | {"председатель студсовета", "руководитель отдела", "заместитель руководителя отдела"}
        for role in self.tokens:
            status, access = self.request("GET", "/api/warnings/permissions", role=role)
            self.assertEqual(status, 200)
            self.assertEqual(access, {"can_view": role in viewers, "can_manage": role in managers})
            self.assertEqual(self.request("GET", "/api/warnings/counts", role=role)[0], 200 if role in viewers else 403)
            self.assertEqual(self.request("GET", f"/api/users/{self.ids['участник']}/warnings", role=role)[0], 200 if role in viewers else 403)
            issued = self.request("POST", f"/api/users/{self.ids['участник']}/warnings", {"reason": "Причина"}, role)
            self.assertEqual(issued[0], 201 if role in managers else 403)

    def test_multiple_warnings_count_only_active_and_keep_removal_audit(self):
        first, second = self.issue(), self.issue("Опоздал на смену")
        key = str(self.ids["участник"])
        self.assertEqual(self.request("GET", "/api/warnings/counts")[1][key], 2)
        status, removed = self.request("POST", f"/api/warnings/{first['id']}/remove", {"reason": "Вопрос решён"}, "заместитель председателя")
        self.assertEqual(status, 200, removed)
        self.assertEqual(removed["reason"], "Не вернул оборудование")
        self.assertEqual(removed["removal_reason"], "Вопрос решён")
        self.assertIsNotNone(removed["removed_at"])
        self.assertEqual(removed["removed_by"]["login"], "warn3")
        self.assertEqual(removed["issued_by"]["login"], "warn0")
        self.assertEqual(self.request("GET", "/api/warnings/counts")[1][key], 1)
        history = self.request("GET", f"/api/users/{self.ids['участник']}/warnings", role="руководитель отдела")[1]
        self.assertEqual([value["id"] for value in history], [second["id"], first["id"]])
        self.assertEqual(history[1]["removal_reason"], "Вопрос решён")
        self.assertEqual(self.request("POST", f"/api/warnings/{first['id']}/remove", {"reason": "Снова"})[0], 409)

    def test_issue_and_removal_require_nonblank_reasons(self):
        warning = self.issue()
        for data in ({}, {"reason": ""}, {"reason": "  "}, {"reason": "x" * 4001}):
            self.assertEqual(self.request("POST", f"/api/users/{self.ids['участник']}/warnings", data)[0], 422)
            self.assertEqual(self.request("POST", f"/api/warnings/{warning['id']}/remove", data)[0], 422)
        for role in ("председатель студсовета", "руководитель отдела", "заместитель руководителя отдела", "секретарь", "участник"):
            self.assertEqual(self.request("POST", f"/api/warnings/{warning['id']}/remove", {"reason": "Удалить"}, role)[0], 403)

    def test_missing_guest_inactive_and_unknown_targets_are_denied(self):
        for path in ("/api/warnings/counts", f"/api/users/{self.ids['участник']}/warnings"):
            self.assertIn(self.request("GET", path, role=None)[0], (401, 403))
            guest = main.create_access_token({"sub": "warn0", "type": "trainee"})
            self.assertEqual(asyncio.run(asgi_request("GET", path, token=guest))[0], 401)
        self.assertEqual(self.request("POST", "/api/users/99999/warnings", {"reason": "test"})[0], 404)
        self.assertEqual(self.request("POST", "/api/warnings/99999/remove", {"reason": "test"})[0], 404)
        user = self.db.get(models.User, self.ids["admin"])
        user.is_active = False
        self.db.commit()
        self.assertEqual(self.request("GET", "/api/warnings/counts")[0], 403)

    def test_warning_actors_and_reasons_are_preserved_when_profile_is_renamed(self):
        entry = self.issue()
        user = self.db.get(models.User, self.ids["admin"])
        user.first_name = "Изменено"
        self.db.commit()
        history = self.request("GET", f"/api/users/{self.ids['участник']}/warnings")[1]
        self.assertEqual(history[0]["issued_by"], entry["issued_by"])
        ordinary = self.request("GET", "/api/users", role="участник")[1]
        self.assertNotIn("warnings", str(ordinary))
        self.assertNotIn("Не вернул оборудование", str(ordinary))

    def test_simultaneous_removal_has_one_winner_and_preserves_one_reason(self):
        warning = self.issue()
        def remove(reason):
            return self.request("POST", f"/api/warnings/{warning['id']}/remove", {"reason": reason})[0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(remove, ["Причина A", "Причина B"]))
        self.assertEqual(sorted(statuses), [200, 409])

    def test_contact_fields_round_trip_clear_and_validate(self):
        path = "/api/user/change/warn9"
        status, user = self.request("PUT", path, {"phone": "+375 (29) 123-45-67", "education_format": "бюджет"})
        self.assertEqual(status, 200, user)
        self.assertEqual(user["phone"], "+375 (29) 123-45-67")
        self.assertEqual(user["education_format"], "бюджет")
        self.assertEqual(self.request("PUT", path, {"education_format": "платное"})[0], 200)
        for invalid in ({"phone": "телефон"}, {"education_format": "иной"}):
            self.assertEqual(self.request("PUT", path, invalid)[0], 422)
        status, cleared = self.request("PUT", path, {"phone": None, "education_format": None})
        self.assertEqual(status, 200)
        self.assertIsNone(cleared["phone"])
        self.assertIsNone(cleared["education_format"])

    def test_new_contact_fields_work_for_single_and_bulk_registration(self):
        base = {"password": "valid-test-password", "first_name": "Имя", "last_name": "Фамилия", "position": "участник",
                "phone": "+375291234567", "education_format": "платное"}
        status, user = self.request("POST", "/api/register", {**base, "login": "single"})
        self.assertEqual(status, 201, user)
        self.assertEqual(user["education_format"], "платное")
        status, users = self.request("POST", "/api/register/bulk", {"users": [{**base, "login": "batch"}]})
        self.assertEqual(status, 201, users)
        self.assertEqual(users[0]["phone"], "+375291234567")

    def test_schema_upgrades_preserve_existing_data_and_are_idempotent(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT)")
                conn.exec_driver_sql("INSERT INTO users VALUES (1, 'existing')")
                conn.exec_driver_sql("CREATE TABLE positions (id INTEGER PRIMARY KEY, name TEXT)")
                conn.exec_driver_sql("CREATE TABLE cards (id INTEGER PRIMARY KEY)")
                conn.exec_driver_sql("CREATE TABLE equipment_items (id INTEGER PRIMARY KEY, name TEXT)")
                conn.exec_driver_sql("INSERT INTO equipment_items VALUES (1, 'existing item')")
            with patch.object(database, "engine", engine):
                database.ensure_schema_upgrades()
                database.ensure_schema_upgrades()
            names = {column["name"] for column in inspect(engine).get_columns("users")}
            self.assertTrue({"phone", "education_format"}.issubset(names))
            self.assertIn("deleted_at", {column["name"] for column in inspect(engine).get_columns("equipment_items")})
            with engine.connect() as conn:
                self.assertEqual(conn.exec_driver_sql("SELECT login, phone FROM users").one(), ("existing", None))
                self.assertEqual(conn.exec_driver_sql("SELECT name, deleted_at FROM equipment_items").one(), ("existing item", None))
        finally:
            engine.dispose()
