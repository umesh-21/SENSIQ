"""On-screen UI drawn over other apps: a count badge at the field's corner, a panel
listing what was found, and short toasts. All windows are topmost and
non-activating, so clicking them never takes the caret away from the user's app.
"""
from __future__ import annotations

import re
import tkinter as tk
from typing import Callable, List, Optional, Tuple

from . import winutil
from .detector.types import HIGH, LOW, MEDIUM, Finding
from .masking import REDACT, suggest

INK = "#1C2433"
MUTED = "#5B6577"
LINE = "#D5DBE4"
SURFACE = "#FFFFFF"
ACTION = "#0E7C7B"
ACTION_DARK = "#0A5F5E"
SEV_COLOR = {HIGH: "#C8102E", MEDIUM: "#A35F00", LOW: "#46607A"}
TOAST_ACCENT = {"high": SEV_COLOR[HIGH], "warn": SEV_COLOR[MEDIUM], "ok": ACTION, "info": MUTED}
KEY = "#FF00FE"  # transparent colour for the round badge
MAX_ROWS = 6


def _preview(s: str, limit: int = 30) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 11] + "…" + s[-10:]


def _shown_original(f: Finding) -> str:
    # Never re-display a full secret on screen.
    if f.kind in REDACT:
        return f.text[:4] + "…" if len(f.text) > 4 else "…"
    return _preview(f.text)


_MARKS = str.maketrans("", "", "")
VISUAL_WHERE = {"AADHAAR_QR": "QR code in the picture", "QR_CODE": "QR code in the picture",
                "PHOTO_ID": "photo in the picture", "FACE": "face in the picture",
                "SIGNATURE": "signature in the picture"}
CONTEXT_MAX = 5  # occurrences shown per row under "where"


