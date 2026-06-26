import os
import shutil
import logging
import subprocess
import tempfile

logger = logging.getLogger(__name__)


def _duration_seconds(path: str) -> float | None:
    """Длительность видео через ffprobe."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def compress_video(path: str, target_bytes: int) -> str | None:
    """
    Сжимает видео, чтобы оно поместилось в target_bytes.
    Возвращает путь к сжатому файлу (новый) или None, если не вышло.
    Требует ffmpeg и ffprobe в PATH.
    """
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        logger.warning("ffmpeg/ffprobe не найдены — сжатие невозможно")
        return None

    dur = _duration_seconds(path)
    if not dur or dur <= 0:
        logger.warning("Не удалось узнать длительность: %s", path)
        return None

    # целевой битрейт (бит/с), оставляем ~92% запаса под контейнер/аудио
    target_bits = target_bytes * 8 * 0.92
    audio_bps = 128_000
    video_bps = int(target_bits / dur) - audio_bps
    if video_bps < 150_000:
        video_bps = 150_000  # ниже смысла нет — видео будет совсем мыльным

    out_path = os.path.join(
        tempfile.gettempdir(),
        "cmp_" + os.path.basename(os.path.splitext(path)[0]) + ".mp4",
    )

    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-c:v", "libx264", "-b:v", str(video_bps),
        "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]
    try:
        logger.info("Сжимаю видео %s до ~%s бит/с", path, video_bps)
        subprocess.run(cmd, capture_output=True, timeout=1800, check=True)
    except Exception:
        logger.exception("Ошибка сжатия видео: %s", path)
        if os.path.exists(out_path):
            os.remove(out_path)
        return None

    if os.path.exists(out_path) and os.path.getsize(out_path) <= target_bytes:
        return out_path

    # не уложились — выкидываем
    if os.path.exists(out_path):
        logger.warning("После сжатия всё ещё больше лимита: %s", out_path)
        os.remove(out_path)
    return None
