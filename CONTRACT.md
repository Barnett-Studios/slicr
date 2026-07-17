# planner — Contract

The **Planner** component: decompose a task into a granular, RED-gated **execution-manifest** — the
producer half of the plan→execute seam. planner *owns* the manifest schema; an executor (and any
orchestrator around it) consume it. Output contract, not a runtime: `plan → execution-manifest (JSON)`.

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
| `id` | ✓ | node id → `NN-<id>.json` |
| `files` | ✓ | editable files (non-empty); a `create` node lists **exactly one** |
| `change` | ✓ | task description shown to the executing model |
| `accept` | ✓ | shell command; **exit 0 ⇔ solved** — the RED oracle, authored up front |
| `kind` | | `edit` (default) \| `create` |
| `forbid` | | subset of `{new_deps}` |
| `local` | | `true` → offload to an executor as a node; `false` (default) → executed inline by the planning model |

## Granularity discipline (why single-region nodes)

A `local: true` node fills **one function body / one contiguous edit**, with a discriminating `accept`
(a RED test) **authored and committed up front** — never let the executor author its own test.
Single-region nodes land near-perfectly vs a ~40% ceiling when a node bundles impl + test. Mark a
task `local: false` only when genuinely cross-cutting/risky. This is the planner's core judgment; the
planning procedure — how you prompt for and validate the plan — lives in your harness (ideally with
plan-validation on a fresh, separate context, not the author).

## Reference producer / validator

[`plan_to_nodes.py`](plan_to_nodes.py) is the deterministic, zero-token bridge: it extracts the
manifest from a plan, validates every entry, and emits `NN-<id>.json` for the `local:true` entries in
order. **Fail-open:** a missing/malformed manifest emits nothing and the executor falls back to plain
single-model execution (exit 0, 0 nodes); a *structurally bad* manifest is a hard `FAILURE(bad_manifest)` (exit 1).

The reference validator enforces the schema's **type** rules too (`id`/`accept` non-empty strings,
`change` a string, `files` items strings, `local` a boolean) — so a manifest the validator accepts is
never rejected by an independent schema-validating consumer on a type mismatch. The **sole** remaining
divergence is unknown entry keys: the validator ignores them, the schema is strict
(`additionalProperties: false` on entries). `test_schema.py` proves the two agree on every shared
good/bad case (including the type cases); the unknown-key case is the one deliberately not exercised.

## Swap-in

Any planner that emits a conforming `execution-manifest` (validated by the schema) drops into the
seam. The stable surface is the JSON Schema + the fail-open extraction semantics + the RED-gated,
single-region node discipline — not any particular planning prose or procedure.
