# Language Operator Graph Specification v0.6

LOG v0.6 extends v0.5 without changing graph syntax, edge semantics, execution layers, capability claims, taint sources, deterministic classification, or cacheability rules.

## New node fields

```json
{
  "input_schema": null,
  "output_schema": {"type": "object"},
  "persist_output": true
}
```

- `input_schema` validates the resolved value immediately before Runtime or resume selection.
- `output_schema` validates Runtime output, cache output, and resumed Artifact output.
- `persist_output` requests Artifact materialization when Artifact support is enabled.

## Existing graph fields

The root retains:

```text
format
version
nodes
edges
execution_order
execution_layers
sinks
```

Nodes retain:

```text
node_id
role
language
code
inputs
input_type
output_type
effects
capabilities
runtime
claims
taint_sources
deterministic
cacheable
```

## Digest integration

Schemas and persistence declarations are part of the program and validated plan digests. Schema digests are also included in per-node execution fingerprints. A contract change therefore invalidates incompatible cache entries and checkpoints.

## Execution Trace v0.6 additions

```json
{
  "resumed": {"node": true},
  "artifacts": {
    "node": {
      "format": "ULCS-Artifact",
      "version": "0.6",
      "digest": "..."
    }
  },
  "checkpoint_path": "..."
}
```

Manifest node records add `resumed`, `artifact_digest`, and `schema_digest`. Replay verification compares artifact and schema digests when they exist in the expected manifest, but deliberately ignores whether a value came from Runtime, cache, or resume.

## Compatibility

- `.sos` syntax remains unchanged.
- Artifact mode defaults to off.
- Workflows without a contract expose null schemas and `persist_output=false`.
- Existing Runtime adapters require no changes.
- v0.5 manifests can still be read, but new manifests are emitted as v0.6.
