from .settings import *

# Speed up test execution by using MD5 password hasher
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]


# Disable migrations during test database generation for speed
class DisableMigrations:

    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = DisableMigrations()

# Silence Telegram notifications during tests
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# Force database host to localhost for all test runs
if (
    DATABASES
    and "default" in DATABASES
    and DATABASES["default"].get("ENGINE") == "django.db.backends.postgresql"
):
    DATABASES["default"]["HOST"] = "localhost"
