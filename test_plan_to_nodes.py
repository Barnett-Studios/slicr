# test_plan_to_nodes.py
import importlib.util
import json
import pathlib
import subprocess
import sys

spec = importlib.util.spec_from_file_location(
    "plan_to_nodes", pathlib.Path(__file__).parent / "plan_to_nodes.py"
)
ptn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ptn)
import pytest

PLAN = """# Some Plan

Prose here.

```json
{
  "execution-manifest": [
    {"id": "compact-path", "files": ["util.py"],
     "change": "Implement compact_path.",
     "accept": "python3 -m unittest -q test_util.TestUtil.test_compact_path 2>&1 | grep -qE '^OK'",
     "forbid": ["new_deps"], "local": true, "kind": "edit"},
    {"id": "cli-entry", "files": ["cli.py"],
     "change": "Create cli.py main().",
     "accept": "python3 -m unittest -q test_cli.TestCli.test_main 2>&1 | grep -qE '^OK'",
     "local": true, "kind": "create"},
    {"id": "wire-config", "files": ["config.py", "app.py"],
     "change": "Cross-cutting wiring.", "accept": "true", "local": false}
  ]
}
```

More prose.
"""


def test_extracts_three_entries():
    assert len(ptn.extract_manifest(PLAN)) == 3


def test_no_manifest_returns_empty():
    assert ptn.extract_manifest("# Plain plan, no manifest") == []


PLAN_WITH_CODE = """# Plan with language-tagged code blocks before the manifest
```python
def f():
    return 1
```
Prose.
```sh
echo hi
```
More prose.
```json
{"execution-manifest": [
  {"id": "a", "files": ["x.py"], "change": "c", "accept": "true", "local": true}
]}
```
"""


def test_extracts_manifest_after_language_tagged_blocks():
    # Regression: ```python / ```sh blocks before ```json must not desync fence pairing.
    m = ptn.extract_manifest(PLAN_WITH_CODE)
    assert len(m) == 1 and m[0]["id"] == "a"


def test_non_manifest_json_block_ignored():
    assert ptn.extract_manifest('```json\n{"foo": 1}\n```') == []


def test_emits_only_local_nodes_in_order(tmp_path):
    written = ptn.emit(ptn.extract_manifest(PLAN), tmp_path)
    assert written == ["01-compact-path.json", "02-cli-entry.json"]
    create = json.loads((tmp_path / "02-cli-entry.json").read_text())
    assert create["kind"] == "create"
    edit = json.loads((tmp_path / "01-compact-path.json").read_text())
    assert "local" not in edit
    assert "kind" not in edit  # edit is default, omitted


def test_unknown_forbid_token_rejected(tmp_path):
    bad = [
        {
            "id": "x",
            "files": ["a.py"],
            "change": "c",
            "accept": "true",
            "forbid": ["network"],
            "local": True,
        }
    ]
    with pytest.raises(ValueError):
        ptn.emit(bad, tmp_path)


def test_create_must_be_single_file(tmp_path):
    bad = [
        {
            "id": "x",
            "files": ["a.py", "b.py"],
            "change": "c",
            "accept": "true",
            "kind": "create",
            "local": True,
        }
    ]
    with pytest.raises(ValueError):
        ptn.emit(bad, tmp_path)


def test_missing_required_key_rejected(tmp_path):
    bad = [{"id": "x", "files": ["a.py"], "local": True}]  # no change/accept
    with pytest.raises(ValueError):
        ptn.emit(bad, tmp_path)


# ── run-json envelope surface (ADR-0052/0055 consume-back) ──────────────────────


def test_run_json_ok_envelope_matches_emit(tmp_path):
    out = json.loads(ptn.run_json(json.dumps({"plan_text": PLAN})))
    assert out["schema_version"] == "1"
    assert out["status"] == "ok"
    nodes = out["body"]["nodes"]
    # Same filenames/order as emit(), and each node byte-identical to the written file.
    written = ptn.emit(ptn.extract_manifest(PLAN), tmp_path)
    assert [n["filename"] for n in nodes] == written
    for n in nodes:
        assert n["node"] == json.loads((tmp_path / n["filename"]).read_text())


