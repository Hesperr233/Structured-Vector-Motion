"""External H.264/sRGB profile for formal export, replay and restricted A ingest."""

import hashlib
import json
import math
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

from .media import MediaError
from .sequence import digest

FPS = Fraction(12, 1)
MAE_LIMIT = 2.0
PSNR_LIMIT = 40.0
NAMES = ("before.mp4", "after.mp4", "comparison.mp4")
COLOR = {
    "pix_fmt": "yuv420p",
    "color_range": "tv",
    "color_space": "bt709",
    "color_primaries": "bt709",
    "color_transfer": "iec61966-2-1",
}
# The input and output of the matrix/range conversions are sRGB code values.
# No transfer-function conversion is part of this profile.
ENCODE_FILTER = (
    "scale=in_range=full:out_range=tv:out_color_matrix=bt709:flags=area+accurate_rnd,format=yuv420p,"
    "setparams=range=limited:color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709"
)
DECODE_FILTER = (
    "scale=in_range=tv:in_color_matrix=bt709:out_range=full:flags=neighbor+accurate_rnd+full_chroma_int,"
    "format=rgb24"
)
PROBE_FIELDS = (
    "format=format_name,start_time,duration:format_tags=major_brand:"
    "stream=index,codec_name,codec_type,width,height,pix_fmt,time_base,nb_frames,"
    "r_frame_rate,avg_frame_rate,start_pts,duration_ts,duration,color_range,color_space,"
    "color_transfer,color_primaries,chroma_location,sample_aspect_ratio:stream_tags=rotate:"
    "stream_side_data=side_data_type,rotation:frame=media_type,stream_index,pts,duration,"
    "pkt_duration,width,height,pix_fmt,color_range,color_space,color_transfer,color_primaries,"
    "key_frame:frame_side_data=side_data_type,rotation"
)


class FormalMediaError(MediaError):
    """A stable CLI diagnostic with a portable operation description."""

    def __init__(self, code: str, message: str):
        number = int(code[-3:])
        if number == 13:
            exit_code = 3
        elif 15 <= number <= 17:
            exit_code = 4
        elif number in (25, 26):
            exit_code = 6
        elif number in (27, 28):
            exit_code = 7
        else:
            exit_code = 2
        super().__init__(message, exit_code=exit_code)
        self.code = code


