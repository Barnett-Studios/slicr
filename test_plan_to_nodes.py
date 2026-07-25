# test_plan_to_nodes.py
import importlib.util
import json
import pathlib

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


def test_run_json_invalid_json_is_error_envelope():
    out = json.loads(ptn.run_json("not json"))
    assert out["status"] == "error"


def test_run_json_bad_manifest_is_error_envelope():
    bad_plan = (
        '```json\n{"execution-manifest": '
        '[{"id": "x", "files": ["a.py"], "local": true}]}\n```'  # no change/accept
    )
    out = json.loads(ptn.run_json(json.dumps({"plan_text": bad_plan})))
    assert out["status"] == "error"


def test_run_json_no_manifest_is_ok_with_zero_nodes():
    out = json.loads(ptn.run_json(json.dumps({"plan_text": "# plain plan"})))
    assert out["status"] == "ok"
    assert out["body"]["nodes"] == []


# ── RC-1: node identity is a path-safe slug (slicr#4, ADR-0056) ─────────────────
# These assert ValueError SPECIFICALLY, never bare `raises`. On unfixed code a
# traversal id already raises FileNotFoundError (the "NN-" prefix binds to the
# first path component and write_text does not create parents), so a test that
# accepted any exception would be a false green.

def _entry(**kw):
    e = {"id": "ok", "files": ["a.py"], "change": "c", "accept": "true", "local": True}
    e.update(kw)
    return e


@pytest.mark.parametrize("bad_id", [
    "../../evil",              # classic traversal
    "../evil",
    "x/../../../../tmp/evil",  # traversal behind a leading segment
    "a/b",                     # any separator at all
    "a\\b",                    # windows separator
    ".hidden",                 # leading dot -> makes ".." unrepresentable
    "..",
    ".",
    "-leading-dash",
    "",
])
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
    "N1-surprising-cochange", "N2-risk-percentile", "N7-risk-provenance-terms",
    "01-compact-path", "go-05-split-join-ext-roundtrip", "cpp-bowling",
    "emit-meta-yaml", "classify-local-error", "a", "A", "0",
    "with_underscore", "with.dot", "a" * 100,
]


@pytest.mark.parametrize("good_id", REAL_ID_SHAPES)
def test_rc1_real_production_ids_still_valid(good_id, tmp_path):
    written = ptn.emit([_entry(id=good_id)], tmp_path / "out")
    assert written == [f"01-{good_id}.json"]
