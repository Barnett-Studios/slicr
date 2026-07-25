#!/usr/bin/env python3
"""plan_to_nodes.py — extract the execution-manifest from a plan file and emit
node JSON for run_nodes.py. Deterministic, zero-token. Missing/malformed
manifest -> emit nothing (graceful fallback to plain single-model execution)."""

import json
import re
import sys
from pathlib import Path, PurePosixPath

ALLOWED_FORBID = {"new_deps"}
ALLOWED_KIND = {"edit", "create"}
# The node id becomes a filename (`NN-<id>.json`), so it must be a path-safe slug.
# The charset forbids every path separator AND a leading '.', which together make
# ".." and "." unrepresentable — the traversal is impossible to express, not merely
# caught. Interior dots are harmless once no separator can appear.
#
# It is deliberately not narrower: all 94 distinct node ids observed in production
# validate under it, including uppercase-leading ones (N1-surprising-cochange). A
# lowercase-only pattern would reject real manifests — a worse outage than the
# latent traversal it prevents.
#
# fullmatch, never match: `$` matches *before* a trailing newline, so re.match
# would accept "evil\n".
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ID_MAX_LEN = 100
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
    # Type checks — kept in lockstep with execution-manifest.schema.json so the
    # reference validator and the formal schema agree (the only intentional
    # divergence is unknown-key leniency; the schema is strict there, this is not).
    if not isinstance(e["id"], str) or not e["id"]:
        raise ValueError(f"entry {idx}: 'id' must be a non-empty string")
    if len(e["id"]) > ID_MAX_LEN:
        # Bounded here so an over-long id fails as a bad manifest rather than as an
        # OSError(File name too long) at the write, which main()'s except ValueError
        # would not catch.
        raise ValueError(f"entry {idx}: 'id' exceeds {ID_MAX_LEN} characters ({len(e['id'])})")
    if not ID_RE.fullmatch(e["id"]):
        raise ValueError(
            f"entry {idx}: 'id' {e['id']!r} is not a path-safe slug "
            f"(must match {ID_RE.pattern} — no path separators, no leading dot)"
        )
    if not isinstance(e["change"], str):
        raise ValueError(f"entry {idx} ({e['id']}): 'change' must be a string")
    if not isinstance(e["accept"], str) or not e["accept"]:
        raise ValueError(f"entry {idx} ({e['id']}): 'accept' must be a non-empty string")
    if not isinstance(e["files"], list) or not e["files"]:
        raise ValueError(f"entry {idx} ({e['id']}): 'files' must be a non-empty list")
    if not all(isinstance(f, str) for f in e["files"]):
        raise ValueError(f"entry {idx} ({e['id']}): 'files' entries must all be strings")
    for f in e["files"]:
        # `files` are the editable paths an executor will write. Absolute or
        # escaping entries are out of contract regardless of which executor
        # consumes them — reject at the producer so the failure surfaces at plan
        # time with a clear message, not mid-batch.
        p = PurePosixPath(f)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(
                f"entry {idx} ({e['id']}): 'files' entry {f!r} must be a relative "
                "path inside the repo (no leading '/', no '..' component)"
            )
    if "local" in e and not isinstance(e["local"], bool):
        raise ValueError(f"entry {idx} ({e['id']}): 'local' must be a boolean")
    kind = e.get("kind", "edit")
    if kind not in ALLOWED_KIND:
        raise ValueError(f"entry {idx} ({e['id']}): kind '{kind}' not in {sorted(ALLOWED_KIND)}")
    if kind == "create" and len(e["files"]) != 1:
        raise ValueError(f"entry {idx} ({e['id']}): create node must list exactly one file")
    bad = set(e.get("forbid", [])) - ALLOWED_FORBID
    if bad:
        raise ValueError(f"entry {idx} ({e['id']}): unknown forbid tokens {sorted(bad)}")


def to_node(e):
    node = {"id": e["id"], "files": e["files"], "change": e["change"], "accept": e["accept"]}
    if e.get("forbid"):
        node["forbid"] = e["forbid"]
    if e.get("kind", "edit") != "edit":
        node["kind"] = e["kind"]
    return node


def compute_nodes(manifest):
    """Validate all entries, then return the ordered [(filename, node)] list for local:true
    entries in manifest order. The single source of truth for node identity + numbering,
    shared by file emission and the run-json envelope so both produce identical results."""
    for idx, e in enumerate(manifest):
        validate_entry(e, idx)
    nodes = []
    n = 0
    for e in manifest:
        if not e.get("local", False):
            continue
        n += 1
        nodes.append((f"{n:02d}-{e['id']}.json", to_node(e)))
    return nodes


def _safe_target(out, fname):
    """Resolve `fname` under `out` and refuse anything that escapes it.

    Defence in depth behind the id slug rule: this is the guard that still holds if
    the filename format changes or the slug check regresses, and the only one that
    covers a symlink pre-planted inside `out` (resolve() follows symlinks;
    os.path.normpath would not, and would compute a contained-looking path whose
    write still lands outside).

    Containment is tested with Path.parents membership, not str.startswith, which
    would wrongly accept '/out2/x' as inside '/out'.
    """
    out = Path(out)
    if PurePosixPath(fname).is_absolute() or ".." in PurePosixPath(fname).parts:
        raise ValueError(f"unsafe node filename {fname!r} (absolute or contains '..') — refused")
    out_resolved = out.resolve()
    target = (out_resolved / fname).resolve()
    if out_resolved not in target.parents:
        raise ValueError(
            f"node filename {fname!r} resolves to {target}, outside {out_resolved} — refused"
        )
    return target


def emit(manifest, out_dir):
    """Validate all entries, then write NN-slug.json for local:true entries in
    manifest order. Returns the filenames written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for fname, node in compute_nodes(manifest):
        _safe_target(out, fname).write_text(json.dumps(node, indent=1))
        written.append(fname)
    return written


def run_json(request_text):
    """ADR-0052 response-envelope mode: a JSON request `{plan_text}` in, a
    `{schema_version, status, body}` envelope string out. body.nodes is the ordered
    [{filename, node}] list a consumer writes verbatim — byte-identical to emit()."""
    try:
        req = json.loads(request_text)
    except ValueError as e:
        return _error_envelope(f"invalid run request JSON: {e}")
    plan_text = req.get("plan_text", "")
    manifest = extract_manifest(plan_text)
    try:
        nodes = compute_nodes(manifest)
    except ValueError as e:
        return _error_envelope(f"bad_manifest: {e}")
    body = {"nodes": [{"filename": fname, "node": node} for fname, node in nodes]}
    return json.dumps({"schema_version": "1", "status": "ok", "body": body})


def _error_envelope(message):
    return json.dumps({"schema_version": "1", "status": "error", "body": {"message": message}})


def main():
    # `run-json`: the ADR-0052 envelope surface (request on stdin, envelope on stdout,
    # exit 0 — the decision, including a bad manifest, is in the envelope) consumed by
    # the framework's execute-plan wrapper via `docker run`.
    if len(sys.argv) == 2 and sys.argv[1] == "run-json":
        print(run_json(sys.stdin.read()))
        sys.exit(0)

    if len(sys.argv) != 3:
        print("usage: plan_to_nodes.py <plan.md> <out-dir>   |   plan_to_nodes.py run-json")
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
