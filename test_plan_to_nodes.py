# test_plan_to_nodes.py
import importlib.util, pathlib, json
spec = importlib.util.spec_from_file_location(
    "plan_to_nodes", pathlib.Path(__file__).parent / "plan_to_nodes.py")
ptn = importlib.util.module_from_spec(spec); spec.loader.exec_module(ptn)
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

PLAN_WITH_CODE = '''# Plan with language-tagged code blocks before the manifest
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
'''

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
    bad = [{"id": "x", "files": ["a.py"], "change": "c", "accept": "true",
            "forbid": ["network"], "local": True}]
    with pytest.raises(ValueError):
        ptn.emit(bad, tmp_path)

def test_create_must_be_single_file(tmp_path):
    bad = [{"id": "x", "files": ["a.py", "b.py"], "change": "c",
            "accept": "true", "kind": "create", "local": True}]
    with pytest.raises(ValueError):
        ptn.emit(bad, tmp_path)

def test_missing_required_key_rejected(tmp_path):
    bad = [{"id": "x", "files": ["a.py"], "local": True}]  # no change/accept
    with pytest.raises(ValueError):
        ptn.emit(bad, tmp_path)
