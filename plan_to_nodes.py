#!/usr/bin/env python3
"""plan_to_nodes.py — extract the execution-manifest from a plan file and emit
node JSON for run_nodes.py. Deterministic, zero-token. Missing/malformed
manifest -> emit nothing (graceful fallback to plain Opus execution)."""
import json, re, sys
from pathlib import Path

ALLOWED_FORBID = {"new_deps"}
ALLOWED_KIND = {"edit", "create"}
# Recognize EVERY fence opening — bare ``` and language-tagged (```python, ```sh, …) — as
# an opening. Recognizing only bare/```json openings desyncs fence pairing on any plan that
# has language-tagged code blocks before its manifest (the tagged opening isn't matched but
# its bare closing ``` is), so the manifest block never gets captured. `[^\n]*` stays on the
# fence line; each block is still filtered by the json.loads + execution-manifest check below.
FENCE_RE = re.compile(r"```[^\n]*\n(.*?)\n```", re.DOTALL)

def extract_manifest(text):
    """Return the execution-manifest list, or [] if none found/parseable."""
    for m in FENCE_RE.finditer(text):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("execution-manifest"), list):
            return data["execution-manifest"]
    return []

def validate_entry(e, idx):
    for key in ("id", "files", "change", "accept"):
        if key not in e:
            raise ValueError(f"entry {idx}: missing required key '{key}'")
    if not isinstance(e["files"], list) or not e["files"]:
        raise ValueError(f"entry {idx} ({e['id']}): 'files' must be a non-empty list")
    kind = e.get("kind", "edit")
    if kind not in ALLOWED_KIND:
        raise ValueError(f"entry {idx} ({e['id']}): kind '{kind}' not in {sorted(ALLOWED_KIND)}")
    if kind == "create" and len(e["files"]) != 1:
        raise ValueError(f"entry {idx} ({e['id']}): create node must list exactly one file")
    bad = set(e.get("forbid", [])) - ALLOWED_FORBID
    if bad:
        raise ValueError(f"entry {idx} ({e['id']}): unknown forbid tokens {sorted(bad)}")

def to_node(e):
    node = {"id": e["id"], "files": e["files"],
            "change": e["change"], "accept": e["accept"]}
    if e.get("forbid"):
        node["forbid"] = e["forbid"]
    if e.get("kind", "edit") != "edit":
        node["kind"] = e["kind"]
    return node

def emit(manifest, out_dir):
    """Validate all entries, then write NN-slug.json for local:true entries in
    manifest order. Returns the filenames written."""
    for idx, e in enumerate(manifest):
        validate_entry(e, idx)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    n = 0
    for e in manifest:
        if not e.get("local", False):
            continue
        n += 1
        fname = f"{n:02d}-{e['id']}.json"
        (out / fname).write_text(json.dumps(to_node(e), indent=1))
        written.append(fname)
    return written

def main():
    if len(sys.argv) != 3:
        print("usage: plan_to_nodes.py <plan.md> <out-dir>")
        sys.exit(2)
    text = Path(sys.argv[1]).read_text()
    manifest = extract_manifest(text)
    if not manifest:
        print("no execution-manifest found — falling back to plain execution (0 nodes)")
        sys.exit(0)
    try:
        written = emit(manifest, sys.argv[2])
    except ValueError as e:
        print(f"FAILURE(bad_manifest): {e}")
        sys.exit(1)
    print(f"wrote {len(written)} node(s): {', '.join(written) or '(none local)'}")
    sys.exit(0)

if __name__ == "__main__":
    main()