def test_run_json_invalid_json_is_rejected_envelope():
    out = json.loads(ptn.run_json("not json"))
    assert out["status"] == "rejected"


def test_run_json_bad_manifest_is_rejected_envelope():
    bad_plan = (
        '```json\n{"execution-manifest": '
        '[{"id": "x", "files": ["a.py"], "local": true}]}\n```'  # no change/accept
    )
    out = json.loads(ptn.run_json(json.dumps({"plan_text": bad_plan})))
    assert out["status"] == "rejected"


def test_run_json_no_manifest_is_ok_with_zero_nodes():
    out = json.loads(ptn.run_json(json.dumps({"plan_text": "# plain plan"})))
    assert out["status"] == "ok"
    assert out["body"]["nodes"] == []


# ── slicr#3 + #10: the three outcomes are distinguishable ───────────────────────
#
# One subject: slicr saying which of its states it is in. #3 is the CLI/extraction half
# (malformed manifest read as absent), #10 the envelope half (a refusal read as a failure).

MALFORMED = """# Plan

```json
{ "execution-manifest": [
  {"id": "a", "files": ["x.py"], "change": "c", "accept": "true", "local": true},
] }
```
"""


def test_malformed_manifest_raises_rather_than_reading_as_absent():
    """slicr#3. `[]` here is the defect itself: absent and unusable are different facts."""
    with pytest.raises(ptn.ManifestRefused, match="not valid JSON"):
        ptn.extract_manifest(MALFORMED)


def test_malformed_manifest_names_its_line():
    with pytest.raises(ptn.ManifestRefused, match="line 4"):
        ptn.extract_manifest(MALFORMED)


def test_manifest_key_holding_a_non_list_is_refused():
    """The same silent-degradation shape one type over: parses, key present, not a list."""
    with pytest.raises(ptn.ManifestRefused, match="dict"):
        ptn.extract_manifest('```json\n{"execution-manifest": {"a": 1}}\n```')


def test_malformed_block_without_the_key_is_still_skipped():
    """The control. Broadening the refusal to every unparseable block would hard-fail any
    plan that quotes malformed JSON in prose — a worse outage than the silence it fixes."""
    plan = '```json\n{"foo": [1,]}\n```\n```json\n{"execution-manifest": []}\n```'
    assert ptn.extract_manifest(plan) == []


def test_run_json_malformed_manifest_is_rejected_not_ok():
    """The worse half of slicr#3, unmentioned in the ticket: run-json answered `ok` with
    zero nodes, so the consumer recorded a successful zero-offload rather than a failure."""
    out = json.loads(ptn.run_json(json.dumps({"plan_text": MALFORMED})))
    assert out["status"] == "rejected"
    assert "line 4" in out["body"]["message"]


def test_refusal_and_failure_are_distinguishable_without_reading_the_message():
    """slicr#10's acceptance criterion, asserted as the pair. Either status alone passes
    while the other is misfiled; only the inequality pins that they are two states."""
    refused = json.loads(ptn.run_json(json.dumps({"plan_text": MALFORMED})))
    failed = json.loads(ptn._error_envelope("boom"))
    assert refused["status"] == "rejected"
    assert failed["status"] == "error"
    assert refused["status"] != failed["status"]


def test_every_validate_entry_refusal_is_rejected():
    """Enumerated from validate_entry's own raise sites (the specification side), not from
    whichever refusals happened to be handy — a sample would pass while a whole arm of the
    validator still emitted `error`."""
    bad_entries = [
        {"id": "x", "files": ["a.py"], "local": True},  # missing key
        {"id": "../evil", "files": ["a.py"], "change": "c", "accept": "true"},  # non-slug id
        {"id": "x", "files": ["/etc/passwd"], "change": "c", "accept": "true"},  # unsafe file
        {"id": "x", "files": ["a.py"], "change": "c", "accept": "true", "forbid": ["net"]},
        {"id": "x", "files": ["a.py"], "change": "c", "accept": "true", "kind": "conjure"},
        {"id": "x", "files": ["a.py", "b.py"], "change": "c", "accept": "true", "kind": "create"},
        {"id": "x", "files": ["a.py"], "change": "c", "accept": "true", "local": "yes"},
    ]
    for e in bad_entries:
        plan = "```json\n" + json.dumps({"execution-manifest": [e]}) + "\n```"
        out = json.loads(ptn.run_json(json.dumps({"plan_text": plan})))
        assert out["status"] == "rejected", f"{e} produced {out}"
        assert out["body"]["message"].startswith("bad_manifest: "), out


