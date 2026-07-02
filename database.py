# Secret key for JWT tokens (generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))")
SECRET_KEY=your-secret-key-change-in-production

# Database URL (SQLite by default)
DATABASE_URL=sqlite:///./app.db

# Port for the application
PORT=8000

# CORS allowed origins (comma-separated)
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# JWT token expiration in days
ACCESS_TOKEN_EXPIRE_DAYS=30
