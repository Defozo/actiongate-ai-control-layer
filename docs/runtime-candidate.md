# Selected Qwen3.5 / Ollama 0.32.0 runtime

The release pins now select Qwen3.5:4b on Ollama 0.32.0. The selection followed
an isolated GPU comparison. Final reference CPU, resource, restart and independent
quality acceptance remain separate required checks; selection alone is not a pass.

The current signed classifier is v2.9, artifact
`5bbb46d9b2ccb7c3b1b7b4e5ff9577272344a9c9f10e87b06b55871c3e6da206`.
It chooses category and risk before writing its explanation and evidence. Its
native source schema encodes the existing benign, threat and unknown consistency
constraints as three disjoint branches, without schema title annotations.
The independent Pydantic validator, literal source-evidence rules, goal review,
model parameters and thresholds are unchanged. The signed artifact explicitly
records output field order. This is a generation constraint, not a repair or
reinterpretation of invalid model output.

The exact unsigned candidate passed 13 authored diagnostic contrasts and all
56 exposed calibration/regression cases on CPU, with zero unknowns, FPR 0 and
recall 1 in both languages. Those are tuning results, not independent acceptance.
See [raw exposed results](../artifacts/business-intent-v4b-exposed.json) and
[preserved prototype history](../artifacts/history/cpu-v28-business-intent-prototypes/).
Production equality passed 29 checks, focused semantic/runtime contracts passed
97/97, and the signed CPU functional preflight passed 13/13. The legal CPU
64 KiB workflow passed 12/12 controls in 354.83 seconds: seven source windows,
separate goal review and output review, nine actual calls, 25,163 settled tokens
and a maximum serialized input bound of 4056/4096. The content was saved exactly.
After the database capacity correction, fresh offline CPU installation passed 15/15 checks in 246.88 seconds,
including actual MCP access, denial during infrastructure failures, same-generation
recovery and reuse of the durable run. The fresh GPU proof passed 19 infrastructure
and 13 functional controls in 160.39 seconds, including actual residency of both
models and independent confirmed guard termination. GPU legal 64 KiB passed in
12.97 seconds; the hidden-attack broker execution passed in 9.32 seconds with
zero business effects. The first complete signed v2.9 CPU suite passed 493/493
tests, with zero failures or skips, stable source hashes and all 17 required
control areas covered. The runner measured 2185.55 seconds. Calibration passed
16/16 and exposed regression 40/40. The first independent v2 evaluation passed
40/40: each language had 10 true positives, 10 true negatives, no false positives,
no false negatives and no unknown results. Four fixed repeat samples retained
their verdicts and risk levels. These are results for the specified samples,
not a claim of perfect classification on arbitrary material.

That complete result and all 95 exact tested sources are preserved in
[the pre-metrics-fix archive](../artifacts/history/cpu-v29-before-metrics-fix/).
After the separately reviewed benchmark-producer endpoint correction, the final
producer-bound CPU suite passed **602/602 tests**, with zero failures or skips,
stable source hashes and 17/17 control coverage, in 2156.49 seconds. It changed no
model, prompt, runtime, validation or threshold. Calibration again passed 16/16,
regression 40/40 and the same independent v2 sample 40/40, with recall 1 and FPR 0
in both languages and zero unknowns. Four fixed repeats were complete and
unchanged. The real hidden 64 KiB CPU attack passed all ten checks in 340.53
seconds. See the [final CPU suite](../artifacts/all-local.json),
[quality report](../artifacts/semantic-quality.json) and
[repeat report](../artifacts/semantic-repeat-variation.json).
The first full 144-row/2928-request performance matrix then exposed PostgreSQL
connection exhaustion at 50 clients. Its complete original evidence is
[archived](../artifacts/history/benchmark-pg100-before-capacity-fix/). Only Compose
database capacity changed: 200 connections, 2 GiB memory and 256 PIDs. The
post-change [controlled contract suite](../artifacts/contract.json) passed
591/591 cases without skips or source changes. The earlier 602-case CPU suite,
long-context measurements and all 95 exact tested sources remain
[preserved under their PostgreSQL-100 configuration](../artifacts/history/cpu-v29-pg100-before-capacity-fix/).
Fresh CPU/GPU installation and recovery proofs use the new capacity; model,
classifier and validators are unchanged. A new full performance matrix remains
pending as a separate requirement.

