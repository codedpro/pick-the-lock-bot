"""
Pick the Lock Bot — plays the Dota 2 "Dark Carnival" Pick the Lock mini-game.

The game: a lock-pick rotates around a circular dial. You LEFT-CLICK the moment the
pick passes over a highlighted bar (yellow = +points, blue = +time). Holding the
RIGHT mouse button speeds the pick up. Clicking off a bar penalises you (the pick
slows and picking is briefly disabled), so timing must be precise.

This bot:
  1. Screen-captures the calibrated dial (centre + radius set once).
  2. Tracks the rotating pick's ANGLE by frame-differencing inside the ring.
  3. Finds the yellow/blue bars by sampling colours around the ring.
  4. Predicts the pick's angle a couple of frames ahead (to beat capture + click
     latency) and LEFT-CLICKS when that lands on a bar.
  5. Optionally HOLDS the RIGHT button to keep the pick at max speed.

Hotkeys (global)
    F8  — toggle bot ON / OFF (starts OFF)
    F7  — re-run calibration
    F10 — show/hide debug preview
    F9 / Esc — quit

Recommended Dota console:  fps_max_ui 30   (the bars are FPS-timed; lower = catchable)
Run Dota in Borderless / Windowed-Fullscreen.
"""

import os
import sys
import json
import time
import math
import ctypes
from collections import deque

import numpy as np
import cv2
import mss
import win32api
import keyboard

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")

# DPI-aware so calibration mouse coords and captured pixels share one coordinate space.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

PREVIEW_WIN = "Pick the Lock Bot — preview"

# ----------------------------- config ---------------------------------------

DEFAULTS = {
    "region": None,            # {left, top, width, height} absolute screen px
    "center": None,            # [cx, cy] region-local centre of the dial
    "radius": None,            # px — radius of the ring the pick travels on
    "ring_width": 16,          # px thickness of the annulus sampled for pick & bars
    "n_samples": 360,          # angular resolution (samples around the ring)
    "diff_thresh": 16,         # frame-diff threshold for finding the moving pick
    "min_move_area": 4,        # min changed-blob area (px) to accept a pick reading
    # Bar colours (BGR). These are starting guesses — tune with capture.bat.
    "yellow_color": [40, 205, 240],
    "blue_color": [235, 175, 45],
    "color_tol": 60,           # per-channel colour tolerance for a bar match
    "min_bar_samples": 3,      # need this many matching angles to count a bar
    "click_margin_deg": 7,     # treat a bar as this many degrees wider (click window)
    "latency_frames": 1.6,     # predict the pick this many frames ahead before clicking
    "click_cooldown": 0.10,    # s — min time between left clicks
    "vel_smooth": 0.5,         # angular-velocity EMA (0..1, lower = smoother)
    "coast_frames": 4,         # keep predicting this many frames after losing the pick
    "boost_mode": "always",    # "always" | "smart" | "off"
    "smart_release_deg": 45,   # (smart) release RMB when a bar is within this many deg ahead
    "click_hold": 0.008,       # s — left button down duration
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"[warn] could not read config.json ({e}); using defaults")
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"[ok] saved {CONFIG_PATH}")


# --------------------------- mouse output -----------------------------------
# Low-level SendInput mouse events (games that ignore synthetic clicks are rare;
# SendInput is what most trainers use and it works with Dota's Panorama UI).

_SendInput = ctypes.windll.user32.SendInput
_PUL = ctypes.POINTER(ctypes.c_ulong)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", _PUL)]


class _INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", ctypes.c_ulong), ("i", _I)]


_INPUT_MOUSE = 0
_LEFTDOWN = 0x0002
_LEFTUP = 0x0004
_RIGHTDOWN = 0x0008
_RIGHTUP = 0x0010


def _mouse_event(flags):
    extra = ctypes.c_ulong(0)
    mi = _MOUSEINPUT(0, 0, 0, flags, 0, ctypes.pointer(extra))
    inp = _INPUT(_INPUT_MOUSE)
    inp.mi = mi
    _SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))


