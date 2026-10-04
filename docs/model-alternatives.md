# Local guard alternatives and isolated evaluation

Checked 2026-10-04. Qwen3.5-4B Q4_K_M with Ollama 0.32.0 and signed guard v2.9 is the selected configuration. Its weights and tokenizer are pinned in the deployment manifests. Model selection followed the historical v2.7 isolated GPU evaluation with a fresh database and separate networks. Version 2.8 corrected the separate goal-review evidence contract. Version 2.9 changes source output field order and encodes existing consistency rules in the native schema, leaving independent validation and thresholds unchanged. The exact unsigned candidate passed 13 diagnostic contrasts and 56 exposed calibration/regression cases on CPU. Production equality passed 29 checks, focused contracts 97/97 and signed CPU preflight 13/13. The final CPU suite passed 602/602 tests with 17/17 control coverage. Independent v2 passed 40/40 on both executions of the same frozen classifier; an incidental search-access note after classifier freeze remains preserved. These sample results do not imply perfect generalization. The earlier [candidate runtime report](../artifacts/qwen35-candidate-runtime.json) records preparation only; executed evidence is linked below.

The first Qwen3.5 comparison on calibration plus exposed regression cases produced 56 unknown results. Investigation found prose responses instead of the requested JSON when using Ollama 0.18.2 with non-thinking and structured-format output. The [raw benign diagnostic](../artifacts/qwen35-tuning/benign-raw.json) preserves that failure. Ollama 0.32.0 and the strict four-key category contract resolved this integration issue in the final GPU evaluation. Earlier failed assessments remain in the artifact history; they are not included as passed evidence.

Current v2.9 runtime evidence also includes a legal 64 KiB CPU workflow
(12/12 controls, 354.83 seconds), fresh offline CPU installation and recovery
(15/15, 246.88 seconds after the database capacity correction), and the separate GPU proof (19 infrastructure plus 13
functional controls, 160.39 seconds). GPU legal and malicious 64 KiB broker
workflows completed in 12.97 and 9.32 seconds respectively. Actual source and
physical placement bindings accompany the reports linked in
[the runtime account](runtime-candidate.md). The first complete signed v2.9 CPU
suite passed 493/493 tests, zero failures/skips, source stability and 17/17 control
coverage. Independent v2 passed 40/40 with zero false positives, false negatives
or unknowns in either language; four fixed repeats were unchanged. This measures
those samples and does not establish perfect generalization. The
[complete archived result and exact sources](../artifacts/history/cpu-v29-before-metrics-fix/)
precede a benchmark-producer endpoint correction. The final producer-bound
[CPU suite](../artifacts/all-local.json) then passed 602/602 tests in 2156.49
seconds, zero failures/skips and 17/17 control coverage. Calibration 16/16,
regression 40/40, v2 40/40 and all four unchanged repeat samples passed again.
The classifier was not changed between these executions. The first complete
performance matrix exposed PostgreSQL connection exhaustion at 50 clients.
The database-only correction to 200 connections, 2 GiB memory and 256 PIDs
passed 591/591 controlled contracts and the fresh installation proofs above.
The previous 602-case suite and long-context measurements are preserved
[with their original PostgreSQL-100 configuration](../artifacts/history/cpu-v29-pg100-before-capacity-fix/).
The revised full performance matrix and remaining application/demo proofs
remain separate requirements.

The historical v2.8 CPU runtime passed 13 functional checks, a complete legal 64 KiB
workflow and 15 fresh offline installation/recovery checks. GPU passed 19
infrastructure and 13 functional checks, with both legal and malicious 64 KiB
workflows verified. [Versioned measured evidence](runtime-candidate.md) remains
separate from the full CPU quality suite. That suite stopped at 252 passed tests
and one failed test, with zero skips: calibration 16/16 and exposed regression
39/40, zero unknowns, but one English benign false positive exceeded the
per-language FPR gate. The independent holdout was not opened. The
[failed CPU assessment](../artifacts/history/cpu-v28-regression-failure/)
is preserved; that historical v2.8 configuration did not pass release acceptance.

The historical signed v2.7 GPU assessment is retained in six versioned reports:
[runtime preflight](../artifacts/history/qwen35-v2.7-ollama032/runtime-preflight.json),
[calibration](../artifacts/history/qwen35-v2.7-ollama032/semantic-calibration.json),
[exposed regression](../artifacts/history/qwen35-v2.7-ollama032/semantic-regression-v1.json),
[legal 64 KiB workflow](../artifacts/history/qwen35-v2.7-ollama032/long-payload-gpu.json),
[hidden 64 KiB attack](../artifacts/history/qwen35-v2.7-ollama032/semantic-long-attack.json), and
[actual UI payloads](../artifacts/history/qwen35-v2.7-ollama032/ui-payload-preflight.json).
These remain calibration and integration evidence, not an independent holdout or
v2.9 acceptance claim. [The runtime account](runtime-candidate.md) explains the
CPU failures, exact evidence diagnoses and current v2.9 checks.

