import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("CHRONOSWAP_DB", "/tmp/chronoswap.sqlite3")
os.environ.setdefault("CHRONOSWAP_SEED_DEMO", "1")
os.environ.setdefault("CHRONOSWAP_COOKIE_SECURE", "1")
os.environ.setdefault("CHRONOSWAP_DEV_EMAIL_LINKS", "1")
if os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"):
    os.environ.setdefault("CHRONOSWAP_PUBLIC_URL", "https://" + os.environ["VERCEL_PROJECT_PRODUCTION_URL"])
elif os.environ.get("VERCEL_URL"):
    os.environ.setdefault("CHRONOSWAP_PUBLIC_URL", "https://" + os.environ["VERCEL_URL"])

import server  # noqa: E402

server.init_db()
server.bootstrap_admin()

handler = server.Handler
