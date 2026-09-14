# Table Tennis Match Video Analyzer

*[Русская версия](README.md)*

A local tool that finds rallies in match footage, tracks the score by the rules
and exports a video with the pauses between points removed. Everything runs on
your machine — no uploads.

The problem it grew from: you film a match on a phone from a tripod, then spend
half an hour cutting out the gaps between rallies by hand.

![Detected rallies](docs/screenshot-rallies.png)

*A 13-minute match: 68 rallies, half the footage is pause between points.
Yellow marks spots worth double-checking.*

## Features

- **Finds rallies** and cuts everything else — ball retrieval, serve
  preparation, breaks between games
- **Tracks the score** by table tennis rules, with a scoreboard overlay
- **Builds highlights** from the most spectacular rallies
- **Stays editable**: boundaries adjust with keys, doubtful spots get flagged
- **Splits practice sessions** into separate clips per drill

## Accuracy

Measured on a match the model had never seen (trained on two others):

| | Recall | Precision |
|---|---|---|
| Without pose estimation | 76% | 87% |
| **With pose estimation** | **92%** | **96%** |

Boundaries land within about 0.5 seconds. Analysing an hour of footage takes
roughly 11 minutes on an M3 MacBook.

Caveat: the model is trained on 34 minutes of video from a single venue.
Expect lower quality elsewhere — see Limitations.

## Install

```bash
brew install ffmpeg          # macOS; use your package manager on Linux
git clone https://github.com/Lodvos/rally-cut
cd rally-cut
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

The pose model (~6 MB) downloads itself on first run.

Built and tested on macOS with Apple Silicon: ffmpeg uses the VideoToolbox
hardware encoder, YOLO runs on MPS. It will run elsewhere, but those two spots
need adjusting.

## Run

```bash
./.venv/bin/python -m uvicorn app.server:app --port 8712
```

Open http://127.0.0.1:8712 — put videos in `video/`, exports land in `output/`.

## How it works

The task is framed as binary classification on a 10 Hz time grid ("is a rally
happening right now"), with rally boundaries recovered in post-processing.
That is easier to label and train than predicting segments directly.

```
video 1080p30
  ├─ 960×540 gray  ──┬─→ ball detector (classical CV)
  │                   └─→ 480×270 → motion features
  ├─ 960×540 BGR 10fps → YOLO11n-pose → skeletons of both players
  └─ audio 16 kHz ────→ STFT → 2–7 kHz energy
              │
              ▼
     45 features × T  →  ±1.5 s context  →  315 features
              │
              ▼
     gradient boosting → p(rally) → smoothing → segments
```

### Features

| Group | Count | What |
|---|---|---|
| Player poses | 25 | stance width, torso lean, raised arm, wrist speed, position relative to the table |
| Ball | 4 | flight present, trajectory length, time to nearest flight |
| Motion | 14 | foreground and motion share per third of the frame, centroids |
| Audio | 2 | energy in the ball-strike band |

The strongest single feature is **stance width** (Cohen's d = 1.12). A player in
ready stance stands wide; between points they stand upright. The best feature
before pose estimation scored 0.65.

### What mattered

**Mirror augmentation.** Every training sample is duplicated with the players
swapped. Without it the model broke after players changed ends between games —
recall dropped from 90% to 74%.

**Adaptive threshold.** Confidence inside rallies runs at 0.97 on familiar
footage but 0.50 on unfamiliar. A fixed threshold cut half the rallies, so it is
now derived from the 97th percentile of each video's own predictions.

**Splitting merged rallies.** With quick serves neighbouring points merge into
one segment; segments longer than 9 seconds get split at confidence dips.

**Telling players apart by kit colour**, sampled from the video itself — so the
system knows who stands where regardless of the score. Reliability is measured,
and when the players dress alike it honestly asks you to set sides manually.

## What didn't work

**Audio.** The first version looked for ball strikes by sound. Measurement
killed it: in a hall with several tables, strike loudness inside and outside
rallies is identical (median 1.9 vs 1.9) — other tables sound the same.

**Identifying the server.** 66% accuracy from a raised arm, 51–61% from ball
position — no better than a coin flip.

**Fully automatic scoring.** One wrong point corrupts every score after it. At
95% per-point accuracy a 70-point match comes out correct just 3% of the time;
at 99%, half the time. You would need 99.5%+, which a single 30 fps camera
cannot deliver. So scoring is semi-automatic: the app plays rallies back to
back, you mark the winner with one key.

## Limitations

- Trained on 34 minutes of video from one venue
- 30 fps is thin for ball tracking — the detector catches about half the flights
- Let serves are not told apart from rallies automatically
- Hardware acceleration assumes Apple Silicon

## Free labels from old edits

If you used to cut matches by hand, every "original + edit" pair is a ready-made
label set. The script finds which parts of the original made it into the edit by
matching frames:

```bash
./.venv/bin/python scripts/add_truth.py video/match.mov video/match_edit.mov
./.venv/bin/python scripts/train.py
```

That is how the first ground truth was obtained: 68 rallies recovered
automatically at 0.999 frame similarity.

## License

MIT