# ── RC-1: node identity is a path-safe slug (slicr#4, ADR-0056) ─────────────────
# These assert ValueError SPECIFICALLY, never bare `raises`. On unfixed code a
# traversal id already raises FileNotFoundError (the "NN-" prefix binds to the
# first path component and write_text does not create parents), so a test that
# accepted any exception would be a false green.


def _entry(**kw):
    e = {"id": "ok", "files": ["a.py"], "change": "c", "accept": "true", "local": True}
    e.update(kw)
    return e


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../evil",  # classic traversal
        "../evil",
        "x/../../../../tmp/evil",  # traversal behind a leading segment
        "a/b",  # any separator at all
        "a\\b",  # windows separator
        ".hidden",  # leading dot -> makes ".." unrepresentable
        "..",
        ".",
        "-leading-dash",
        "",
    ],
)
def test_rc1_id_must_be_a_slug(bad_id, tmp_path):
    with pytest.raises(ValueError):
        ptn.emit([_entry(id=bad_id)], tmp_path / "out")


def test_rc1_id_absolute_rejected(tmp_path):
    with pytest.raises(ValueError):
        ptn.emit([_entry(id="/tmp/evil")], tmp_path / "out")


def test_rc1_id_trailing_newline_rejected(tmp_path):
    # Pins re.fullmatch over re.match: `$` matches before a trailing newline, so
    # re.match("[A-Za-z0-9][...]*$", "evil\n") succeeds and would let it through.
    with pytest.raises(ValueError):
        ptn.emit([_entry(id="evil\n")], tmp_path / "out")


def test_rc1_id_length_bounded(tmp_path):
    # Unbounded, this reaches the filesystem and raises OSError(File name too long),
    # which main()'s `except ValueError` does not catch -> traceback, not
    # FAILURE(bad_manifest). The bound keeps the failure in the declared class.
    with pytest.raises(ValueError):
        ptn.emit([_entry(id="a" * 250)], tmp_path / "out")


@pytest.mark.parametrize("bad_file", ["../../etc/passwd", "/etc/passwd", "a/../../b"])
def test_rc1_files_traversal_rejected(bad_file, tmp_path):
    with pytest.raises(ValueError):
        ptn.emit([_entry(files=[bad_file])], tmp_path / "out")


def test_rc1_no_file_written_outside_out_dir(tmp_path):
    # The security property itself, asserted on the filesystem rather than on the
    # exception: nothing lands outside out/ regardless of how the write fails.
    out = tmp_path / "out"
    canary = tmp_path / "evil.json"
    with pytest.raises(ValueError):
        ptn.emit([_entry(id="../evil")], out)
    assert not canary.exists()
    assert list(out.glob("**/*")) == [] or not out.exists()


