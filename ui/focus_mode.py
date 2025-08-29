# ui/focus_mode.py
from __future__ import annotations

import os
import sys
import re
import json
import time
import threading
import subprocess
import webbrowser
from datetime import date
from typing import Literal, Optional, Callable

import customtkinter as ctk

from config import FOCUS_DEFAULTS
from ui.styles import COLORS
from ui.center_notice import CenterNotice
from services.notification_center import NotificationCenter, NotificationAction
from services.settings_store import settings

State = Literal["IDLE", "WORK", "BREAK_SHORT", "BREAK_LONG", "PAUSED"]


# ---------- Journal Focus (local) ----------
def _append_focus_minutes(minutes: int) -> None:
    """
    Ajoute une ligne {date, minutes} dans data/focus_log.json.
    Tolérant aux erreurs et idempotent côté UI.
    """
    try:
        path = os.path.join("data", "focus_log.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                rows = json.load(f) or []
        rows.append({"date": date.today().isoformat(), "minutes": int(minutes)})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    except Exception:
        # Pas d'exception bloquante pour l'UI
        pass


class FocusMode(ctk.CTkFrame):
    """
    Pomodoro minimaliste (Apple-like)
    - Anneau 200px, dégradé vert → jaune → orange → rouge
    - Boutons Start / Pause / Stop
    - Notifications système + bannière centrale
    - Spotify optionnel au début de chaque WORK
    """
    def __init__(self, parent, *, on_session_end: Optional[Callable[[str], None]] = None):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=16)

        # Concurrence
        self._lock = threading.Lock()
        self._ticker: Optional[threading.Thread] = None
        self._running = False

        # --------- Config depuis settings (fallback FOCUS_DEFAULTS) ---------
        f = settings.get("focus", {}) or {}
        self.work_min = int(f.get("work_min", FOCUS_DEFAULTS["WORK_MIN"]))
        self.short_min = int(f.get("short_break_min", FOCUS_DEFAULTS["SHORT_BREAK_MIN"]))
        self.long_min = int(f.get("long_break_min", FOCUS_DEFAULTS["LONG_BREAK_MIN"]))
        self.before_long = int(f.get("sessions_before_long", FOCUS_DEFAULTS["SESSIONS_BEFORE_LONG"]))
        self.spotify_url = str(f.get("spotify_url", FOCUS_DEFAULTS["SPOTIFY_URL"]))
        self.launch_spotify = bool(f.get("launch_spotify", True))

        # State
        self.state: State = "IDLE"
        self.session_index = 0          # #WORK effectuées dans le cycle
        self.remaining = 0.0            # secondes restantes dans la phase courante
        self._current_total = 0.0       # durée totale de la phase (pour le log)

        # Notifications
        self._nc = NotificationCenter.instance()
        self._on_session_end = on_session_end

        # UI
        self._build_ui()
        self._set_state("IDLE")
        self._draw_ring(0.0)
        self._set_time(self.work_min * 60)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        header.grid_columnconfigure(0, weight=1)

        self.lbl_sub = ctk.CTkLabel(
            header, text="Pomodoro prêt",
            font=("SF Pro Text", 11),
            text_color=COLORS["text_secondary"]
        )
        self.lbl_sub.grid(row=1, column=0, sticky="w")

        # Canvas anneau
        canvas_wrap = ctk.CTkFrame(self, fg_color="transparent")
        canvas_wrap.grid(row=1, column=0, padx=14, pady=6, sticky="nsew")
        canvas_wrap.grid_rowconfigure(0, weight=1)
        canvas_wrap.grid_columnconfigure(0, weight=1)

        self._ring_size = 200
        self._ring_pad = 12
        self._ring_width = 8

        self.canvas = ctk.CTkCanvas(
            canvas_wrap, width=self._ring_size, height=self._ring_size,
            bg=COLORS["bg_card"], highlightthickness=0
        )
        self.canvas.grid(row=0, column=0, pady=2)

        # Temps + état
        self.lbl_time = ctk.CTkLabel(
            self, text="25:00",
            font=("SF Pro Display", 36, "bold"),
            text_color=COLORS["text"]
        )
        self.lbl_state = ctk.CTkLabel(
            self, text="IDLE",
            font=("SF Pro Text", 11),
            text_color=COLORS["text_secondary"]
        )
        self.lbl_time.grid(row=2, column=0, pady=(0, 4))
        self.lbl_state.grid(row=3, column=0)

        # Boutons
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=4, column=0, padx=12, pady=10, sticky="ew")
        for i in range(3):
            controls.grid_columnconfigure(i, weight=1, uniform="btns")

        btn_h = 36
        self.btn_start = ctk.CTkButton(controls, text="Start", height=btn_h, corner_radius=12, command=self.start)
        self.btn_pause = ctk.CTkButton(controls, text="Pause", height=btn_h, corner_radius=12,
                                       command=self.pause, state="disabled")
        self.btn_stop = ctk.CTkButton(
            controls, text="Stop", height=btn_h, corner_radius=12, command=self.stop,
            fg_color=COLORS["bg_card_hover"], text_color=COLORS["text"]
        )
        self.btn_start.grid(row=0, column=0, padx=4, sticky="ew")
        self.btn_pause.grid(row=0, column=1, padx=4, sticky="ew")
        self.btn_stop.grid(row=0, column=2, padx=4, sticky="ew")

    # --------------- Actions UI ---------------
    def start(self) -> None:
        if self.state in ("IDLE", "BREAK_SHORT", "BREAK_LONG"):
            self._begin("WORK", self.work_min * 60)
        elif self.state == "PAUSED":
            self._resume()

    def pause(self) -> None:
        with self._lock:
            if self.state in ("WORK", "BREAK_SHORT", "BREAK_LONG") and self._running:
                self._running = False
                self._set_state("PAUSED")
                self._update_controls()

    def stop(self) -> None:
        """
        Stoppe la phase en cours et, si c'était une session WORK (même en pause),
        journalise les minutes déjà effectuées.
        """
        with self._lock:
            # Capture des infos avant reset
            st = self.state
            last_state = getattr(self, "_last_active_state", st)
            total = float(self._current_total)
            rem = float(self.remaining)
            self._running = False

        # Si on était (ou on avait été) en WORK, comptabiliser minutes écoulées
        was_work = (st == "WORK") or (st == "PAUSED" and last_state == "WORK")
        if was_work and total > 0.0:
            elapsed_min = int((total - rem) / 60)  # floor en minutes complètes
            if elapsed_min > 0:
                _append_focus_minutes(elapsed_min)
                try:
                    self.event_generate("<<FocusLogged>>", when="tail")
                except Exception:
                    pass
                self.lbl_sub.configure(text=f"Arrêt — {elapsed_min} min enregistrées",
                                       text_color=COLORS["text_secondary"])

        # Reset UI
        self._set_state("IDLE")
        self._update_controls()
        self._set_time(self.work_min * 60)
        self._draw_ring(0.0)

    # --------------- Mécanique interne ---------------
    def _resume(self) -> None:
        self._set_state(getattr(self, "_last_active_state", "WORK"))
        self._start_ticker()

    def _begin(self, phase: State, seconds: int) -> None:
        self.remaining = float(seconds)
        self._current_total = float(seconds)
        self._set_time(seconds)
        self._set_state(phase)
        if phase == "WORK" and self.launch_spotify:
            self.open_spotify()
        self._start_ticker()

    def _start_ticker(self) -> None:
        with self._lock:
            self._running = True
            self._last_active_state = self.state
        self._update_controls()
        if self._ticker and self._ticker.is_alive():
            return
        self._ticker = threading.Thread(target=self._run_loop, daemon=True)
        self._ticker.start()

    def _run_loop(self) -> None:
        last = time.perf_counter()
        total = self.remaining
        while True:
            with self._lock:
                if not self._running:
                    break
                if self.remaining <= 0:
                    cur_state = self.state
                    self._running = False
            if not self._running and self.remaining <= 0:
                self.after(0, lambda s=cur_state: self._phase_completed(s))
                break

            now = time.perf_counter()
            dt = now - last
            last = now
            with self._lock:
                self.remaining = max(0.0, self.remaining - dt)

            self.after(0, self._tick_ui, total)
            time.sleep(0.05)

    def _tick_ui(self, total: float) -> None:
        self._set_time(self.remaining)
        progress = 1.0 - (self.remaining / total if total else 1.0)
        self._draw_ring(progress)

    def _phase_completed(self, finished: State) -> None:
        if finished == "WORK":
            # Log + signalement dashboard (session complète)
            _append_focus_minutes(int(round(self._current_total / 60)))
            try:
                self.event_generate("<<FocusLogged>>", when="tail")
            except Exception:
                pass

            self.session_index += 1
            self._nc.notify(
                title="Session terminée",
                message="Bravo ! Prenez une pause bien méritée.",
                level="success",
                category="pomodoro",
                actions=[NotificationAction("Relancer",
                                            callback=lambda: self._begin("WORK", self.work_min * 60))]
            )
            CenterNotice.show(self.winfo_toplevel(), "Session terminée",
                              "Bravo ! Faites une pause avant la prochaine session.")

            # Enchaînement pause courte/longue
            if self.session_index % self.before_long == 0:
                self._begin("BREAK_LONG", self.long_min * 60)
            else:
                self._begin("BREAK_SHORT", self.short_min * 60)
        else:
            self._nc.notify(
                title="Pause terminée",
                message="C’est reparti 👊",
                level="info",
                category="pomodoro",
                actions=[NotificationAction("Démarrer",
                                            callback=lambda: self._begin("WORK", self.work_min * 60))]
            )
            CenterNotice.show(self.winfo_toplevel(), "Pause terminée", "On y retourne ?")
            self._begin("WORK", self.work_min * 60)

        # Callback externe éventuel
        if self._on_session_end:
            try:
                self._on_session_end(finished)
            except Exception:
                pass

    # --------------- Helpers Spotify ---------------
    def _to_spotify_uri(self, url_or_uri: str) -> str | None:
        """open.spotify.com/playlist/... → spotify:playlist:... ; retourne l'URI si déjà au bon format."""
        if not url_or_uri:
            return None
        s = url_or_uri.strip()
        if s.startswith("spotify:playlist:"):
            return s
        m = re.search(r"open\.spotify\.com/playlist/([a-zA-Z0-9]+)", s)
        if m:
            return f"spotify:playlist:{m.group(1)}"
        return None

    def _open_uri_cross_platform(self, uri: str) -> bool:
        """Ouvre l'URI via le handler du système (Spotify Desktop si installé)."""
        try:
            if sys.platform.startswith("win"):
                os.startfile(uri)  # type: ignore[attr-defined]
                return True
            if sys.platform == "darwin":
                subprocess.Popen(["open", uri])
                return True
            subprocess.Popen(["xdg-open", uri])
            return True
        except Exception:
            return False

    def open_spotify(self) -> None:
        if not self.spotify_url:
            return
        uri = self._to_spotify_uri(self.spotify_url)
        if uri and self._open_uri_cross_platform(uri):
            return
        # Fallback navigateur
        try:
            webbrowser.open(self.spotify_url)
        except Exception:
            pass

    # --------------- Helpers UI ---------------
    def _set_state(self, state: State) -> None:
        self.state = state
        map_sub = {
            "IDLE": "Pomodoro prêt",
            "WORK": f"Travail — session #{self.session_index + 1}",
            "BREAK_SHORT": "Pause courte",
            "BREAK_LONG": "Pause longue",
            "PAUSED": "En pause",
        }
        self.lbl_state.configure(text=state, text_color=COLORS["text_secondary"])
        self.lbl_sub.configure(text=map_sub.get(state, ""), text_color=COLORS["text_secondary"])
        self._update_controls()

    def _set_time(self, seconds: float) -> None:
        s = int(round(seconds))
        mm, ss = divmod(s, 60)
        self.lbl_time.configure(text=f"{mm:02d}:{ss:02d}", text_color=COLORS["text"])

    def _update_controls(self) -> None:
        self.btn_stop.configure(fg_color=COLORS["bg_card_hover"], text_color=COLORS["text"])
        st = self.state
        if st == "IDLE":
            self.btn_start.configure(state="normal", text="Start")
            self.btn_pause.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        elif st in ("WORK", "BREAK_SHORT", "BREAK_LONG"):
            self.btn_start.configure(state="disabled", text="Start")
            self.btn_pause.configure(state="normal", text="Pause")
            self.btn_stop.configure(state="normal")
        elif st == "PAUSED":
            self.btn_start.configure(state="normal", text="Resume")
            self.btn_pause.configure(state="disabled")
            self.btn_stop.configure(state="normal")

    # ---- Dégradé vert → jaune → orange → rouge
    def _blend(self, c1: str, c2: str, t: float) -> str:
        t = max(0.0, min(1.0, t))
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return f"#{r:02X}{g:02X}{b:02X}"

    def _color_for(self, progress: float) -> str:
        p = max(0.0, min(1.0, progress))
        if p <= 0.5:
            return self._blend("#16A34A", "#F59E0B", p / 0.5)         # vert -> jaune
        if p <= 0.8:
            return self._blend("#F59E0B", "#F97316", (p - 0.5) / 0.3) # jaune -> orange
        return self._blend("#F97316", "#DC2626", (p - 0.8) / 0.2)     # orange -> rouge

    def _draw_ring(self, progress: float) -> None:
        self.canvas.delete("all")
        size = self._ring_size
        pad = self._ring_pad
        x0, y0 = pad, pad
        x1, y1 = size - pad, size - pad

        base = COLORS.get("bg_card_hover", "#E8EAED")
        self.canvas.configure(bg=COLORS["bg_card"])
        self.canvas.create_oval(x0, y0, x1, y1, outline=base, width=self._ring_width)

        angle = max(0.0, min(1.0, progress)) * 360.0
        color = self._color_for(progress)
        self.canvas.create_arc(
            x0, y0, x1, y1, start=-90, extent=angle, style="arc",
            width=self._ring_width, outline=color
        )

    # --------- (Optionnel) ré-appliquer la palette si le thème change ---------
    def apply_colors(self) -> None:
        self.configure(fg_color=COLORS["bg_card"])
        self.lbl_sub.configure(text_color=COLORS["text_secondary"])
        self.lbl_state.configure(text_color=COLORS["text_secondary"])
        self.lbl_time.configure(text_color=COLORS["text"])
        try:
            self.btn_stop.configure(fg_color=COLORS["bg_card_hover"], text_color=COLORS["text"])
        except Exception:
            pass
        self._draw_ring(0.0)
