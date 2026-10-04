# Optional GPU runtime

Team: DEFOZO SOFTWARE HOUSE. Author: Michał Kiełtyka.

The default runtime and acceptance profile use CPU. `deploy/compose.gpu.yaml`
adds NVIDIA device access and the CUDA 12 libraries from the same pinned Ollama
image. It keeps separate guard and business workers, process supervisors, queues
and memory limits. Both workers share the GPU. This configuration does not
provide a hardware VRAM partition or a GPU time quota.

The profile explicitly selects `cuda_v12` and two CPU threads per worker.
The selected classifier v2.9 passed 19 GPU infrastructure and 13 functional
checks in 160.39 seconds after the PostgreSQL capacity correction. Both pinned models actually held 3,416,596,151 bytes
in VRAM. The guard process stopped in 52.031 ms while business inference remained
active and completed 492 output tokens without an epoch change. Its [preflight](../artifacts/gpu-preflight.json),
[source binding](../artifacts/gpu-reference/deployed-source.json) and
[physical placement](../artifacts/gpu-reference/runtime-hardware.json) bind
these measurements to actual worker images and signed model artifacts.

The same frozen classifier and model on the preceding PostgreSQL-100 project
`actiongate-gpu-29b4b2ed`, now stopped, passed the
[legal 64 KiB workflow](../artifacts/gpu-reference/long-payload-gpu.json), which
passed 12/12 checks in 12.97 seconds, with nine real calls and 25,125 known settled
tokens. Its maximum serialized input was 4056/4096 tokens. The matching
[hidden attack](../artifacts/gpu-reference/semantic-long-attack.json) passed all
10 checks in 9.32 seconds of broker execution (16.92 seconds for the complete
pytest invocation), including all seven windows, separate goal review and zero
business effects. These long-context measurements were not rerun for the database-only
capacity change. The host was shared: 7033 MiB of VRAM and 14% GPU utilization
were observed before the new GPU preflight. The first complete signed v2.9 CPU suite passed
493/493 tests, including independent v2 40/40 and four unchanged repeats. The
[archived result](../artifacts/history/cpu-v29-before-metrics-fix/all-local.json)
records stable source hashes and all 17 control areas. After the benchmark-producer
endpoint correction, the [final CPU suite](../artifacts/all-local.json) passed
602/602 tests, zero failures/skips and 17/17 control coverage, in 2156.49 seconds.
The same v2 sample again passed 40/40, with zero unknowns and unchanged fixed
repeats. Model and classifier behavior remained unchanged.

The first full 144-row/2928-request performance matrix exposed PostgreSQL connection
exhaustion at 50 clients. Its [original evidence](../artifacts/history/benchmark-pg100-before-capacity-fix/)
is preserved. The deployment now uses 200 connections, a 2 GiB PostgreSQL memory
limit and 256 PIDs, confirmed by the [actual configuration probe](../artifacts/gpu-reference/pg-capacity.json).
The database-only change passed 591/591 controlled CPU contracts and fresh CPU/GPU
installation and recovery proofs. The 602-case CPU quality suite is preserved
[with its original PostgreSQL-100 context](../artifacts/history/cpu-v29-pg100-before-capacity-fix/).
The subsequent full matrix completed all 144 cells and 2,928 attempts with zero
transport errors. The initial deterministic overhead target was not met; see
[benchmark results](benchmark.md) for timings and capacity rejections.

## Reproduce the GPU checks

Earlier runtime versions and their failed or successful checks remain in the
dated artifact history. Use the current model and image digests when comparing
results. GPU measurements do not replace the CPU acceptance profile, and a
shared GPU does not provide hardware isolation between workers.

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
