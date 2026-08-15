"""Push-to-talk / tap-toggle global hotkey state machine.

Extracted from ``TrayApp`` (voice-transcriber#177): owns the pynput
listener registration plus the mutable fields and millisecond-race guards
needed to tell a tap (press + release, toggle semantics) from a
push-to-talk hold (press, wait >= ``ptt_threshold_ms``, release = stop) on
the same key. ``TrayApp`` supplies two narrow callbacks at construction —
whether a recorder is currently active, and how to enqueue a
toggle-record event — and calls ``notify_recording_started`` /
``notify_recording_stopped`` around the actual recorder lifecycle so the
release-side race guard (issue #28) has an accurate recorder age.
"""

from __future__ import annotations

# Standard library imports
import logging
import time
from typing import Callable, Optional

# Third-party imports
from pynput import keyboard

from src import AppConfig
from src.inject import parse_simple_hotkey

logger = logging.getLogger(__name__)


class HotkeyController:
    # Minimum age of an in-flight recording before a PTT release will stop
    # it. Guards against the press → release happening so fast that the
    # take has barely begun: in that case the user almost certainly meant
    # a tap-toggle (and would lose the take if we stopped it here).
    _MIN_PTT_RECORD_AGE_MS = 400

    def __init__(
        self,
        config: AppConfig,
        has_active_recorder: Callable[[], bool],
        enqueue_toggle: Callable[[], None],
    ) -> None:
        self.config = config
        self._has_active_recorder = has_active_recorder
        self._enqueue_toggle = enqueue_toggle

        self._listener = None  # GlobalHotKeys or Listener depending on hotkey shape
        self._target_key = None  # pynput Key when in tap/hold mode
        self._key_down: bool = False
        # When set, the in-flight recording was started by a hotkey press —
        # transcription will paste at caret after copy. Cleared by
        # ``consume_record_from_hotkey`` once consumed, or by a manual
        # (tk-button) toggle via ``request_manual_toggle``.
        self.record_from_hotkey: bool = False
        # ``time.monotonic()`` of the press that started the current take,
        # while the user is still holding. Used to discriminate tap vs PTT
        # on release.
        self._press_started_recording_at: Optional[float] = None
        # ``time.monotonic()`` of the moment the recorder was actually
        # created, as reported by ``notify_recording_started``. Used as a
        # second gate on PTT release so a press that races the 80 ms event
        # pump can't stop a take that's barely begun (issue #28).
        self._recorder_active_at: Optional[float] = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Register the global hotkey.

        Single-key hotkeys (``<F8>``) get a low-level keyboard.Listener so we
        can time press<->release and offer push-to-talk alongside tap-toggle.
        Modifier combos fall through to the legacy GlobalHotKeys path —
        toggle-only, since holding a 3-key chord for PTT is awkward.

        ``suppress_hotkey`` is honoured on the simple-key path only; combos
        keep pass-through behaviour to avoid swallowing modifier keystrokes
        from the focused window.
        """
        hotkey = self.config.hotkey
        target_key = parse_simple_hotkey(hotkey)
        if target_key is None:
            try:
                mapping = {hotkey: self._on_combo_toggle}
                self._listener = keyboard.GlobalHotKeys(mapping)
                self._listener.start()
                logger.info(f"🧷 Hotkey {hotkey} (toggle-only — combo)")
            except Exception as e:
                logger.error(f"❌ Failed to register hotkey {hotkey!r}: {e}")
            return

        self._target_key = target_key
        suppress = bool(self.config.suppress_hotkey)
        try:
            if suppress:
                # pynput's `suppress=True` flag is all-or-nothing — it eats
                # every key. To suppress only the hotkey, use the per-event
                # `event_filter` hook: ignore non-target keys (return False
                # → no callback, no suppression), and for the target key
                # dispatch press/release manually then raise
                # SuppressException via `suppress_event()` so Windows drops
                # the keystroke before it reaches the focused window.
                target_vk = target_key.value.vk
                # Forward-declare so the closure can call suppress_event()
                # on the listener it's attached to.
                listener_box: list = []
                _WM_KEYDOWNS = (0x0100, 0x0104)  # WM_KEYDOWN, WM_SYSKEYDOWN
                _WM_KEYUPS = (0x0101, 0x0105)    # WM_KEYUP,   WM_SYSKEYUP

                def _filter(msg, data):
                    if data.vkCode != target_vk:
                        return False
                    if msg in _WM_KEYDOWNS:
                        self._on_press(target_key)
                    elif msg in _WM_KEYUPS:
                        self._on_release(target_key)
                    listener_box[0].suppress_event()  # raises, drops the key

                # NB: pynput strips kwargs that don't start with the platform
                # prefix, so on Windows the filter must be passed as
                # ``win32_event_filter`` — bare ``event_filter`` is silently
                # ignored.
                self._listener = keyboard.Listener(
                    on_press=lambda _k: None,
                    on_release=lambda _k: None,
                    win32_event_filter=_filter,
                )
                listener_box.append(self._listener)
            else:
                self._listener = keyboard.Listener(
                    on_press=self._on_press,
                    on_release=self._on_release,
                )
            self._listener.start()
            logger.info(
                f"🧷 Hotkey {hotkey} (tap = toggle, hold ≥ "
                f"{self.config.ptt_threshold_ms} ms = push-to-talk, "
                f"suppress={'on' if suppress else 'off'})"
            )
        except Exception as e:
            logger.error(f"❌ Failed to register hotkey {hotkey!r}: {e}")

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def restart(self) -> None:
        self.stop()
        self._key_down = False
        self._press_started_recording_at = None
        self.start()

    # -------------------------------------------------------- recorder sync

    def notify_recording_started(self) -> None:
        """TrayApp calls this the moment a take's recorder is created,
        regardless of what started it (hotkey, tk button, or menu)."""
        self._recorder_active_at = time.monotonic()

    def notify_recording_stopped(self) -> None:
        self._recorder_active_at = None

    def request_manual_toggle(self) -> None:
        """Tk-button entry point: never paste at caret, even when this
        kicks off a take."""
        if not self._has_active_recorder():
            self.record_from_hotkey = False

    def consume_record_from_hotkey(self) -> bool:
        """Read and clear the hotkey-initiated flag for the take that just
        finished transcribing — a single read-and-clear so a mid-flight
        tk-window interaction can't change the outcome."""
        value = self.record_from_hotkey
        self.record_from_hotkey = False
        return value

    # ------------------------------------------------------------- events

    def _on_press(self, key) -> None:
        if key != self._target_key:
            return
        if self._key_down:
            return  # auto-repeat from a held key — already handled
        self._key_down = True
        if not self._has_active_recorder():
            # Press starts a fresh take. Tentatively in PTT mode until
            # release tells us how long the key was held.
            self._press_started_recording_at = time.monotonic()
            self.record_from_hotkey = True
            self._enqueue_toggle()
        elif self._press_started_recording_at is None:
            # Already recording in tap-waiting state → this press is the
            # second tap and should stop the take immediately.
            self._enqueue_toggle()
        # else: press while still holding the original — ignore

    def _on_release(self, key) -> None:
        if key != self._target_key:
            return
        self._key_down = False
        started_at = self._press_started_recording_at
        if started_at is None:
            return  # release of a second-tap stop, or unrelated release
        held_ms = (time.monotonic() - started_at) * 1000
        self._press_started_recording_at = None
        threshold = max(0, int(self.config.ptt_threshold_ms))
        recorder_age_ms = (
            (time.monotonic() - self._recorder_active_at) * 1000
            if self._recorder_active_at is not None
            else 0.0
        )
        if (
            held_ms >= threshold
            and self._has_active_recorder()
            and recorder_age_ms >= self._MIN_PTT_RECORD_AGE_MS
        ):
            self._enqueue_toggle()
        # else: tap (or PTT release that raced a barely-started recorder).
        # Recording continues; await the second tap.

    def _on_combo_toggle(self) -> None:
        if not self._has_active_recorder():
            self.record_from_hotkey = True
        self._enqueue_toggle()
