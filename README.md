# 🔓 Pick the Lock Bot — Auto-Player for Dota 2 "Pick the Lock" (Dark Carnival Minigame)

**A free, open-source computer-vision bot that automatically plays the Dota 2 _Pick the Lock_ mini-game** from the **Dark Carnival** event. It tracks the rotating lock-pick around the dial and **left-clicks the instant it crosses a highlighted bar** — scoring points hands-free — while holding **right-click** to keep the pick at max speed.

<p align="center">
  <a href="https://github.com/codedpro/pick-the-lock-bot/releases/latest">
    <img alt="Download the latest release" src="https://img.shields.io/github/v/release/codedpro/pick-the-lock-bot?label=Download%20.exe&style=for-the-badge">
  </a>
  <img alt="Platform: Windows" src="https://img.shields.io/badge/platform-Windows-blue?style=for-the-badge">
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-yellow?style=for-the-badge">
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge"></a>
</p>

> **Keywords:** Dota 2 Pick the Lock bot · Dark Carnival minigame auto-player · rotating lock-pick auto-clicker · Dota 2 event script · OpenCV screen-capture game bot · Python Windows game automation · Slark emoji minigame.

> ⭐ **If it helps you rack up points, please [star the repo](https://github.com/codedpro/pick-the-lock-bot) — it helps others find it!**

> 🥾 Also check out its sibling: **[Boot Breaker Bot](https://github.com/codedpro/boot-breaker-bot)** for the other Dark Carnival minigame.

---

## 🎯 The game (how Pick the Lock works)

A lock-pick **rotates around a circular dial**. You **left-click** the moment it passes over a highlighted **bar**:

- **Yellow bar** → **+1,000 points**
- **Blue bar** → **+1.5 seconds** on the timer
- The pick **reverses direction** after each bonus hit.
- **Holding right-click speeds the pick up.**
- **Clicking off a bar** slows the pick and briefly disables picking — so **timing is everything**.

Point goals: **6,000** to advance, **12k / 18k** for tickets, **24,000** for the Slark emoji.

This bot does the timing for you.

---

## ✨ Features

- 🎯 **Angle tracking** — locks onto the spinning pick and measures its angle + speed every frame.
- ⚡ **Lead-predicted clicks** — fires the left-click a hair early to beat capture + click latency, so it lands *on* the bar.
- 🟡🔵 **Bar detection** — finds the yellow and blue bars by colour around the ring.
- 🚀 **Right-click speed boost** — holds RMB for max spin (configurable: `always` / `smart` / `off`).
- 👁️ **Live preview** — shows the dial, detected bars, the pick, and the predicted click point.
- 🔧 **One-time calibration** — click the dial's centre and edge once; saved for next time.
- 🖱️ **Global hotkeys** — start/stop with **F8**.

---

## 🚀 Quick Start — Two Easy Ways

### 🟢 Option 1 — The easy way (no Python, no setup)

1. **[⬇️ Download `pick-the-lock-bot.exe` from the latest release](https://github.com/codedpro/pick-the-lock-bot/releases/latest).**
2. Double-click it. _(Windows SmartScreen may warn about an unrecognized app — click **More info → Run anyway**. The full source is in this repo.)_
3. Follow the on-screen **calibration** (below), then press **F8** in Dota to let it play.

### 🔵 Option 2 — Run from source (auto-installs everything)

Don't have Python? The launcher installs it for you.

1. Click the green **`< > Code`** button above → **Download ZIP**, and unzip anywhere.
2. Double-click **`run.bat`** — the first launch auto-installs Python (if missing) and all packages into a local folder.
3. Follow **calibration**, then press **F8**.

> 💡 Nothing is installed system-wide — everything lives in a local `.venv` folder next to the bot.

---

## 🎮 Before You Start (Dota settings)

1. Dota console: `fps_max_ui 30` — the bars are **FPS-timed**; lower FPS makes them catchable (and the bot far more accurate).
2. Run Dota in **Borderless / Windowed-Fullscreen**.
3. Open the **Pick the Lock** mini-game so the dial is visible.

---

## 🛠️ Calibration (one time, ~15 seconds)

Run **`calibrate.bat`** (or press **F7** in the bot). With the dial on screen:

| Step | Do this |
|------|---------|
| 1 | Mouse to the **top-left** of the dial area → **F1** |
| 2 | Mouse to the **bottom-right** of the dial area → **F2** |
| 3 | Mouse to the **centre** of the dial → **F3** |
| 4 | Mouse onto the **ring** where the pick travels (any point on the circle) → **F4** |

Saved to `config.json`. Then run **`run.bat`**, put your cursor over the dial, focus Dota, and press **F8**.

**Preview markers:** 🟢 green = the pick (with a line to centre) · 🟡 yellow dots = detected bars · 🟣 magenta cross = predicted click point.

---

## ⌨️ Hotkeys (work anywhere)

| Key | Action |
|-----|--------|
| **F8** | Toggle bot **ON / OFF** (starts OFF) |
| **F10** | Show / hide the preview |
| **F7** | Re-run calibration |
| **F9 / Esc** | Quit |

---

## ⚙️ Tuning (`config.json`)

| Field | Meaning |
|-------|---------|
| `boost_mode` | `"always"` (max speed), `"smart"` (release before a bar), or `"off"` |
| `yellow_color` / `blue_color` | Bar colours in **BGR**; adjust if bars aren't detected (see 🩺 below) |
| `color_tol` | Colour match tolerance (raise if bars flicker in/out) |
| `latency_frames` | How far ahead the click is predicted — raise if it clicks **late**, lower if **early** |
| `click_margin_deg` | Click window width in degrees around a bar |
| `click_cooldown` | Min seconds between clicks (raise if it double-clicks one pass) |
| `radius` / `ring_width` | Ring geometry; widen `ring_width` if the pick isn't tracked reliably |
| `smart_release_deg` | (smart mode) how far ahead of a bar to ease off the boost |

---

## 🩺 Troubleshooting

| Problem | Fix |
|---------|-----|
| **Nothing clicks / `bars=0` in preview** | The bar colours don't match. Run **`capture.bat`**, then tweak `yellow_color` / `blue_color` (BGR) or raise `color_tol`. |
| **Clicks land just before/after the bar** | Adjust `latency_frames` (raise = later, lower = earlier). |
| **The green pick marker jitters / is lost** | Increase `ring_width`, or lower `diff_thresh`. Make sure `fps_max_ui 30` is set. |
| **It double-clicks one pass** | Raise `click_cooldown`. |
| **Too many misses at max speed** | Set `boost_mode` to `"smart"` or `"off"`. |
| **Still stuck?** | Run **`capture.bat`** and open an issue with the image. |

---

## 🧠 How It Works

The bot screen-captures the calibrated dial, then each frame: (1) frame-differences inside the ring to find the moving **pick** and compute its **angle + angular velocity**; (2) samples colours around the ring to locate the **yellow/blue bars**; (3) predicts the pick's angle a couple of frames ahead and **left-clicks** when that prediction lands on a bar; (4) holds **right-click** for the speed boost. Pure OpenCV + NumPy — no game memory reading or injection; it only looks at the screen and clicks, like a human.

---

## 🧩 Requirements (source install)

Handled automatically by `run.bat`. For reference: **Windows**, **Python 3.12**, and the packages in [`requirements.txt`](requirements.txt) (`numpy`, `opencv-python`, `mss`, `pywin32`, `keyboard`).

---

## ⚠️ Disclaimer

A **hobby / educational computer-vision project** for a single-player mini-game. Use at your own risk and in accordance with the Dota 2 / Steam Terms of Service. Not affiliated with or endorsed by Valve.

## 📄 License

Released under the [MIT License](LICENSE). Contributions welcome — open an issue or PR!
