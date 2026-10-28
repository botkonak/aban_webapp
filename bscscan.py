"""بررسی تراکنش‌های USDT در شبکه BSC (BEP20) با استفاده از NodeReal (MegaNode)."""
import logging
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

import aiohttp

from database import db
from plans import CONTRACT_USDT_BEP20 as USDT_CONTRACT_BEP20

log = logging.getLogger("bscscan")

# آدرس پایه NodeReal برای BSC Mainnet
NODEREAL_BASE_URL = "https://bsc-mainnet.nodereal.io/v1/"
USDT_DECIMALS = 18


def _redact(url: str) -> str:
    """کلید API در مسیر URL است؛ لاگ نباید آن را ثبت کند."""
    base = url.rsplit("/", 1)[0] if url else ""
    return base + "/***" if base else ""


def _hex_to_int(v: Any) -> int:
    try:
        s = str(v or "0x0").strip()
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except (TypeError, ValueError):
        return 0


async def lookup_transfer(tx_hash: str, expected_wallet: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    هش تراکنش را از NodeReal استعلام می‌کند و جزئیات واریز USDT به ولت ربات را برمی‌گرداند.
    خروجی: ({"amount": Decimal, "sender": str, "receiver": str, "hash": str}, "") یا (None, پیام خطای قابل نمایش به کاربر).
    مقایسه‌ی مبلغ و بررسی تکراری بودن هش بر عهده‌ی فراخواننده است.
    """
    tx_hash = (tx_hash or "").strip()
    expected_wallet = (expected_wallet or "").strip()

    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        return None, "فرمت هش تراکنش اشتباه است. باید با 0x شروع شود و ۶۶ کاراکتر باشد."
    try:
        int(tx_hash[2:], 16)
    except ValueError:
        return None, "فرمت هش تراکنش اشتباه است. فقط حروف a-f و ارقام مجاز است."
    if not expected_wallet:
        return None, "آدرس ولت مقصد در تنظیمات ربات ثبت نشده است."

    api_key = (db.get("nodereal_api_key") or db.get("bscscan_api_key") or "").strip()
    if not api_key:
        return None, "کلید API بلاک‌چین (NodeReal) در پنل مدیریت وب‌اپ ثبت نشده است."

    url = f"{NODEREAL_BASE_URL}{api_key}"
    payload = {
        "jsonrpc": "2.0",
        "method": "nr_getTransactionByAddress",
        "params": [{
            "category": ["20"],          # فقط توکن‌های BEP20
            "addressType": "to",         # تراکنش‌هایی که به این آدرس ارسال شده‌اند
            "address": expected_wallet,
            "order": "desc",
            "maxCount": "0x3E8",         # بررسی ۱۰۰۰ تراکنش آخر (سقف مجاز API)؛ تراکنش‌های اسپم جای پرداخت واقعی را نگیرند
            "fromBlock": "0x0",
            "toBlock": "latest",
        }],
        "id": 1,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None, "خطا در ارتباط با شبکه بلاک‌چین. کمی بعد دوباره امتحان کنید."
                data = await resp.json(content_type=None)
    except Exception as exc:
        log.error("NodeReal API error at %s: %s", _redact(url), type(exc).__name__)
        return None, "خطا در استعلام تراکنش از شبکه. کمی بعد دوباره امتحان کنید."

    if not isinstance(data, dict):
        return None, "پاسخ نامعتبر از شبکه بلاک‌چین دریافت شد."
    if data.get("error"):
        msg = (data["error"] or {}).get("message", "خطای ناشناخته") if isinstance(data["error"], dict) else str(data["error"])
        log.error("NodeReal API error: %s", msg)
        return None, "خطا در استعلام تراکنش از شبکه. لطفاً مطمئن شوید هش را درست وارد کرده‌اید."

    result = data.get("result")
    if not isinstance(result, dict):
        return None, "پاسخ نامعتبر از شبکه بلاک‌چین دریافت شد."
    transfers = result.get("transfers") or []
    target = next((t for t in transfers if isinstance(t, dict) and str(t.get("hash", "")).lower() == tx_hash.lower()), None)
    if target is None:
        return None, "تراکنشی با این هش برای آدرس ولت ما پیدا نشد (اگر تازه واریز کرده‌اید چند دقیقه بعد دوباره امتحان کنید)."

    if str(target.get("contractAddress", "")).lower() != USDT_CONTRACT_BEP20.lower():
        return None, "این تراکنش مربوط به توکن USDT معتبر نیست."
    if str(target.get("receiptsStatus")) != "1":
        return None, "این تراکنش در شبکه ناموفق بوده است."
    to_addr = str(target.get("to") or "")
    if not to_addr or to_addr.lower() != expected_wallet.lower():
        return None, "این تراکنش به آدرس ولت ما ارسال نشده است."

    amount = Decimal(_hex_to_int(target.get("value"))) / (Decimal(10) ** USDT_DECIMALS)
    return {
        "amount": amount,
        "sender": str(target.get("from") or ""),
        "receiver": to_addr or expected_wallet,
        "hash": tx_hash.lower(),
    }, ""
