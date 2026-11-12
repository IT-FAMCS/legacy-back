import asyncio
import unittest
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

# This harness sets DATABASE_URL to a temporary DB before importing main.
from test_trainee_access import asgi_request
import main
import models
import database


class EquipmentTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        database.engine.dispose()

    def setUp(self):
        models.Base.metadata.drop_all(database.engine)
        models.Base.metadata.create_all(database.engine)
        self.db = database.SessionLocal()
        self.tokens = {}
        self.ids = {}
        names = ["admin", "админ", "председатель", "заместитель председателя по IT",
                 "зам. председателя", "председатель студсовета", "руководитель отдела",
                 "руководитель отдела/направления", "заместитель руководителя отдела",
                 "участник", "секретарь"]
        for index, name in enumerate(names):
            pos = models.Position(name=name, can_manage_positions=True, can_edit_any_user=True)
            self.db.add(pos)
            self.db.flush()
            user = models.User(login=f"equip{index}", first_name="Тест", last_name=name,
                               password_hash="unused", position_id=pos.id)
            self.db.add(user)
            self.db.flush()
            self.ids[name] = user.id
            self.tokens[name] = main.create_access_token({"sub": user.login})
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def request(self, method, path="", body=None, role="admin"):
        token = self.tokens.get(role) if role else None
        return asyncio.run(asgi_request(method, "/api/equipment" + path, body, token))

    def create(self, **values):
        status, result = self.request("POST", body={"name": "Микрофон", "description": "Радио", **values})
        self.assertEqual(status, 201, result)
        return result

    def test_options_expose_default_storage_and_exact_management_roles(self):
        for role in self.tokens:
            status, result = self.request("GET", "/options", role=role)
            self.assertEqual(status, 200, result)
            self.assertEqual(result["can_manage"], role not in ("участник", "секретарь"))
            self.assertEqual(result["locations"][0]["name"], "в главке")
            self.assertNotIn("password_hash", str(result))

    def test_only_managers_can_create_or_edit_items_and_storage(self):
        item = self.create()
        for role in ("участник", "секретарь"):
            for method, path, body in [
                ("POST", "", {"name": "Кабель"}),
                ("PATCH", f"/{item['id']}", {"name": "Кабель", "version": item["version"]}),
                ("POST", "/locations", {"name": "Склад"}),
            ]:
                self.assertEqual(self.request(method, path, body, role)[0], 403)
        for role in self.tokens:
            if role not in ("участник", "секретарь"):
                self.assertEqual(self.request("POST", body={"name": role}, role=role)[0], 201)

    def test_default_holder_is_storage_without_taken_date(self):
        item = self.create()
        self.assertEqual(item["location"]["name"], "в главке")
        self.assertIsNone(item["holder_user"])
        self.assertIsNone(item["taken_at"])

    def test_member_take_uses_authenticated_identity_and_server_time_and_records_history(self):
        item = self.create()
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        status, taken = self.request("POST", f"/{item['id']}/take", {"version": item["version"]}, "участник")
        self.assertEqual(status, 200, taken)
        self.assertEqual(taken["holder_user"]["id"], self.ids["участник"])
        self.assertEqual(taken["holder_user"]["login"], "equip9")
        self.assertIsNone(taken["location"])
        self.assertGreaterEqual(datetime.fromisoformat(taken["taken_at"]), before)
        status, history = self.request("GET", f"/{item['id']}/history", role="участник")
        self.assertEqual(status, 200)
        self.assertEqual([event["action"] for event in history], ["taken", "created"])
        self.assertEqual(history[0]["holder_login"], "equip9")
        self.assertEqual(history[0]["actor_login"], "equip9")
        self.assertEqual(history[0]["taken_at"], taken["taken_at"])
        forged = {"version": taken["version"], "holder_user_id": self.ids["admin"], "taken_at": "2000-01-01"}
        self.assertEqual(self.request("POST", f"/{item['id']}/take", forged, "участник")[0], 422)

    def test_manager_assign_return_and_date_correction_preserve_history(self):
        item = self.create()
        status, assigned = self.request("PATCH", f"/{item['id']}", {
            "version": item["version"], "holder_user_id": self.ids["участник"],
            "location_id": None, "taken_at": "2026-01-02T12:30:00+03:00"})
        self.assertEqual(status, 200, assigned)
        self.assertEqual(assigned["taken_at"], "2026-01-02T09:30:00")
        status, edited = self.request("PATCH", f"/{item['id']}", {"version": assigned["version"], "description": "Новое описание"})
        self.assertEqual(status, 200)
        self.assertEqual(edited["taken_at"], assigned["taken_at"])
        status, corrected = self.request("PATCH", f"/{item['id']}", {"version": edited["version"], "taken_at": "2026-01-01T08:00:00Z"})
        self.assertEqual(status, 200)
        default = self.request("GET", "/options")[1]["locations"][0]["id"]
        status, returned = self.request("PATCH", f"/{item['id']}", {"version": corrected["version"], "holder_user_id": None, "location_id": default})
        self.assertEqual(status, 200)
        self.assertIsNone(returned["taken_at"])
        self.assertIsNone(returned["holder_user"])
        history = self.request("GET", f"/{item['id']}/history", role="участник")[1]
        self.assertEqual(len(history), 4)
        self.assertEqual(history[2]["taken_at"], "2026-01-02T09:30:00")
        self.assertEqual(history[1]["taken_at"], "2026-01-01T08:00:00")

    def test_invalid_holders_blank_fields_and_dates_are_rejected(self):
        item = self.create()
        default = self.request("GET", "/options")[1]["locations"][0]["id"]
        for data in [
            {"holder_user_id": self.ids["участник"], "location_id": default},
            {"holder_user_id": None, "location_id": None},
            {"holder_user_id": 99999, "location_id": None},
            {"holder_user_id": None, "location_id": 99999},
            {"name": "   "}, {"name": None}, {"taken_at": "bad-date"},
            {"taken_at": "2026-01-01T00:00:00Z"},
        ]:
            with self.subTest(data=data):
                self.assertEqual(self.request("PATCH", f"/{item['id']}", {"version": item["version"], **data})[0], 422)
        user = self.db.get(models.User, self.ids["участник"])
        user.is_active = False
        self.db.commit()
        self.assertEqual(self.request("PATCH", f"/{item['id']}", {"version": item["version"], "holder_user_id": user.id, "location_id": None})[0], 422)

    def test_storage_creation_is_shared_and_case_insensitive(self):
        status, location = self.request("POST", "/locations", {"name": "  Склад  "})
        self.assertEqual(status, 201)
        self.assertEqual(location["name"], "Склад")
        self.assertEqual(self.request("POST", "/locations", {"name": "склад"})[0], 409)
        self.assertEqual(self.request("POST", "/locations", {"name": "   "})[0], 422)
        self.assertIn(location, self.request("GET", "/options", role="участник")[1]["locations"])

    def test_missing_guest_and_inactive_accounts_cannot_access_inventory_or_history(self):
        item = self.create()
        for path in ("", "/options", f"/{item['id']}/history"):
            self.assertIn(self.request("GET", path, role=None)[0], (401, 403))
            guest = main.create_access_token({"sub": "equip0", "type": "trainee"})
            self.assertEqual(asyncio.run(asgi_request("GET", "/api/equipment" + path, token=guest))[0], 401)
        user = self.db.get(models.User, self.ids["участник"])
        user.is_deactivated = True
        self.db.commit()
        self.assertEqual(self.request("GET", role="участник")[0], 403)

    def test_stale_versions_cannot_overwrite_holder_or_append_history(self):
        item = self.create()
        self.assertEqual(self.request("POST", f"/{item['id']}/take", {"version": item["version"]}, "участник")[0], 200)
        self.assertEqual(self.request("POST", f"/{item['id']}/take", {"version": item["version"]}, "admin")[0], 409)
        self.assertEqual(self.request("PATCH", f"/{item['id']}", {"version": item["version"], "name": "Stale"})[0], 409)
        self.assertEqual(len(self.request("GET", f"/{item['id']}/history")[1]), 2)

    def test_simultaneous_takes_have_one_winner_and_one_history_event(self):
        item = self.create()
        def take(role):
            return self.request("POST", f"/{item['id']}/take", {"version": item["version"]}, role)[0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(take, ["участник", "admin"]))
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(len(self.request("GET", f"/{item['id']}/history")[1]), 2)

    def test_history_and_holder_survive_new_database_session(self):
        item = self.create()
        self.request("POST", f"/{item['id']}/take", {"version": item["version"]}, "участник")
        self.db.close()
        self.db = database.SessionLocal()
        listed = self.request("GET")[1]
        self.assertEqual(listed[0]["holder_user"]["id"], self.ids["участник"])
        self.assertEqual(len(self.request("GET", f"/{item['id']}/history")[1]), 2)

    def test_unknown_item_returns_404(self):
        for method, path, body in [("GET", "/999/history", None), ("POST", "/999/take", {"version": 1}), ("PATCH", "/999", {"version": 1, "name": "test"})]:
            self.assertEqual(self.request(method, path, body)[0], 404)

    def test_unrelated_deputy_role_cannot_manually_manage_equipment(self):
        user = self.db.get(models.User, self.ids["секретарь"])
        user.position_ref.name = "заместитель руководителя проекта"
        self.db.commit()
        self.assertFalse(self.request("GET", "/options", role="секретарь")[1]["can_manage"])
        self.assertEqual(self.request("POST", body={"name": "Кабель"}, role="секретарь")[0], 403)

    def test_only_executive_can_archive_items_and_history_survives(self):
        item = self.create()
        for role in ("участник", "секретарь", "руководитель отдела", "заместитель руководителя отдела", "председатель студсовета"):
            self.assertEqual(self.request("DELETE", f"/{item['id']}", {"version": item["version"]}, role)[0], 403)
        for role in ("admin", "админ", "председатель", "заместитель председателя по IT", "зам. председателя"):
            current = self.create()
            self.assertEqual(self.request("DELETE", f"/{current['id']}", {"version": current["version"]}, role)[0], 200)
            self.assertNotIn(current["id"], [value["id"] for value in self.request("GET")[1]])
            self.assertEqual(self.request("POST", f"/{current['id']}/take", {"version": current["version"]}, "участник")[0], 404)
            history = self.request("GET", f"/{current['id']}/history", role="участник")[1]
            self.assertEqual([event["action"] for event in history], ["deleted", "created"])

    def test_archive_rejects_a_stale_version(self):
        item = self.create()
        self.request("POST", f"/{item['id']}/take", {"version": item["version"]}, "участник")
        self.assertEqual(self.request("DELETE", f"/{item['id']}", {"version": item["version"]})[0], 409)
        self.assertEqual(len(self.request("GET")[1]), 1)
