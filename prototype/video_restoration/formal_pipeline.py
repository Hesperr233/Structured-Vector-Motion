"""File-only handoffs for the R1-002 development candidate."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import __version__ as pillow_version

from . import formal_bundle as b
from .causal_repair import restore
from .sequence import digest, save_sequence, write_json


def code_sha() -> str:
    repo = Path(__file__).resolve().parents[2]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    files = ["prototype/video_restoration/*.py", "svm", "schema"]
    changed = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", *files], cwd=repo
    )
    b.require(
        not changed and re.fullmatch("[0-9a-f]{40}", sha),
        13,
        "implementation sources must match the recorded commit",
    )
    return sha


def distinct(*paths: Path) -> None:
    resolved = [p.resolve() for p in paths]
    for i, left in enumerate(resolved):
        for right in resolved[i + 1 :]:
            b.require(
                left != right and left not in right.parents and right not in left.parents,
                12,
                "input and output paths must be disjoint",
            )


@contextmanager
def fresh(output: Path):
    b.require(not output.exists() and not output.is_symlink(), 12, "output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    b.regular(output.parent)
    with TemporaryDirectory(dir=output.parent, prefix=".cvr-new-") as directory:
        working = Path(directory) / "result"
        working.mkdir()
        yield working
        working.rename(output)


@contextmanager
def exclusive(run: Path):
    """OS releases the advisory lock even when the process is killed."""
    b.regular(run)
    lock_path = run / ".stage-lock"
    if lock_path.exists():
        b.regular(lock_path)
    with lock_path.open("a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise b.StageError(12, "another stage owns this run") from exc
        yield


def copy_video(source, destination, video):
    destination.mkdir(exist_ok=True)
    (destination / "frames").mkdir()
    for item in video["frames"]:
        shutil.copyfile(source / item["file"], destination / item["file"])
    shutil.copyfile(source / "video-manifest.json", destination / "video-manifest.json")


def ingest(args):
    distinct(args.source, args.output)
    b.regular(args.source)
    with fresh(args.output) as target:
        if args.source.is_file():
            from .formal_media import decode_input_mp4

            b.require(args.source.suffix.lower() == ".mp4", 6, "unsupported input media")
            b.require(
                args.decoder_profile in (None, "cvr-h264-bt709-srgb@1"),
                1,
                "unsupported decoder profile",
            )
            folder = target / "source"
            folder.mkdir()
            original = folder / "original.mp4"
            shutil.copyfile(args.source, original)
            pixels, details = decode_input_mp4(
                original, args.decoder, args.ffprobe, drop_audio=args.drop_audio
            )
            video = b.make_video(target, pixels, "controlled-mp4", "A-MP4", "A")
            info = {
                "kind": "archived-mp4",
                "sha256": digest(original.read_bytes()),
            }
            write_json(
                folder / "decoder.json", {"file": "source/original.mp4", "source": info, **details}
            )
        else:
            b.require(
                not args.drop_audio and args.decoder_profile in (None, "rgb8-png@1"),
                18,
                "invalid PNG ingest options",
            )
            _, video = b.read_video(args.source)
            copy_video(args.source, target, video)
            info = {
                "kind": "degraded-png-snapshot",
                "sha256": digest((target / "video-manifest.json").read_bytes()),
            }
        b.read_video(target)
        write_json(
            target / "ingest-record.json",
            {
                **b.VERSION,
                "stage": "ingest",
                "exit_code": 0,
                "bundle_digest": video["bundle_digest"],
                "source": info,
            },
        )
    return 0


def identity(video, mask_sha, config_sha, sha):
    projected = {
        **b.VERSION,
        "bundle_digest": video["bundle_digest"],
        "mask_manifest_sha256": mask_sha,
        "config_sha256": config_sha,
        "algorithm_id": b.ALGORITHM,
        "code_sha": sha,
    }
    return {**projected, "run_id": "run:" + b.hashed(projected).split(":", 1)[1]}


def semantic(run_id, frames):
    keys = ("index", "pixel_sha256", "status", "changed_pixels", "unresolved_pixels")
    return b.hashed({"run_id": run_id, "frames": [{k: item[k] for k in keys} for item in frames]})


def advance(run, manifest, stage, status, code, diagnostic=None):
    entry = {"status": status, "exit_code": code, "diagnostic": diagnostic}
    manifest["stages"][stage] = entry
    manifest.update(status=status, exit_code=code, last_stage=stage, diagnostic=diagnostic)
    log_path = run / "run.log"
    if log_path.exists():
        b.regular(log_path)
    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        log.write(f"{stage} {status} exit={code} {diagnostic or ''}\n")
    write_json(run / "run-manifest.json", manifest)


def repair(args):
    distinct(args.input, args.masks, args.config, args.output)
    sha = code_sha()
    with fresh(args.output) as run:
        _, video = b.read_video(args.input)
        ingested = b.json_file(args.input / "ingest-record.json")
        b.version(ingested)
        b.fields(ingested, {*b.VERSION, "stage", "exit_code", "bundle_digest", "source"}, 14)
        b.fields(ingested["source"], {"kind", "sha256"}, 14)
        b.require(
            ingested["stage"] == "ingest"
            and ingested["source"]["kind"] in ("degraded-png-snapshot", "archived-mp4")
            and isinstance(ingested["source"]["sha256"], str)
            and re.fullmatch("sha256:[0-9a-f]{64}", ingested["source"]["sha256"]),
            14,
            "invalid public ingest provenance",
        )
        if ingested["source"]["kind"] == "degraded-png-snapshot":
            b.require(
                ingested["source"]["sha256"]
                == digest((args.input / "video-manifest.json").read_bytes()),
                13,
                "ingest snapshot mismatch",
            )
        b.require(
            ingested.get("bundle_digest") == video["bundle_digest"]
            and ingested.get("exit_code") == 0,
            13,
            "input missing completed ingest",
        )
        copy_video(args.input, run / "input", video)
        # Snapshot only declared files; never copy or hash neighbouring evaluation trees.
        _, mask = b.read_masks(args.masks, video)
        mr = run / "masks"
        mr.mkdir()
        shutil.copyfile(args.masks / "mask-manifest.json", mr / "mask-manifest.json")
        for role, desc in mask["roles"].items():
            (mr / role).mkdir()
            for item in desc["files"]:
                shutil.copyfile(args.masks / item["file"], mr / item["file"])
        b.regular(args.config)
        shutil.copyfile(args.config, run / "config.json")
        pixels, video = b.read_video(run / "input")
        masks, mask = b.read_masks(mr, video)
        config = b.read_config(run / "config.json", video, mask)
        after, evidence = restore(pixels, masks, mask, config)
        records = save_sequence(run / "frames-repaired", after, names=b.names(len(after)))
        frames = [
            {
                **b.record(i, "frames-repaired", record),
                **report,
                "tick": video["frame_ticks"][i],
                "pts": str(Fraction(i, 12)),
            }
            for i, (record, report) in enumerate(zip(records, evidence, strict=True))
        ]
        ids = identity(
            video,
            digest((mr / "mask-manifest.json").read_bytes()),
            digest((run / "config.json").read_bytes()),
            sha,
        )
        unresolved = sum(item["unresolved_pixels"] for item in frames)
        status, code = ("PARTIAL", 5) if unresolved else ("REPAIRED", 0)
        manifest = {
            **ids,
            "semantic_digest": semantic(ids["run_id"], frames),
            "scenario_kind": video["scenario_kind"],
            "clip_id": video["clip_id"],
            "profile_id": video["profile_id"],
            "real_video": False,
            "generalization_claimed": False,
            "clean_reference_in_input": False,
            "input_manifest_sha256": digest((run / "input/video-manifest.json").read_bytes()),
            "input_source": ingested["source"],
            "repair_status": status,
            "unresolved_pixels": unresolved,
            "unresolved_frames": sum(item["unresolved_pixels"] > 0 for item in frames),
            "frames": frames,
            "parameters": config,
            "repair_roi": mask["repair_roi"],
            "feather_px": mask["feather_px"],
            "mask_manifest": "masks/mask-manifest.json",
            "timebase_ticks_per_second": 12,
            "fps": video["fps"],
            "frame_count": len(frames),
            "tools": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "pillow": pillow_version,
            },
            "stages": {"ingest": {"exit_code": 0, "status": "INGESTED"}},
            "contract_state": "DRAFT",
            "acceptance_status": "NOT_RUN",
        }
        advance(run, manifest, "repair", status, code, "CVR-E021" if unresolved else None)
    return code


def sealed(run, manifest):
    b.version(manifest)
    before, v = b.read_video(run / "input")
    masks, mask = b.read_masks(run / "masks", v)
    config = b.read_config(run / "config.json", v, mask)
    ids = identity(
        v,
        digest((run / "masks/mask-manifest.json").read_bytes()),
        digest((run / "config.json").read_bytes()),
        code_sha(),
    )
    b.require(
        all(manifest.get(k) == value for k, value in ids.items())
        and digest((run / "input/video-manifest.json").read_bytes())
        == manifest.get("input_manifest_sha256"),
        13,
        "run identity differs from current sealed inputs or implementation",
    )
    after, records = b.sequence(run / "frames-repaired", len(before), v["width"], v["height"])
    frames = manifest["frames"]
    b.require(
        len(records) == len(frames)
        and all(
            all(f.get(k) == val for k, val in r.items())
            for f, r in zip(frames, records, strict=True)
        )
        and manifest["semantic_digest"] == semantic(ids["run_id"], frames),
        27,
        "sealed output mismatch",
    )
    changed = np.any(before != after, axis=3)
    area = b.rectangle(config["photometric"]["roi"], v["width"], v["height"])
    if not config["photometric"]["enabled"]:
        area.fill(False)
    b.require(not np.any(changed & ~(masks["repair"] | area)), 29, "unauthorized pixel changes")
    unresolved = sum(f["unresolved_pixels"] for f in frames)
    b.require(
        unresolved == manifest["unresolved_pixels"]
        and (unresolved == 0) == (manifest["repair_status"] == "REPAIRED")
        and all(int(changed[i].sum()) == f["changed_pixels"] for i, f in enumerate(frames)),
        27,
        "inconsistent pixel accounting",
    )
    return before, after


def validate_output(args, run, filename):
    expected = run / filename
    b.require(
        args.output.absolute() == expected.absolute() and not args.output.is_symlink(),
        12,
        "output must be the designated run artifact",
    )
    distinct(run, args.reference)


def scoring_data(run, ref, manifest, before, after):
    from .formal_scoring import score

    result = score(run, ref, manifest, before, after)
    result.update(
        {
            k: manifest[k]
            for k in (
                *b.VERSION,
                "run_id",
                "semantic_digest",
                "bundle_digest",
                "code_sha",
                "clip_id",
                "profile_id",
                "scenario_kind",
            )
        }
    )
    result.update(
        scoring_code_sha=manifest["code_sha"],
        real_video=False,
        generalization_claimed=False,
        acceptance_authority=False,
    )
    return result


def evidence_file(run, relative_path, expected_sha):
    path = run / relative_path
    b.regular(path)
    b.require(digest(path.read_bytes()) == expected_sha, 27, "published evidence digest mismatch")


def publish(run, staging, manifest, stage):
    # A killed process can leave already-renamed files. Only replace our own
    # exact, journaled outputs. A stray file belonging to the caller is preserved.
    pending = run / ".stage-pending.json"
    previous = b.json_file(pending) if pending.exists() else {}
    if previous.get("stage") == manifest["last_stage"]:
        previous = {}
    if previous:
        b.require(
            previous.get("run_id") == manifest["run_id"] and previous.get("stage") == stage,
            12,
            "incompatible interrupted publication",
        )
    products = {}
    for src in staging.iterdir():
        b.require(src.is_file() and not src.is_symlink(), 27, "invalid staged output")
        dest = run / src.name
        if dest.exists() or dest.is_symlink():
            b.regular(dest)
            b.require(
                previous.get("files", {}).get(src.name) == digest(dest.read_bytes()),
                12,
                "unowned or modified output occupies the stage destination",
            )
        products[src.name] = digest(src.read_bytes())
    write_json(pending, {"stage": stage, "run_id": manifest["run_id"], "files": products})
    for name in products:
        (staging / name).replace(run / name)


def stage(args):
    run = args.run
    command = args.command
    with exclusive(run):
        m = b.json_file(run / "run-manifest.json")
        b.require(
            m.get("last_stage") != "verify" and m.get("status") != "SUCCESS",
            12,
            "terminal run is immutable",
        )
        previous = {"export": "repair", "evaluate": "export", "verify": "evaluate"}[command]
        b.require(m.get("last_stage") == previous, 12, "out of order or repeated stage")
        # Terminal partial/failed runs can produce explicitly requested inspection
        # evidence. Their status can never be promoted to a normal handoff.
        diagnostic_mode = m["status"] in ("FAILED", "PARTIAL")
        b.require(
            not diagnostic_mode or args.diagnostic, 21, "use --diagnostic for partial evidence"
        )
        if command in ("evaluate", "verify"):
            validate_output(
                args, run, "metrics.json" if command == "evaluate" else "acceptance-report.json"
            )
        with TemporaryDirectory(dir=run, prefix=".cvr-stage-") as working:
            staging = Path(working)
            try:
                before, after = sealed(run, m)
                status = (
                    m["status"]
                    if diagnostic_mode
                    else {"export": "EXPORTED", "evaluate": "SCORED", "verify": "FAILED"}[command]
                )
                code = 5 if status == "PARTIAL" else 7 if status == "FAILED" else 0
                diagnostic = None
                if command == "export":
                    from .formal_media import export_formal_media

                    media = export_formal_media(
                        staging, before, after, args.encoder, args.ffprobe, fps=Fraction(12)
                    )
                    m["media"] = media
                    m["media_verified"] = media["status"] == "VERIFIED"
                    if not m["media_verified"]:
                        status, code, diagnostic = "FAILED", 7, "CVR-E028"
                elif command == "evaluate":
                    metrics = scoring_data(run, args.reference, m, before, after)
                    write_json(staging / "metrics.json", metrics)
                    m["metrics_sha256"] = digest((staging / "metrics.json").read_bytes())
                    if metrics.get("source_evidence"):
                        m["source_evidence"] = metrics["source_evidence"]
                else:
                    b.require(args.contract == b.CONTRACT, 1, "unsupported verify contract")
                    b.require(args.expected_code_sha == m["code_sha"], 13, "candidate SHA mismatch")
                    evidence_file(run, "metrics.json", m["metrics_sha256"])
                    for name in ("before.mp4", "after.mp4", "comparison.mp4"):
                        item = m["media"]["files"][name]
                        evidence_file(run, name, item["sha256"])
                    metrics = b.json_file(run / "metrics.json")
                    replay = scoring_data(run, args.reference, m, before, after)
                    b.require(replay == metrics, 27, "reference or scoring replay differs")
                    from .formal_media import verify_media

                    media = verify_media(
                        run, before, after, args.encoder, args.ffprobe, fps=Fraction(12)
                    )
                    checks = [
                        {
                            "id": "identity_and_scoring_replay",
                            "status": "MET",
                            "actual": m["code_sha"],
                            "expected": args.expected_code_sha,
                        },
                        {
                            "id": "T7_no_unresolved",
                            "status": "MET" if not m["unresolved_pixels"] else "FAILED",
                            "actual": m["unresolved_pixels"],
                            "expected": 0,
                        },
                        {
                            "id": "full_media_decode",
                            "status": "MET" if media["status"] == "VERIFIED" else "FAILED",
                            "actual": media["status"],
                            "expected": "VERIFIED",
                        },
                        {
                            "id": "G1_contract_freeze",
                            "status": "NOT_RUN",
                            "actual": "DRAFT",
                            "expected": "frozen",
                        },
                        {
                            "id": "independent_CI_holdouts",
                            "status": "NOT_RUN",
                            "expected": "independent CI report",
                        },
                    ]
                    failed = (
                        m["unresolved_pixels"]
                        or media["status"] != "VERIFIED"
                        or metrics.get("quality_failures")
                    )
                    overall = "CHANGES_REQUIRED" if failed else "BLOCKED"
                    report = {
                        **{
                            k: m[k]
                            for k in (
                                *b.VERSION,
                                "code_sha",
                                "run_id",
                                "semantic_digest",
                                "bundle_digest",
                                "scenario_kind",
                                "metrics_sha256",
                            )
                        },
                        "scoring_code_sha": metrics["scoring_code_sha"],
                        "verification_code_sha": code_sha(),
                        "independent_acceptance": False,
                        "real_video": False,
                        "generalization_claimed": False,
                        "overall": overall,
                        "checks": checks,
                        "metric_checks": metrics.get("metrics", {}),
                        "guardrail_checks": metrics.get("guardrails", {}),
                        "quality_failures": metrics.get("quality_failures", []),
                        "missing_checks": metrics.get("missing_checks", []),
                        "source_evidence": m.get("source_evidence"),
                        "media_sha256": {k: v["sha256"] for k, v in m["media"]["files"].items()},
                    }
                    write_json(staging / "acceptance-report.json", report)
                    m["acceptance_report_sha256"] = digest(
                        (staging / "acceptance-report.json").read_bytes()
                    )
                    m["acceptance_status"] = overall
                    status, code = (
                        ("PARTIAL", 5)
                        if m["unresolved_pixels"] and media["status"] == "VERIFIED"
                        else ("FAILED", 7)
                    )
                    diagnostic = "CVR-E021" if status == "PARTIAL" else "CVR-E031"
                publish(run, staging, m, command)
                advance(
                    run, m, command, status, code, diagnostic or ("CVR-E021" if code == 5 else None)
                )
                (run / ".stage-pending.json").unlink(missing_ok=True)
                return code
            except (b.StageError, OSError, ValueError, KeyError, TypeError) as exc:
                code = getattr(exc, "exit_code", 8)
                diagnostic = getattr(exc, "code", "CVR-E030")
                if command == "verify" and not (run / "acceptance-report.json").exists():
                    overall = "BLOCKED" if code == 6 else "CHANGES_REQUIRED"
                    failure = {
                        **b.VERSION,
                        "run_id": m.get("run_id"),
                        "code_sha": m.get("code_sha"),
                        "semantic_digest": m.get("semantic_digest"),
                        "overall": overall,
                        "metrics_sha256": m.get("metrics_sha256"),
                        "independent_acceptance": False,
                        "checks": [
                            {
                                "id": "verify",
                                "status": "FAILED",
                                "actual": diagnostic,
                                "expected": "intact sealed evidence and complete oracle",
                            }
                        ],
                    }
                    write_json(run / "acceptance-report.json", failure)
                    m["acceptance_status"] = overall
                    m["acceptance_report_sha256"] = digest(
                        (run / "acceptance-report.json").read_bytes()
                    )
                advance(run, m, command, "FAILED", code, diagnostic)
                raise
