"""Isolated equipment browser fixture; run from legacy-back, never uses app.db."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="legacy-equipment-browser-") as directory:
        os.environ["DATABASE_URL"] = "sqlite:///" + directory.replace("\\", "/") + "/test.db"
        os.environ["SECRET_KEY"] = "equipment-browser-test-only"
        os.environ["ALLOWED_ORIGINS"] = "http://127.0.0.1:4174"
        import main
        import models
        import database
        import uvicorn

        with database.SessionLocal() as db:
            password = main.hash_password("smoke-password")
            for login, title, first_name in [("admin", "admin", "Админ"), ("member", "участник", "Участник")]:
                position = models.Position(name=title, can_manage_positions=login == "admin",
                                           can_register_users=login == "admin", can_edit_categories=login == "admin")
                db.add(position)
                db.flush()
                db.add(models.User(login=login, first_name=first_name, last_name="Тестовый",
                                   password_hash=password, position_id=position.id))
            db.commit()
        try:
            uvicorn.run(main.app, host="127.0.0.1", port=8012, log_level="warning")
        finally:
            database.engine.dispose()
