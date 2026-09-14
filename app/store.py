"""Хранение результатов анализа рядом с видео (data/<имя>/)."""
import json, os, hashlib, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO_DIR = os.path.join(ROOT, "video")
DATA_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "output")


def key(name):
    return hashlib.sha1(name.encode()).hexdigest()[:10]


def dir_for(name):
    d = os.path.join(DATA_DIR, key(name))
    os.makedirs(d, exist_ok=True)
    return d


def path(name, fn):
    return os.path.join(dir_for(name), fn)


def load(name, fn):
    p = path(name, fn)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return None            # файл мог быть повреждён — ведём себя как при его отсутствии


def save(name, fn, obj):
    """Запись через временный файл: читатель никогда не увидит половину данных."""
    p = path(name, fn)
    d = os.path.dirname(p)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def videos():
    if not os.path.isdir(VIDEO_DIR):
        return []
    ok = (".mp4", ".mov", ".m4v", ".mkv", ".avi")
    out = []
    for n in sorted(os.listdir(VIDEO_DIR)):
        if n.lower().endswith(ok) and not n.startswith("."):
            p = os.path.join(VIDEO_DIR, n)
            out.append(dict(name=n, size=os.path.getsize(p),
                            analyzed=os.path.exists(path(n, "analysis.json")),
                            proxy=os.path.exists(path(n, "proxy.mp4"))))
    return out
