"""Schema ↔ reference-validator consistency.

execution-manifest.schema.json is the formal contract; plan_to_nodes.py is the
reference producer/validator. This test proves they agree on the cases both cover:
a well-formed manifest validates under both, and each malformed case the reference
validator rejects is also rejected by the schema. (The reference validator is
lenient on *unknown entry keys* — it ignores them — while the schema is strict;
that divergence is documented in CONTRACT.md and not exercised here.)"""

import importlib.util
import json
import pathlib

import jsonschema
import pytest

HERE = pathlib.Path(__file__).parent
SCHEMA = json.loads((HERE / "schema" / "execution-manifest.schema.json").read_text())

spec = importlib.util.spec_from_file_location("plan_to_nodes", HERE / "plan_to_nodes.py")
ptn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ptn)

_validator = jsonschema.Draft202012Validator(SCHEMA)


def schema_ok(entries):
    """True iff {"execution-manifest": entries} validates against the schema."""
    return _validator.is_valid({"execution-manifest": entries})


def validator_ok(entries, tmp_path):
    """True iff the reference validator accepts every entry."""
    try:
        ptn.emit(entries, tmp_path)
        return True
    except ValueError:
        return False


GOOD = [
    {
        "id": "compact-path",
        "files": ["util.py"],
        "change": "Implement compact_path.",
        "accept": "true",
        "forbid": ["new_deps"],
        "local": True,
        "kind": "edit",
    },
    {
        "id": "cli-entry",
        "files": ["cli.py"],
        "change": "Create cli.py main().",
        "accept": "true",
        "local": True,
        "kind": "create",
    },
    {
        "id": "wire-config",
        "files": ["config.py", "app.py"],
        "change": "Cross-cutting.",
        "accept": "true",
        "local": False,
    },
]

BAD_CASES = [
    (
        "unknown_forbid",
        [
            {
                "id": "x",
                "files": ["a.py"],
                "change": "c",
                "accept": "true",
                "forbid": ["network"],
                "local": True,
            }
        ],
    ),
    (
        "create_multifile",
        [
            {
                "id": "x",
                "files": ["a.py", "b.py"],
                "change": "c",
                "accept": "true",
                "kind": "create",
                "local": True,
            }
        ],
    ),
    ("missing_required", [{"id": "x", "files": ["a.py"], "local": True}]),
    ("empty_files", [{"id": "x", "files": [], "change": "c", "accept": "true"}]),
    # Type-strictness cases — the validator was tightened to match the schema on these
    # so a manifest the reference validator accepts is never rejected by an independent
    # schema-validating consumer (and vice versa). Only unknown-key leniency diverges.
    ("empty_id", [{"id": "", "files": ["a.py"], "change": "c", "accept": "true"}]),
    ("empty_accept", [{"id": "x", "files": ["a.py"], "change": "c", "accept": ""}]),
    (
        "non_bool_local",
        [{"id": "x", "files": ["a.py"], "change": "c", "accept": "true", "local": "yes"}],
    ),
    ("non_string_file_item", [{"id": "x", "files": [1, 2], "change": "c", "accept": "true"}]),
    ("non_string_change", [{"id": "x", "files": ["a.py"], "change": 5, "accept": "true"}]),
]


def test_schema_is_valid_draft_2020_12():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_good_manifest_accepted_by_both(tmp_path):
    assert schema_ok(GOOD)
    assert validator_ok(GOOD, tmp_path)


@pytest.mark.parametrize("name,entries", BAD_CASES, ids=[c[0] for c in BAD_CASES])
def test_bad_manifest_rejected_by_both(name, entries, tmp_path):
    assert not schema_ok(entries), f"schema wrongly accepted {name}"
    assert not validator_ok(entries, tmp_path), f"reference validator wrongly accepted {name}"


# ── RC-1: the schema carries the slug rule too (slicr#4, ADR-0056) ──────────────
# The schema is the published contract; the reference validator is the authority
# where the two dialects disagree (ECMA-262 `$` vs Python's, on a trailing
# newline). Both must reject a traversal id, or an external producer reading only
# the schema would emit manifests this repo refuses.

def _entry(**kw):
    e = {"id": "ok", "files": ["a.py"], "change": "c", "accept": "true", "local": True}
    e.update(kw)
    return e


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", ".hidden", "..", "-lead", "a" * 250])
def test_rc1_schema_rejects_unsafe_id(bad_id):
    assert not schema_ok([_entry(id=bad_id)])


@pytest.mark.parametrize("good_id", [
    "N1-surprising-cochange", "01-compact-path", "with_underscore", "with.dot", "a",
])
def test_rc1_schema_accepts_real_ids(good_id):
    assert schema_ok([_entry(id=good_id)])


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", ".hidden", "..", "-lead", "a" * 250])
def test_rc1_schema_and_validator_agree_on_unsafe_id(bad_id, tmp_path):
    # Lockstep: neither surface may be the weaker one.
    assert not schema_ok([_entry(id=bad_id)])
    with pytest.raises(ValueError):
        ptn.validate_entry(_entry(id=bad_id), 0)
