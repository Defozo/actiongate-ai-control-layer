# Optional GPU runtime

Team: DEFOZO SOFTWARE HOUSE. Author: Michał Kiełtyka.

The default runtime and acceptance profile use CPU. `deploy/compose.gpu.yaml`
adds NVIDIA device access and the CUDA 12 libraries from the same pinned Ollama
image. It keeps separate guard and business workers, process supervisors, queues
and memory limits. Both workers share the GPU. This configuration does not
provide a hardware VRAM partition or a GPU time quota.

The profile explicitly selects `cuda_v12` and two CPU threads per worker.
The current Qwen3.5:4b / Ollama 0.32.0 / guard v2.8 deployment passed all 19
infrastructure and 13 functional checks on 2026-10-04 Europe/Warsaw. Both models
actually held 3,416,596,151 bytes in VRAM. The guard watchdog confirmed process
termination in 53.981 ms while the business worker remained active and completed
492 output tokens without an epoch change. The complete isolated proof took
204.91 seconds. The retained project is `actiongate-gpu-95567ddf`, using
`.state/clean-install/95567ddf/compose.override.yaml` plus `deploy/compose.gpu.yaml`.
See the [current GPU preflight](../artifacts/gpu-preflight.json),
[source binding](../artifacts/gpu-reference/deployed-source.json) and
[physical placement](../artifacts/gpu-reference/runtime-hardware.json).

The same signed v2.8 GPU stack passed the complete legal 64 KiB workflow in
13.81 seconds and the hidden 64 KiB attack test in 19.92 seconds. Both inspect
every source window and make a separate goal-action assessment. The legal write
also includes output inspection and preserves the exact content. Reports are
[legal 64 KiB](../artifacts/gpu-reference/long-payload-gpu.json) and
[hidden attack](../artifacts/gpu-reference/semantic-long-attack.json).
These are integration proofs, not the independent quality holdout or the full
performance matrix. The host had other workloads: 7253 MiB were already used
and GPU utilization was 21% before this proof.

The subsequent CPU acceptance suite failed its exposed regression gate with
one English benign false positive (39/40 correct, zero unknowns). It stopped
before the independent holdout. The full GPU performance matrix therefore
remains pending; the existing runtime proofs do not imply release acceptance.
See the [preserved CPU failure](../artifacts/history/cpu-v28-regression-failure/).

The following results belong to the historical v2.7 deployment and must not be
presented as v2.8 acceptance.

The historical v2.7 deployment passed all
19 infrastructure checks and all 13 functional checks on the reference RTX 4090
on 2026-10-03. Both models actually held 3,416,596,151 bytes in VRAM. The guard
watchdog confirmed process termination in 1.49 ms while the business worker
remained active and then completed 492 output tokens without an epoch change.
The signed generation survived OPA, database, worker and gateway restarts.
The measured project was `actiongate-gpu-a40dce0f`, now stopped; its report is
[`artifacts/history/guard-v2.7-reference/gpu-preflight.json`](../artifacts/history/guard-v2.7-reference/gpu-preflight.json),
with separate source and physical placement proofs in the adjacent `gpu-reference`
directory. These runtime checks are separate from
the independent semantic quality holdout. The host also runs other workloads;
the measurements do not claim exclusive CPU or GPU access.

The v2.8 goal review uses server-resolved references to validated findings instead
of generated quotations from their JSON serialization. The CPU failure and the
new contract are documented in [runtime-candidate.md](runtime-candidate.md).
The main deployment remains the CPU reference; the final GPU project is retained
separately for performance measurement and the interactive demonstration.

An earlier Ollama 0.18.2 / Qwen3 comparison encountered a CUDA 13 model-allocation
stall. Its successful CUDA 12 diagnostic is preserved in
`artifacts/gpu-cuda12-diagnostic.json`. That historical result is not the evidence
for the currently selected model and runtime.

Prepare the additional image with network access if its pinned base layers are
not already available:

```powershell
uv run python scripts/runtime_bootstrap.py compose -f compose.yaml -f deploy/compose.gpu.yaml build guard-worker
```

Run the isolated proof:

```powershell
uv run python scripts/gpu_preflight.py
```

This creates a separate Compose project with private database, copied policy
and internal networks. Its acceptance client calls the real edge inside the
project; Docker does not expose host ports from these internal bridges.
It reuses the immutable prepared model volume.
It never recreates the reference CPU services. Startup uses existing images
with downloads disabled. The project is removed after verification; `--keep`
retains it for diagnosis.

The report is `artifacts/gpu-preflight.json`. It must show real model inference
on both workers, positive `size_vram` for each loaded model, full-context and
tool-call checks, a confirmed watchdog stop while the other worker is still
executing, successful completion of that other inference, and recovery of the
same signed generation after infrastructure restarts. Device visibility alone
does not pass this proof. Absence of a passing report means the profile remains
unverified on the current machine. Regenerate the library inventory with
`uv run python scripts/gpu_inventory.py`; it reads the actual prepared image
without network access or loading models.

To use a verified GPU profile as the main deployment, explicitly include the
override in the start command. Stop or finish active runs before switching an
existing deployment's worker profile:

```powershell
uv run python scripts/runtime_bootstrap.py compose -f compose.yaml -f deploy/compose.gpu.yaml up -d --no-build --pull never
```

Local inference accounting records measured CPU time and occupied worker time.
It does not label either quantity as GPU seconds or provider charges.
