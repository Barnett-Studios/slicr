# planner

**Decompose a task into a granular, RED-gated execution-manifest — the producer half of the
plan→execute seam.** Opus plans; the local cascade executes. The Planner's job is the decomposition
and the contract it hands down: single-region nodes, each with a discriminating acceptance test
authored up front.

> Part of the Barnett Studios agentic-harness toolkit → cxpak · commitward · abproof · cascadr ·
> cordon · corpus · **planner** · …

## What's here

- [`schema/execution-manifest.schema.json`](schema/execution-manifest.schema.json) — the owned
  contract (JSON Schema 2020-12). This is what makes the plan→execute seam swappable.
- [`plan_to_nodes.py`](plan_to_nodes.py) — the deterministic, zero-token reference producer/validator:
  plan → validated `NN-<id>.json` nodes for the Executor. Fail-open on a missing/malformed manifest.
- `test_plan_to_nodes.py`, `test_schema.py` — the validator's behavior + schema↔validator consistency
  (14 tests).

The planning *procedure* itself is the `code-plan` skill (and `code-validate-plan`, run on a fresh
subagent) — those ship as Library assets and are referenced here, not duplicated. See
[`CONTRACT.md`](CONTRACT.md).

## Use

```sh
# extract + validate a plan's manifest into executor nodes
python3 plan_to_nodes.py plan.md ./nodes/
# → wrote 2 node(s): 01-compact-path.json, 02-cli-entry.json

# validate a manifest against the formal schema
python3 -c "import json,jsonschema; \
  s=json.load(open('schema/execution-manifest.schema.json')); \
  jsonschema.Draft202012Validator(s).validate(json.load(open('manifest.json')))"
```

A missing or malformed manifest is **not** an error — `plan_to_nodes.py` emits nothing and the loop
falls back to plain lead-model execution. A *structurally bad* manifest (unknown `forbid`, a `create`
node with multiple files, a missing required key) is a hard failure.

## The discipline it encodes

Each `local: true` node fills one function body / one contiguous edit, with a RED test authored and
committed up front — never the executor's own test. That single-region + external-oracle rule is why
offloaded nodes land near-perfectly instead of hitting the bundled-impl-and-test ceiling.
