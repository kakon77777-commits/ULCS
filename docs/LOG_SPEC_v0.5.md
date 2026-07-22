# Language Operator Graph (LOG) Specification v0.5

## Overview

LOG v0.5 extends v0.4 with repeatability metadata. The `.sos` surface syntax is unchanged.

Top-level structure:

```json
{
  "format": "ULCS-Language-Operator-Graph",
  "version": "0.5",
  "nodes": [],
  "edges": [],
  "execution_order": [],
  "execution_layers": [],
  "sinks": []
}
```

## Node fields

A v0.5 node contains:

- `node_id`
- `role`
- `language`
- `code`
- `inputs`
- `input_type`
- `output_type`
- `effects`
- `capabilities`
- `runtime`
- `claims`
- `taint_sources`
- `deterministic`
- `cacheable`

Example:

```json
{
  "node_id": "errors",
  "role": "extract",
  "language": "regex",
  "code": "ERROR.*",
  "inputs": [
    {"node_id": "text", "field": null, "alias": null}
  ],
  "input_type": "Json",
  "output_type": "MatchList",
  "effects": [],
  "capabilities": [],
  "runtime": "python-re",
  "claims": [],
  "taint_sources": [],
  "deterministic": true,
  "cacheable": true
}
```

## Repeatability fields

### `deterministic`

Indicates that the Runtime adapter declares, or the core recognizes, that equal canonical inputs and equal validated Runtime identity should produce equal canonical outputs.

This is an adapter contract, not a mathematical proof. Third-party adapter declarations are trusted metadata.

### `cacheable`

Indicates that the node is eligible for the v0.5 content-addressed cache. A node must be deterministic and must not declare external filesystem, network, database, or process-spawn effects.

`deterministic=true` does not necessarily imply `cacheable=true`. A deterministic read from an external resource is still excluded because the external resource content is not part of the v0.5 cache key.

## Compatibility

- v0.4 `claims`, `taint_sources` and `execution_layers` are preserved.
- v0.3 `capabilities` is preserved as a compatibility alias of `effects`.
- v0.2 edges, execution order and sinks are preserved.
- `.sos` syntax does not change.
- Consumers that reject unknown fields should update for `deterministic` and `cacheable`.

## Relationship to manifests

LOG describes the validated graph before execution. It does not contain actual input digests, output digests, cache hits or final propagated taints. Those values appear in Execution Trace and Execution Manifest v0.5.

See:

- `CACHE_SPEC_v0.5.md`
- `EXECUTION_MANIFEST_v0.5.md`
- `RESOURCE_POLICY_v0.4.md`
- `EXECUTION_TRACE_v0.4.md`