| Candidate | License | Exact Ollama manifest | Model layer bytes |
| --- | --- | --- | --- |
| `qwen3.5:4b`, Q4_K_M | Apache-2.0 | `sha256:2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd` | 3,389,971,840 |
| `qwen3:8b-q4_K_M` | Apache-2.0 | `sha256:500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` | 5,225,374,496 |

Sizes and digests came from the public [Qwen3.5 registry manifest](https://registry.ollama.ai/v2/library/qwen3.5/manifests/4b) and [Qwen3 registry manifest](https://registry.ollama.ai/v2/library/qwen3/manifests/8b-q4_K_M), without fetching model layers. The corresponding layer digests are `81fb60c7daa80fc1123380b98970b320ae233409f0f71a72ed7b9b0d62f40490` and `a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f`.

Qwen3.5-4B is the first candidate because it leaves substantially more room in an 8 GiB worker. The official model card publishes IFEval 89.8, but that benchmark is not ActionGate's bilingual injection task and does not establish the quality of our deterministic non-thinking configuration. The architecture has 32 layers, with full attention every fourth layer, four KV heads and head dimension 256. Inference from those dimensions: an 8192-token fp16 full-attention KV cache is approximately 256 MiB, plus recurrent state, compute buffers, runner and tokenizer memory. Quantized model bytes are approximately 3.16 GiB. The separate GPU preparation report contains measured runtime allocations; the completed CPU resource and quality acceptance is linked above. [Official Qwen3.5-4B model card](https://huggingface.co/Qwen/Qwen3.5-4B)

Ollama 0.18.2 already registered `qwen35` in its [Qwen3-Next backend](https://github.com/ollama/ollama/blob/v0.18.2/model/models/qwen3next/model.go), with dedicated [renderer](https://github.com/ollama/ollama/blob/v0.18.2/model/renderers/qwen35.go) and [parser](https://github.com/ollama/ollama/blob/v0.18.2/model/parsers/qwen35.go). That source-level support was insufficient for the tested structured-output path, so the selected deployment uses the actually verified Ollama 0.32.0. Qwen3.5 defaults to thinking; deployment preserves the explicit API non-thinking setting and validates complete schema-constrained output. The model does not support Qwen3's textual thinking toggle. Its selected tokenizer uses repository revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`, `tokenizer.json` SHA-256 `5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42`, 12,807,982 bytes. [Pinned tokenizer metadata](https://huggingface.co/api/models/Qwen/Qwen3.5-4B/tree/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a)

Qwen3-8B is a larger, older alternative, with native [Ollama 0.18.2 support](https://github.com/ollama/ollama/blob/v0.18.2/model/models/qwen3/model.go). Its 36 layers, eight KV heads and head dimension 128 imply approximately 1.125 GiB of fp16 KV at 8192 tokens. Model bytes are approximately 4.87 GiB, leaving approximately 2 GiB for other allocations inside 8 GiB. This is a tighter candidate and requires peak-memory validation; a larger parameter count does not prove better classification than Qwen3-4B-Instruct-2507. [Official Qwen3-8B model card](https://huggingface.co/Qwen/Qwen3-8B), [pinned configuration](https://huggingface.co/Qwen/Qwen3-8B/resolve/b968826d9c46dd6066d109eabc6255188de91218/config.json)

Its tokenizer revision is `b968826d9c46dd6066d109eabc6255188de91218`, with SHA-256 `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`, 11,422,654 bytes. This digest matches ActionGate's earlier Qwen3 tokenizer, not the selected Qwen3.5 tokenizer. Both candidates declare Qwen2Tokenizer-compatible tokenization; ActionGate uses the pinned tokenizer JSON directly. [Qwen3-8B tokenizer metadata](https://huggingface.co/api/models/Qwen/Qwen3-8B/tree/b968826d9c46dd6066d109eabc6255188de91218)

A replacement requires new approved model manifests, exact template/parser validation, the matching tokenizer pin, a signed generation, actual CPU/GPU readiness and resource tests, then calibration/regression before the independent assessment. The selected Qwen3.5/Ollama 0.32 configuration is pinned and has passed the current CPU/GPU runtime and complete CPU quality checks. The versioned reports distinguish preparation, exposed tuning, independent sample assessment and measured runtime behavior.
