#!/usr/bin/env python3
"""plan_to_nodes.py — extract the execution-manifest from a plan file and emit
node JSON for run_nodes.py. Deterministic, zero-token.

Three outcomes, deliberately distinguishable (slicr#3, slicr#10):

  * no manifest       -> emit nothing, exit 0 / `ok` with 0 nodes. Graceful fallback
                         to plain single-model execution; the plan never asked for one.
  * manifest refused  -> exit 1 / `rejected`. slicr did its job and the answer is "this
                         input is not usable". A consumer must FAIL, not fall open —
                         falling open past a refusal reruns the rejected input elsewhere,
                         which is a bypass of the guard that just fired.
  * slicr failed      -> `error`. Nothing was learned about the input; fall open.

"Malformed" used to be folded into the first bucket, so one JSON typo in a plan produced
a benign message, a success exit, and zero offloaded nodes — indistinguishable from a plan
that never had a manifest."""

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
# The shape emit() writes, and therefore the only shape it will delete when pruning a
# previous run's output (slicr#5). Kept in lockstep with the f-string in compute_nodes:
# `\d+` rather than `\d\d` because the counter is zero-padded to two, not capped at two.
NODE_FILE_RE = re.compile(r"\d+-[A-Za-z0-9][A-Za-z0-9._-]*\.json")
# Recognize EVERY fence opening — bare ``` and language-tagged (```python, ```sh, …) — as
# an opening. Recognizing only bare/```json openings desyncs fence pairing on any plan that
# has language-tagged code blocks before its manifest (the tagged opening isn't matched but
# its bare closing ``` is), so the manifest block never gets captured. `[^\n]*` stays on the
# fence line; each block is still filtered by the json.loads + execution-manifest check below.
FENCE_RE = re.compile(r"```[^\n]*\n(.*?)\n```", re.DOTALL)
# The discriminator between "a block that was MEANT to be a manifest" and "some other
# code block". It has to be a substring test on the raw text: a block that will not parse
# cannot be inspected for the key structurally, which is the whole reason the malformed
# case was indistinguishable from the absent one. Quoted, so it matches the JSON key and
# not prose mentioning the word.
MANIFEST_KEY = '"execution-manifest"'


class ManifestRefused(ValueError):
    """The plan carried something meant to be a manifest, and it is not usable.

    Distinct from "no manifest here", which is not an error at all. Subclasses ValueError
    so every existing `except ValueError` around this module still catches it — the new
    class narrows a refusal, it does not open a hole in an old handler.
    """


