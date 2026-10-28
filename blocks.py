"""بلوک‌های محتوا: {block1} {block2} {block3}. در هر سیگنال از هر بلوک یک متن تصادفی انتخاب می‌شود."""
from __future__ import annotations

from typing import Dict

import config
from database import db

BLOCK_LABELS: Dict[str, str] = {
    "block1": "بلوک ۱",
    "block2": "بلوک ۲",
    "block3": "بلوک ۳",
}


def block_type_of(n: str) -> str:
    """'1' -> 'block1' (ورودی نامعتبر به block1 برمی‌گردد)."""
    t = f"block{n}"
    return t if t in config.BLOCK_TYPES else config.BLOCK_TYPES[0]


def block_number(block_type: str) -> str:
    return block_type.replace("block", "")


def label(block_type: str) -> str:
    return BLOCK_LABELS.get(block_type, block_type)


def pick(block_type: str) -> str:
    """یک متن تصادفی از بین متن‌های فعال بلوک؛ اگر خالی بود رشته خالی."""
    return db.pick_block(block_type)
