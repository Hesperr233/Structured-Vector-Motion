"""Publish acceptance from the independent CI process after its checks pass."""

import json
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile


def _json_bytes(data: dict) -> bytes:
    return (
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _replace_json(path: Path, data: bytes) -> None:
    with NamedTemporaryFile(dir=path.parent, prefix=".verify-", delete=False) as handle:
        staged = Path(handle.name)
        handle.write(data)
    try:
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def publish_verified_run(run: Path, case: dict, report: dict) -> None:
    """Seal a private CI run. The state changes only after the report is present."""
    if report["qualification"] != "FROZEN" or case["overall"] != "PASS":
        raise ValueError("Only a passing independent frozen case can be published")
    if not report["freeze_sha256"] or not report["holdout_seal_sha256"]:
        raise ValueError("Frozen acceptance requires matching contract evidence")

    manifest_path = run / "run-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    identity = ("run_id", "semantic_digest", "metrics_sha256", "clip_id", "profile_id")
    if (
        manifest["last_stage"] != "evaluate"
        or manifest["status"] != "SCORED"
        or manifest["unresolved_pixels"] != 0
        or manifest["code_sha"] != report["implementation_sha"]
        or not all(manifest[key] == case[key] for key in identity)
    ):
        raise ValueError("Run no longer matches the independently verified scored evidence")
    media = {name: item["sha256"] for name, item in case["media"]["files"].items()}
    if (
        case["media"]["status"] != "PASS"
        or set(media) != {"before.mp4", "after.mp4", "comparison.mp4"}
        or media != {name: item["sha256"] for name, item in manifest["media"]["files"].items()}
    ):
        raise ValueError("Run media no longer matches the independently decoded evidence")

    output = {
        **{key: manifest[key] for key in identity},
        "overall": "PASS",
        "independent_acceptance": True,
        "contract_version": manifest["contract_version"],
        "draft_revision": manifest["draft_revision"],
        "code_sha": report["implementation_sha"],
        "ci_sha": report["ci_sha"],
        "contract_sha": report["contract_sha"],
        "freeze_sha256": report["freeze_sha256"],
        "holdout_seal_sha256": report["holdout_seal_sha256"],
        "reference_manifest_sha256": case["reference_manifest_sha256"],
        "checks": case["checks"],
        "metrics": case["metrics"],
        "guardrails": case["guardrails"],
        "media_sha256": media,
    }
    encoded = _json_bytes(output)
    acceptance_path = run / "acceptance-report.json"
    if acceptance_path.is_symlink():
        raise ValueError("A linked acceptance report occupies the stage destination")
    if acceptance_path.exists():
        if acceptance_path.read_bytes() != encoded:
            raise ValueError("A different acceptance report occupies the stage destination")
    else:
        _replace_json(acceptance_path, encoded)

    manifest["stages"]["verify"] = {"status": "SUCCESS", "exit_code": 0, "diagnostic": None}
    manifest.update(
        status="SUCCESS",
        last_stage="verify",
        exit_code=0,
        diagnostic=None,
        acceptance_status="PASS",
        contract_state="FROZEN",
        acceptance_report_sha256="sha256:" + sha256(encoded).hexdigest(),
    )
    _replace_json(manifest_path, _json_bytes(manifest))
