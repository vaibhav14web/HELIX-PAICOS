import os
import json
import time
import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.timetable_scheduler")

_DEFAULT_TIMETABLE_PATH = Path(__file__).resolve().parents[2] / "config" / "timetable.json"


class TimetableScheduler:
    def __init__(
        self,
        event_bus: EventBus,
        timetable_path: str | Path | None = None,
        poll_interval: float = 1.0,
    ):
        self._event_bus = event_bus
        self._timetable_path = (
            Path(timetable_path)
            if timetable_path
            else Path(os.getenv("HELIX_TIMETABLE_PATH", _DEFAULT_TIMETABLE_PATH))
        )
        self._poll_interval = poll_interval
        self._reminders: list[dict[str, Any]] = []
        self._loop_task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()
        self._subscriptions: list[str] = []

    async def start(self) -> None:
        async with self._lock:
            if self._running:
                return
            self._running = True
            self.load_timetable()

            self._event_bus.subscribe("reminder.add", self._handle_add_reminder)
            self._subscriptions = ["reminder.add"]

            self._loop_task = asyncio.create_task(self._scheduler_loop())
            logger.info("TimetableScheduler started (path=%s)", self._timetable_path)

    async def stop(self) -> None:
        async with self._lock:
            if not self._running:
                return
            self._running = False

            if self._loop_task and not self._loop_task.done():
                self._loop_task.cancel()
                try:
                    await self._loop_task
                except asyncio.CancelledError:
                    pass
                self._loop_task = None

            for event_type in self._subscriptions:
                self._event_bus.unsubscribe(event_type, self._handle_add_reminder)
            self._subscriptions.clear()

            logger.info("TimetableScheduler stopped")

    def load_timetable(self) -> list[dict[str, Any]]:
        """Load timetable entries from JSON file."""
        if self._timetable_path.is_file():
            try:
                with open(self._timetable_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "reminders" in data:
                    self._reminders = data["reminders"]
                elif isinstance(data, list):
                    self._reminders = data
                else:
                    self._reminders = []
            except Exception as e:
                logger.warning("Failed to load timetable from %s: %s", self._timetable_path, e)
                self._reminders = []
        else:
            self._reminders = []
        return self._reminders

    def save_timetable(self) -> None:
        """Save current reminders to JSON file."""
        try:
            self._timetable_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._timetable_path, "w", encoding="utf-8") as f:
                json.dump({"reminders": self._reminders}, f, indent=2)
        except Exception as e:
            logger.error("Failed to save timetable to %s: %s", self._timetable_path, e)

    def add_reminder(
        self,
        text: str,
        delay_seconds: float | None = None,
        timestamp: float | None = None,
        trigger_at: str | None = None,
        time_str: str | None = None,
        recurring: bool = False,
    ) -> dict[str, Any]:
        """Add a new reminder to the timetable."""
        now = time.time()
        if delay_seconds is not None:
            target_ts = now + delay_seconds
        elif timestamp is not None:
            target_ts = timestamp
        elif trigger_at is not None:
            dt = datetime.fromisoformat(trigger_at)
            target_ts = dt.timestamp()
        elif time_str is not None:
            # HH:MM format today
            now_dt = datetime.now()
            parts = time_str.split(":")
            hour, minute = int(parts[0]), int(parts[1])
            target_dt = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target_dt.timestamp() < now:
                # Next day if already passed
                target_dt = target_dt.replace(day=now_dt.day + 1)
            target_ts = target_dt.timestamp()
        else:
            raise ValueError("Must specify delay_seconds, timestamp, trigger_at, or time_str")

        reminder_id = f"rem_{uuid.uuid4().hex[:8]}"
        entry = {
            "id": reminder_id,
            "text": text,
            "timestamp": target_ts,
            "recurring": recurring,
            "triggered": False,
            "created_at": now,
        }
        self._reminders.append(entry)
        self.save_timetable()
        logger.info("Added reminder '%s' (id=%s, target_ts=%.2f)", text, reminder_id, target_ts)
        return entry

    async def _handle_add_reminder(self, event: HelixEvent) -> None:
        payload = event.payload
        text = payload.get("text", "")
        if not text:
            return
        delay_seconds = payload.get("delay_seconds")
        timestamp = payload.get("timestamp")
        trigger_at = payload.get("trigger_at")
        time_str = payload.get("time")
        recurring = payload.get("recurring", False)
        self.add_reminder(
            text=text,
            delay_seconds=delay_seconds,
            timestamp=timestamp,
            trigger_at=trigger_at,
            time_str=time_str,
            recurring=recurring,
        )

    async def _scheduler_loop(self) -> None:
        """Background loop that checks for due reminders."""
        while self._running:
            try:
                await self._check_reminders()
            except Exception as e:
                logger.exception("Error in TimetableScheduler loop: %s", e)
            await asyncio.sleep(self._poll_interval)

    async def _check_reminders(self) -> None:
        now = time.time()
        state_changed = False

        for r in self._reminders:
            if r.get("triggered", False):
                continue

            target_ts = r.get("timestamp")
            if target_ts is None and "trigger_at" in r:
                try:
                    dt = datetime.fromisoformat(r["trigger_at"])
                    target_ts = dt.timestamp()
                except ValueError:
                    continue

            if target_ts is not None and now >= target_ts:
                r["triggered"] = True
                state_changed = True
                logger.info("Reminder due: '%s' (id=%s)", r.get("text"), r.get("id"))
                await self._event_bus.publish_event(
                    source="timetable_scheduler",
                    event_type="reminder.due",
                    payload={
                        "reminder_id": r.get("id"),
                        "text": r.get("text"),
                        "timestamp": target_ts,
                    },
                )

        if state_changed:
            self.save_timetable()