def _exec(role: str, name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise FormalMediaError("CVR-E025", f"required {role} executable unavailable")
    return str(Path(binary).resolve())


def _run(command: list[str], *, purpose: str, data: bytes | None = None):
    try:
        p = subprocess.run(command, input=data, capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FormalMediaError("CVR-E026", f"{purpose}: tool could not finish") from exc
    if p.returncode:
        raise FormalMediaError("CVR-E026", f"{purpose}: tool returned {p.returncode}")
    return p


def _tools(encoder: str, ffprobe: str) -> tuple[str, str, dict]:
    binaries = {"ffmpeg": _exec("ffmpeg", encoder), "ffprobe": _exec("ffprobe", ffprobe)}
    versions = {}
    for role, binary in binaries.items():
        raw = _run([binary, "-version"], purpose=f"{role} version").stdout.decode(errors="replace")
        words = raw.split()
        if len(words) < 3 or words[:2] != [role, "version"]:
            raise FormalMediaError("CVR-E026", f"{role} did not supply its version")
        with Path(binary).open("rb") as f:
            tool_hash = hashlib.file_digest(f, "sha256").hexdigest()
        versions[role] = {"version": words[2], "executable_sha256": f"sha256:{tool_hash}"}
    return binaries["ffmpeg"], binaries["ffprobe"], versions


def _ratio(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _fraction(value) -> Fraction | None:
    try:
        return Fraction(value)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def _probe(ffprobe: str, path: Path) -> tuple[dict, list[str]]:
    args = ["-v", "error", "-f", "mov", "-show_entries", PROBE_FIELDS, "-of", "json"]
    p = _run([ffprobe, *args, str(path.resolve())], purpose="ffprobe presentation frames")
    try:
        info = json.loads(p.stdout)
    except (ValueError, UnicodeDecodeError) as error:
        raise FormalMediaError("CVR-E028", "ffprobe returned malformed metadata") from error
    if p.stderr or not isinstance(info, dict):
        raise FormalMediaError("CVR-E028", "ffprobe reported a media error")
    return info, ["ffprobe", *args, "{media}"]


def _has_rotation(item: dict) -> bool:
    return "rotate" in item.get("tags", {}) or any(
        d.get("side_data_type") == "Display Matrix" or "rotation" in d
        for d in item.get("side_data_list", [])
    )


def _video_contract(
    info: dict, shape: tuple, fps: Fraction, *, ingest: bool, drop_audio: bool = False
) -> tuple[dict, list[dict]]:
    """Validate the presentation timeline using integer PTS, including the final frame."""
    number, height, width, _ = shape
    streams = info.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]

    def reject(input_code: str, message: str):
        raise FormalMediaError(input_code if ingest else "CVR-E028", message)

    if len(videos) != 1 or len(streams) != 1 + len(audios):
        reject("CVR-E015", "requires exactly one video and only permitted stream types")
    if audios and not (ingest and drop_audio):
        reject("CVR-E016", "audio stream present without permission to discard audio")
    video = videos[0]
    fmt = info.get("format", {})
    if "mp4" not in fmt.get("format_name", "").split(",") or (
        fmt.get("tags", {}).get("major_brand", "").strip() == "qt"
    ):
        reject("CVR-E015", "unsupported container")
    if video.get("codec_name") != "h264" or any(video.get(k) != v for k, v in COLOR.items()):
        reject("CVR-E015", "unexpected codec, pixel format, matrix, range, primaries or transfer")
    if (video.get("width"), video.get("height")) != (width, height):
        reject("CVR-E003", "unexpected video dimensions")
    frames = [f for f in info.get("frames", []) if f.get("stream_index") == video.get("index")]
    if len(frames) != number or str(video.get("nb_frames", number)) != str(number):
        reject("CVR-E002", "unexpected presentation frame count")
    if _has_rotation(video) or any(_has_rotation(f) for f in frames):
        reject("CVR-E017", "rotation metadata is unsupported")
    if any(
        (f.get("width"), f.get("height")) != (width, height)
        or any(f.get(k) != v for k, v in COLOR.items())
        for f in frames
    ):
        reject("CVR-E015", "per-frame video format changed")
    time_base = _fraction(video.get("time_base"))
    first = video.get("start_pts")
    if (
        time_base is None
        or time_base <= 0
        or type(first) is not int
        or (not ingest and first != 0)
        or _fraction(video.get("r_frame_rate")) != fps
        or _fraction(video.get("avg_frame_rate")) != fps
    ):
        reject("CVR-E017", "invalid or non-CFR video timebase")
    for i, frame in enumerate(frames):
        pts = frame.get("pts")
        duration = frame.get("duration", frame.get("pkt_duration"))
        if type(pts) is not int or (pts - first) * time_base != i / fps:
            reject("CVR-E017", f"presentation timestamp differs at index {i}")
        if type(duration) is not int or duration * time_base != 1 / fps:
            reject("CVR-E017", f"presentation duration differs at index {i}")
    if _fraction(video.get("duration_ts")) is None or video["duration_ts"] * time_base != (
        number / fps
    ):
        reject("CVR-E017", "video duration in stream ticks differs")
    durations = [video.get("duration")]
    if not ingest:
        durations.append(fmt.get("duration"))
    for value in durations:
        actual = _fraction(value)
        if actual is None or abs(actual - number / fps) > Fraction(1, 100):
            reject("CVR-E017", "reported duration outside 0.01 second tolerance")
    return video, frames


def _decode(binary: str, path: Path, shape: tuple) -> tuple[np.ndarray, dict]:
    args = [
        "-v",
        "error",
        "-xerror",
        "-err_detect",
        "explode",
        "-nostdin",
        "-nostats",
        "-progress",
        "pipe:2",
        "-noautorotate",
        "-f",
        "mov",
        "-i",
    ]
    output_args = [
        "-map",
        "0:v:0",
        "-vf",
        DECODE_FILTER,
        "-fps_mode",
        "passthrough",
        "-an",
        "-c:v",
        "rawvideo",
        "-threads",
        "1",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    p = _run([binary, *args, str(path.resolve()), *output_args], purpose="full RGB decode")
    # Capture only machine-readable counters. Arbitrary tool stderr can contain private paths.
    progress = {}
    for line in p.stderr.decode(errors="replace").splitlines():
        k, _, v = line.partition("=")
        if k in {"frame", "progress", "dup_frames", "drop_frames", "total_size"}:
            progress[k] = v.strip()
    if (
        len(p.stdout) != math.prod(shape)
        or progress.get("progress") != "end"
        or progress.get("frame") != str(shape[0])
        or any(progress.get(k) != "0" for k in ("dup_frames", "drop_frames"))
    ):
        raise FormalMediaError(
            "CVR-E028", "incomplete full RGB decode or frames dropped/duplicated"
        )
    pixels = np.frombuffer(p.stdout, dtype=np.uint8).reshape(shape)
    return pixels, {
        "argv": ["ffmpeg", *args, "{media}", *output_args],
        "exit": p.returncode,
        "progress": progress,
        "decoded_rgb_bytes": len(p.stdout),
        "decoded_rgb_sha256": digest(p.stdout),
    }


def decode_input_mp4(path: Path, encoder: str, ffprobe: str, *, drop_audio: bool = False):
    """Read the caller's archived source; restricted MP4 ingest is A only."""
    binary, probe_bin, tools = _tools(encoder, ffprobe)
    info, probe_argv = _probe(probe_bin, path)
    shape = (24, 256, 256, 3)
    video, frames = _video_contract(info, shape, FPS, ingest=True, drop_audio=drop_audio)
    rgb, decode_log = _decode(binary, path, shape)
    return rgb, {
        "decoder_profile": "h264-yuv420p-srgb-bt709-limited@0.1",
        "tools": tools,
        "probe_argv": probe_argv,
        "stream": video,
        "audio_streams_dropped": [
            s["index"] for s in info["streams"] if s["codec_type"] == "audio"
        ],
        "frame_mapping": [
            {"index": i, "source_pts": f["pts"], "presentation_pts": _ratio(i / FPS), "tick": i}
            for i, f in enumerate(frames)
        ],
        "decode_log": decode_log,
    }


def _faststart(path: Path) -> bool:
    """Read top-level ISO BMFF box headers; reject an mdat preceding moov."""
    size = path.stat().st_size
    with path.open("rb") as f:
        while f.tell() < size:
            start = f.tell()
            header = f.read(8)
            if len(header) != 8:
                return False
            n = int.from_bytes(header[:4], "big")
            if n == 1:
                n = int.from_bytes(f.read(8), "big")
            elif n == 0:
                n = size - start
            if start + n < f.tell() or start + n > size:
                return False
            if header[4:] == b"moov":
                return True
            if header[4:] == b"mdat":
                return False
            f.seek(start + n)
    return False


def _frame_metrics(decoded: np.ndarray, source: np.ndarray) -> dict:
    delta = decoded.astype(np.int32) - source
    absolute = int(np.abs(delta).sum(dtype=np.int64))
    squared = int((delta * delta).sum(dtype=np.int64))
    mae = absolute / source.size
    mse = squared / source.size
    psnr = 10 * math.log10(255**2 / mse) if squared else None
    return {
        "source_pixel_sha256": digest(source.tobytes()),
        "decoded_pixel_sha256": digest(decoded.tobytes()),
        "mae_dn": mae,
        "mse_dn2": mse,
        "psnr_db": psnr,
        "psnr_status": "FINITE" if squared else "POSITIVE_INFINITY",
        "passed": mae <= MAE_LIMIT and (psnr is None or psnr >= PSNR_LIMIT),
    }


def _check_arrays(before: np.ndarray, after: np.ndarray, fps: Fraction):
    if (
        before.ndim != 4
        or before.shape[-1] != 3
        or before.shape != after.shape
        or min(before.shape) < 1
        or before.dtype != np.uint8
        or after.dtype != np.uint8
        or before.shape[1] % 2
        or before.shape[2] % 2
        or fps <= 0
    ):
        raise FormalMediaError(
            "CVR-E003", "expected equal, even-sized RGB8 sequences and positive CFR"
        )


def _encode(binary: str, path: Path, frames: np.ndarray, fps: Fraction) -> list[str]:
    _, height, width, _ = frames.shape
    args = [
        "-n",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        _ratio(fps),
        "-i",
        "pipe:0",
        "-an",
        "-map",
        "0:v:0",
        "-vf",
        ENCODE_FILTER,
        "-fps_mode",
        "passthrough",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "12",
        "-g",
        "12",
        "-keyint_min",
        "12",
        "-sc_threshold",
        "0",
        "-threads",
        "1",
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "iec61966-2-1",
        "-color_range",
        "tv",
        "-video_track_timescale",
        str(fps.numerator),
        "-movflags",
        "+faststart+write_colr",
        "-f",
        "mp4",
    ]
    _run([binary, *args, str(path.resolve())], purpose=f"encode {path.name}", data=frames.tobytes())
    return ["ffmpeg", *args, path.name]


def _verify_file(
    binary: str, probe_bin: str, path: Path, before: np.ndarray, after: np.ndarray, fps: Fraction
) -> dict:
    if not path.is_file():
        raise FormalMediaError("CVR-E027", f"{path.name} missing")
    comp = path.name == "comparison.mp4"
    number, height, width, _ = before.shape
    shape = (number, height, (2 if comp else 1) * width, 3)
    with path.open("rb") as f:
        sha = "sha256:" + hashlib.file_digest(f, "sha256").hexdigest()
    record = {"file": path.name, "sha256": sha, "file_sha256": sha}
    try:
        info, probe_argv = _probe(probe_bin, path)
        stream, frames = _video_contract(info, shape, fps, ingest=False)
        faststart = _faststart(path)
        rgb, log = _decode(binary, path, shape)
        rows = []
        for i, frame in enumerate(frames):
            if comp:
                panels = {
                    "left_before": _frame_metrics(rgb[i, :, :width], before[i]),
                    "right_after": _frame_metrics(rgb[i, :, width:], after[i]),
                }
            else:
                source = before if path.name == "before.mp4" else after
                panels = {path.stem: _frame_metrics(rgb[i], source[i])}
            rows.append(
                {
                    "index": i,
                    "stream_pts": frame["pts"],
                    "presentation_pts": _ratio(i / fps),
                    "panels": panels,
                    "passed": all(p["passed"] for p in panels.values()),
                }
            )
        passed = faststart and all(row["passed"] for row in rows)
        record.update(
            {
                "status": "VERIFIED" if passed else "FAILED",
                "code": "EXIT_OK" if passed else "CVR-E028",
                "verification": {
                    "stream": stream,
                    "format": info["format"],
                    "faststart": faststart,
                    "stream_count": len(info["streams"]),
                    "frame_count": len(frames),
                    "probe_argv": probe_argv,
                    "decode_log": log,
                    "frames": rows,
                },
            }
        )
    except FormalMediaError as exc:
        if exc.exit_code != 7:
            raise
        record.update({"status": "FAILED", "code": exc.code, "detail": str(exc)})
    return record


def _verify_files(
    run: Path,
    before: np.ndarray,
    after: np.ndarray,
    fps: Fraction,
    binary: str,
    probe_bin: str,
    tools: dict,
) -> dict:
    files = {
        name: _verify_file(binary, probe_bin, run / name, before, after, fps) for name in NAMES
    }
    passed = all(f["status"] == "VERIFIED" for f in files.values())
    return {
        "status": "VERIFIED" if passed else "FAILED",
        "code": "EXIT_OK" if passed else "CVR-E028",
        "tools": tools,
        "limits": {"max_frame_mae_dn": MAE_LIMIT, "min_frame_psnr_db": PSNR_LIMIT},
        "files": files,
    }


def verify_media(
    run: Path,
    before: np.ndarray,
    after: np.ndarray,
    encoder: str,
    ffprobe: str,
    *,
    fps: Fraction = FPS,
) -> dict:
    """Probe and decode actual media anew without trusting the earlier export report."""
    _check_arrays(before, after, fps)
    return _verify_files(run, before, after, fps, *_tools(encoder, ffprobe))


def export_formal_media(
    output: Path,
    before: np.ndarray,
    after: np.ndarray,
    encoder: str,
    ffprobe: str,
    *,
    fps: Fraction = FPS,
) -> dict:
    """Write to the caller's empty staging directory; the caller owns publication."""
    _check_arrays(before, after, fps)
    if not output.is_dir() or any(output.iterdir()):
        raise FormalMediaError("CVR-E012", "media stage must be a new empty directory")
    binary, probe_bin, tools = _tools(encoder, ffprobe)
    encode_args = {}
    for name, frames in zip(
        NAMES, (before, after, np.concatenate((before, after), axis=2)), strict=True
    ):
        encode_args[name] = _encode(binary, output / name, frames, fps)
    report = _verify_files(output, before, after, fps, binary, probe_bin, tools)
    for name in NAMES:
        report["files"][name]["encode_argv"] = encode_args[name]
        report["files"][name]["stdin_rgb_roles"] = {
            "before.mp4": ["before"],
            "after.mp4": ["after"],
            "comparison.mp4": ["before", "after"],
        }[name]
    return report
