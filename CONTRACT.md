# planner — Contract

The **Planner** component: decompose a task into a granular, RED-gated **execution-manifest** — the
producer half of the plan→execute seam. The Planner *owns* the manifest schema; the Executor and
Orchestrator consume it. Output contract, not a runtime: `plan → execution-manifest (JSON)`.

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
| `local` | | `true` → offload to the local cascade as a node; `false` (default) → the lead model executes it |

## Granularity discipline (why single-region nodes)

A `local: true` node fills **one function body / one contiguous edit**, with a discriminating `accept`
(a RED test) **authored and committed up front** — never let the executor author its own test.
Single-region nodes land near-perfectly vs a ~40% ceiling when a node bundles impl + test. Mark a
task `local: false` only when genuinely cross-cutting/risky. This is the Planner's core judgment; the
reference planning procedure is the `code-plan` skill (validated by `code-validate-plan` on a fresh
subagent, not the author).

## Reference producer / validator

[`plan_to_nodes.py`](plan_to_nodes.py) is the deterministic, zero-token bridge: it extracts the
manifest from a plan, validates every entry, and emits `NN-<id>.json` for the `local:true` entries in
order. **Fail-open:** a missing/malformed manifest emits nothing and the loop falls back to plain lead
execution (exit 0, 0 nodes); a *structurally bad* manifest is a hard `FAILURE(bad_manifest)` (exit 1).

The reference validator is **lenient on unknown entry keys** (ignores them); the schema is strict
(`additionalProperties: false` on entries). `test_schema.py` proves the two agree on every shared
good/bad case.

## Swap-in

Any planner that emits a conforming `execution-manifest` (validated by the schema) drops into the
seam. The stable surface is the JSON Schema + the fail-open extraction semantics + the RED-gated,
single-region node discipline — not the `code-plan` prose, which is one reference procedure.
