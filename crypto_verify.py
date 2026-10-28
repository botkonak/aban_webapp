"""تأیید خودکار واریز رمزارز روی چند شبکه (BSC، TRON، Arbitrum، TON).

هر شبکه با API رسمی خودش بررسی می‌شود:
  - BSC (BEP20 / BNB): NodeReal
  - TRON (TRC20 / TRX): Tronscan
  - Arbitrum (ARB): Arbiscan (با fallback به Etherscan V2)
  - TON (DOGS): TonAPI
کلیدهای API فقط از تنظیمات وب‌اپ خوانده می‌شوند و هیچ‌جا در کد نیستند.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

import aiohttp

import bscscan
import plans
from database import db

log = logging.getLogger("crypto_verify")

EVM_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
TRON_RE = re.compile(r"^[0-9a-fA-F]{64}$")
TON_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
TON_B64_RE = re.compile(r"^[A-Za-z0-9_\-+/=]{40,60}$")

# آدرس قرارداد توکن‌های رسمی از plans (منبع یکتا) خوانده می‌شود؛ نام‌های عمومی TRON_USDT و
# ARB_TOKEN برای سازگاری با فراخوان‌های قبلی حفظ شده‌اند.
TRON_USDT = plans.CONTRACT_USDT_TRC20
ARB_TOKEN = plans.CONTRACT_ARB_ARBITRUM
DOGS_JETTON = plans.CONTRACT_DOGS_TON
NODEREAL_BASE = "https://bsc-mainnet.nodereal.io/v1/"
ARBISCAN_API = "https://api.arbiscan.io/api"
ETHERSCAN_V2_API = "https://api.etherscan.io/v2/api"
ARB_RPC = "https://arb1.arbitrum.io/rpc"
TRONSCAN_API = "https://apilist.tronscanapi.com/api/transaction-info"
TONAPI_BASE = "https://tonapi.io/v2"

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


@dataclass
class Verification:
    ok: bool = False
    amount: Decimal = Decimal(0)
    sender: str = ""
    receiver: str = ""
    error: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def fail(self, message: str) -> "Verification":
        self.ok = False
        self.error = message
        return self


def api_key(name: str) -> str:
    if name == "nodereal":
        return (db.get("nodereal_api_key") or db.get("bscscan_api_key") or "").strip()
    return (db.get(name) or "").strip()


# ----------------------------------------------------------------------------
# نرمال‌سازی هش و آدرس
# ----------------------------------------------------------------------------
def normalize_hash(chain: str, tx_hash: str) -> Tuple[str, str]:
    raw = (tx_hash or "").strip()
    if chain in (plans.CHAIN_BSC, plans.CHAIN_ARBITRUM):
        if not EVM_RE.match(raw):
            return "", "فرمت هش تراکنش اشتباه است؛ باید با 0x شروع شود و ۶۶ کاراکتر باشد."
        return raw.lower(), ""
    if chain == plans.CHAIN_TRON:
        h = raw[2:] if raw.lower().startswith("0x") else raw
        if not TRON_RE.match(h):
            return "", "فرمت هش تراکنش TRON اشتباه است؛ باید ۶۴ کاراکتر هگز باشد."
        return h.lower(), ""
    if chain == plans.CHAIN_TON:
        if TON_HEX_RE.match(raw):
            return raw.lower(), ""
        if TON_B64_RE.match(raw):
            return raw, ""
        return "", "فرمت هش تراکنش TON اشتباه است؛ باید هش ۶۴ کاراکتری یا Base64 باشد."
    return raw, ""


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58_INDEX[ch]
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def tron_hex(addr: str) -> str:
    """آدرس TRON (Base58 با T یا هگز 41...) -> هگز 42 کاراکتری با پیشوند 41."""
    a = (addr or "").strip()
    if not a:
        return ""
    if a.lower().startswith("0x"):
        a = a[2:]
    if re.fullmatch(r"[0-9a-fA-F]{40}", a):
        return ("41" + a).lower()
    if re.fullmatch(r"[0-9a-fA-F]{42}", a):
        return a.lower()
    if not a.startswith("T"):
        return a.lower()
    try:
        raw = _b58decode(a)
    except (KeyError, ValueError):
        return a.lower()
    if len(raw) != 25:
        return a.lower()
    body, checksum = raw[:21], raw[21:]
    if hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4] != checksum:
        return a.lower()
    return body.hex().lower()


def same_tron(a: str, b: str) -> bool:
    return bool(a and b and tron_hex(a) == tron_hex(b))


def ton_hex(addr: str) -> str:
    """آدرس TON (Base64 یا hex یا raw) -> ۶۴ کاراکتر هگز کلید عمومی."""
    a = (addr or "").strip()
    if not a:
        return ""
    if a.lower().startswith("0:"):
        a = a.split(":", 1)[1]
    if TON_HEX_RE.match(a):
        return a.lower()
    try:
        raw = base64.urlsafe_b64decode(a + "=" * (-len(a) % 4))
    except Exception:
        try:
            raw = base64.b64decode(a + "=" * (-len(a) % 4))
        except Exception:
            return a.lower()
    if len(raw) == 36:
        return raw[2:].hex().lower()
    if len(raw) == 32:
        return raw.hex().lower()
    return a.lower()


def same_ton(a: str, b: str) -> bool:
    return bool(a and b and ton_hex(a) == ton_hex(b))


def _same_evm(a: str, b: str) -> bool:
    return bool(a and b and a.strip().lower() == b.strip().lower())


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _scaled(value: Any, decimals: int) -> Decimal:
    return _dec(value) / (Decimal(10) ** int(decimals))


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
_SECRET_QUERY = re.compile(r"(?i)(apikey|api_key|access_token|token|key)=([^&\s]+)")


def _redact_url(url: str) -> str:
    """کلید API را از آدرس قبل از ثبت در لاگ حذف می‌کند (NodeReal کلید را در مسیر دارد)."""
    text = _SECRET_QUERY.sub(r"\1=***", url or "")
    return re.sub(r"(/v1/)[^/?#\s]+", r"\1***", text)


async def _get_json(
    url: str, *, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None
) -> Tuple[int, Any]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                return resp.status, await resp.json(content_type=None)
    except Exception as exc:
        log.warning("GET %s failed: %s", _redact_url(url), type(exc).__name__)
        return 0, None


async def _post_json(url: str, payload: Dict[str, Any]) -> Tuple[int, Any]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                return resp.status, await resp.json(content_type=None)
    except Exception as exc:
        log.warning("POST %s failed: %s", _redact_url(url), type(exc).__name__)
        return 0, None


# ----------------------------------------------------------------------------
# تأیید هر شبکه
# ----------------------------------------------------------------------------
async def verify_transfer(network: Dict[str, Any], tx_hash: str, expected_wallet: str) -> Verification:
    chain = (network.get("chain") or "").lower()
    kind = (network.get("kind") or "").lower()
    network_key = network.get("network_key") or ""
    wallet = (expected_wallet or network.get("wallet") or "").strip()
    if not wallet:
        return Verification().fail("آدرس کیف پول مقصد تنظیم نشده است.")
    if not tx_hash.strip():
        return Verification().fail("هش تراکنش را وارد کنید.")

    normalized, err = normalize_hash(chain, tx_hash)
    if err:
        return Verification().fail(err)

    if chain == plans.CHAIN_BSC:
        if network_key == plans.NET_BNB_BEP20 or kind == "native":
            return await _verify_bsc_native(normalized, wallet)
        return await _verify_bsc_bep20(normalized, wallet)
    if chain == plans.CHAIN_TRON:
        if network_key == plans.NET_TRX_TRC20 or kind == "native":
            return await _verify_tron_native(normalized, wallet)
        return await _verify_tron_trc20(normalized, wallet)
    if chain == plans.CHAIN_ARBITRUM:
        return await _verify_arbitrum(normalized, wallet)
    if chain == plans.CHAIN_TON:
        return await _verify_ton(normalized, wallet)
    return Verification().fail("شبکه‌ی پرداخت پشتیبانی نمی‌شود.")


async def _verify_bsc_bep20(tx_hash: str, wallet: str) -> Verification:
    info, err = await bscscan.lookup_transfer(tx_hash, wallet)
    if info is None:
        return Verification().fail(err or "تراکنش پیدا نشد.")
    return Verification(
        ok=True,
        amount=_dec(info.get("amount")),
        sender=str(info.get("sender") or ""),
        receiver=str(info.get("receiver") or wallet),
        raw=info,
    )


async def _rpc_call(url: str, method: str, params: list) -> Any:
    status, data = await _post_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if status != 200 or not isinstance(data, dict):
        return None
    return data.get("result")


async def _verify_bsc_native(tx_hash: str, wallet: str) -> Verification:
    key = api_key("nodereal")
    if not key:
        return Verification().fail("کلید API نودریل (NodeReal) ثبت نشده است.")
    rpc = f"{NODEREAL_BASE}{key}"
    tx = await _rpc_call(rpc, "eth_getTransactionByHash", [tx_hash])
    if not isinstance(tx, dict):
        return Verification().fail("تراکنشی با این هش پیدا نشد (اگر تازه واریز کرده‌اید چند دقیقه بعد دوباره تلاش کنید).")
    receipt = await _rpc_call(rpc, "eth_getTransactionReceipt", [tx_hash])
    if not isinstance(receipt, dict) or str(receipt.get("status") or "").lower() != "0x1":
        return Verification().fail("این تراکنش در شبکه ناموفق بوده است.")
    to_addr = str(tx.get("to") or "")
    if not _same_evm(to_addr, wallet):
        return Verification().fail("این تراکنش به کیف پول تعیین‌شده ارسال نشده است.")
    amount = _scaled(int(str(tx.get("value") or "0x0"), 16), 18)
    return Verification(
        ok=True, amount=amount, sender=str(tx.get("from") or ""), receiver=to_addr,
        raw={"tx": tx, "receipt": receipt},
    )


async def _verify_tron_trc20(tx_hash: str, wallet: str) -> Verification:
    key = api_key("tronscan_api_key")
    if not key:
        return Verification().fail("کلید API ترون‌اسکن ثبت نشده است.")
    status, data = await _get_json(
        TRONSCAN_API, params={"hash": tx_hash}, headers={"TRON-PRO-API-KEY": key}
    )
    if status != 200 or not isinstance(data, dict) or not data:
        return Verification().fail("تراکنشی با این هش پیدا نشد یا پاسخ شبکه نامعتبر بود.")
    if str(data.get("contractRet") or "").upper() not in ("", "SUCCESS"):
        return Verification().fail("این تراکنش در شبکه TRON ناموفق بوده است.")
    info = data.get("tokenTransferInfo")
    if not isinstance(info, dict):
        return Verification().fail("این تراکنش انتقال توکن TRC20 نیست.")
    contract = str(info.get("contract_address") or "")
    if not same_tron(contract, TRON_USDT):
        return Verification().fail("این تراکنش مربوط به USDT رسمی شبکه TRON نیست.")
    to_addr = str(info.get("to_address") or "")
    if not same_tron(to_addr, wallet):
        return Verification().fail("این تراکنش به کیف پول تعیین‌شده ارسال نشده است.")
    decimals = int(info.get("decimals") or 6)
    if info.get("amount_str") not in (None, ""):
        amount = _scaled(info.get("amount_str"), decimals)
    else:
        amount = _dec(info.get("amount") or 0)
    return Verification(
        ok=True, amount=amount,
        sender=str(info.get("from_address") or ""),
        receiver=to_addr,
        raw=info,
    )


async def _verify_tron_native(tx_hash: str, wallet: str) -> Verification:
    key = api_key("tronscan_api_key")
    if not key:
        return Verification().fail("کلید API ترون‌اسکن ثبت نشده است.")
    status, data = await _get_json(
        TRONSCAN_API, params={"hash": tx_hash}, headers={"TRON-PRO-API-KEY": key}
    )
    if status != 200 or not isinstance(data, dict) or not data:
        return Verification().fail("تراکنشی با این هش پیدا نشد یا پاسخ شبکه نامعتبر بود.")
    if str(data.get("contractRet") or "").upper() not in ("", "SUCCESS"):
        return Verification().fail("این تراکنش در شبکه TRON ناموفق بوده است.")
    body = data.get("contractData")
    if not isinstance(body, dict):
        return Verification().fail("این تراکنش انتقال TRX نیست.")
    to_addr = str(body.get("to_address") or "")
    if not same_tron(to_addr, wallet):
        return Verification().fail("این تراکنش به کیف پول تعیین‌شده ارسال نشده است.")
    amount = _scaled(body.get("amount") or 0, 6)
    return Verification(
        ok=True, amount=amount,
        sender=str(body.get("owner_address") or ""),
        receiver=to_addr,
        raw=body,
    )


async def _arb_tokentx(params: Dict[str, Any]) -> Optional[list]:
    key = api_key("arbiscan_api_key")
    if not key:
        return None
    p = dict(params)
    p["apikey"] = key
    status, data = await _get_json(ARBISCAN_API, params=p)
    if status != 200 or not isinstance(data, dict) or str(data.get("status")) != "1":
        # API قدیمی Arbiscan ممکن است بازنشسته شده باشد؛ نسخه‌ی V2 چندشبکه‌ای را امتحان می‌کنیم
        p2 = dict(p)
        p2["chainid"] = 42161
        status, data = await _get_json(ETHERSCAN_V2_API, params=p2)
    if status != 200 or not isinstance(data, dict) or str(data.get("status")) != "1":
        return None
    result = data.get("result")
    return result if isinstance(result, list) else None


async def _verify_arbitrum(tx_hash: str, wallet: str) -> Verification:
    if not api_key("arbiscan_api_key"):
        return Verification().fail("کلید API آربی‌اسکن ثبت نشده است.")
    transfers = await _arb_tokentx({
        "module": "account",
        "action": "tokentx",
        "contractaddress": ARB_TOKEN,
        "address": wallet,
        "page": 1,
        "offset": 100,
        "sort": "desc",
    })
    if transfers is None:
        return Verification().fail("استعلام از Arbiscan انجام نشد؛ کلید API یا اتصال شبکه را بررسی کنید.")
    target = next(
        (t for t in transfers if isinstance(t, dict) and str(t.get("hash", "")).lower() == tx_hash),
        None,
    )
    if target is None:
        return Verification().fail("تراکنشی با این هش برای کیف پول ما در شبکه Arbitrum پیدا نشد.")
    if not _same_evm(str(target.get("contractAddress") or ""), ARB_TOKEN):
        return Verification().fail("این تراکنش مربوط به توکن ARB رسمی نیست.")
    to_addr = str(target.get("to") or "")
    if not _same_evm(to_addr, wallet):
        return Verification().fail("این تراکنش به کیف پول تعیین‌شده ارسال نشده است.")
    receipt = await _rpc_call(ARB_RPC, "eth_getTransactionReceipt", [tx_hash])
    if isinstance(receipt, dict) and str(receipt.get("status") or "").lower() == "0x0":
        return Verification().fail("این تراکنش در شبکه Arbitrum ناموفق بوده است.")
    decimals = int(target.get("tokenDecimal") or 18)
    return Verification(
        ok=True,
        amount=_scaled(target.get("value") or 0, decimals),
        sender=str(target.get("from") or ""),
        receiver=to_addr,
        raw=target,
    )


def _ton_event_transfer(data: Dict[str, Any], wallet: str) -> Optional[Dict[str, Any]]:
    actions = data.get("actions")
    if not isinstance(actions, list):
        return None
    for action in actions:
        if not isinstance(action, dict):
            continue
        body = action.get("JettonTransfer") or action.get("jetton_transfer")
        if not isinstance(body, dict):
            continue
        jetton = body.get("jetton") if isinstance(body.get("jetton"), dict) else {}
        jetton_addr = str((jetton or {}).get("address") or body.get("jetton_address") or "")
        recipient = body.get("recipient") if isinstance(body.get("recipient"), dict) else {}
        to_addr = str((recipient or {}).get("address") or body.get("destination") or "")
        # جتون باید صریحاً DOGS باشد؛ نبودِ آدرس جتون یعنی «توکن ناشناس» و پذیرفته نمی‌شود.
        if not jetton_addr or not same_ton(jetton_addr, DOGS_JETTON):
            continue
        if not same_ton(to_addr, wallet):
            continue
        return {
            "amount": body.get("amount") or 0,
            "sender": str(((body.get("sender") or {}) if isinstance(body.get("sender"), dict) else {}).get("address") or ""),
            "receiver": to_addr,
            "raw": body,
        }
    return None


async def _jetton_wallet_owned(dest: str, wallet: str, headers: Dict[str, str]) -> Optional[bool]:
    """آیا آدرس کیف پول جتون به کیف پول ما تعلق دارد؟ True/False و در نبود اطلاعات None."""
    st, acc = await _get_json(f"{TONAPI_BASE}/accounts/{dest}", headers=headers)
    if st == 200 and isinstance(acc, dict):
        owner = str(acc.get("owner") or "")
        if owner:
            return same_ton(owner, wallet)
    st, data = await _get_json(f"{TONAPI_BASE}/accounts/{wallet}/jetton_wallets", headers=headers)
    if st == 200 and isinstance(data, dict):
        items = data.get("jetton_wallets") or data.get("wallets") or []
        if isinstance(items, list):
            return any(
                same_ton(str((w or {}).get("address") or ""), dest)
                for w in items if isinstance(w, dict)
            )
    return None


async def _verify_ton(tx_hash: str, wallet: str) -> Verification:
    key = api_key("tonapi_api_key")
    if not key:
        return Verification().fail("کلید API تون‌اپی (TonAPI) ثبت نشده است.")
    headers = {"Authorization": f"Bearer {key}"}
    status, data = await _get_json(f"{TONAPI_BASE}/events/{tx_hash}", headers=headers)
    if status == 200 and isinstance(data, dict):
        found = _ton_event_transfer(data, wallet)
        if found:
            return Verification(
                ok=True,
                amount=_scaled(found["amount"], 9),
                sender=found["sender"],
                receiver=found["receiver"],
                raw=found["raw"],
            )

    status, tx = await _get_json(f"{TONAPI_BASE}/blockchain/transactions/{tx_hash}", headers=headers)
    if status != 200 or not isinstance(tx, dict):
        return Verification().fail("تراکنشی با این هش در شبکه TON پیدا نشد.")
    msg = tx.get("in_msg")
    if not isinstance(msg, dict):
        return Verification().fail("این تراکنش واریز ورودی به کیف پول نیست.")
    body = msg.get("decoded_body")
    if not isinstance(body, dict) or body.get("@type") not in ("jetton_transfer", "jetton_transfer_notification"):
        return Verification().fail("این تراکنش انتقال جتون (DOGS) نیست.")
    amount = _scaled(body.get("amount") or 0, 9)

    # توکن باید حتماً DOGS باشد؛ کیف پول جتون مبدأ، آدرس قرارداد جتون را در خود دارد.
    source = str(msg.get("source") or "")
    if not source:
        return Verification().fail("توکن واریزشده قابل تأیید نیست؛ هش تراکنش را دوباره بررسی کنید.")
    st, src_acc = await _get_json(f"{TONAPI_BASE}/accounts/{source}", headers=headers)
    if st != 200 or not isinstance(src_acc, dict):
        return Verification().fail("تأیید توکن واریزی از TonAPI انجام نشد؛ کمی بعد دوباره تلاش کنید.")
    src_jetton = src_acc.get("jetton")
    src_master = str((src_jetton or {}).get("address") or "") if isinstance(src_jetton, dict) else ""
    if not src_master or not same_ton(src_master, DOGS_JETTON):
        return Verification().fail("این تراکنش مربوط به توکن DOGS رسمی نیست.")

    if str(body.get("@type")) == "jetton_transfer_notification":
        # پیام اطلاع‌رسانی روی کیف پول مقصد اجرا می‌شود.
        if not same_ton(str(tx.get("account") or ""), wallet):
            return Verification().fail("این تراکنش به کیف پول تعیین‌شده ارسال نشده است.")
        return Verification(ok=True, amount=amount, sender=source, receiver=wallet, raw=body)

    destination = str(body.get("destination") or "")
    if not destination:
        return Verification().fail("مقصد این انتقال جتون مشخص نیست.")
    if not same_ton(destination, wallet):
        owned = await _jetton_wallet_owned(destination, wallet, headers)
        if owned is None:
            return Verification().fail("تأیید مقصد واریز از TonAPI انجام نشد؛ کمی بعد دوباره تلاش کنید.")
        if not owned:
            return Verification().fail("این تراکنش به کیف پول تعیین‌شده ارسال نشده است.")
    return Verification(ok=True, amount=amount, sender=source, receiver=wallet, raw=body)
