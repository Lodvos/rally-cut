"""Извлечение низкоуровневых сигналов из видео через ffmpeg."""
import subprocess, json, numpy as np

SR = 16000          # частота дискретизации аудио для анализа
FRAME_FPS = 10      # частота кадров для сигнала движения
FRAME_W, FRAME_H = 160, 90


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fps = 0.0
    if v and v.get("avg_frame_rate", "0/0") != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = float(n) / float(d) if float(d) else 0.0
    return {
        "duration": float(info["format"]["duration"]),
        "width": v and int(v["width"]), "height": v and int(v["height"]),
        "fps": fps, "vcodec": v and v["codec_name"],
        "acodec": a and a["codec_name"], "has_audio": a is not None,
    }


def audio_mono(path):
    """Моно float32 дорожка на SR Гц."""
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1",
         "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


def motion_series(path):
    """Средняя абсолютная разница соседних уменьшенных кадров, FRAME_FPS Гц."""
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", f"fps={FRAME_FPS},scale={FRAME_W}:{FRAME_H},format=gray",
         "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE)
    n = FRAME_W * FRAME_H
    prev, vals = None, []
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        cur = np.frombuffer(buf, dtype=np.uint8).astype(np.float32)
        if prev is not None:
            vals.append(float(np.abs(cur - prev).mean()))
        prev = cur
    p.stdout.close(); p.wait()
    return np.array(vals, dtype=np.float32)
