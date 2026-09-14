"""Сборка итогового видео из выбранных сегментов."""
import subprocess, tempfile, os, shutil, threading
import concurrent.futures as futures

WORKERS = 4        # аппаратный кодер тянет несколько потоков: замеры дали ~2.5x

FADE = 0.04  # короткое аудио-затухание на стыках, чтобы не щёлкало


def _encoder(quality):
    # аппаратный кодер Apple Silicon: быстро и без заметной потери качества
    return ["-c:v", "h264_videotoolbox", "-b:v", quality, "-profile:v", "high",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def cut_segment(src, start, end, out, quality, on_time=None):
    """Вырезать кусок. on_time(секунд_готово) вызывается по ходу кодирования.

    quality="copy" — без перекодирования: мгновенно и с исходным качеством,
    но границы ложатся на ближайшие опорные кадры (расхождение до секунды).
    """
    dur = end - start
    if quality == "copy":
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", src,
             "-t", f"{dur:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero",
             "-movflags", "+faststart", out],
            check=True, capture_output=True)
        if on_time:
            on_time(dur)
        return
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
           "-af", f"afade=t=in:st=0:d={FADE},"
                  f"afade=t=out:st={max(0, dur - FADE):.3f}:d={FADE}",
           *_encoder(quality),
           "-video_track_timescale", "90000",
           "-movflags", "+faststart"]
    if on_time:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd.append(out)
    if not on_time:
        subprocess.run(cmd, check=True, capture_output=True)
        return
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in p.stdout:
        if line.startswith("out_time_ms="):
            try:
                on_time(int(line.split("=")[1]) / 1_000_000)
            except ValueError:
                pass
    p.wait()
    if p.returncode:
        raise subprocess.CalledProcessError(p.returncode, cmd, stderr=p.stderr.read())


def cut_many(src, segments, paths, quality, on_done=None, workers=WORKERS):
    """Режет куски параллельно — порядок результата задаётся списком путей."""
    lock = threading.Lock()
    done = [0]

    def one(i):
        cut_segment(src, segments[i]["start"], segments[i]["end"], paths[i], quality)
        if on_done:
            with lock:
                done[0] += 1
                on_done(done[0], len(segments))

    n = min(workers, max(1, len(segments)))
    with futures.ThreadPoolExecutor(n) as ex:
        for f in futures.as_completed([ex.submit(one, i) for i in range(len(segments))]):
            f.result()          # исключение из потока не должно потеряться


def build(src, segments, out_path, quality="12M", progress=None):
    """segments: [{'start': float, 'end': float}, ...] в порядке монтажа."""
    tmp = tempfile.mkdtemp(prefix="tt_export_")
    try:
        parts = [os.path.join(tmp, f"{i:04d}.mp4") for i in range(len(segments))]
        cut_many(src, segments, parts, quality, on_done=progress)
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            for p in parts:
                f.write(f"file '{p}'\n")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", lst, "-c", "copy", "-movflags", "+faststart", out_path],
            check=True, capture_output=True)
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
