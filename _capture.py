"""
Capture helper for Pick the Lock Bot.

Run this with the lock dial on screen and press F2 whenever a highlighted bar
(yellow or blue) is visible. It saves screenshots (the calibrated dial region AND
the full monitor) so detection colours/geometry can be tuned.

    F2  -> save the current dial    (capture_dial_*.png)
    Esc -> quit
"""
import os
import json
import time
import ctypes

import numpy as np
import cv2
import mss
import keyboard

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "config.json")


def main():
    region = None
    if os.path.exists(CFG):
        try:
            with open(CFG, "r", encoding="utf-8") as f:
                r = json.load(f).get("region")
            if r:
                region = {"top": r["top"], "left": r["left"],
                          "width": r["width"], "height": r["height"]}
        except Exception:
            pass

    print(__doc__)
    if region is None:
        print("(No calibrated region yet — will save the full monitor only.)")
    print("Ready. Press F2 to capture, Esc to quit.\n")

    saved = 0
    with mss.mss() as sct:
        full = sct.monitors[1]
        while True:
            if keyboard.is_pressed("esc"):
                break
            if keyboard.is_pressed("f2"):
                saved += 1
                fp = os.path.join(HERE, f"capture_dial_full_{saved}.png")
                cv2.imwrite(fp, np.asarray(sct.grab(full))[:, :, :3])
                print(f"[saved] {os.path.basename(fp)}")
                if region is not None:
                    rp = os.path.join(HERE, f"capture_dial_region_{saved}.png")
                    cv2.imwrite(rp, np.asarray(sct.grab(region))[:, :, :3])
                    print(f"[saved] {os.path.basename(rp)}")
                while keyboard.is_pressed("f2"):
                    time.sleep(0.02)
            time.sleep(0.01)

    print("\nDone. Tell Claude — it will read the capture_dial_*.png files.")


if __name__ == "__main__":
    main()