def test_rc1_symlink_in_out_dir_cannot_escape(tmp_path):
    # Pins Path.resolve() over os.path.normpath: normpath would compute a target
    # that looks contained while the write follows the symlink out of the tree.
    out = tmp_path / "out"
    out.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (out / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        ptn._safe_target(out, "link/escaped.json")
    assert not (outside / "escaped.json").exists()


# Compatibility floor. A pattern that rejects real ids is a worse outage than the
# latent traversal it prevents. Shapes below are drawn from the 94 distinct node ids
# observed in production; the uppercase-leading ones are real and are exactly what
# the originally-proposed ^[a-z0-9][a-z0-9-]*$ would have broken. (Shape-covering
# sample rather than all 94 verbatim — this is a public repo and the internal node
# names carry no test value beyond their character shapes.)
REAL_ID_SHAPES = [
    "N1-surprising-cochange",
    "N2-risk-percentile",
    "N7-risk-provenance-terms",
    "01-compact-path",
    "go-05-split-join-ext-roundtrip",
    "cpp-bowling",
    "emit-meta-yaml",
    "classify-local-error",
    "a",
    "A",
    "0",
    "with_underscore",
    "with.dot",
    "a" * 100,
]


@pytest.mark.parametrize("good_id", REAL_ID_SHAPES)
def test_rc1_real_production_ids_still_valid(good_id, tmp_path):
    written = ptn.emit([_entry(id=good_id)], tmp_path / "out")
    assert written == [f"01-{good_id}.json"]


# ── RC-1 follow-up: `files` must not accept a tilde-expanding path (review) ──────
#
# `is_absolute()` and a `..` component are the two ways a path *looks* like it leaves
# the repo. A leading `~` is a third: PurePosixPath treats "~/.ssh/authorized_keys" as
# a clean relative path, but any consumer that calls expanduser() — or hands the string
# to a shell, where tilde expansion happens at word start — resolves it to an absolute
# path outside the repo entirely.
#
# The check is `startswith("~")` rather than a parts inspection because that is exactly
# what expands: `os.path.expanduser` and shell tilde expansion both trigger only at
# position 0. `./~/x` and `backup~` do NOT expand and stay legal — over-rejecting a
# legitimate emacs backup file would be its own bug.


@pytest.mark.parametrize("bad_file", ["~/.ssh/authorized_keys", "~root/.ssh/id_rsa", "~"])
def test_rc1_files_tilde_rejected(bad_file, tmp_path):
    with pytest.raises(ValueError, match="files"):
        ptn.emit([_entry(files=[bad_file])], tmp_path)


@pytest.mark.parametrize("ok_file", ["./~/x", "backup~", "src/~notexpanded", "a~b/c.py"])
def test_rc1_files_non_expanding_tilde_still_allowed(ok_file, tmp_path):
    """Only a LEADING tilde expands. Rejecting the rest would break real filenames."""
    written = ptn.emit([_entry(files=[ok_file])], tmp_path)
    assert len(written) == 1


# ── slicr#3 + #10, through the front doors ─────────────────────────────────────
#
# The library-level tests above pin the functions. These run the script, because the
# defect was in what each front door DID with the answer: the same `[]` meant "nothing to
# do, exit 0" on one and "successful zero-offload" on the other.

SCRIPT = str(pathlib.Path(__file__).parent / "plan_to_nodes.py")


def _run(args, stdin=""):
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        input=stdin,
        capture_output=True,
        text=True,
    )


def test_cli_malformed_manifest_exits_nonzero(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(MALFORMED)
    r = _run([str(plan), str(tmp_path / "out")])
    assert r.returncode != 0, r.stdout
    assert "FAILURE(bad_manifest)" in r.stdout
    assert str(plan) in r.stdout  # the ticket asks for path + message


def test_cli_no_manifest_still_falls_back_gracefully(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plain plan, no manifest\n")
    out = tmp_path / "out"
    r = _run([str(plan), str(out)])
    assert r.returncode == 0, r.stderr
    assert "no execution-manifest found" in r.stdout
    assert not out.exists() or list(out.glob("*.json")) == []


def test_run_json_internal_failure_still_emits_an_error_envelope():
    """slicr#10's second criterion, and the only producer of `error`. A non-string
    plan_text used to escape as a traceback with NO envelope at all — a consumer told to
    branch on `status` got empty stdout and nothing to branch on."""
    r = _run(["run-json"], stdin=json.dumps({"plan_text": 42}))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["status"] == "error"
    assert out["body"]["message"].startswith("internal failure: TypeError")


def test_rc1_tilde_would_have_escaped_the_repo():
    """Pins WHY this is rejected: the string really does resolve outside the repo."""
    import os.path

    assert os.path.isabs(os.path.expanduser("~/.ssh/authorized_keys"))
    assert not os.path.isabs(os.path.expanduser("./~/x")), "control: this one is inert"
