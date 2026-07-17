# planner

[![CI](https://github.com/Barnett-Studios/planner/actions/workflows/ci.yml/badge.svg)](https://github.com/Barnett-Studios/planner/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)

**Decompose a task into a granular, RED-gated execution-manifest — the producer half of the
plan→execute seam.** A capable model plans; cheaper executors run the nodes. planner's job is the
decomposition and the contract it hands down: single-region nodes, each with a discriminating
acceptance test authored up front.

> Part of the Barnett Studios agentic-harness toolkit → cxpak · commitward · abproof · cascadr ·
> cordon · corpus · **planner** · …

## What's here

- [`schema/execution-manifest.schema.json`](schema/execution-manifest.schema.json) — the owned
  contract (JSON Schema 2020-12). This is what makes the plan→execute seam swappable.
- [`plan_to_nodes.py`](plan_to_nodes.py) — the deterministic, zero-token reference producer/validator:
  plan → validated `NN-<id>.json` nodes for your executor. Fail-open on a missing/malformed manifest.
- `test_plan_to_nodes.py`, `test_schema.py` — the validator's behavior + schema↔validator consistency
  (14 tests).

The planning *procedure* itself — how you prompt a model to produce the manifest, and how you
validate the plan before decomposing it — lives in your harness as a skill or prompt. planner owns
only the deterministic producer/validator and the schema contract. See [`CONTRACT.md`](CONTRACT.md).

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

A missing or malformed manifest is **not** an error — `plan_to_nodes.py` emits nothing and the
executor falls back to plain single-model execution. A *structurally bad* manifest (unknown `forbid`, a `create`
node with multiple files, a missing required key) is a hard failure.

## The discipline it encodes

Each `local: true` node fills one function body / one contiguous edit, with a RED test authored and
committed up front — never the executor's own test. That single-region + external-oracle rule is why
offloaded nodes land near-perfectly instead of hitting the bundled-impl-and-test ceiling.

## License

Licensed under either of [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE) at your option.
Unless you explicitly state otherwise, any contribution you intentionally submit for
inclusion in the work shall be dual-licensed as above, without any additional terms.

---

Built by [Barnett Studios](https://barnett-studios.com/) — part of the agentic-harness
toolkit: [cxpak](https://github.com/Barnett-Studios/cxpak) ·
[commitward](https://github.com/Barnett-Studios/commitward) ·
[cascadr](https://github.com/Barnett-Studios/cascadr) ·
[abproof](https://github.com/Barnett-Studios/abproof) ·
[cordon](https://github.com/Barnett-Studios/cordon) · **planner**.
