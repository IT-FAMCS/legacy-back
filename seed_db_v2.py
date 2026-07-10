from database import SessionLocal, engine, ensure_schema_upgrades
from passlib.context import CryptContext
import models

# Create all tables first, then patch any that already existed before this
# column/table was added — this script runs before gunicorn/main.py in the
# Dockerfile CMD and queries models.User directly below, so it needs the
# patch applied here too, not just in main.py.
models.Base.metadata.create_all(bind=engine)
ensure_schema_upgrades()

pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')
db = SessionLocal()

# Create default positions
positions_data = [
    {
        "name": "admin",
        "hierarchy_level": 1,
        "can_register_users": True,
        "can_edit_categories": True,
        "can_delete_categories": False,
        "can_edit_cards": True,
        "can_delete_cards": True,
        "can_edit_any_user": True,
        "can_manage_positions": True,
    },
    {
        "name": "председатель",
        "hierarchy_level": 2,
        "can_register_users": True,
        "can_edit_categories": True,
        "can_delete_categories": False,
        "can_edit_cards": True,
        "can_delete_cards": True,
        "can_edit_any_user": True,
        "can_manage_positions": True,
    },
    {
        "name": "председатель студсовета",
        "hierarchy_level": 2,
        "can_register_users": True,
        "can_edit_categories": True,
        "can_delete_categories": False,
        "can_edit_cards": True,
        "can_delete_cards": True,
        "can_edit_any_user": True,
        "can_manage_positions": True,
    },
    {
        "name": "заместитель председателя",
        "hierarchy_level": 2,
        "can_register_users": True,
        "can_edit_categories": True,
        "can_delete_categories": False,
        "can_edit_cards": True,
        "can_delete_cards": True,
        "can_edit_any_user": True,
        "can_manage_positions": True,
    },
    {
        "name": "секретарь",
        "hierarchy_level": 3,
        "can_register_users": True,
        "can_edit_categories": True,
        "can_delete_categories": False,
        "can_edit_cards": True,
        "can_delete_cards": False,
        "can_edit_any_user": True,
        "can_manage_positions": False,
    },
    {
        "name": "руководитель отдела/направления",
        "hierarchy_level": 4,
        "can_register_users": False,
        "can_edit_categories": False,
        "can_delete_categories": False,
        "can_edit_cards": True,
        "can_delete_cards": False,
        "can_edit_any_user": False,
        "can_manage_positions": False,
    },
    {
        "name": "заместитель руководителя отдела",
        "hierarchy_level": 5,
        "can_register_users": False,
        "can_edit_categories": False,
        "can_delete_categories": False,
        "can_edit_cards": True,
        "can_delete_cards": False,
        "can_edit_any_user": False,
        "can_manage_positions": False,
    },
    {
        "name": "участник",
        "hierarchy_level": 6,
        "can_register_users": False,
        "can_edit_categories": False,
        "can_delete_categories": False,
        "can_edit_cards": False,
        "can_delete_cards": False,
        "can_edit_any_user": False,
        "can_manage_positions": False,
    },
]

positions = {}
for pos_data in positions_data:
    existing = db.query(models.Position).filter(models.Position.name == pos_data["name"]).first()
    if not existing:
        position = models.Position(**pos_data)
        db.add(position)
        positions[pos_data["name"]] = position
        print(f"Created position: {pos_data['name']}")
    else:
        positions[pos_data["name"]] = existing
        print(f"Position exists: {pos_data['name']}")

db.commit()

# Create only admin user
existing_admin = db.query(models.User).filter(models.User.login == "admin").first()
if not existing_admin:
    position = positions.get("admin")
    if position:
        admin = models.User(
            login="admin",
            password_hash=pwd_context.hash("admin123"),
            first_name="Админ",
            last_name="Системный",
            position_id=position.id,
            is_active=True,
            is_deactivated=False
        )
        db.add(admin)
        db.commit()
        
        # Create default department and assign to admin
        dept = models.Department(name="Администрирование", description="Административный отдел")
        db.add(dept)
        db.commit()
        
        # Link admin to department
        user_dept = models.UserDepartment(user_id=admin.id, department_id=dept.id)
        db.add(user_dept)
        print(f"Created admin user with department")
else:
    print(f"Admin user exists")

db.commit()

# Create categories
categories_data = [
    {"name": "Отделы и направления", "description": "Информация об отделах и направлениях организации"},
    {"name": "Мероприятия", "description": "Информация о мероприятиях организации"},
    {"name": "Общая информация", "description": "Положение, правила и другая общая информация"},
]

for cat_data in categories_data:
    existing = db.query(models.Category).filter(models.Category.name == cat_data["name"]).first()
    if not existing:
        cat = models.Category(
            name=cat_data["name"],
            description=cat_data["description"]
        )
        db.add(cat)
        print(f"Created category: {cat_data['name']}")
    else:
        print(f"Category exists: {cat_data['name']}")

db.commit()
db.close()

print("\n=== Database populated successfully! ===")
print("Only admin user created. Use admin/admin123 to login.")
