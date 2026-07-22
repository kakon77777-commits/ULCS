# ULCS Execution Manifest and Replay Verification v0.5

## Purpose

Execution Trace may contain complete values. Execution Manifest stores comparison metadata and digests without storing full node outputs. It is used to determine whether two runs used the same workflow, validated plan and policy, and produced the same per-node canonical results.

A manifest is not a signature or a host-attestation mechanism.

## CLI

Create a baseline:

```bash
ulcs workflow.sos --policy policy.json --yes \
  --emit-manifest output/run.manifest.json
```

Replay and compare:

```bash
ulcs workflow.sos --policy policy.json --yes \
  --verify-manifest output/run.manifest.json
```

A mismatch returns exit code `6`.

## Format

```json
{
  "format": "ULCS-Execution-Manifest",
  "version": "0.5",
  "program_digest": "<sha256>",
  "plan_digest": "<sha256>",
  "policy_digest": "<sha256>",
  "execution_layers": [["source"], ["transform"]],
  "nodes": {}
}
```

- `program_digest` covers node IDs, roles, languages, source code and input references.
- `plan_digest` covers validated runtimes, types, effects, claims, taint sources, repeatability classification and execution layers.
- `policy_digest` covers mode, allow rules, deny rules and limits. The policy file path is excluded.

## Node record

```json
{
  "source": {
    "fingerprint": "<sha256>",
    "input_digest": "<sha256>",
    "output_digest": "<sha256>",
    "runtime": "python-isolated-subprocess",
    "claims": ["python.execute@runtime://python"],
    "taints": [],
    "deterministic": false,
    "cacheable": false,
    "cache_hit": false,
    "output_bytes": 42
  }
}
```

The manifest has no `outputs` field.

## Replay comparison

v0.5 compares:

- program, plan and policy digests
- execution layers and node order
- node fingerprint
- input and output digests
- runtime identity
- propagated taints

v0.5 deliberately ignores `cache_hit`, `output_bytes`, elapsed time and thread identity. A baseline Runtime execution and a later cache hit can therefore match when their canonical input and output are identical.

## Meaning and limits

A successful comparison means the workflow definition, validated plan, effective policy, canonical node inputs, canonical node outputs and propagated taints match.

It does not prove that the result is correct in the external world, that an undeclared side effect did not occur, that the host was trustworthy, or that the manifest file was not replaced. External files, databases and network state are not automatically snapshotted.

Future versions may add signed manifests, dependency and container digests, external artifact digests and append-only transparency records. v0.5 establishes only the minimal deterministic comparison layer.
