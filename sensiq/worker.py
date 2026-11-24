"""Runs detection off the UI thread, debounced so it only fires when typing pauses."""
from __future__ import annotations

import logging
import threading
import time

from .paths import models_dir

log = logging.getLogger(__name__)


class DetectorWorker(threading.Thread):
    def __init__(self, config, ignore, events, stop_event: threading.Event):
        super().__init__(name="Detector", daemon=True)
        self.config = config
        self.ignore = ignore
        self.events = events
        self.stop_event = stop_event
        self.engine = None
        self._cv = threading.Condition()
        self._job = None
        self._reload = False
        self.engine_lock = threading.Lock()  # the file scanner shares this engine
        self.ready = threading.Event()

    def detect_now(self, text: str, mark_ignored: bool = False):
        """Synchronous detection of a whole document for other threads (file and image scans):
        every character is checked. Typing checks waiting meanwhile get the engine between chunks."""
        self.ready.wait(120)
        with self.engine_lock:
            engine = self.engine
        if engine is None:
            return []
        if len(text) <= engine.CHUNK:
            with self.engine_lock:
                return engine.detect(text, limit=0, mark_ignored=mark_ignored)
        import dataclasses
        out = []
        pos = 0
        while pos < len(text):
            end = len(text)
            if end - pos > engine.CHUNK:
                cut = text.rfind("\n", pos + engine.CHUNK // 2, pos + engine.CHUNK)
                end = cut + 1 if cut > pos else pos + engine.CHUNK
            with self.engine_lock:  # released between chunks so typing stays responsive
                found = engine.detect(text[pos:end], limit=0, mark_ignored=mark_ignored)
            out.extend(dataclasses.replace(f, start=f.start + pos, end=f.end + pos) for f in found)
            pos = end
        return out

    def submit(self, key, text: str) -> None:
        with self._cv:
            self._job = (key, text, time.monotonic())
            self._cv.notify()

    def request_reload(self) -> None:
        with self._cv:
            self._reload = True
            self._cv.notify()

    def _build(self) -> None:
        from .detector.engine import DetectionEngine
        try:
            engine = DetectionEngine(self.config, self.ignore, models_dir())
            with self.engine_lock:
                self.engine = engine
            self.events.put(("engine_ready", self.engine.status()))
        except Exception:
            log.exception("Detection engine failed to start")
            self.events.put(("engine_ready", {"ner_loaded": False, "ner_error": "engine failed to start"}))
        finally:
            self.ready.set()

    def run(self) -> None:
        self._build()
        while not self.stop_event.is_set():
            with self._cv:
                while self._job is None and not self._reload and not self.stop_event.is_set():
                    self._cv.wait(0.5)
                job, reload = self._job, self._reload
                self._reload = False
            if reload:
                self._build()
            if job is None:
                continue
            key, text, submitted = job
            delay = self.config.data.get("debounce_ms", 500) / 1000.0 - (time.monotonic() - submitted)
            if delay > 0:
                self.stop_event.wait(delay)
                with self._cv:
                    if self._job is not job:
                        continue  # newer text arrived while waiting
            with self._cv:
                if self._job is job:
                    self._job = None
            if self.engine is None:
                continue
            try:
                with self.engine_lock:
                    findings = self.engine.detect(text)
            except Exception:
                log.exception("Detection failed")
                findings = []
            self.events.put(("findings", key, text, findings))
