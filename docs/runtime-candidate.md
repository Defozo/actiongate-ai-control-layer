# Local inference runtime

The reference deployment uses the pinned Qwen3.5-4B Q4_K_M model with
Ollama 0.32.0 and guard contract v2.9. Separate business and guard workers have
their own queues, process supervisors and limits. The CPU profile is the
default; the [GPU profile](gpu.md) adds explicit NVIDIA device access.

## Readiness and controls

The worker verifies its actual runtime version against the signed manifest.
Model and tokenizer digests, prepared generation and real residency are checked
before acceptance. Structured output is validated independently by the gateway.
An invalid response remains unknown and fails closed.

Long inputs are inspected in bounded source windows, followed by a separate
goal/action review and output review. References in the goal assessment must
resolve to validated server-provided findings or the proposed action. Worker
cancellation releases a slot only after process termination is confirmed.

## Recorded v2.9 measurements

| Measurement | Recorded result | Evidence |
| --- | --- | --- |
| CPU functional preflight | 13 checks passed | [Preflight](../artifacts/runtime-preflight.json) |
| CPU legal 64 KiB workflow | 12 checks; 354.83 seconds; nine model calls | [Long input](../artifacts/long-payload-cpu.json) |
| CPU hidden 64 KiB attack | 10 checks; 340.53 seconds; no business effect | [Attack result](../artifacts/semantic-long-attack.json) |
| Fresh offline CPU installation and recovery | 15 checks; 246.88 seconds | [Clean install](../artifacts/clean-install.json) |
| GPU infrastructure and functional preflight | 19 infrastructure and 13 functional checks; 160.39 seconds | [GPU preflight](../artifacts/gpu-preflight.json) |

The 602-case CPU suite and long-context measurements used the original
PostgreSQL-100 allocation. After the database-only change to 200 connections,
2 GiB and 256 processes, 591 controlled contracts and fresh CPU/GPU installation
checks passed. Application, model and classifier hashes remained unchanged.
The complete revised benchmark subsequently covered 144 cells and 2,928
attempts with zero transport errors. It did not meet the initial 50 ms
deterministic p95 overhead target. [Benchmark documentation](benchmark.md)
records capacity rejections, timings and measurement limits.

Historical model trials and their exact sources are retained as dated evidence.
They are distinct from current deployment checks. For current source/image
bindings use [deployed source](../artifacts/deployed-source.json),
[hardware placement](../artifacts/runtime-hardware.json) and [acceptance](acceptance.md).

## Verify your installation

After bootstrap and start, run `scripts/doctor.ps1`,
`uv run python scripts/deployed_source.py`,
`uv run python scripts/runtime_hardware.py` and the `all-local` verification suite.
Preparation needs network access for pinned dependencies and weights. The
prepared local profile operates without a paid provider. See the
[installation guide](../README.md#run-locally) for the complete commands.
