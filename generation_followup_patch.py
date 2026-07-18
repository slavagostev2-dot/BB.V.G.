from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"marker missing in {path}: {old[:100]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "wheel_event_runtime.py",
    '    action_id: int | None = None,\n    verification_status: str = "",\n) -> None:\n    current = monitor_module.now_utc()\n',
    '    action_id: int | None = None,\n    verification_status: str = "",\n    server_start_at: datetime | None = None,\n) -> None:\n    current = monitor_module.now_utc()\n',
)
replace_once(
    "wheel_event_runtime.py",
    '        action_id=action_id,\n        available_at=available_at,\n        verification_status=verification_status,\n    )\n    _tag_availability(\n',
    '        action_id=action_id,\n        available_at=available_at,\n        verification_status=verification_status,\n        server_start_at=server_start_at,\n    )\n    _tag_availability(\n',
)

path = Path("tests/test_recurring_event_hotfix.py")
text = path.read_text(encoding="utf-8")
marker = "    def test_notification_dedup_is_scoped_to_publication_and_phase(self) -> None:\n"
test = '''    def test_scheduled_availability_preserves_server_generation(self) -> None:
        current = datetime(2026, 7, 18, 4, 0, tzinfo=UTC)
        server_start = current + timedelta(minutes=30)
        captured: dict[str, object] = {}

        class FakeMonitor:
            DISPLAY_TZ = UTC
            WHEEL_VERIFICATION_FAILED = "failed"
            _bbvg_original_deadline_parser = staticmethod(lambda text, date: (None, "none"))

            @staticmethod
            def now_utc():
                return current

            @staticmethod
            def wheel_identifier(link):
                return link.rstrip("/").rsplit("/", 1)[-1]

            @staticmethod
            def wheel_key(link):
                return FakeMonitor.wheel_identifier(link).casefold()

            @staticmethod
            def human_remaining(value):
                return "30 мин"

            @staticmethod
            def parse_datetime(value):
                return wheel_event_runtime._parse_datetime(value)

            @staticmethod
            def wheel_reply_markup(*args, **kwargs):
                return {"inline_keyboard": []}

            @staticmethod
            def send_message(*args, **kwargs):
                return {"ok": True}

            @staticmethod
            def remember_active_wheel(state, message, link, deadline, status, method, excerpt, **kwargs):
                captured.update(kwargs)
                state.setdefault("active_wheels", {})[FakeMonitor.wheel_key(link)] = {
                    "identifier": FakeMonitor.wheel_identifier(link),
                    "url": link,
                }

        state: dict[str, object] = {"active_wheels": {}}
        message = SimpleNamespace(
            source="source",
            message_id=1,
            date=current,
            text="wheel",
            message_url="https://telegram.me/source/1",
        )
        wheel_event_runtime._availability_message(
            FakeMonitor,
            state,
            message,
            "https://betboom.ru/freestream/reused",
            server_start,
            "server start",
            action_id=100,
            verification_status="confirmed",
            server_start_at=server_start,
        )
        self.assertEqual(captured["server_start_at"], server_start)

'''
if test not in text:
    if marker not in text:
        raise RuntimeError("test marker missing")
    text = text.replace(marker, test + marker, 1)
path.write_text(text, encoding="utf-8")
