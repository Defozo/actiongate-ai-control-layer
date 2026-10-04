# Local model selection and evaluation

The selected local configuration uses **Qwen3.5-4B Q4_K_M**, **Ollama 0.32.0**
and the signed **guard v2.9** contract. The signed model manifest pins the model,
tokenizer, runtime and guard artifacts. [Runtime documentation](runtime-candidate.md)
describes readiness and physical placement checks.

The guard returns a constrained category, evidence and risk assessment. The
gateway validates the response and applies a fixed signed projection to its
public verdict. Invalid or incomplete inspection fails closed. The local model
does not grant permission to bypass identity, data labels or budget controls.

## Evaluation sets

The repository contains 16 calibration cases, 40 exposed v1 regression cases
and a separately frozen 40-case v2 holdout. Each 40-case set has ten attack and
ten benign examples per language, English and Polish. V1 informed model/contract
development and is regression evidence. The v2 manifest records the freeze;
the [provenance note](../artifacts/history/holdout-access-note.json) records
incidental access to three unlabelled search matches after classifier freeze.

The recorded v2.9 CPU suite passed 602 tests with no failures or skips and all
17 control areas covered. The frozen v2 sample passed 40/40 with no unknowns;
four fixed repeat samples retained their verdict and risk level. Repeating the
same frozen cases does not create additional independent samples. These small
authored sets do not establish performance on arbitrary production inputs.

See [all-local results](../artifacts/all-local.json),
[quality results](../artifacts/semantic-quality.json),
[repeat results](../artifacts/semantic-repeat-variation.json) and
[acceptance](acceptance.md) for exact source bindings and execution context.
Earlier failed model integrations and development evaluations remain in the
dated evidence history; they are not results for the shipped guard.

## Changing models

Replacing weights, tokenizer, prompt, schema or runtime requires updated signed
manifests and fresh functional, quality, resource and deployment checks.
Do not reuse an older model's acceptance result. Keep calibration separate from
held-out evaluation and record any exposure that affects independence.
The reference profile uses CPU; optional GPU results remain separately labelled.

Qwen3.5 weights and tokenizer use Apache-2.0. Runtime and library licenses are
recorded in [third-party notices](../THIRD_PARTY_NOTICES.md) and the preserved
license files. Model weights are obtained during preparation, not bundled here.
