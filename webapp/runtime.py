"""وضعیت مشترک زمان اجرای وب‌اپ و کارگر ارسال پیام گروهی."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import config
from webapp.repo import repo

log = logging.getLogger("webapp.runtime")


class MessageJob:
    def __init__(self, text: str, user_ids: List[int], admin_id: int, target_type: str):
        self.id = uuid.uuid4().hex[:12]
        self.text = text
        self.user_ids = user_ids
        self.admin_id = admin_id
        self.target_type = target_type
        self.total = len(user_ids)
        self.sent = 0
        self.failed = 0
        self.status = "pending"          # pending | running | done | cancelled | failed
        self.created_at = time.time()
        self.finished_at = 0.0
        self.error = ""
        self._cancel = False
        self.task: Optional[asyncio.Task] = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "sent": self.sent,
            "failed": self.failed,
            "progress": round((self.sent + self.failed) * 100.0 / self.total, 1) if self.total else 100.0,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }

    def cancel(self) -> None:
        self._cancel = True


class Runtime:
    def __init__(self) -> None:
        self.bot = None
        self.application = None
        self.started_at = time.time()
        self.jobs: Dict[str, MessageJob] = {}

    def attach(self, application) -> None:
        self.application = application
        self.bot = getattr(application, "bot", None)
        self.started_at = time.time()

    # ------------------------------------------------------------------
    async def send_text(self, uid: int, text: str, parse_mode: Optional[str] = None) -> tuple[bool, str]:
        if self.bot is None:
            return False, "ربات در حال حاضر در دسترس نیست."
        try:
            from telegram.error import RetryAfter, TelegramError

            try:
                await self.bot.send_message(
                    int(uid), text, parse_mode=parse_mode,
                    disable_web_page_preview=True,
                )
                return True, ""
            except RetryAfter as exc:
                wait = float(getattr(exc, "retry_after", 1) or 1)
                await asyncio.sleep(min(wait, 30))
                await self.bot.send_message(
                    int(uid), text, parse_mode=parse_mode,
                    disable_web_page_preview=True,
                )
                return True, ""
            except TelegramError as exc:
                return False, str(exc)
        except Exception as exc:
            log.warning("send to %s failed: %s", uid, exc)
            return False, str(exc)

    # ------------------------------------------------------------------
    def start_job(self, text: str, user_ids: List[int], admin_id: int, target_type: str) -> MessageJob:
        job = MessageJob(text, user_ids, admin_id, target_type)
        self.jobs[job.id] = job
        job.task = asyncio.create_task(self._run_job(job), name=f"msg-job-{job.id}")
        self._cleanup()
        return job

    async def _run_job(self, job: MessageJob) -> None:
        job.status = "running"
        try:
            for uid in job.user_ids:
                if job._cancel:
                    job.status = "cancelled"
                    break
                ok, _ = await self.send_text(uid, job.text)
                if ok:
                    job.sent += 1
                else:
                    job.failed += 1
                await asyncio.sleep(0.06)
            if job.status == "running":
                job.status = "done"
        except asyncio.CancelledError:
            job.status = "cancelled"
            raise
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            log.exception("message job %s failed", job.id)
        finally:
            job.finished_at = time.time()
            try:
                repo.log_message(
                    job.admin_id, job.target_type, "",
                    job.text, job.sent, job.failed,
                )
            except Exception:
                log.exception("message job log failed")

    def _cleanup(self) -> None:
        if len(self.jobs) < 40:
            return
        stale = sorted(self.jobs.values(), key=lambda j: j.created_at)[:20]
        for job in stale:
            if job.status in ("done", "failed", "cancelled"):
                self.jobs.pop(job.id, None)


runtime = Runtime()