def where_found(f: Finding, extracted, width: int = 90) -> dict:
    """Where a detail sits in the checked text, for the card's "where" drop-down:
    {"page": "Page 3" or "", "prev": earlier line or "", "before", "match", "after"}.
    Short lines (OCR often puts a PIN or a date alone on a line) also get the line above."""
    text = getattr(extracted, "text", "") or ""
    page = ""
    pages = getattr(extracted, "pages", None) or []
    if pages:
        idx = [p.index for p in pages if p.start <= f.start]
        if idx:
            page = f"Page {idx[-1] + 1}"
    frames = getattr(extracted, "frame_words", None) or []
    if not page and len(frames) > 1:
        for i, words in enumerate(frames):
            if words and words[0][0] <= f.start <= words[-1][1]:
                page = f"Page {i + 1}"
                break
    if f.source == "visual" or f.kind in VISUAL_WHERE:
        return {"page": page, "prev": "", "before": "", "match": VISUAL_WHERE.get(f.kind, "in the picture"),
                "after": "", "visual": True}
    if not text or f.end > len(text):
        return {"page": page, "prev": "", "before": "", "match": _shown_original(f), "after": ""}
    ls = text.rfind("\n", 0, f.start) + 1
    le = text.find("\n", f.end)
    le = len(text) if le < 0 else le
    clean = lambda s: " ".join(s.translate(_MARKS).split())
    before, after = clean(text[ls:f.start]), clean(text[f.end:le])
    match = _shown_original(f) if f.kind in REDACT else clean(text[f.start:f.end])
    budget = max(20, width - len(match))
    room_b = max(budget // 2, budget - len(after))  # unused space on one side goes to the other
    room_a = max(budget // 2, budget - len(before))
    if len(before) > room_b:
        before = "…" + before[-room_b:].lstrip()
    if len(after) > room_a:
        after = after[:room_a].rstrip() + "…"
    prev = ""
    if len(before) + len(after) < 14 and ls > 0:  # the line says little on its own
        pe = ls - 1
        while pe > 0:
            ps = text.rfind("\n", 0, pe) + 1
            cand = clean(text[ps:pe])
            if cand:
                prev = cand if len(cand) <= width else "…" + cand[-width:]
                break
            pe = ps - 1
    if before and not before.endswith(" "):
        before += " "
    if after and not after.startswith((" ", ",", ".", ";", ":")):
        after = " " + after
    return {"page": page, "prev": prev, "before": before, "match": match, "after": after}


class Overlay:
    def __init__(self, root: tk.Tk, *, on_replace: Callable[[Finding], None],
                 on_ignore: Callable[[Finding, bool], None], on_fix_all: Callable[[], None],
                 on_style: Callable[[], None], get_style: Callable[[], str]):
        self.root = root
        self.on_replace, self.on_ignore, self.on_fix_all = on_replace, on_ignore, on_fix_all
        self.on_style, self.get_style = on_style, get_style
        try:
            self.scale = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        except Exception:
            self.scale = 1.0
        self.anchor: Optional[Tuple[int, int, int, int]] = None
        self.findings: List[Finding] = []
        self.badge = None
        self.badge_canvas = None
        self.badge_xy: Optional[Tuple[int, int]] = None
        self.panel = None
        self.panel_open = False
        self.toast_win = None
        self._toast_job = None
        self.f_body = ("Segoe UI", 9)
        self.f_small = ("Segoe UI", 8)
        self.f_label = ("Segoe UI Semibold", 9)
        self.f_title = ("Segoe UI Semibold", 10)
        self.f_mono = ("Consolas", 9)
        self.f_mono_strike = ("Consolas", 9, "overstrike")

    def px(self, v: float) -> int:
        return int(round(v * self.scale))

    # ---- window plumbing --------------------------------------------------------------
    def _new_window(self, bg: str):
        w = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        try:
            w.attributes("-topmost", True)
        except tk.TclError:
            pass
        w.configure(bg=bg)
        w._overlay_ready = False
        return w

    def _show(self, w, x: int, y: int, width: Optional[int] = None, height: Optional[int] = None) -> None:
        w.geometry(f"+{x}+{y}" if width is None else f"{width}x{height}+{x}+{y}")
        prev = winutil.foreground_window()
        if w.state() == "withdrawn":
            w.deiconify()
        w.lift()
        w.update_idletasks()
        if not w._overlay_ready:
            winutil.make_overlay(w)
            w._overlay_ready = True
        winutil.restore_foreground(prev)

    def _clamp(self, x: int, y: int, w: int, h: int) -> Tuple[int, int]:
        vx, vy, vw, vh = winutil.virtual_screen()
        return min(max(vx, x), vx + vw - w), min(max(vy, y), vy + vh - h)

    # ---- public API ------------------------------------------------------------------
    def set_anchor(self, rect: Tuple[int, int, int, int]) -> None:
        self.anchor = rect
        if self.findings:
            self._place_badge()
            if self.panel_open:
                self._position_panel()

    def update(self, findings: List[Finding]) -> None:
        self.findings = list(findings)
        if not self.findings:
            self._hide_badge()
            self.close_panel()
            return
        self._ensure_badge()
        self._draw_badge()
        self._place_badge()
        if self.panel_open:
            self._open_panel()

    def hide_all(self) -> None:
        self.findings = []
        self._hide_badge()
        self.close_panel()

    # ---- badge ----------------------------------------------------------------------
    def _ensure_badge(self) -> None:
        if self.badge is not None:
            return
        self.badge = self._new_window(KEY)
        try:
            self.badge.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        size = self.px(24)
        c = tk.Canvas(self.badge, width=size, height=size, bg=KEY, highlightthickness=0, bd=0, cursor="hand2")
        c.pack()
        c.bind("<Button-1>", lambda e: self.toggle_panel())
        self.badge_canvas = c

    def _draw_badge(self) -> None:
        c, size = self.badge_canvas, self.px(24)
        c.delete("all")
        color = SEV_COLOR[max(f.severity for f in self.findings)]
        c.create_oval(1, 1, size - 1, size - 1, fill=color, outline=SURFACE, width=max(1, self.px(1.5)))
        n = len(self.findings)
        c.create_text(size / 2, size / 2, text=str(n) if n < 10 else "9+", fill=SURFACE, font=("Segoe UI Semibold", 8))

    def _badge_position(self) -> Optional[Tuple[int, int]]:
        if not self.anchor:
            return None
        left, top, right, bottom = self.anchor
        w, h, size = right - left, bottom - top, self.px(24)
        if w < self.px(40) or h < self.px(12):
            return None
        if h < size + self.px(16):  # single-line box: sit inside, vertically centred
            x, y = right - size - self.px(6), top + (h - size) // 2
        else:                        # multi-line box: bottom-right corner, like a grammar checker
            x, y = right - size - self.px(12), bottom - size - self.px(10)
        return self._clamp(x, y, size, size)

    def _place_badge(self) -> None:
        if self.badge is None:
            return
        xy = self._badge_position()
        if xy is None:
            self._hide_badge()
            return
        if xy != self.badge_xy or self.badge.state() == "withdrawn":
            self.badge_xy = xy
            self._show(self.badge, *xy)

    def _hide_badge(self) -> None:
        if self.badge is not None:
            self.badge.withdraw()
        self.badge_xy = None

    # ---- panel ----------------------------------------------------------------------
    def toggle_panel(self) -> None:
        if self.panel_open:
            self.close_panel()
        else:
            self._open_panel()

    def close_panel(self) -> None:
        self.panel_open = False
        if self.panel is not None:
            self.panel.withdraw()

    def _open_panel(self) -> None:
        if not self.findings:
            return
        if self.panel is None:
            self.panel = self._new_window(LINE)
        self._build_panel()
        self.panel_open = True
        self._position_panel()

    def _position_panel(self) -> None:
        p = self.panel
        p.update_idletasks()
        w, h = p.winfo_reqwidth(), p.winfo_reqheight()
        if self.badge_xy:
            bx, by = self.badge_xy
            size = self.px(24)
            x, y = bx + size - w, by - h - self.px(8)
            vx, vy, vw, vh = winutil.virtual_screen()
            if y < vy:
                y = by + size + self.px(8)
        else:
            left, top, right, bottom = winutil.work_area()
            x, y = right - w - self.px(24), bottom - h - self.px(24)
        x, y = self._clamp(x, y, w, h)
        if self.badge_xy:  # never cover the badge itself
            bx, by = self.badge_xy
            size = self.px(24)
            if x < bx + size and x + w > bx and y < by + size and y + h > by:
                x, _ = self._clamp(bx - w - self.px(8), y, w, h)
        self._show(p, x, y, w, h)

    def _link(self, parent, text: str, command, fg: str = ACTION, font=None):
        lbl = tk.Label(parent, text=text, fg=fg, bg=parent["bg"], font=font or self.f_label,
                       cursor="hand2", padx=0, pady=self.px(2))
        lbl.bind("<Button-1>", lambda e: command())
        lbl.bind("<Enter>", lambda e: lbl.configure(fg=INK))
        lbl.bind("<Leave>", lambda e: lbl.configure(fg=fg))
        return lbl

    def _button(self, parent, text: str, command, filled: bool):
        bg = ACTION if filled else parent["bg"]
        fg = SURFACE if filled else INK
        lbl = tk.Label(parent, text=text, bg=bg, fg=fg, font=self.f_label, cursor="hand2",
                       padx=self.px(12), pady=self.px(4),
                       highlightthickness=0 if filled else 1, highlightbackground=LINE)
        lbl.bind("<Button-1>", lambda e: command())
        hover = ACTION_DARK if filled else "#EEF1F5"
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=hover))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=bg))
        return lbl

    def _build_panel(self) -> None:
        p = self.panel
        for child in p.winfo_children():
            child.destroy()
        px = self.px
        body = tk.Frame(p, bg=SURFACE, padx=px(16), pady=px(14))
        body.pack(padx=1, pady=1, fill="both")
        n = len(self.findings)
        tk.Label(body, text=f"{n} private detail{'s' if n != 1 else ''} in this field", font=self.f_title,
                 fg=INK, bg=SURFACE, anchor="w").pack(fill="x")
        tk.Label(body, text="Checked on this computer. Replace them before you send.", font=self.f_small,
                 fg=MUTED, bg=SURFACE, anchor="w").pack(fill="x", pady=(px(2), px(10)))
        tk.Frame(body, bg=SURFACE, width=px(340), height=0).pack()

        style = self.get_style()
        ordered = sorted(self.findings, key=lambda f: (-f.severity, f.start))  # most serious first
        for f in ordered[:MAX_ROWS]:
            self._row(body, f, style)
        if n > MAX_ROWS:
            tk.Label(body, text=f"{n - MAX_ROWS} more not shown. Replace all covers them.", font=self.f_small,
                     fg=MUTED, bg=SURFACE, anchor="w").pack(fill="x", pady=(px(2), 0))

        tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=px(10))
        footer = tk.Frame(body, bg=SURFACE)
        footer.pack(fill="x")
        style_text = "Use labels like [Aadhaar]" if style == "partial" else "Use partial masks like XXXX 1234"
        self._link(footer, style_text, self.on_style, fg=MUTED, font=self.f_small).pack(side="left")
        self._button(footer, "Close", self.close_panel, filled=False).pack(side="right")
        self._button(footer, f"Replace all ({n})" if n > 1 else "Replace", self.on_fix_all,
                     filled=True).pack(side="right", padx=(0, px(8)))

    def _row(self, parent, f: Finding, style: str) -> None:
        px = self.px
        color = SEV_COLOR[f.severity]
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=(0, px(8)))
        tk.Frame(row, bg=color, width=px(3)).pack(side="left", fill="y")
        inner = tk.Frame(row, bg=SURFACE, padx=px(10))
        inner.pack(side="left", fill="x", expand=True)

        top = tk.Frame(inner, bg=SURFACE)
        top.pack(fill="x")
        tk.Label(top, text=f.label, font=self.f_label, fg=INK, bg=SURFACE).pack(side="left")
        if f.source == "ner":
            tk.Label(top, text=f"name model, {int(f.score * 100)}% sure", font=self.f_small, fg=MUTED,
                     bg=SURFACE).pack(side="left", padx=(px(6), 0))

        values = tk.Frame(inner, bg=SURFACE)
        values.pack(fill="x", pady=px(2))
        tk.Label(values, text=_shown_original(f), font=self.f_mono_strike, fg=color, bg=SURFACE).pack(side="left")
        tk.Label(values, text="  →  ", font=self.f_small, fg=MUTED, bg=SURFACE).pack(side="left")
        tk.Label(values, text=_preview(suggest(f, style)), font=self.f_mono, fg=ACTION, bg=SURFACE).pack(side="left")

        actions = tk.Frame(inner, bg=SURFACE)
        actions.pack(fill="x")
        self._link(actions, "Replace", lambda f=f: self.on_replace(f)).pack(side="left")
        self._link(actions, "Ignore", lambda f=f: self.on_ignore(f, False), fg=MUTED).pack(side="left", padx=(px(14), 0))
        self._link(actions, "Always ignore", lambda f=f: self.on_ignore(f, True), fg=MUTED).pack(side="left", padx=(px(14), 0))

    # ---- file / image report ------------------------------------------------------------
    ACTION_TEXT = {"image": "Replace and copy", "copy": "Replace and copy",
                   "dialog": "Replace and upload", "drag": "Make safe copy", "uploaded": "Make safe copy",
                   "browser": "Replace and send", "opened": "Make safe copy"}
    HINT_TEXT = {
        "image": "Tick what to replace. The clipboard will then hold the safe image, so pasting sends that.",
        "copy": "Tick what to replace. The clipboard will then hold safe copies, so pasting sends those.",
        "dialog": "Tick what to replace. The safe copy will be uploaded instead of the original.",
        "drag": "Tick what to replace. A safe copy will be made for you to share instead.",
        "uploaded": "It was already added to the page. Tick what to replace: you get a safe copy to paste "
                    "there instead, after removing the original.",
        "browser": "The page is waiting. Tick what to replace: the page then gets the safe copy. "
                   "'Use original' sends it as it is.",
        "opened": "Tick what to replace to get a safe copy you can share instead.",
    }

    def file_report(self, ctx, results, on_apply: Callable[[dict], None], ms: int = 300000,
                    on_not_private: Optional[Callable[[list], None]] = None,
                    on_flag_again: Optional[Callable[[list], None]] = None,
                    on_close: Optional[Callable[[str], None]] = None) -> None:
        """Card for images and files: what was found, a tick box per detail, and a button that
        replaces the ticked details and hands on the safe version. on_close(reason) is called
        once: "original" (Use original), "timeout", or "done" (after the safe copy)."""
        self.close_file_report()
        self._report_on_close = on_close
        px = self.px
        worst = max(f.severity for r in results for f in r.findings)
        w = self._new_window(SEV_COLOR[worst])
        body = tk.Frame(w, bg=SURFACE, padx=px(16), pady=px(14))
        body.pack(padx=(px(4), 1), pady=1, fill="both")
        total = sum(len(r.findings) for r in results)
        what = "this image" if ctx.kind == "image" else ("these files" if len(results) > 1 else "this file")
        top = tk.Frame(body, bg=SURFACE)
        top.pack(fill="x")
        tk.Label(top, text=f"{total} private detail{'s' if total != 1 else ''} in {what}", font=self.f_title,
                 fg=INK, bg=SURFACE, anchor="w").pack(side="left")
        # ✕ closes the report without doing anything, so another file can be chosen
        close_x = tk.Label(top, text="✕", font=("Segoe UI", 11), fg=MUTED, bg=SURFACE, cursor="hand2",
                           padx=px(4))
        close_x.pack(side="right")
        close_x.bind("<Button-1>", lambda e: self.close_file_report("dismissed"))
        close_x.bind("<Enter>", lambda e: close_x.configure(fg=INK))
        close_x.bind("<Leave>", lambda e: close_x.configure(fg=MUTED))
        tk.Label(body, text=f"{ctx.source}. {self.HINT_TEXT.get(ctx.kind, '')}", font=self.f_small, fg=MUTED,
                 bg=SURFACE, anchor="w", wraplength=px(360), justify="left").pack(fill="x", pady=(px(2), px(8)))
        tk.Frame(body, bg=SURFACE, width=px(360), height=0).pack()

        style = self.get_style()
        ticks = {}      # (result index, group key) -> {"on": bool, "items": [findings]}
        setters = []    # functions(bool) that set one row's tick
        action_btn = {}

        def refresh_button():
            n = sum(len(v["items"]) for v in ticks.values() if v["on"])
            btn = action_btn.get("w")
            if btn is not None:
                base = self.ACTION_TEXT.get(ctx.kind, "Replace")
                btn.configure(text=f"{base} ({n})" if n else base)

        def keep_open(_e=None):
            """Any interaction restarts the card's auto-close timer."""
            if getattr(self, "_report_job", None) is not None:
                try:
                    self.root.after_cancel(self._report_job)
                except Exception:
                    pass
                self._report_job = self.root.after(ms, lambda: self.close_file_report("timeout"))

        # ---- select all / none ----
        bar = tk.Frame(body, bg=SURFACE)
        bar.pack(fill="x", pady=(0, px(4)))
        tk.Label(bar, text="Replace:", font=self.f_small, fg=MUTED, bg=SURFACE).pack(side="left")

        def set_all(on: bool):
            for s in setters:
                s(on)
            refresh_button()
            keep_open()
        self._link(bar, "all", lambda: set_all(True), font=self.f_small).pack(side="left", padx=(px(6), 0))
        tk.Label(bar, text="·", font=self.f_small, fg=MUTED, bg=SURFACE).pack(side="left", padx=px(4))
        self._link(bar, "none", lambda: set_all(False), font=self.f_small).pack(side="left")

        # ---- scrollable list of every detail ----
        area = tk.Frame(body, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        area.pack(fill="both", expand=True)
        canvas = tk.Canvas(area, bg=SURFACE, highlightthickness=0, bd=0)
        bar_v = tk.Scrollbar(area, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar_v.set)
        inner = tk.Frame(canvas, bg=SURFACE, padx=px(6), pady=px(4))
        canvas.create_window((0, 0), window=inner, anchor="nw")

        def make_row(parent, key, items, extracted=None):
            """One row per distinct value; the same name on 200 pages is one row "×200".
            "▸ where" opens the line(s) each occurrence comes from."""
            f = items[0]
            holder = tk.Frame(parent, bg=SURFACE)
            holder.pack(fill="x", pady=(0, px(3)))
            row = tk.Frame(holder, bg=SURFACE, cursor="hand2")
            row.pack(fill="x")
            box = tk.Label(row, text="☑", font=("Segoe UI Symbol", 11), fg=ACTION, bg=SURFACE, padx=px(2))
            box.pack(side="left")
            tk.Frame(row, bg=SEV_COLOR[f.severity], width=px(3)).pack(side="left", fill="y", padx=(px(4), 0))
            tk.Label(row, text=f.label, font=self.f_body, fg=INK, bg=SURFACE, padx=px(8)).pack(side="left")
            orig = tk.Label(row, text=_shown_original(f), font=self.f_mono_strike, fg=SEV_COLOR[f.severity], bg=SURFACE)
            orig.pack(side="left")
            tk.Label(row, text=" → ", font=self.f_small, fg=MUTED, bg=SURFACE).pack(side="left")
            new = tk.Label(row, text=_preview(suggest(f, style), 22), font=self.f_mono, fg=ACTION, bg=SURFACE)
            new.pack(side="left")
            if len(items) > 1:
                tk.Label(row, text=f"  ×{len(items)}", font=self.f_label, fg=MUTED, bg=SURFACE).pack(side="left")
            state = {"on": True, "items": items}
            ticks[key] = state

            def set_on(on: bool):
                state["on"] = on
                box.configure(text="☑" if on else "☐", fg=ACTION if on else MUTED)
                orig.configure(font=self.f_mono_strike if on else self.f_mono)
                new.configure(fg=ACTION if on else LINE)
            setters.append(set_on)

            def toggle(_e=None):
                set_on(not state["on"])
                refresh_button()
                keep_open()
            for widget in (row,) + tuple(row.winfo_children()):
                widget.bind("<Button-1>", toggle)

            if on_not_private is not None:
                marked = {"on": False}

                def not_private(_e=None):
                    keep_open()
                    if not marked["on"]:      # mark: never flag this value again
                        marked["on"] = True
                        set_on(False)
                        on_not_private(items)
                        link.configure(text="won't be flagged again", fg=MUTED, cursor="arrow")
                        if on_flag_again is not None:
                            undo.pack(side="right", before=link)
                    refresh_button()
                    return "break"

                def flag_again(_e=None):    # undo a mistaken "not private"
                    keep_open()
                    if marked["on"]:
                        marked["on"] = False
                        on_flag_again(items)
                        set_on(True)
                        link.configure(text="not private", fg=MUTED, cursor="hand2")
                        undo.pack_forget()
                    refresh_button()
                    return "break"
                undo = tk.Label(row, text="undo", font=self.f_small, fg=ACTION, bg=SURFACE,
                                cursor="hand2", padx=0)
                undo.bind("<Button-1>", flag_again)
                link = tk.Label(row, text="not private", font=self.f_small, fg=MUTED, bg=SURFACE,
                                cursor="hand2", padx=px(8))
                link.pack(side="right")
                link.bind("<Button-1>", not_private)

            # ---- "where": the line each occurrence comes from ----
            detail = {"frame": None}
            where = tk.Label(row, text="▸ where", font=self.f_small, fg=ACTION, bg=SURFACE,
                             cursor="hand2", padx=px(4))
            where.pack(side="right")

            def build_detail():
                box_ = tk.Frame(holder, bg="#F4F6F9", padx=px(8), pady=px(4))
                tk.Label(box_, text="Found in:", font=self.f_small, fg=MUTED, bg="#F4F6F9",
                         anchor="w").pack(fill="x")
                bg = "#F4F6F9"
                for occ in items[:CONTEXT_MAX]:
                    info = where_found(occ, extracted, width=58)
                    if info["prev"]:
                        tk.Label(box_, text=(info["page"] + " · " if info["page"] else "") + info["prev"],
                                 font=self.f_small, fg=MUTED, bg=bg, anchor="w").pack(fill="x")
                    line = tk.Frame(box_, bg=bg)
                    line.pack(fill="x", pady=(0, px(3)))
                    if info["page"] and not info["prev"]:
                        tk.Label(line, text=info["page"] + " · ", font=self.f_small, fg=MUTED, bg=bg,
                                 padx=0).pack(side="left")
                    if info["before"]:
                        tk.Label(line, text=info["before"], font=self.f_small, fg=INK, bg=bg,
                                 padx=0).pack(side="left")
                    tk.Label(line, text=info["match"], font=("Segoe UI Semibold", 8), padx=px(1),
                             fg=SEV_COLOR[occ.severity], bg="#FFF3CD").pack(side="left")
                    if info["after"]:
                        tk.Label(line, text=info["after"], font=self.f_small, fg=INK, bg=bg,
                                 padx=0).pack(side="left")
                if len(items) > CONTEXT_MAX:
                    tk.Label(box_, text=f"…and {len(items) - CONTEXT_MAX} more places", font=self.f_small,
                             fg=MUTED, bg="#F4F6F9", anchor="w").pack(fill="x")
                return box_

            def show_where(_e=None):
                keep_open()
                if detail["frame"] is None:
                    detail["frame"] = build_detail()
                    bind_wheel(detail["frame"])
                if detail["frame"].winfo_ismapped():
                    detail["frame"].pack_forget()
                    where.configure(text="▸ where")
                else:
                    detail["frame"].pack(fill="x", padx=(px(22), 0), pady=(px(2), px(2)))
                    where.configure(text="▾ where")
                relayout()
                return "break"
            where.bind("<Button-1>", show_where)

        for i, r in enumerate(results):
            head = tk.Frame(inner, bg=SURFACE)
            head.pack(fill="x", pady=(px(4) if i else 0, px(2)))
            tk.Label(head, text=_preview(r.name, 40), font=self.f_label, fg=INK, bg=SURFACE).pack(side="left")
            meta = f"{len(r.findings)} found" + (f", {r.method}" if r.method else "")
            tk.Label(head, text=f"  ({meta})", font=self.f_small, fg=MUTED, bg=SURFACE).pack(side="left")
            groups = {}
            for f in sorted(r.findings, key=lambda f: (-f.severity, f.start)):
                groups.setdefault((f.kind, " ".join(f.text.lower().split())), []).append(f)
            for gk, items in groups.items():
                make_row(inner, (i, gk), items, r.extracted)
            if r.note:
                tk.Label(inner, text=f"Note: {r.note}", font=self.f_small, fg=MUTED, bg=SURFACE, anchor="w",
                         wraplength=px(360), justify="left").pack(fill="x")
            if not getattr(r, "can_redact", True):
                tk.Label(inner, text="This file can be checked but not changed: it will be shared as it is.",
                         font=self.f_small, fg=SEV_COLOR[MEDIUM], bg=SURFACE, anchor="w",
                         wraplength=px(360), justify="left").pack(fill="x")

        wa_left, wa_top, wa_right, wa_bottom = winutil.work_area()
        max_h = max(px(160), min(px(420), int((wa_bottom - wa_top) * 0.55)))
        canvas.pack(side="left", fill="both", expand=True)
        scroll_note = tk.Label(body, text=f"Scroll to see all {total} details.", font=self.f_small, fg=MUTED,
                               bg=SURFACE, anchor="w")

        def fit():
            """Size the list to its content (up to max_h, then it scrolls)."""
            inner.update_idletasks()
            content_w, content_h = inner.winfo_reqwidth(), inner.winfo_reqheight()
            canvas.configure(width=max(content_w, px(360)), height=min(content_h, max_h),
                             scrollregion=(0, 0, content_w, content_h))
            if content_h > max_h:
                if not bar_v.winfo_ismapped():
                    bar_v.pack(side="right", fill="y")
                if not scroll_note.winfo_ismapped():
                    scroll_note.pack(fill="x", pady=(px(3), 0), after=area)
            else:
                canvas.yview_moveto(0)

        def wheel(e):
            if inner.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
            keep_open()
            return "break"

        def bind_wheel(widget):
            widget.bind("<MouseWheel>", wheel)
            for child in widget.winfo_children():
                bind_wheel(child)

        def relayout():
            """After a "where" drop-down opens/closes: re-fit, keep the card's corner in place."""
            fit()
            w.update_idletasks()
            ww, hh = w.winfo_reqwidth(), w.winfo_reqheight()
            left, top, right, bottom = winutil.work_area()
            x, y = self._clamp(right - ww - px(24), bottom - hh - px(24), ww, hh)
            w.geometry(f"{ww}x{hh}+{x}+{y}")

        fit()
        bind_wheel(canvas)
        bind_wheel(inner)

        if ctx.kind != "image" and any(r.path is not None and r.path.suffix.lower() == ".pdf" for r in results):
            tk.Label(body, text="PDF pages are turned into images so hidden text is really removed.",
                     font=self.f_small, fg=MUTED, bg=SURFACE, anchor="w", wraplength=px(360),
                     justify="left").pack(fill="x", pady=(px(4), 0))
        tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=px(10))
        footer = tk.Frame(body, bg=SURFACE)
        footer.pack(fill="x")

        def apply():
            chosen = {}
            for (i, _), state in ticks.items():
                if state["on"]:
                    chosen.setdefault(i, []).extend(state["items"])
            if not chosen:  # nothing ticked: same as "Use original"
                self.close_file_report("original")
                return
            for child in footer.winfo_children():
                child.destroy()
            tk.Label(footer, text="Making the safe version…", font=self.f_label, fg=MUTED,
                     bg=SURFACE).pack(side="right")
            on_apply(chosen)

        self._button(footer, "Use original", lambda: self.close_file_report("original"),
                     filled=False).pack(side="left")
        btn = self._button(footer, self.ACTION_TEXT.get(ctx.kind, "Replace"), apply, filled=True)
        btn.pack(side="right")
        action_btn["w"] = btn
        self._report_apply = apply
        refresh_button()

        w.update_idletasks()
        ww, hh = w.winfo_reqwidth(), w.winfo_reqheight()
        left, top, right, bottom = winutil.work_area()
        x, y = self._clamp(right - ww - px(24), bottom - hh - px(24), ww, hh)
        self._show(w, x, y, ww, hh)
        self.report_win = w
        self._report_job = self.root.after(ms, lambda: self.close_file_report("timeout"))

    def apply_report(self) -> None:
        """Press the card's main button (used by automated tests)."""
        fn = getattr(self, "_report_apply", None)
        if fn is not None and getattr(self, "report_win", None) is not None:
            fn()

    def close_file_report(self, reason: str = "closed") -> None:
        callback = getattr(self, "_report_on_close", None)
        self._report_on_close = None
        self._report_apply = None
        if callback is not None and getattr(self, "report_win", None) is not None:
            try:
                callback(reason)
            except Exception:
                pass
        job = getattr(self, "_report_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            self._report_job = None
        win = getattr(self, "report_win", None)
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
            self.report_win = None

    # ---- offer (toast with a button) ---------------------------------------------------
    def offer(self, message: str, button: str, on_click: Callable[[], None], ms: int = 12000) -> None:
        self._close_toast()
        px = self.px
        w = self._new_window(MUTED)
        inner = tk.Frame(w, bg=SURFACE, padx=px(12), pady=px(10))
        inner.pack(padx=(px(4), 1), pady=1)
        tk.Label(inner, text=message, font=self.f_body, fg=INK, bg=SURFACE, wraplength=px(300),
                 justify="left").pack(anchor="w")
        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x", pady=(px(8), 0))

        def go():
            self._close_toast()
            on_click()
        self._button(row, button, go, filled=True).pack(side="right")
        self._button(row, "Not now", self._close_toast, filled=False).pack(side="right", padx=(0, px(8)))
        w.update_idletasks()
        ww, hh = w.winfo_reqwidth(), w.winfo_reqheight()
        left, top, right, bottom = winutil.work_area()
        x, y = self._clamp(right - ww - px(24), bottom - hh - px(24), ww, hh)
        self._show(w, x, y, ww, hh)
        self.toast_win = w
        self._toast_job = self.root.after(ms, self._close_toast)

    # ---- "checking…" note -------------------------------------------------------------
    def checking(self, message: str, ms: int = 600000) -> None:
        """Shown the moment a file is picked to upload/send, until its report (or "nothing
        found") arrives. Separate from toasts so other short notices don't hide it."""
        self.close_checking()
        px = self.px
        w = self._new_window(ACTION)
        inner = tk.Frame(w, bg=SURFACE, padx=px(12), pady=px(10))
        inner.pack(padx=(px(4), 1), pady=1)
        head = tk.Label(inner, text="PII Guard is checking", font=self.f_label, fg=ACTION, bg=SURFACE, anchor="w")
        head.pack(fill="x")
        tk.Label(inner, text=message, font=self.f_body, fg=INK, bg=SURFACE, wraplength=px(300),
                 justify="left").pack(anchor="w", pady=(px(2), 0))
        w.update_idletasks()
        ww, hh = w.winfo_reqwidth(), w.winfo_reqheight()
        left, top, right, bottom = winutil.work_area()
        x, y = self._clamp(right - ww - px(24), bottom - hh - px(24), ww, hh)
        self._show(w, x, y, ww, hh)
        self.checking_win = w
        state = {"n": 0}

        def tick():
            if getattr(self, "checking_win", None) is not w:
                return
            state["n"] = (state["n"] + 1) % 4
            try:
                head.configure(text="PII Guard is checking" + "." * state["n"])
            except tk.TclError:
                return
            self._checking_tick = self.root.after(400, tick)
        self._checking_tick = self.root.after(400, tick)
        self._checking_job = self.root.after(ms, self.close_checking)

    def close_checking(self) -> None:
        for name in ("_checking_tick", "_checking_job"):
            job = getattr(self, name, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, name, None)
        w = getattr(self, "checking_win", None)
        self.checking_win = None
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass

    # ---- update card ----------------------------------------------------------------
    @staticmethod
    def notes_text(md: str, limit: int = 4000) -> list:
        """Release notes (Markdown) as [(text, style)] lines: style "h" heading, "b" bullet, "" text."""
        out = []
        for raw in (md or "").replace("\r", "").split("\n"):
            line = raw.rstrip()
            if not line.strip() or line.strip().startswith("<!--"):
                if out and out[-1][0]:
                    out.append(("", ""))
                continue
            line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line)
            line = re.sub(r"`([^`]*)`", r"\1", line)
            line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
            m = re.match(r"^\s*#{1,6}\s+(.*)", line)
            if m:
                out.append((m.group(1).strip(), "h"))
                continue
            m = re.match(r"^(\s*)[-*+]\s+(.*)", line)
            if m:
                out.append(("  " * (len(m.group(1)) // 2) + "• " + m.group(2).strip(), "b"))
                continue
            out.append((line.strip(), ""))
        while out and not out[-1][0]:
            out.pop()
        text_len = 0
        for i, (t, _) in enumerate(out):
            text_len += len(t)
            if text_len > limit:
                return out[:i] + [("… see the full list on GitHub.", "")]
        return out

    def update_card(self, info, current: str, on_update: Callable[[], None], on_skip: Callable[[], None],
                    on_page: Callable[[], None]) -> None:
        """A new version is out: what's new, and Update now / Later / Skip this version."""
        self.close_update_card()
        px = self.px
        w = self._new_window(ACTION)
        body = tk.Frame(w, bg=SURFACE, padx=px(16), pady=px(14))
        body.pack(padx=(px(4), 1), pady=1, fill="both")
        tk.Label(body, text=f"PII Guard {info.version} is available", font=self.f_title, fg=INK, bg=SURFACE,
                 anchor="w").pack(fill="x")
        size = f" · {info.setup_size / 1e6:.0f} MB download" if info.setup_size else ""
        tk.Label(body, text=f"You have version {current}{size}", font=self.f_small, fg=MUTED, bg=SURFACE,
                 anchor="w").pack(fill="x", pady=(px(2), px(8)))
        tk.Label(body, text="What's new", font=self.f_label, fg=INK, bg=SURFACE, anchor="w").pack(fill="x")
        frame = tk.Frame(body, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        frame.pack(fill="both", expand=True, pady=(px(4), 0))
        lines = self.notes_text(info.notes) or [("No notes were published for this version.", "")]
        t = tk.Text(frame, font=self.f_small, fg=INK, bg=SURFACE, bd=0, highlightthickness=0, wrap="word",
                    width=60, height=min(14, max(4, len(lines) + 1)), padx=px(8), pady=px(6), cursor="arrow")
        t.tag_configure("h", font=self.f_label, spacing1=px(4))
        t.tag_configure("b", lmargin1=px(4), lmargin2=px(14))
        for text, style in lines:
            t.insert("end", text + "\n", style or ())
        t.configure(state="disabled")
        sb = tk.Scrollbar(frame, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        links = tk.Frame(body, bg=SURFACE)
        links.pack(fill="x", pady=(px(6), 0))
        self._link(links, "See on GitHub", on_page, font=self.f_small).pack(side="left")

        def skip():
            self.close_update_card()
            on_skip()
        self._link(links, "Skip this version", skip, fg=MUTED, font=self.f_small).pack(side="right")
        tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=px(10))
        footer = tk.Frame(body, bg=SURFACE)
        footer.pack(fill="x")
        status = tk.Label(footer, text="", font=self.f_label, fg=MUTED, bg=SURFACE)

        def go():
            for child in footer.winfo_children():
                if child is not status:
                    child.destroy()
            status.configure(text="Starting download…")
            status.pack(side="right")
            on_update()
        self._button(footer, "Later", self.close_update_card, filled=False).pack(side="left")
        self._button(footer, "Update now", go, filled=True).pack(side="right")
        self._update_status = status
        w.update_idletasks()
        ww, hh = w.winfo_reqwidth(), w.winfo_reqheight()
        left, top, right, bottom = winutil.work_area()
        x, y = self._clamp(right - ww - px(24), bottom - hh - px(24), ww, hh)
        self._show(w, x, y, ww, hh)
        self.update_win = w

    def update_status(self, text: Optional[str]) -> None:
        """Progress line on the update card; None closes the card (failed update)."""
        if text is None:
            self.close_update_card()
            return
        lbl = getattr(self, "_update_status", None)
        if lbl is not None:
            try:
                lbl.configure(text=text)
            except tk.TclError:
                pass

    def close_update_card(self) -> None:
        w = getattr(self, "update_win", None)
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
        self.update_win = None
        self._update_status = None

    # ---- toast ----------------------------------------------------------------------
    def toast(self, message: str, kind: str = "info", ms: int = 4500) -> None:
        self._close_toast()
        px = self.px
        w = self._new_window(TOAST_ACCENT.get(kind, MUTED))
        inner = tk.Frame(w, bg=SURFACE, padx=px(12), pady=px(8))
        inner.pack(padx=(px(4), 1), pady=1)
        tk.Label(inner, text=message, font=self.f_body, fg=INK, bg=SURFACE, wraplength=px(300),
                 justify="left").pack()
        w.update_idletasks()
        ww, hh = w.winfo_reqwidth(), w.winfo_reqheight()
        if self.badge_xy and not self.panel_open:
            bx, by = self.badge_xy
            x, y = bx + px(24) - ww, by - hh - px(8)
        else:
            left, top, right, bottom = winutil.work_area()
            x, y = right - ww - px(24), bottom - hh - px(24)
        x, y = self._clamp(x, y, ww, hh)
        self._show(w, x, y, ww, hh)
        for widget in (w, inner) + tuple(inner.winfo_children()):
            widget.bind("<Button-1>", lambda e: self._close_toast())
        self.toast_win = w
        self._toast_job = self.root.after(ms, self._close_toast)

    def _close_toast(self) -> None:
        if self._toast_job is not None:
            try:
                self.root.after_cancel(self._toast_job)
            except Exception:
                pass
            self._toast_job = None
        if self.toast_win is not None:
            try:
                self.toast_win.destroy()
            except Exception:
                pass
            self.toast_win = None
