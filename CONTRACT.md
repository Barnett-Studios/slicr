# slicr — Contract

The **Slicr** component: decompose a task into a granular, RED-gated **execution-manifest** — the
producer half of the plan → execute seam. slicr *owns* the manifest schema; an executor (and any
orchestrator around it) consume it. Output contract, not a runtime: `plan → execution-manifest (JSON)`.

## Versioning

`VERSION` holds this component's version and every release carries a matching `v<version>` tag.
The number was previously carried by the git tag alone, so nothing in a checkout said which
version it was — a consumer holding a working copy, or an image built from one, had no in-tree
way to answer that.

Under the 0.x convention the **minor** is the breaking position: a change to this component's
request/response contract or CLI surface is a minor bump, and behaviour-preserving fixes are
patches. The version in `VERSION`, the git tag, and the published ghcr image tag are the same
number by construction — dotclaude#63 is what a drift between those looks like.

## The execution-manifest schema (the owned contract)

Canonical, machine-checkable: [`schema/execution-manifest.schema.json`](schema/execution-manifest.schema.json)
(JSON Schema draft 2020-12). A plan file embeds **one fenced JSON block**:

```json
{ "execution-manifest": [
  { "id": "compact-path", "files": ["util.py"], "change": "Implement compact_path.",
    "accept": "python3 -m unittest -q … | grep -qE '^OK'", "forbid": ["new_deps"],
    "local": true, "kind": "edit" }
] }
```

Per entry:

| Key | Req | Meaning |
|---|---|---|
| `id` | ✓ | node id → `NN-<id>.json`. **Path-safe slug**: `^[A-Za-z0-9][A-Za-z0-9._-]*$`, ≤100 chars |
| `files` | ✓ | editable files (non-empty), **relative paths inside the repo** — no leading `/`, no `..` component; a `create` node lists **exactly one** |
| `change` | ✓ | task description shown to the executing model |
| `accept` | ✓ | shell command; **exit 0 ⇔ solved** — the RED oracle, authored up front |
| `kind` | | `edit` (default) \| `create` |
| `forbid` | | subset of `{new_deps}` |
| `local` | | `true` → offload to an executor as a node; `false` (default) → executed inline by the planning model |

## The manifest is untrusted input

The manifest is authored by a planning **model**. `id` becomes a filename and `files` become
write targets, so both are validated as a trust boundary rather than consumed as given: the id
charset forbids every path separator and a leading dot, making `..` and `.` *unrepresentable*,
and every write additionally resolves under the output directory before it happens (`resolve()`,
not `normpath` — a symlink planted inside the output directory must not be a way out).

A manifest that tries to escape the output directory is **rejected loudly** (`ValueError` →
`FAILURE(bad_manifest)` / exit 1, or a `rejected` envelope in `run-json`); it is not sanitized into
something writable, because silently renaming a node changes its identity and makes the emitted
files disagree with the manifest that produced them.

## Three outcomes, and why they are three

| Outcome | CLI | `run-json` | What the consumer must do |
|---|---|---|---|
| No manifest in the plan | exit 0, `no execution-manifest found` | `ok`, `nodes: []` | fall back to plain execution |
| Manifest **refused** | exit 1, `FAILURE(bad_manifest): <path>: <detail>` | `rejected` | **fail** |
| slicr **failed** | traceback, exit ≠ 0 | `error` | fall open |

The middle row is the one that did not exist. A refusal was reported as `error` (slicr#10) and a
manifest that would not *parse* was reported as absent (slicr#3) — so a consumer obeying the
ADR-0052 "fall open on any status != ok" rule silently re-ran a **refused** manifest on its own
in-tree planner, and one JSON typo produced a benign 0-node success. A guard that falls open past
its own verdict is not a guard: the input was judged, and running it somewhere else is a bypass.

`rejected` and `error` differ in **who failed**, not in who wrote the input. An unparseable
`run-json` *request* is therefore `rejected` too: slicr judged it and declined. The consumer that
emitted it is a program, and one that falls open past its own malformed request never learns it
emits one.

`error` has exactly one producer — a catch-all around `run_json` in `main()`. Before it, an
unexpected exception escaped as a traceback and **no envelope at all**, so a consumer told to
branch on `status` got empty stdout and nothing to branch on.

**Refused, specifically:** a fenced block that declares `"execution-manifest"` and will not parse,
or whose value is not a list, plus every `validate_entry` rejection. A fenced block that does *not*
declare the key is still skipped silently whatever its contents — refusing every unparseable block
would hard-fail any plan that quotes malformed JSON in prose, a worse outage than the silence it
fixes. The residual is the mirror image: a plan whose prose *does* quote a broken manifest block
verbatim is now refused. That is the deliberate direction — loud over silent.

## Granularity discipline (why single-region nodes)

A `local: true` node fills **one function body / one contiguous edit**, with a discriminating `accept`
(a RED test) **authored and committed up front** — never let the executor author its own test.
Single-region nodes land near-perfectly vs a ~40% ceiling when a node bundles impl + test. Mark a
task `local: false` only when genuinely cross-cutting/risky. This is the slicr's core judgment; the
planning procedure — how you prompt for and validate the plan — lives in your harness (ideally with
plan-validation on a fresh, separate context, not the author).

## Reference producer / validator

[`plan_to_nodes.py`](plan_to_nodes.py) is the deterministic, zero-token bridge: it extracts the
manifest from a plan, validates every entry, and emits `NN-<id>.json` for the `local:true` entries in
order. **Fail-open on absence:** a plan with no manifest emits nothing and the executor falls back to
plain single-model execution (exit 0, 0 nodes). A manifest that is present and unusable — unparseable
or structurally bad — is a hard `FAILURE(bad_manifest)` (exit 1), per the table above.

**The output directory is slicr's to keep current.** `emit` deletes the node files a previous
run left behind and the new manifest no longer contains, and names them on stdout. An executor
globs that directory, so a node the plan dropped or renamed is a node that still *runs* — a
shorter re-plan silently executed the tail of the old one (slicr#5). The delete is bounded to
direct children whose names match the `NN-<id>.json` shape emit itself writes: a README, a log,
or an executor's own state file in the same directory is not slicr's to remove. Validation runs
before the directory is touched at all, so a re-plan that is *rejected* leaves the previous run
exactly as it was rather than losing it on the way to raising.

`run-json` has no output directory — the consumer writes `body.nodes` itself — so **that
consumer owns the same obligation**: the node set is the whole answer, not an addition to
whatever is already on disk.

The reference validator enforces the schema's **type** rules too (`id`/`accept` non-empty strings,
`change` a string, `files` items strings, `local` a boolean) — so a manifest the validator accepts is
never rejected by an independent schema-validating consumer on a type mismatch. The **sole** remaining
divergence is unknown entry keys: the validator ignores them, the schema is strict
(`additionalProperties: false` on entries). `test_schema.py` proves the two agree on every shared
good/bad case (including the type cases); the unknown-key case is the one deliberately not exercised.

## Swap-in

Any slicr that emits a conforming `execution-manifest` (validated by the schema) drops into the
seam. The stable surface is the JSON Schema + the fail-open extraction semantics + the RED-gated,
single-region node discipline — not any particular planning prose or procedure.
