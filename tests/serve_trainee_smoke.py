"""Isolated local browser-test fixture; never uses the real app.db.

Run from legacy-back: python tests/serve_trainee_smoke.py
Then from legacy-frontend: node --experimental-websocket tests/browser-trainee-smoke.mjs
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="legacy-browser-smoke-") as directory:
        os.environ["DATABASE_URL"] = "sqlite:///" + directory.replace("\\", "/") + "/smoke.db"
        os.environ["SECRET_KEY"] = "local-browser-smoke-test-only"
        os.environ["ALLOWED_ORIGINS"] = "http://127.0.0.1:4173"
        import main
        import models
        import database
        import uvicorn

        with database.SessionLocal() as db:
            admin = models.Position(name="admin", can_edit_cards=True, can_manage_positions=True)
            secretary = models.Position(name="секретарь", can_edit_cards=True)
            db.add_all([admin, secretary])
            db.flush()
            for name, position in [("admin", admin), ("secretary", secretary)]:
                db.add(models.User(login=name, first_name="Smoke", last_name="Test", position_id=position.id,
                                   password_hash=main.hash_password("smoke-password")))
            category = models.Category(name="Стажировка", description="Первые шаги")
            private = models.Category(name="Закрытая категория")
            db.add_all([category, private])
            db.flush()
            db.add_all([
                models.Card(title="Привет, стажёр", content="# Первые шаги\n\n:::users IT\n\n- [ ] Прочитать",
                            category_id=category.id, trainee_access=True, access_logins="admin"),
                models.Card(title="Секретная карточка", content="Закрытый материал", category_id=private.id),
                models.TraineeCode(id=1, code="SMOKE-CODE-2026", version="browser-test-version"),
            ])
            db.commit()
        try:
            uvicorn.run(main.app, host="127.0.0.1", port=8000, log_level="warning")
        finally:
            database.engine.dispose()
