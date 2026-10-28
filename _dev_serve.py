"""اجرای موقت وب‌اپ روی پایگاه دادهٔ تستی — فقط برای بازبینی محلی، در بستهٔ نهایی نیست."""
import os
import shutil
import sys
import time

SRC = os.path.join("data", "bot.db")
DST = os.path.join("data", "_dev_test.db")
for suffix in ("", "-wal", "-shm"):
    path = DST + suffix
    if os.path.exists(path):
        os.remove(path)
shutil.copyfile(SRC, DST)

os.environ["DATABASE_URL"] = "sqlite:///" + DST
os.environ.setdefault("SESSION_SECRET", "dev-session-secret")
os.environ["WEBAPP_URL"] = "http://127.0.0.1:8137"

sys.path.insert(0, os.getcwd())
import uvicorn  # noqa: E402
import config  # noqa: E402
from database import db  # noqa: E402
import strategy_manager  # noqa: E402
import plans  # noqa: E402
from webapp.repo import repo  # noqa: E402
from webapp.auth import admin_link_token  # noqa: E402
from webapp.app import create_app  # noqa: E402

DEV_UID = 900001
DEV_PASS = "Dev#Test123"

db.init()
strategy_manager.seed_builtin()
plans.ensure_defaults()
repo.ensure_defaults()

db.x("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (DEV_UID,))
db.grant_webapp_sub(DEV_UID, 365)
repo.set_password(DEV_UID, DEV_PASS)
db.x("UPDATE webapp_users SET username = 'devuser', first_name = 'کاربر آزمایشی', access_enabled = 1 WHERE user_id = ?", (DEV_UID,))

DEV_UID2 = 900002
db.x("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (DEV_UID2,))
repo.set_password(DEV_UID2, "Free#Test123")
db.x("UPDATE webapp_users SET username = 'devfree', first_name = 'کاربر بدون اشتراک', access_enabled = 1 WHERE user_id = ?", (DEV_UID2,))

# چند سیگنال نمونه تا پیمایش «سایر نمادها» قابل آزمایش باشد
now = time.time()
for i, (sym, exch, tf, strat) in enumerate([
    ("BTCUSDT", "Toobit", "1h", "خط روند صعودی"),
    ("ETHUSDT", "Toobit", "4h", "حمایت کلاسیک"),
    ("SOLUSDT", "Binance", "1d", "مقاومت نواحی"),
]):
    db.x(
        "INSERT INTO structural_signals(symbol, exchange, timeframe, strategy_key, strategy_title, strategy_type, "
        "signal_type, direction, price, candle_ts, details, is_active, detected_at) "
        "VALUES(?, ?, ?, ?, ?, '', 'حمایت', 'LONG', ?, ?, '', 1, ?) ON CONFLICT DO NOTHING",
        (sym, exch, tf, "dev_key_%d" % i, strat, 1000 + i, now - 600 * i, now - 600 * i),
    )

print("DEV USER   devuser / %s  (اشتراک فعال)" % DEV_PASS, flush=True)
print("DEV FREE   devfree / Free#Test123  (بدون اشتراک)", flush=True)
print("DEV ADMIN  http://127.0.0.1:8137/admin?token=" + admin_link_token(config.ADMIN_UID), flush=True)

uvicorn.run(create_app(None), host="127.0.0.1", port=8137, log_level="warning")
