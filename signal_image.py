"""تولید تصویر سیگنال با استفاده از عکس قالب (آبان ترید)."""
import io
import os
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

from models import Signal
from ui import TEHRAN

# مسیر assets کنار همین فایل (مستقل از پوشه‌ای که ربات از آن اجرا می‌شود)
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def _font(name: str, size: int):
    try:
        return ImageFont.truetype(os.path.join(ASSETS, name), size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)     # Pillow 10.1+: فونت پیش‌فرض با اندازه‌ی دلخواه
        except TypeError:
            return ImageFont.load_default()


def generate_signal_image(sig: Signal) -> io.BytesIO:
    """مشخصات سیگنال را روی عکس قالب (با توجه به جهت) می‌نویسد."""
    
    # ۱. انتخاب عکس و رنگ‌ها بر اساس جهت
    if sig.side == "LONG":
        bg_path = os.path.join(ASSETS, "long.jpg")
        color_main = (8, 153, 129)     # سبز
        badge_text = f"LONG · {sig.leverage}X"
    else:
        bg_path = os.path.join(ASSETS, "short.jpg")
        color_main = (242, 54, 69)     # قرمز
        badge_text = f"SHORT · {sig.leverage}X"

    # ۲. باز کردن عکس
    try:
        img = Image.open(bg_path).convert("RGBA")
    except OSError:      # نبودن فایل یا فایل خراب
        img = Image.new("RGBA", (1299, 1211), (19, 23, 34, 255))
        
    draw = ImageDraw.Draw(img)

    # ۳. بارگذاری فونت‌ها
    font_title = _font("Montserrat-SemiBold.ttf", 55)
    font_badge = _font("Montserrat-SemiBold.ttf", 55)
    font_large = _font("Montserrat-SemiBold.ttf", 55)
    font_medium = _font("Montserrat-Medium.ttf", 40)
    font_tp = _font("Montserrat-SemiBold.ttf", 48)
    font_small = _font("Montserrat-Medium.ttf", 30)

    # ۴. فرمت‌بندی اعداد و محاسبات
    d = sig.decimals
    def fmt(val):
        s = f"{val:.{d}f}"
        return s.rstrip('0').rstrip('.') if '.' in s else s

    now = datetime.now(TEHRAN)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    # محاسبه R:R
    risk_val = abs(sig.entry - sig.sl)
    rr_val = abs(sig.tp3 - sig.entry) / risk_val if risk_val > 0 else 0

    # رنگ‌های پایه
    white = (255, 255, 255)
    gray = (160, 165, 175)

    # ========================================================
    # چیدمان دقیق
    # ========================================================
    
    # تاریخ و ساعت (سمت راست)
    draw.text((1020, 130), date_str, font=font_small, fill=gray)

    # اسم نماد
    draw.text((85, 120), f"{sig.symbol}USDT Perpetual", font=font_title, fill=white)

    # جهت و اهرم (سفید)
    draw.text((85, 220), badge_text, font=font_badge, fill=white)

    # Risk و R:R (در یک خط)
    risk_str = f"Risk {sig.risk:.1f}%   |   R:R {rr_val:.1f}"
    draw.text((650, 230), risk_str, font=font_medium, fill=white)

    # Entry
    draw.text((85, 400), "Entry", font=font_small, fill=gray)
    draw.text((85, 440), fmt(sig.entry), font=font_large, fill=white)

    # TPها
    draw.text((85, 570), f"TP¹  {fmt(sig.tp1)}", font=font_tp, fill=color_main)
    draw.text((85, 640), f"TP²  {fmt(sig.tp2)}", font=font_tp, fill=color_main)
    draw.text((85, 710), f"TP³  {fmt(sig.tp3)}", font=font_tp, fill=color_main)

    # Stop
    draw.text((85, 820), "STOP", font=font_small, fill=gray)
    draw.text((85, 860), fmt(sig.sl), font=font_large, fill=color_main)

    # ========================================================

    # ذخیره در حافظه
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=95)
    buf.seek(0)
    return buf