class MouseController:
    def __init__(self):
        self.right_down = False

    def left_click(self, hold=0.008):
        _mouse_event(_LEFTDOWN)
        time.sleep(hold)
        _mouse_event(_LEFTUP)

    def right_hold(self, want):
        if want and not self.right_down:
            _mouse_event(_RIGHTDOWN)
            self.right_down = True
        elif not want and self.right_down:
            _mouse_event(_RIGHTUP)
            self.right_down = False

    def release_all(self):
        self.right_hold(False)


# --------------------------- angle helpers ----------------------------------

def ang_norm(a):
    return a % 360.0


def ang_diff(a, b):
    """Shortest signed difference a-b in (-180, 180]."""
    return (a - b + 180.0) % 360.0 - 180.0


# --------------------------- detection --------------------------------------

class Dial:
    """Precomputed ring sampling + moving-pick tracker for one calibrated dial."""

    def __init__(self, cfg, W, H):
        self.cfg = cfg
        self.W, self.H = W, H
        cx, cy = cfg["center"]
        r = cfg["radius"]
        rw = cfg["ring_width"]
        n = cfg["n_samples"]
        self.cx, self.cy, self.r, self.n = cx, cy, r, n

        # Precompute (radius x angle) sample coordinates for the annulus.
        radii = np.arange(r - rw // 2, r + rw // 2 + 1)
        ang = np.deg2rad(np.arange(n) * (360.0 / n))
        cos, sin = np.cos(ang), np.sin(ang)
        xs = (cx + np.outer(radii, cos)).round().astype(np.int32)
        ys = (cy + np.outer(radii, sin)).round().astype(np.int32)
        np.clip(xs, 0, W - 1, out=xs)
        np.clip(ys, 0, H - 1, out=ys)
        self._xs, self._ys = xs, ys

        # Boolean annulus mask (region-sized) for restricting the pick frame-diff.
        Y, X = np.ogrid[0:H, 0:W]
        dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
        self.ring_mask = ((dist >= r - rw) & (dist <= r + rw)).astype(np.uint8) * 255

        self.prev_gray = None
        self.angle = None          # last pick angle (deg)
        self.vel = 0.0             # deg/frame (EMA, signed)
        self.lost = 0

    def sample_ring(self, bgr):
        """Mean BGR colour at each of n angles around the ring -> (n, 3) float."""
        ring = bgr[self._ys, self._xs, :3].astype(np.float32)  # (radii, n, 3)
        return ring.mean(axis=0)

    def bar_mask(self, ring):
        """Boolean (n,) — angles whose ring colour matches the yellow or blue bar."""
        cfg = self.cfg
        tol = cfg["color_tol"]
        y = np.array(cfg["yellow_color"], np.float32)
        b = np.array(cfg["blue_color"], np.float32)
        my = np.all(np.abs(ring - y) <= tol, axis=1)
        mb = np.all(np.abs(ring - b) <= tol, axis=1)
        return my | mb

    def update_pick(self, gray):
        """Track the rotating pick's angle via frame-difference in the ring."""
        if self.prev_gray is None:
            self.prev_gray = gray
            return None
        diff = cv2.absdiff(gray, self.prev_gray)
        self.prev_gray = gray
        _, mask = cv2.threshold(diff, self.cfg["diff_thresh"], 255, cv2.THRESH_BINARY)
        mask = cv2.bitwise_and(mask, self.ring_mask)
        n, _, stats, cent = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n <= 1:
            return self._coast()
        # largest moving blob = the pick
        i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        if stats[i, cv2.CC_STAT_AREA] < self.cfg["min_move_area"]:
            return self._coast()
        mx, my = cent[i]
        a = ang_norm(math.degrees(math.atan2(my - self.cy, mx - self.cx)))
        if self.angle is not None:
            d = ang_diff(a, self.angle)
            k = self.cfg["vel_smooth"]
            self.vel = k * d + (1 - k) * self.vel
        self.angle = a
        self.lost = 0
        return a

    def _coast(self):
        self.lost += 1
        if self.angle is not None and self.lost <= self.cfg["coast_frames"]:
            self.angle = ang_norm(self.angle + self.vel)
            return self.angle
        return None

    def predicted_index(self):
        """Index into the ring where the pick will be after latency_frames."""
        if self.angle is None:
            return None
        pred = ang_norm(self.angle + self.vel * self.cfg["latency_frames"])
        return int(round(pred * self.n / 360.0)) % self.n


def dilate_mask(mask, margin_idx):
    """Widen a boolean ring mask by +/- margin_idx samples (wraps around)."""
    if margin_idx <= 0:
        return mask
    out = mask.copy()
    for s in range(1, margin_idx + 1):
        out |= np.roll(mask, s) | np.roll(mask, -s)
    return out


# --------------------------- calibration ------------------------------------

def _wait_key(key):
    while not keyboard.is_pressed(key):
        time.sleep(0.01)
    while keyboard.is_pressed(key):
        time.sleep(0.01)


def calibrate(cfg):
    print("\n=== CALIBRATION ===")
    print("Open Pick the Lock so the round dial is fully visible.")
    print(" 1) Mouse to the TOP-LEFT corner of the dial area, press F1")
    _wait_key("f1")
    x1, y1 = win32api.GetCursorPos()
    print(f"    ({x1},{y1})")
    print(" 2) Mouse to the BOTTOM-RIGHT corner of the dial area, press F2")
    _wait_key("f2")
    x2, y2 = win32api.GetCursorPos()
    print(f"    ({x2},{y2})")

    left, top = min(x1, x2), min(y1, y2)
    width, height = abs(x2 - x1), abs(y2 - y1)
    if width < 40 or height < 40:
        print("[error] region too small — retry.")
        return calibrate(cfg)
    cfg["region"] = {"left": left, "top": top, "width": width, "height": height}

    print(" 3) Mouse to the CENTRE of the dial, press F3")
    _wait_key("f3")
    cx, cy = win32api.GetCursorPos()
    cfg["center"] = [int(cx - left), int(cy - top)]

    print(" 4) Mouse onto the RING where the pick travels (any point on the circle),")
    print("    press F4")
    _wait_key("f4")
    rx, ry = win32api.GetCursorPos()
    r = int(round(math.hypot(rx - cx, ry - cy)))
    cfg["radius"] = r

    print(f"    centre={cfg['center']}  radius={r}px")
    save_config(cfg)
    print(f"[ok] region {width}x{height} @ ({left},{top})")
    return cfg


# ----------------------------- main -----------------------------------------

class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.active = False
        self.preview = True
        self.quit = False
        self.win_ready = False
        self.mouse = MouseController()

    def _bind(self):
        keyboard.add_hotkey("f8", self._toggle)
        keyboard.add_hotkey("f10", self._toggle_preview)
        keyboard.add_hotkey("f9", self._stop)
        keyboard.add_hotkey("esc", self._stop)

    def _toggle(self):
        self.active = not self.active
        if not self.active:
            self.mouse.release_all()
        print(f"[bot] {'ACTIVE' if self.active else 'paused'}")

    def _toggle_preview(self):
        self.preview = not self.preview
        if not self.preview:
            cv2.destroyAllWindows()
            self.win_ready = False

    def _stop(self):
        self.quit = True

    def run(self):
        cfg = self.cfg
        r = cfg["region"]
        W, H = r["width"], r["height"]
        monitor = {"top": r["top"], "left": r["left"], "width": W, "height": H}
        dial = Dial(cfg, W, H)
        margin_idx = int(round(cfg["click_margin_deg"] * cfg["n_samples"] / 360.0))
        smart_idx = int(round(cfg["smart_release_deg"] * cfg["n_samples"] / 360.0))

        self._bind()
        print("\n=== RUNNING ===  (starts PAUSED — focus Dota, press F8)")
        print("  F8 toggle | F10 preview | F7 recal | F9/Esc quit\n")

        last = time.perf_counter()
        fps = 0.0
        last_click = 0.0
        bars_seen = 0
        try:
            with mss.mss() as sct:
                while not self.quit:
                    shot = np.asarray(sct.grab(monitor))
                    gray = cv2.cvtColor(shot, cv2.COLOR_BGRA2GRAY)
                    bgr = shot[:, :, :3]

                    dial.update_pick(gray)
                    ring = dial.sample_ring(bgr)
                    bars = dial.bar_mask(ring)
                    bars_seen = int(bars.sum())
                    bars_wide = dilate_mask(bars, margin_idx)

                    pidx = dial.predicted_index()
                    reliable = dial.angle is not None and dial.lost <= 2

                    now = time.perf_counter()
                    clicked = False
                    boosting = False
                    if self.active and reliable and pidx is not None:
                        on_bar = bool(bars_wide[pidx])
                        # --- left click on a bar ---
                        if on_bar and bars_seen >= cfg["min_bar_samples"] \
                                and now - last_click >= cfg["click_cooldown"]:
                            self.mouse.left_click(cfg["click_hold"])
                            last_click = now
                            clicked = True
                        # --- right-button speed boost ---
                        mode = cfg.get("boost_mode", "always")
                        if mode == "off":
                            boosting = False
                        elif mode == "always":
                            boosting = True
                        else:  # smart: boost unless a bar is just ahead
                            ahead = dilate_mask(bars, smart_idx)
                            boosting = not bool(ahead[pidx])
                        self.mouse.right_hold(boosting)
                    else:
                        self.mouse.right_hold(False)

                    dt = now - last
                    last = now
                    if dt > 0:
                        fps = 0.9 * fps + 0.1 * (1.0 / dt)

                    if self.preview:
                        self._draw(bgr, dial, bars, pidx, clicked, boosting,
                                   bars_seen, fps)
                        if cv2.waitKey(1) & 0xFF == 27:
                            break
        finally:
            self.mouse.release_all()
            cv2.destroyAllWindows()
        print("[bot] stopped.")

    def _draw(self, bgr, dial, bars, pidx, clicked, boosting, bars_seen, fps):
        img = bgr.copy()
        cx, cy, r, n = dial.cx, dial.cy, dial.r, dial.n
        cv2.circle(img, (int(cx), int(cy)), int(r), (80, 80, 80), 1)
        cv2.circle(img, (int(cx), int(cy)), 2, (80, 80, 80), -1)
        # highlight detected bars
        for i in np.where(bars)[0]:
            a = math.radians(i * 360.0 / n)
            x = int(cx + r * math.cos(a)); y = int(cy + r * math.sin(a))
            cv2.circle(img, (x, y), 2, (0, 255, 255), -1)
        # pick position
        if dial.angle is not None:
            a = math.radians(dial.angle)
            x = int(cx + r * math.cos(a)); y = int(cy + r * math.sin(a))
            cv2.circle(img, (x, y), 6, (0, 255, 0), 2)
            cv2.line(img, (int(cx), int(cy)), (x, y), (0, 255, 0), 1)
        # predicted click point
        if pidx is not None:
            a = math.radians(pidx * 360.0 / n)
            x = int(cx + r * math.cos(a)); y = int(cy + r * math.sin(a))
            cv2.drawMarker(img, (x, y), (255, 0, 255), cv2.MARKER_TILTED_CROSS, 12, 2)
        state = "ACTIVE" if self.active else "paused"
        col = (0, 255, 0) if self.active else (180, 180, 180)
        tags = ("CLICK " if clicked else "") + ("BOOST" if boosting else "")
        cv2.putText(img, f"{state} {fps:4.0f}fps bars={bars_seen} {tags} [F8|F10]",
                    (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        if not self.win_ready:
            cv2.namedWindow(PREVIEW_WIN, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(PREVIEW_WIN, max(360, dial.W), max(360, dial.H))
            cv2.moveWindow(PREVIEW_WIN, 0, 0)
            try:
                cv2.setWindowProperty(PREVIEW_WIN, cv2.WND_PROP_TOPMOST, 1)
            except Exception:
                pass
            self.win_ready = True
        cv2.imshow(PREVIEW_WIN, img)


def main():
    cfg = load_config()
    if "--calibrate" in sys.argv or cfg.get("region") is None \
            or cfg.get("center") is None or cfg.get("radius") is None:
        cfg = calibrate(cfg)
    while True:
        bot = Bot(cfg)
        recal = {"v": False}
        keyboard.add_hotkey("f7", lambda: (recal.__setitem__("v", True), bot._stop()))
        bot.run()
        keyboard.unhook_all_hotkeys()
        if recal["v"]:
            cfg = calibrate(cfg)
            continue
        break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