Current evidence: [production equality](../artifacts/source-v29-production-equality.json),
[focused contracts](../artifacts/source-v29-contract.xml),
[CPU preflight](../artifacts/runtime-preflight.json), and
[deployed source binding](../artifacts/deployed-source.json),
[legal CPU 64 KiB](../artifacts/long-payload-cpu.json), and
[offline CPU installation](../artifacts/clean-install.json),
[GPU preflight](../artifacts/gpu-preflight.json), and
[GPU source, hardware and long-context proofs](../artifacts/gpu-reference/).
Independent v2 was first evaluated only after the earlier gates passed. A
[separate provenance note](../artifacts/history/holdout-access-note.json) records
incidental access to three unlabelled search matches after v2.9 was frozen;
those matches were not used to change the classifier.

Qwen3.5 on Ollama 0.18.2 ignored the native JSON format constraint with
`think=false`. With prompt v2.3, all 56 exposed calibration/regression results
were rejected as unknown because the output was prose. Adding an explicit JSON
example in v2.4 produced valid JSON in all 56 cases, but one Polish benign example
remained a false positive. The original reports are preserved under
`artifacts/history/qwen35-v2.3` and `artifacts/history/qwen35-v2.4-ollama0182`.

The runtime candidate addresses the underlying format behavior independently of
prompt tuning. [Ollama PR 15901](https://github.com/ollama/ollama/pull/15901) was
merged on 7 July 2026 and applies the format constraint immediately for thinking
parsers when thinking is disabled. The fix is present in the
[v0.32.0 source](https://raw.githubusercontent.com/ollama/ollama/v0.32.0/server/routes.go)
and [stable release](https://github.com/ollama/ollama/releases/tag/v0.32.0).
The released image is pinned to
`sha256:57f573b47f1f71ebb445789f279fe3e596a8beab182f7cf486db9205bad87c5a`.

`runtime/Dockerfile` includes its separate llama-server and required
adjacent libraries. The GPU target contains only cuda_v12, with observed
cuBLAS 12.8.5.5 and cudart 12.8.90. The inventory records their SHA256 values and
the built image identity. The corresponding CUDA 12.8.2 EULA is preserved in
the GPU image.

Use `scripts/clean_install.py --runtime-profile gpu --model-candidate qwen35
--runtime-candidate ollama032 --prepare-only --keep` after building the candidate
worker and matching gateway/test images. The script creates private networks,
database and signed policy, copies the candidate manifest with runtime 0.32.0,
and verifies actual GPU residency. Prepared-only status is not an acceptance
pass. Run `scripts/native_json_probe.py` and the exposed calibration/regression
tests inside that project; no sealed holdout data is used during this comparison.

The worker verifies its actual `/api/version` before readiness, and the signed
manifest must agree with that version. The native-format probe supplies a schema
without a JSON instruction in its message, isolating runtime format enforcement.
This native-format probe passed on the actual 0.32.0 GPU worker. Both local roles
were observed resident in GPU memory at 3,416,596,151 bytes each with context 8192.
The initial 30 second diagnostic cold-load deadline expired with a confirmed stop;
upstream logs isolated 31.40 seconds of model loading and 0.515 seconds of prefill.
Repeating under the unchanged signed 180/120 second role deadlines succeeded.

The complete v2.4 comparison on 0.32.0 scored 16/16 calibration and 38/40 regression:
one false positive and one model-generated unknown. It did not pass quality
acceptance. The raw outputs, scores and native-format proof are preserved under
`artifacts/history/qwen35-v2.4-ollama032`.

The historical v2.7 contract asks the model for one category, source evidence,
risk level and reason. A fixed, signed mapping exposes the corresponding public
verdict. Strict validation rejects contradictory category/risk combinations;
unknown remains a fail-closed result. No risk thresholds were relaxed.
The primary prompt and validation schema exactly match the successful unsigned
prototype, and the subsequent signed run passed all 16 calibration and 40 exposed
regression cases with no false positives, false negatives or unknown results.
These exposed cases are not the independent holdout.

The signed GPU workflow also saved an exact 64 KiB report after seven source
windows, a separate goal-action assessment and an output assessment. All nine
actual calls fit the 4096-token input ceiling (observed maximum 3894), and the
ledger settled 24,126 actual tokens without an overrun. A real goal-drift instruction
hidden in the middle of another 64 KiB document was blocked after full source
coverage and the separate goal review, with no resulting side effect. PII
redaction/save and injection blocking through the UI workflow both passed.
Reports are retained in `artifacts/qwen35-ollama032-tuning` and the versioned
`artifacts/history/qwen35-v2.7-ollama032` directory.

The promoted reference CPU runtime subsequently passed all 13 functional checks,
including actual business tool calling and a confirmed watchdog stop. Its normal
broker workflow saved the exact 64 KiB report in 454.68 seconds, with all seven
source windows, the separate goal review and output inspection completed. The
12 checks in [the archived CPU report](../artifacts/history/guard-v2.7-reference/long-payload-cpu.json) passed. A fresh offline CPU stack
also passed the real document/MCP workflow and OPA, database, guard and gateway
failure/recovery checks. These runtime observations do not replace the independent
semantic holdout or the final source-bound acceptance suite.

The first complete CPU suite on v2.7 stopped with 250 passed tests and one failed
test before calibration and the independent holdout. A real attack in the middle
of a 64 KiB document was safely blocked, but its semantic assessment was incomplete.
Reproduction with the exact input envelope confirmed seven valid source-window
results, including the detected attack. The final goal review added quotation marks around a phrase from a finding's
reason; the resulting string did not occur literally in the derived context.
Strict evidence validation rejected it. The final response used only 89 of 256
output tokens, so this was not an output-limit failure. See the
[failed suite](../artifacts/history/cpu-v27-long-failure/all-local.json) and
[exact goal evidence diagnostic](../artifacts/history/cpu-v27-long-failure/goal-diagnostic.json).

The historical v2.8 contract left the primary source-window prompt, schema and
literal evidence validation unchanged. Its separate goal review returns exact
server-provided reference IDs. The gateway independently checks membership and
resolves each ID to the actual proposed effect or complete validated source
finding, including the original evidence. A native JSON schema enforces the
existing benign, threat and unknown consistency rules; it does not reinterpret
an unknown or repair an invalid model response. Partial validation failures retain
safe stage and coverage diagnostics while remaining incomplete and fail-closed.

The executed four-case goal prototype passed all four cases. Production prompt,
schema and serialized context exactly match that prototype, and 68 focused
contract tests passed. The renewed CPU runtime preflight passed 13 checks, with
both pinned models physically resident on CPU and no GPU access. The current
[legal CPU 64 KiB workflow](../artifacts/history/guard-v2.8-reference/long-payload-cpu.json) passed all 12 checks
in 391.87 seconds: seven source windows, a separate goal review and output
inspection, nine actual calls, at most 3894 of 4096 serialized input tokens per
call, and 25,148 known settled tokens. The stored content matched exactly.
The [hidden CPU 64 KiB attack](../artifacts/history/guard-v2.8-reference/semantic-long-attack.json) then passed
all 10 controls in 495.95 seconds. Seven source windows and the separate goal
review were complete; the goal reference resolved to the offending fourth
window and its original evidence. No business effect occurred, and all 23,683
tokens were accounted for with known usage.
The [fresh offline CPU installation](../artifacts/history/guard-v2.8-reference/clean-install.json) passed all
15 checks in 439.91 seconds, including the actual document/MCP workflow, denied
dispatch during OPA/database/guard failures and recovery of the durable run after
worker and gateway restarts. The [v2.8 GPU proof](../artifacts/history/guard-v2.8-reference/gpu-preflight.json)
passed 19 infrastructure and 13 functional checks in 204.91 seconds, with both
models resident in VRAM and a confirmed independent guard stop. Legal 64 KiB
passed in 13.81 seconds and the hidden 64 KiB attack test in 19.92 seconds;
the separate [GPU evidence directory](../artifacts/history/guard-v2.8-reference/gpu-reference/) preserves
source, physical placement and both long-context reports.

The complete v2.8 CPU suite stopped after 252 passed tests and one failed test,
with zero skips and stable source hashes, in 2362.95 seconds. Calibration passed
16/16. Exposed regression passed 39/40 with zero unknown results and all 20
attacks detected. One English authorized-recording case was a false positive,
giving English FPR 10% against the required maximum 5%; Polish FPR was 0%.
This was a single source window, so the separate goal review was not invoked.
The [failed suite and quality reports](../artifacts/history/cpu-v28-regression-failure/)
are preserved together with its exact reconstructed request envelope. The
original raw reply and evidence quotes were not retained by the quality reporter.
The suite stopped before opening the independent holdout and before completing
all control coverage; that historical v2.8 release did not pass acceptance.
Historical v2.8 evidence is in [production equality](../artifacts/goal-reference-production-equality.json),
[focused tests](../artifacts/goal-reference-contract.xml),
[CPU preflight](../artifacts/history/guard-v2.8-reference/runtime-preflight.json) and
[CPU placement](../artifacts/history/guard-v2.8-reference/runtime-hardware.json).

The current functional, resource, fresh-install, CPU/GPU long-context and CPU
quality evidence is complete. Final release acceptance additionally requires
the full measured performance matrix and the remaining application/demo proofs.
