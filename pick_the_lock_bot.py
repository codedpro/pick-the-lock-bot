"""
Pick the Lock Bot — plays the Dota 2 "Dark Carnival" Pick the Lock mini-game.

The game: a lock-pick rotates around a circular dial. You LEFT-CLICK the moment the
pick passes over a highlighted bar (gold = +points, blue = +time). The bars START
WIDE and SHRINK until they vanish, so you must hit them quickly. Holding the RIGHT
mouse button speeds the pick up. Clicking off a bar penalises you (the pick slows
and picking is briefly disabled), so timing must be precise.

Detection (colour-agnostic, so it works for gold, blue, or a recoloured pick):
  * PICK  — the fast-moving element, found by frame-differencing inside the ring;
            we track its ANGLE and angular velocity.
  * BARS  — saturated pixels on the dial face that PERSIST at a fixed angle across
            several frames. The pick moves so it never persists in one spot; the
            flying spark particles move too — only the real (shrinking) bars stay
            put, so this cleanly separates bars from pick and sparks.
Then we predict the pick's angle a couple of frames ahead and LEFT-CLICK when that
lands on a bar. Optionally HOLD RIGHT to keep the pick at max speed.

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

import numpy as np
import cv2
import mss
import win32api
import keyboard

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")

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
    "radius": None,            # px — radius of the ring the bars sit on
    "n_samples": 360,          # angular resolution (bins around the circle)
    "diff_thresh": 15,         # frame-diff threshold for the moving pick
    "min_move_area": 4,        # min moving-blob area (px) to accept a pick reading
    # Bars are found by SATURATION + PERSISTENCE, not by a fixed colour.
    "sat_min": 30,             # min (max-min) channel spread to be a "coloured" pixel
    "v_min": 65,               # min brightness (brightest channel) for a bar pixel
    "bar_inner_margin": 34,    # bar search annulus = [radius-this, radius+bar_outer_margin]
    "bar_outer_margin": 24,
    "pick_inner_margin": 48,   # pick search annulus around the radius
    "pick_outer_margin": 58,
    "bar_min_px": 5,           # min saturated pixels in an angle bin to light it up
    "bar_persist_frames": 3,   # a bin must persist this many frames to count as a bar
    "min_bar_deg": 3,          # ignore specks narrower than this
    "max_bar_deg": 55,         # ignore arcs WIDER than this (that's the static ring, not a bar)
    "max_total_bar_deg": 170,  # if more of the circle than this lights up, it's the ring -> ignore all
    "click_margin_deg": 8,     # treat a bar as this many degrees wider (click window)
    "latency_frames": 1.6,     # predict the pick this many frames ahead before clicking
    "click_cooldown": 0.10,    # s — min time between left clicks
    "vel_smooth": 0.5,         # angular-velocity EMA (0..1, lower = smoother)
    "coast_frames": 4,         # keep predicting this many frames after losing the pick
    "boost_mode": "always",    # "always" | "smart" | "off"
    "smart_release_deg": 45,   # (smart) ease off RMB when a bar is within this many deg ahead
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
    return (a - b + 180.0) % 360.0 - 180.0


def dilate_mask(mask, margin_idx):
    """Widen a boolean ring mask by +/- margin_idx samples (wraps around)."""
    if margin_idx <= 0:
        return mask
    out = mask.copy()
    for s in range(1, margin_idx + 1):
        out |= np.roll(mask, s) | np.roll(mask, -s)
    return out


# --------------------------- detection --------------------------------------

class Dial:
    """Tracks the moving pick (by motion) and the bars (by saturation + persistence)."""

    def __init__(self, cfg, W, H):
        self.cfg = cfg
        self.W, self.H = W, H
        cx, cy = cfg["center"]
        r = cfg["radius"]
        n = cfg["n_samples"]
        self.cx, self.cy, self.r, self.n = cx, cy, r, n

        Y, X = np.ogrid[0:H, 0:W]
        dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
        ang = (np.degrees(np.arctan2(Y - cy, X - cx)) % 360.0)
        self.ang_idx = np.minimum((ang * n / 360.0).round().astype(np.int32), n - 1)

        self.bar_annulus = ((dist >= r - cfg["bar_inner_margin"]) &
                            (dist <= r + cfg["bar_outer_margin"]))
        self.pick_ring = (((dist >= r - cfg["pick_inner_margin"]) &
                           (dist <= r + cfg["pick_outer_margin"])).astype(np.uint8) * 255)

        self.prev_gray = None
        self.angle = None
        self.vel = 0.0
        self.lost = 0
        self.bar_persist = np.zeros(n, np.int32)
        self.bars = np.zeros(n, bool)
        self.arcs = []

    def update(self, gray, bgr):
        moving = None
        if self.prev_gray is not None:
            diff = cv2.absdiff(gray, self.prev_gray)
            _, mv = cv2.threshold(diff, self.cfg["diff_thresh"], 255, cv2.THRESH_BINARY)
            moving = cv2.dilate(mv, np.ones((3, 3), np.uint8))
            self._track_pick(cv2.bitwise_and(mv, self.pick_ring))
        else:
            self._coast()
        self.prev_gray = gray
        self._update_bars(bgr, moving)

    def _track_pick(self, pick_mask):
        n, _, stats, cent = cv2.connectedComponentsWithStats(pick_mask, connectivity=8)
        if n <= 1:
            return self._coast()
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
        return None

    def _update_bars(self, bgr, moving):
        img = bgr.astype(np.int16)
        maxc = img.max(axis=2)
        minc = img.min(axis=2)
        sat = ((maxc - minc) >= self.cfg["sat_min"]) & (maxc >= self.cfg["v_min"])
        sat &= self.bar_annulus
        if moving is not None:
            sat &= (moving == 0)              # drop the moving pick & flying sparks
        counts = np.bincount(self.ang_idx[sat], minlength=self.n)
        present = counts >= self.cfg["bar_min_px"]
        self.bar_persist = np.where(present,
                                    np.minimum(self.bar_persist + 1, 12), 0)
        self.bars = self._filter_arcs(self.bar_persist >= self.cfg["bar_persist_frames"])

    def _filter_arcs(self, mask):
        """Keep only bar-sized arcs. Rejects the static full ring (a huge arc) and
        1-2px specks, and drops everything if too much of the circle lights up."""
        n = self.n
        self.arcs = []
        s = int(mask.sum())
        if s == 0:
            return mask
        if s > self.cfg["max_total_bar_deg"] * n / 360.0:
            return np.zeros(n, bool)          # basically the whole ring -> not real bars
        idx = np.where(mask)[0]
        runs = []
        start = prev = idx[0]
        for x in idx[1:]:
            if x == prev + 1:
                prev = x
            else:
                runs.append((start, prev)); start = prev = x
        runs.append((start, prev))
        # merge an arc that wraps past 0 degrees
        if len(runs) >= 2 and runs[0][0] == 0 and runs[-1][1] == n - 1:
            s0, e0 = runs.pop(0)
            s1, e1 = runs.pop(-1)
            runs.append((s1, e0 + n))
        out = np.zeros(n, bool)
        lo = self.cfg["min_bar_deg"] * n / 360.0
        hi = self.cfg["max_bar_deg"] * n / 360.0
        for a, b in runs:
            L = b - a + 1
            self.arcs.append((a % n, b % n, L))
            if lo <= L <= hi:
                for k in range(a, b + 1):
                    out[k % n] = True
        return out

    def predicted_index(self):
        if self.angle is None:
            return None
        pred = ang_norm(self.angle + self.vel * self.cfg["latency_frames"])
        return int(round(pred * self.n / 360.0)) % self.n


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

    print(" 4) Mouse onto a BAR / the ring where the bars appear, press F4")
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
        self.diag = False
        self.diag_dir = os.path.join(HERE, "diag")
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
        logf = None
        if self.diag:
            os.makedirs(self.diag_dir, exist_ok=True)
            logf = open(os.path.join(self.diag_dir, "diag_log.txt"), "w", encoding="utf-8")
            t0 = time.perf_counter()
            next_save = 0.0
            frame_no = 0
            print("[diag] Recording ~60s — NO clicks are sent. "
                  "Press F8 to 'activate' and play normally.")
        try:
            with mss.mss() as sct:
                while not self.quit:
                    shot = np.asarray(sct.grab(monitor))
                    gray = cv2.cvtColor(shot, cv2.COLOR_BGRA2GRAY)
                    bgr = shot[:, :, :3]

                    dial.update(gray, bgr)
                    bars = dial.bars
                    bars_seen = int(bars.sum())
                    bars_wide = dilate_mask(bars, margin_idx)
                    pidx = dial.predicted_index()
                    reliable = dial.angle is not None and dial.lost <= 2

                    now = time.perf_counter()
                    clicked = False
                    boosting = False
                    would_click = (reliable and pidx is not None and bars_seen > 0
                                   and bool(bars_wide[pidx]))
                    if self.active:
                        mode = cfg.get("boost_mode", "always")
                        if mode == "off":
                            boosting = False
                        elif mode == "smart" and reliable and pidx is not None:
                            boosting = not bool(dilate_mask(bars, smart_idx)[pidx])
                        else:
                            boosting = True   # "always", or "smart" with no lock yet
                        if would_click and now - last_click >= cfg["click_cooldown"]:
                            last_click = now
                            clicked = True
                        if not self.diag:                     # diag = observe only
                            if clicked:
                                self.mouse.left_click(cfg["click_hold"])
                            self.mouse.right_hold(boosting)
                    elif not self.diag:
                        self.mouse.right_hold(False)

                    dt = now - last
                    last = now
                    if dt > 0:
                        fps = 0.9 * fps + 0.1 * (1.0 / dt)

                    if self.diag:
                        ang = None if dial.angle is None else round(dial.angle, 1)
                        logf.write(f"t={now - t0:6.2f} act={int(self.active)} "
                                   f"fps={fps:4.0f} pick={ang} vel={round(dial.vel, 2)} "
                                   f"lost={dial.lost} pidx={pidx} bars={bars_seen} "
                                   f"arcs={dial.arcs} wclick={int(would_click)}\n")
                        ov = self._draw(bgr, dial, bars, pidx, clicked, boosting,
                                        bars_seen, fps)
                        cv2.waitKey(1)
                        if now - t0 >= next_save and frame_no < 60:
                            cv2.imwrite(os.path.join(self.diag_dir,
                                        f"frame_{frame_no:03d}.png"), ov)
                            frame_no += 1
                            next_save += 1.5
                        if now - t0 >= 60.0:
                            print("[diag] Done. 60s recorded.")
                            break
                    elif self.preview:
                        self._draw(bgr, dial, bars, pidx, clicked, boosting,
                                   bars_seen, fps)
                        if cv2.waitKey(1) & 0xFF == 27:
                            break
        finally:
            self.mouse.release_all()
            cv2.destroyAllWindows()
            if logf is not None:
                logf.close()
                print(f"[diag] logs + frames saved in: {self.diag_dir}")
        print("[bot] stopped.")

    def _draw(self, bgr, dial, bars, pidx, clicked, boosting, bars_seen, fps):
        img = bgr.copy()
        cx, cy, r, n = dial.cx, dial.cy, dial.r, dial.n
        cv2.circle(img, (int(cx), int(cy)), int(r), (70, 70, 70), 1)
        cv2.drawMarker(img, (int(cx), int(cy)), (70, 70, 70), cv2.MARKER_CROSS, 8, 1)
        for i in np.where(bars)[0]:
            a = math.radians(i * 360.0 / n)
            x = int(cx + r * math.cos(a)); y = int(cy + r * math.sin(a))
            cv2.circle(img, (x, y), 3, (0, 215, 255), -1)
        if dial.angle is not None:
            a = math.radians(dial.angle)
            x = int(cx + r * math.cos(a)); y = int(cy + r * math.sin(a))
            cv2.circle(img, (x, y), 6, (0, 255, 0), 2)
            cv2.line(img, (int(cx), int(cy)), (x, y), (0, 255, 0), 1)
        if pidx is not None:
            a = math.radians(pidx * 360.0 / n)
            x = int(cx + r * math.cos(a)); y = int(cy + r * math.sin(a))
            cv2.drawMarker(img, (x, y), (255, 0, 255), cv2.MARKER_TILTED_CROSS, 14, 2)
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
        return img


def main():
    cfg = load_config()
    diag = "--diag" in sys.argv
    if "--calibrate" in sys.argv or cfg.get("region") is None \
            or cfg.get("center") is None or cfg.get("radius") is None:
        cfg = calibrate(cfg)
    while True:
        bot = Bot(cfg)
        bot.diag = diag
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
