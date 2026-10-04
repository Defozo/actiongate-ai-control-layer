# Selected Qwen3.5 / Ollama 0.32.0 runtime

The release pins now select Qwen3.5:4b on Ollama 0.32.0. The selection followed
an isolated GPU comparison. Final reference CPU, resource, restart and independent
quality acceptance remain separate required checks; selection alone is not a pass.

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

The selected v2.8 contract leaves the primary source-window prompt, schema and
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
[legal CPU 64 KiB workflow](../artifacts/long-payload-cpu.json) passed all 12 checks
in 391.87 seconds: seven source windows, a separate goal review and output
inspection, nine actual calls, at most 3894 of 4096 serialized input tokens per
call, and 25,148 known settled tokens. The stored content matched exactly.
The [hidden CPU 64 KiB attack](../artifacts/semantic-long-attack.json) then passed
all 10 controls in 495.95 seconds. Seven source windows and the separate goal
review were complete; the goal reference resolved to the offending fourth
window and its original evidence. No business effect occurred, and all 23,683
tokens were accounted for with known usage.
The [fresh offline CPU installation](../artifacts/clean-install.json) passed all
15 checks in 439.91 seconds, including the actual document/MCP workflow, denied
dispatch during OPA/database/guard failures and recovery of the durable run after
worker and gateway restarts. The [v2.8 GPU proof](../artifacts/gpu-preflight.json)
passed 19 infrastructure and 13 functional checks in 204.91 seconds, with both
models resident in VRAM and a confirmed independent guard stop. Legal 64 KiB
passed in 13.81 seconds and the hidden 64 KiB attack test in 19.92 seconds;
the separate [GPU evidence directory](../artifacts/gpu-reference/) preserves
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
all control coverage; full release acceptance has not passed.
Current evidence is in [production equality](../artifacts/goal-reference-production-equality.json),
[focused tests](../artifacts/goal-reference-contract.xml),
[CPU preflight](../artifacts/runtime-preflight.json) and
[CPU placement](../artifacts/runtime-hardware.json).

Final release acceptance still requires the complete functional, resource, CPU,
GPU and independent quality evidence.
