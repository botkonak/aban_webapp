"""مدل‌های داده (آبان ترید)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class User:
    user_id: int
    phone_number: Optional[str]
    is_verified: bool
    joined_at: str  # UTC: YYYY-MM-DD HH:MM:SS
    pre_alert: bool = True      # هشدار قبل از سیگنال (انتخاب خود کاربر)
    sub_until: float = 0.0      # پایان اشتراک (epoch)؛ 0 یعنی هیچ‌وقت اشتراک نداشته است


@dataclass
class Channel:
    id: int
    channel_id: str
    channel_title: str
    is_active: bool
    sort_order: int
    invite_link: Optional[str] = None


@dataclass
class Block:
    id: int
    block_type: str
    content: str
    is_active: bool
    created_at: str


@dataclass
class SubRequest:
    """درخواست خرید اشتراک (ارسال تراکنش توسط کاربر)."""
    id: int
    user_id: int
    text: str
    photo_id: str        # "p:<file_id>" (عکس) یا "d:<file_id>" (فایل) یا خالی
    status: str          # pending | approved | rejected
    reason: str
    created_at: str
    service_type: str = "bot"


@dataclass
class Candidate:
    """نتیجه تحلیل یک نماد (قبل از گرفتن قیمت لحظه‌ای)."""
    pair: str            # مثل BTC/USDT:USDT
    base: str            # مثل BTC
    side: str            # LONG | SHORT
    score: float         # 0..100
    atr: float
    close: float         # آخرین کلوز بسته‌شده
    adx: float = 0.0
    rsi: float = 0.0
    poc: float = 0.0     # POC تایم‌فریم ورود (برای حد ضرر پویا)
    standby: bool = False  # فقط یک شرط کم دارد (حالت آماده‌باش)؛ سیگنال نیست
    wait: str = ""       # شرطی که منتظرش هستیم (sq_wait / vol / srsi / ...)
    reasons: tuple = ()  # دلایل تکنیکال تأییدشده (برای توضیح AI): ("واگرایی مثبت RSI", ...)


@dataclass
class Signal:
    symbol: str          # نماد نمایشی (مثل BTC)
    pair: str
    side: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    risk: float
    leverage: int
    decimals: int
    score: float = 0.0
    ai_info: str = ""    # توضیح کوتاه تولیدشده توسط AI (پترن {aiinfo})؛ خالی یعنی خط حذف می‌شود


@dataclass
class AIKey:
    """یکی از ۱۵ اسلات کلید API هوش مصنوعی."""
    slot: int
    name: str
    api_key: str
    enabled: bool
    err_until: float     # تا این زمان (epoch) کلید به‌خاطر خطا موقتاً کنار گذاشته شده است
    last_error: str
    ok_count: int
    fail_count: int
    account_id: str = ""  # Account ID مخصوص همین کلید (آدرس API ازش ساخته می‌شود)


@dataclass
class ScanResult:
    signal: Optional[Signal]
    reason: str  # ok | none | no_template | error | bot_off


@dataclass
class SupportTicket:
    """تیکت پشتیبانی (جدول support_tickets)."""
    id: int
    user_id: int
    status: str = "open"          # open | closed
    username: str = ""
    first_name: str = ""
    created_at: str = ""
    closed_at: str = ""


@dataclass
class SupportMessage:
    """پیام یک تیکت پشتیبانی (جدول support_messages)."""
    id: int
    ticket_id: int
    sender: str = "user"          # user | admin
    sender_id: int = 0
    message_type: str = "text"    # text | photo
    text: str = ""
    file_id: str = ""
    created_at: str = ""
