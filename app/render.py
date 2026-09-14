"""Сборка видео со счётом: на каждый кусок накладывается своё табло."""
import os, subprocess, tempfile, shutil
from . import scoreboard, export

MARGIN = 40          # отступ табло от края кадра, px при 1920


def plan_rallies(states, rallies, setup):
    """Только розыгрыши: кусок = розыгрыш, на нём счёт на момент его начала."""
    out = []
    for st in states:
        i = st["rally"]
        if i >= len(rallies):
            break
        r = rallies[i]
        out.append(dict(start=r["start"], end=r["end"], state=st))
    return out


def plan_full(states, rallies, duration, setup):
    """Полное видео: режем по концам розыгрышей, счёт обновляется после каждого."""
    out = []
    prev = 0.0
    for st in states:
        i = st["rally"]
        if i >= len(rallies):
            break
        end = rallies[i]["end"]
        out.append(dict(start=prev, end=end, state=st))
        prev = end
    if prev < duration and states:
        last = dict(states[-1])
        last = {**last, "points_a": last["games_after_a"] and last["after_a"] or last["after_a"],
                "points_b": last["after_b"],
                "games_a": last["games_after_a"], "games_b": last["games_after_b"]}
        out.append(dict(start=prev, end=duration, state=last))
    return out


def _one(src, piece, png, part, quality):
    dur = piece["end"] - piece["start"]
    fade = min(export.FADE, max(0.0, dur / 4))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-ss", f"{piece['start']:.3f}", "-i", src,
         # картинку нужно зациклить, иначе ffmpeg ждёт её кадры вечно
         "-loop", "1", "-framerate", "30", "-i", png,
         "-t", f"{dur:.3f}",
         "-filter_complex",
         f"[0:v][1:v]overlay=x={MARGIN}:y=H-h-{MARGIN}:shortest=1[v]",
         "-map", "[v]", "-map", "0:a?",
         "-af", f"afade=t=in:st=0:d={fade},"
                f"afade=t=out:st={max(0, dur - fade):.3f}:d={fade}",
         "-c:v", "h264_videotoolbox", "-b:v", quality, "-profile:v", "high",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         "-video_track_timescale", "90000", "-movflags", "+faststart",
         "-f", "mp4", part],
            check=True, capture_output=True)


def build(src, plan, setup, out_path, quality="12M", width=1920, progress=None):
    import threading
    import concurrent.futures as futures
    tmp = tempfile.mkdtemp(prefix="tt_score_")
    try:
        parts = [os.path.join(tmp, f"{i:04d}.mp4") for i in range(len(plan))]
        for i, piece in enumerate(plan):
            scoreboard.save(piece["state"], setup, os.path.join(tmp, f"b{i:04d}.png"),
                            width=width)
        lock = threading.Lock(); done = [0]

        def work(i):
            _one(src, plan[i], os.path.join(tmp, f"b{i:04d}.png"), parts[i], quality)
            if progress:
                with lock:
                    done[0] += 1
                    progress(done[0], len(plan))

        with futures.ThreadPoolExecutor(min(export.WORKERS, max(1, len(plan)))) as ex:
            for f in futures.as_completed([ex.submit(work, i) for i in range(len(plan))]):
                f.result()

        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            for p in parts:
                f.write(f"file '{p}'\n")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-c", "copy", "-movflags", "+faststart", out_path],
            check=True, capture_output=True)
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
