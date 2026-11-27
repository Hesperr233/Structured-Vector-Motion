"""Exercise the codec tools on the fixture without a product adapter."""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from .fixture import COUNT, FPS, SIZE, generate


def run(tool: str, *args: str, input_bytes: bytes | None = None) -> bytes:
    binary = shutil.which(tool)
    if binary is None:
        raise RuntimeError(f"Required tool missing: {tool}")
    try:
        return subprocess.run(
            [binary, *args], input=input_bytes, capture_output=True, check=True, timeout=60
        ).stdout
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"{tool} failed (exit {error.returncode}): {detail}") from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"{tool} could not complete: {error}") from error


class MediaSmokeTest(unittest.TestCase):
    def test_required_tools_reject_missing_binary(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Required tool missing"):
            run("svm-no-such-media-tool-803fa0e8", "-version")

    def test_encode_probe_and_decode_h264(self) -> None:
        versions = []
        for tool in ("ffmpeg", "ffprobe"):
            version = run(tool, "-version").decode(errors="replace").splitlines()[0]
            print(version, flush=True)
            match = re.match(rf"{tool} version (?:n)?(\d+\.\d+)", version)
            self.assertIsNotNone(match, version)
            parsed = tuple(map(int, match[1].split(".")))
            self.assertGreaterEqual(parsed, (6, 0))
            versions.append(parsed)
        self.assertEqual(versions[0], versions[1])

        fixture = generate("combined")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.mp4"
            run(
                "ffmpeg",
                "-v",
                "error",
                "-n",
                "-f",
                "rawvideo",
                "-pixel_format",
                "gray",
                "-video_size",
                f"{SIZE[0]}x{SIZE[1]}",
                "-framerate",
                str(FPS),
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "17",
                "-pix_fmt",
                "yuv420p",
                str(path),
                input_bytes=b"".join(frame.tobytes() for frame in fixture.degraded),
            )
            metadata = json.loads(
                run(
                    "ffprobe",
                    "-v",
                    "error",
                    "-count_frames",
                    "-show_streams",
                    "-show_frames",
                    "-of",
                    "json",
                    str(path),
                )
            )
            (stream,) = metadata["streams"]
            self.assertEqual(stream["codec_type"], "video")
            self.assertEqual(stream["codec_name"], "h264")
            self.assertEqual(stream["pix_fmt"], "yuv420p")
            self.assertEqual((stream["width"], stream["height"]), SIZE)
            self.assertEqual(Fraction(stream["avg_frame_rate"]), FPS)
            self.assertEqual(Fraction(stream["duration"]), Fraction(COUNT, FPS))
            self.assertEqual(int(stream["nb_read_frames"]), COUNT)
            time_base = Fraction(stream["time_base"])
            timestamps = [
                int(frame["best_effort_timestamp"]) * time_base for frame in metadata["frames"]
            ]
            self.assertEqual(timestamps, [Fraction(index, FPS) for index in range(COUNT)])
            decoded = run(
                "ffmpeg",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(path),
                "-fps_mode",
                "passthrough",
                "-threads",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "pipe:1",
            )
            frame_bytes = SIZE[0] * SIZE[1]
            self.assertEqual(len(decoded), COUNT * frame_bytes)
            for index, source in enumerate(fixture.degraded):
                with self.subTest(frame=index):
                    data = decoded[index * frame_bytes : (index + 1) * frame_bytes]
                    image = Image.frombytes("L", SIZE, data)
                    self.assertLess(ImageStat.Stat(ImageChops.difference(image, source)).mean[0], 5)
                    bright = [0] * 150 + [255] * 106
                    self.assertEqual(image.point(bright).getbbox(), source.point(bright).getbbox())
            invalid = Path(temp) / "corrupt.mp4"
            invalid.write_bytes(b"not an MP4")
            with self.assertRaisesRegex(RuntimeError, "ffprobe failed"):
                run("ffprobe", "-v", "error", "-show_streams", "-show_frames", str(invalid))