def extract_manifest(text):
    """Return the execution-manifest list, or [] if the plan carries no manifest.

    Raises ManifestRefused if a fenced block declares `execution-manifest` but will not
    parse, or parses to something that is not a list. A malformed block that does NOT
    declare the key is still skipped silently — an unrelated broken JSON snippet in a plan
    document is not slicr's business.
    """
    for m in FENCE_RE.finditer(text):
        block = m.group(1)
        declares_manifest = MANIFEST_KEY in block
        line = text.count("\n", 0, m.start(1)) + 1
        try:
            data = json.loads(block)
        except ValueError as e:
            if declares_manifest:
                raise ManifestRefused(
                    f"bad_manifest: fenced block at line {line} declares "
                    f"{MANIFEST_KEY} but is not valid JSON: {e}"
                ) from e
            continue
        if isinstance(data, dict) and "execution-manifest" in data:
            value = data["execution-manifest"]
            if not isinstance(value, list):
                raise ManifestRefused(
                    f"bad_manifest: fenced block at line {line} declares "
                    f"{MANIFEST_KEY} as {type(value).__name__} — must be a list"
                )
            return value
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
        # A leading `~` is the third way out. PurePosixPath sees a clean relative path,
        # but any consumer that calls expanduser() — or hands the string to a shell,
        # where tilde expansion also fires only at word start — resolves it to an
        # absolute path outside the repo. `startswith` matches that expansion rule
        # exactly, so `backup~` and `./~/x`, which do not expand, stay legal.
        if p.is_absolute() or ".." in p.parts or f.startswith("~"):
            raise ValueError(
                f"entry {idx} ({e['id']}): 'files' entry {f!r} must be a relative "
                "path inside the repo (no leading '/', no '..' component, no "
                "leading '~')"
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


def prune_stale_nodes(out, keep):
    """Delete node files in `out` that this run did not produce. Returns their names.

    Emitting into a directory that already holds a previous plan's nodes leaves the ones
    the new manifest dropped or renamed — and an executor globs the directory, so it runs
    them (slicr#5). A shorter re-plan silently executed the tail of the old one.

    Deliberately narrow, because this deletes files:
      * direct children only, never a walk;
      * only names matching the `NN-<id>.json` shape this script itself emits, so anything
        else in the directory — a README, a log, an executor's own state — is untouched;
      * `keep` spares the files just written, so a node whose identity is unchanged is
        never briefly absent.
    """
    pruned = []
    for p in sorted(out.iterdir()):
        if p.name in keep or not NODE_FILE_RE.fullmatch(p.name) or not p.is_file():
            continue
        p.unlink()
        pruned.append(p.name)
    return pruned


def emit(manifest, out_dir):
    """Validate all entries, then write NN-slug.json for local:true entries in manifest
    order. Returns `(written, pruned)` — the filenames written, and the stale node files
    from a previous run that were removed.

    compute_nodes runs first, before the directory is touched at all: a manifest that will
    not validate must leave the previous run's output exactly as it was, rather than
    deleting it on the way to raising."""
    nodes = compute_nodes(manifest)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = [fname for fname, _ in nodes]
    pruned = prune_stale_nodes(out, set(written))
    for fname, node in nodes:
        _safe_target(out, fname).write_text(json.dumps(node, indent=1))
    return written, pruned


def run_json(request_text):
    """ADR-0052 response-envelope mode: a JSON request `{plan_text}` in, a
    `{schema_version, status, body}` envelope string out. body.nodes is the ordered
    [{filename, node}] list a consumer writes verbatim — byte-identical to emit().

    Every failure here is slicr judging its input and declining, so every one is
    `rejected`. That includes an unparseable *request*: the caller is a program, and a
    consumer that falls open past its own malformed request never learns it emits one.
    `error` is reserved for slicr failing at its job, which by construction cannot be
    raised from inside this function — see main()."""
    try:
        req = json.loads(request_text)
    except ValueError as e:
        return _rejected_envelope(f"invalid run request JSON: {e}")
    plan_text = req.get("plan_text", "")
    try:
        # extract_manifest inside the try: a manifest that will not parse is a refusal,
        # the same class of answer as one that parses into an invalid entry.
        nodes = compute_nodes(extract_manifest(plan_text))
    except ManifestRefused as e:
        return _rejected_envelope(str(e))
    except ValueError as e:
        # The `bad_manifest:` prefix is load-bearing: consumers predating `rejected`
        # pattern-match it to tell a refusal from a failure (dotclaude#37). It stays until
        # they are all on the status.
        return _rejected_envelope(f"bad_manifest: {e}")
    body = {"nodes": [{"filename": fname, "node": node} for fname, node in nodes]}
    return json.dumps({"schema_version": "1", "status": "ok", "body": body})


def _envelope(status, body):
    return json.dumps({"schema_version": "1", "status": status, "body": body})


def _rejected_envelope(message):
    """slicr evaluated the input and declined it. The consumer must fail, not fall open."""
    return _envelope("rejected", {"message": message})


def _error_envelope(message):
    """slicr failed at its own job. Nothing was learned about the input — fall open."""
    return _envelope("error", {"message": message})


def main():
    # `run-json`: the ADR-0052 envelope surface (request on stdin, envelope on stdout,
    # exit 0 — the decision, including a bad manifest, is in the envelope) consumed by
    # the framework's execute-plan wrapper via `docker run`.
    if len(sys.argv) == 2 and sys.argv[1] == "run-json":
        try:
            out = run_json(sys.stdin.read())
        except Exception as e:  # noqa: BLE001 — the envelope IS this surface's error channel
            # The only producer of `error`. Without it an unexpected exception escapes as a
            # traceback and NO envelope, which ADR-0052 cannot classify at all: the consumer
            # sees empty stdout and a non-zero exit, and the status it is told to branch on
            # was never emitted.
            out = _error_envelope(f"internal failure: {type(e).__name__}: {e}")
        print(out)
        sys.exit(0)

    if len(sys.argv) != 3:
        print("usage: plan_to_nodes.py <plan.md> <out-dir>   |   plan_to_nodes.py run-json")
        sys.exit(2)
    text = Path(sys.argv[1]).read_text()
    try:
        manifest = extract_manifest(text)
    except ManifestRefused as e:
        # Same FAILURE(bad_manifest) shape the emit path below already uses — a refused
        # manifest is one outcome, not two, whichever stage of the read noticed it. The
        # prefix lives in the exception message for the envelope's sake; strip it here so
        # it appears exactly once.
        detail = str(e).removeprefix("bad_manifest: ")
        print(f"FAILURE(bad_manifest): {sys.argv[1]}: {detail}")
        sys.exit(1)
    if not manifest:
        print("no execution-manifest found — falling back to plain execution (0 nodes)")
        sys.exit(0)
    try:
        written, pruned = emit(manifest, sys.argv[2])
    except ValueError as e:
        print(f"FAILURE(bad_manifest): {e}")
        sys.exit(1)
    print(f"wrote {len(written)} node(s): {', '.join(written) or '(none local)'}")
    if pruned:
        # Named, not counted. Deleting files on someone's behalf is worth showing, and the
        # names are what tells an operator whether the plan really dropped those nodes.
        print(f"removed {len(pruned)} stale node(s) from a previous run: {', '.join(pruned)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
