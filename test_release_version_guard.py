"""The VERSION-agreement guard in .github/workflows/release.yml (#27).

CONTRACT.md states that VERSION, the git tag and the published image tag are the same number
"by construction". Nothing constructed it until that guard existed, so this file exists to keep
the guard honest rather than to keep the workflow parsing.

The shell body is read OUT of the workflow rather than copied here. A copy would pass while the
workflow drifted — which is the failure this whole issue is about.
"""
import os
import pathlib
import subprocess
import tempfile

import pytest
import yaml

WORKFLOW = pathlib.Path(__file__).parent / ".github" / "workflows" / "release.yml"


def guard_body():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["image"]["steps"]
    step = next((s for s in steps if s.get("name", "").startswith("VERSION must agree")), None)
    assert step is not None, "the VERSION-agreement step is gone from release.yml"
    return step["run"]


def run_guard(version_file, resolved):
    with tempfile.TemporaryDirectory() as d:
        if version_file is not None:
            pathlib.Path(d, "VERSION").write_text(version_file)
        return subprocess.run(
            ["bash", "-c", guard_body()], cwd=d, capture_output=True, text=True,
            env={**os.environ, "RESOLVED": resolved},
        ).returncode


def test_the_guard_runs_before_anything_is_pushed():
    """A mismatch must cost a failed workflow, not a published image to un-publish."""
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["image"]["steps"]
    names = [s.get("name", "") for s in steps]
    guard = next(i for i, n in enumerate(names) if n.startswith("VERSION must agree"))
    push = next(i for i, n in enumerate(names) if n.startswith("Build and push"))
    assert guard < push, f"guard at {guard} must precede the push at {push}"


def test_the_guard_reads_the_resolved_reference_not_the_raw_tag():
    """dotclaude#63: a guard that strips the `v` itself compares matching numbers while the
    push uses the raw tag — the two disagree about what "the version" is and both pass. The
    guard must consume the same value metadata-action resolved for the push."""
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["image"]["steps"]
    step = next(s for s in steps if s.get("name", "").startswith("VERSION must agree"))
    assert "steps.meta.outputs.version" in str(step.get("env", {}))
    assert "github.ref_name" not in step["run"], "must not re-derive the version itself"


def test_agreement_passes():
    assert run_guard("0.3.0", "0.3.0") == 0


def test_whitespace_in_the_version_file_is_tolerated():
    assert run_guard(" 0.3.0\n", "0.3.0") == 0


def test_a_mismatch_is_refused():
    assert run_guard("0.3.0", "0.2.0") == 1


@pytest.mark.parametrize("version_file", [None, "", "   \n"])
def test_an_unusable_version_file_is_refused_not_skipped(version_file):
    """Missing or blank must fail, never pass quietly — a guard that no-ops when its input is
    absent is the control-reports-success-without-running class this repo keeps finding."""
    assert run_guard(version_file, "0.3.0") == 1


def test_an_unresolved_reference_is_refused():
    """If metadata-action produced nothing, comparing against "" would make every VERSION
    disagree — or, with a naive check, make every VERSION agree. Refuse instead."""
    assert run_guard("0.3.0", "") == 1
