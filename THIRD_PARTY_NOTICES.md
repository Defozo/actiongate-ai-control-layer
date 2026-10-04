# Third-party notices

Prepared for DEFOZO SOFTWARE HOUSE. Team member: Michał Kiełtyka.

Exact Python and JavaScript versions below come from uv.lock, runtime/requirements.lock and ui/package-lock.json. License declarations come from the installed locked distributions or exact-version publisher metadata. Preserved license and notice texts are in artifacts/licenses. This inventory distinguishes model weights from their inference runner.

The submission archive contains application source, model/tokenizer manifests, reports and notices. Container base images and model weights are fetched from pinned upstream references by bootstrap; their existing license and OS package notices remain in the original distributions. The application-level CycloneDX inventory does not claim to enumerate every OS package inside those images.

## Model and runtime licenses

| Component | License | Primary source |
| --- | --- | --- |
| Qwen3.5-4B | Apache-2.0 | [Upstream license](https://huggingface.co/Qwen/Qwen3.5-4B/resolve/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a/LICENSE) |
| Qwen3.5-4B-tokenizer | Apache-2.0 | [Upstream license](https://huggingface.co/Qwen/Qwen3.5-4B/resolve/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a/LICENSE) |
| ollama-0.32.0 | MIT | [Upstream license](https://raw.githubusercontent.com/ollama/ollama/v0.32.0/LICENSE) |
| opa-1.15.0 | Apache-2.0 | [Upstream license](https://raw.githubusercontent.com/open-policy-agent/opa/v1.15.0/LICENSE) |
| postgresql-17.11 | PostgreSQL | [Upstream license](https://raw.githubusercontent.com/postgres/postgres/REL_17_11/COPYRIGHT) |
| nginx-1.30.5 | BSD-2-Clause | [Upstream license](https://raw.githubusercontent.com/nginx/nginx/release-1.30.5/LICENSE) |
| CPython-3.13.15 | PSF-2.0 | [Upstream license](https://raw.githubusercontent.com/python/cpython/v3.13.15/LICENSE) |
| Node.js-24.18.0 | MIT AND third-party licenses | [Upstream license](https://raw.githubusercontent.com/nodejs/node/v24.18.0/LICENSE) |
| NVIDIA-CUDA-Toolkit-12.8.2 | NVIDIA CUDA Toolkit EULA | [Upstream license](https://docs.nvidia.com/cuda/archive/12.8.2/eula/index.html) |

The optional Groq adapter calls a hosted service and does not redistribute its model weights. Provider service terms apply separately. The prepared local model `qwen3.5:4b` and its pinned tokenizer use Apache-2.0; `ollama:0.32.0` uses MIT. Exact model, tokenizer and runtime references come from models/model-manifest.json.

The optional GPU image uses NVIDIA CUDA libraries governed by the CUDA Toolkit EULA and supplement. Their exact versions were observed in the pinned Ollama source image and checked against NVIDIA's versioned redistributable catalogs. The source ZIP includes references and notices, not the CUDA binaries or container image. The CPU target does not copy these CUDA backend directories.

| Optional GPU library | Version | Toolkit / primary version evidence |
| --- | --- | --- |
| libcudart | 12.8.90 | [CUDA 12.8.2 catalog](https://developer.download.nvidia.com/compute/cuda/redist/redistrib_12.8.2.json) |
| libcublas/libcublasLt | 12.8.5.5 | [CUDA 12.8.2 catalog](https://developer.download.nvidia.com/compute/cuda/redist/redistrib_12.8.2.json) |

## Locked application dependencies

| Ecosystem | Package | Version | License declaration |
| --- | --- | --- | --- |
| npm | @oxc-project/types | 0.152.0 | MIT |
| npm | @playwright/test | 1.63.0 | Apache-2.0 |
| npm | @radix-ui/primitive | 1.1.7 | MIT |
| npm | @radix-ui/react-compose-refs | 1.1.5 | MIT |
| npm | @radix-ui/react-context | 1.2.2 | MIT |
| npm | @radix-ui/react-dialog | 1.1.23 | MIT |
| npm | @radix-ui/react-dismissable-layer | 1.1.19 | MIT |
| npm | @radix-ui/react-focus-guards | 1.1.6 | MIT |
| npm | @radix-ui/react-focus-scope | 1.1.16 | MIT |
| npm | @radix-ui/react-id | 1.1.4 | MIT |
| npm | @radix-ui/react-portal | 1.1.17 | MIT |
| npm | @radix-ui/react-presence | 1.1.10 | MIT |
| npm | @radix-ui/react-primitive | 2.1.10 | MIT |
| npm | @radix-ui/react-slot | 1.3.3 | MIT |
| npm | @radix-ui/react-use-callback-ref | 1.1.4 | MIT |
| npm | @radix-ui/react-use-controllable-state | 1.2.6 | MIT |
| npm | @radix-ui/react-use-effect-event | 0.0.5 | MIT |
| npm | @radix-ui/react-use-layout-effect | 1.1.4 | MIT |
| npm | @reduxjs/toolkit | 2.13.0 | MIT |
| npm | @rolldown/binding-android-arm-eabi | 1.2.12 | MIT |
| npm | @rolldown/binding-android-arm64 | 1.2.12 | MIT |
| npm | @rolldown/binding-darwin-arm64 | 1.2.12 | MIT |
| npm | @rolldown/binding-darwin-x64 | 1.2.12 | MIT |
| npm | @rolldown/binding-freebsd-x64 | 1.2.12 | MIT |
| npm | @rolldown/binding-linux-arm-gnueabihf | 1.2.12 | MIT |
| npm | @rolldown/binding-linux-arm64-gnu | 1.2.12 | MIT |
| npm | @rolldown/binding-linux-arm64-musl | 1.2.12 | MIT |
| npm | @rolldown/binding-linux-ppc64-gnu | 1.2.12 | MIT |
| npm | @rolldown/binding-linux-s390x-gnu | 1.2.12 | MIT |
| npm | @rolldown/binding-linux-x64-gnu | 1.2.12 | MIT |
| npm | @rolldown/binding-linux-x64-musl | 1.2.12 | MIT |
| npm | @rolldown/binding-openharmony-arm64 | 1.2.12 | MIT |
| npm | @rolldown/binding-win32-arm64-msvc | 1.2.12 | MIT |
| npm | @rolldown/binding-win32-x64-msvc | 1.2.12 | MIT |
| npm | @rolldown/pluginutils | 1.0.1 | MIT |
| npm | @standard-schema/spec | 1.1.0 | MIT |
| npm | @standard-schema/utils | 0.3.0 | MIT |
| npm | @tanstack/query-core | 5.104.1 | MIT |
| npm | @tanstack/react-query | 5.104.1 | MIT |
| npm | @tanstack/react-table | 8.21.3 | MIT |
| npm | @tanstack/table-core | 8.21.3 | MIT |
| npm | @types/d3-array | 3.2.2 | MIT |
| npm | @types/d3-color | 3.1.3 | MIT |
| npm | @types/d3-ease | 3.0.2 | MIT |
| npm | @types/d3-interpolate | 3.0.4 | MIT |
| npm | @types/d3-path | 3.1.1 | MIT |
| npm | @types/d3-scale | 4.0.9 | MIT |
| npm | @types/d3-shape | 3.2.0 | MIT |
| npm | @types/d3-time | 3.0.4 | MIT |
| npm | @types/d3-timer | 3.0.2 | MIT |
| npm | @types/react | 19.3.0 | MIT |
| npm | @types/react-dom | 19.3.0 | MIT |
| npm | @types/use-sync-external-store | 0.0.6 | MIT |
| npm | @typescript/typescript-aix-ppc64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-darwin-arm64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-darwin-x64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-freebsd-arm64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-freebsd-x64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-linux-arm | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-linux-arm64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-linux-loong64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-linux-mips64el | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-linux-ppc64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-linux-riscv64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-linux-s390x | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-linux-x64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-netbsd-arm64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-netbsd-x64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-openbsd-arm64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-openbsd-x64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-sunos-x64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-win32-arm64 | 7.0.2 | Apache-2.0 |
| npm | @typescript/typescript-win32-x64 | 7.0.2 | Apache-2.0 |
| npm | @vitejs/plugin-react | 6.1.1 | MIT |
| npm | aria-hidden | 1.2.6 | MIT |
| npm | clsx | 2.1.1 | MIT |
| npm | csstype | 3.2.3 | MIT |
| npm | d3-array | 3.2.4 | ISC |
| npm | d3-color | 3.1.0 | ISC |
| npm | d3-ease | 3.0.1 | BSD-3-Clause |
| npm | d3-format | 3.1.2 | ISC |
| npm | d3-interpolate | 3.0.1 | ISC |
| npm | d3-path | 3.1.0 | ISC |
| npm | d3-scale | 4.0.2 | ISC |
| npm | d3-shape | 3.2.0 | ISC |
| npm | d3-time | 3.1.0 | ISC |
| npm | d3-time-format | 4.1.0 | ISC |
| npm | d3-timer | 3.0.1 | ISC |
| npm | decimal.js-light | 2.5.1 | MIT |
| npm | detect-libc | 2.1.2 | Apache-2.0 |
| npm | detect-node-es | 1.1.0 | MIT |
| npm | es-toolkit | 1.52.0 | MIT |
| npm | eventemitter3 | 5.0.4 | MIT |
| npm | fdir | 6.5.0 | MIT |
| npm | fsevents | 2.3.3 | MIT |
| npm | get-nonce | 1.0.1 | MIT |
| npm | immer | 11.1.21 | MIT |
| npm | internmap | 2.0.3 | ISC |
| npm | lightningcss | 1.33.0 | MPL-2.0 |
| npm | lightningcss-android-arm64 | 1.33.0 | MPL-2.0 |
| npm | lightningcss-darwin-arm64 | 1.33.0 | MPL-2.0 |
| npm | lightningcss-darwin-x64 | 1.33.0 | MPL-2.0 |
| npm | lightningcss-freebsd-x64 | 1.33.0 | MPL-2.0 |
| npm | lightningcss-linux-arm-gnueabihf | 1.33.0 | MPL-2.0 |
| npm | lightningcss-linux-arm64-gnu | 1.33.0 | MPL-2.0 |
| npm | lightningcss-linux-arm64-musl | 1.33.0 | MPL-2.0 |
| npm | lightningcss-linux-x64-gnu | 1.33.0 | MPL-2.0 |
| npm | lightningcss-linux-x64-musl | 1.33.0 | MPL-2.0 |
| npm | lightningcss-win32-arm64-msvc | 1.33.0 | MPL-2.0 |
| npm | lightningcss-win32-x64-msvc | 1.33.0 | MPL-2.0 |
| npm | lucide-react | 1.51.0 | ISC |
| npm | nanoid | 3.3.19 | MIT |
| npm | picocolors | 1.1.1 | ISC |
| npm | picomatch | 4.0.7 | MIT |
| npm | playwright | 1.63.0 | Apache-2.0 |
| npm | playwright-core | 1.63.0 | Apache-2.0 |
| npm | postcss | 8.5.28 | MIT |
| npm | prettier | 3.9.9 | MIT |
| npm | react | 19.3.0 | MIT |
| npm | react-dom | 19.3.0 | MIT |
| npm | react-is | 19.3.0 | MIT |
| npm | react-redux | 9.3.0 | MIT |
| npm | react-remove-scroll | 2.7.2 | MIT |
| npm | react-remove-scroll-bar | 2.3.8 | MIT |
| npm | react-style-singleton | 2.2.3 | MIT |
| npm | recharts | 3.10.1 | MIT |
| npm | redux | 5.0.1 | MIT |
| npm | redux-thunk | 3.1.0 | MIT |
| npm | reselect | 5.2.0 | MIT |
| npm | rolldown | 1.2.12 | MIT |
| npm | scheduler | 0.28.0 | MIT |
| npm | source-map-js | 1.2.2 | BSD-3-Clause |
| npm | tiny-invariant | 1.3.3 | MIT |
| npm | tinyglobby | 0.2.17 | MIT |
| npm | tslib | 2.8.1 | 0BSD |
| npm | typescript | 7.0.2 | Apache-2.0 |
| npm | use-callback-ref | 1.3.3 | MIT |
| npm | use-sidecar | 1.1.3 | MIT |
| npm | use-sync-external-store | 1.7.0 | MIT |
| npm | victory-vendor | 37.3.6 | MIT AND ISC |
| npm | vite | 8.3.2 | MIT |
| pypi | alembic | 1.20.0 | MIT |
| pypi | annotated-doc | 0.0.5 | MIT |
| pypi | annotated-types | 0.8.0 | MIT |
| pypi | anyio | 4.15.1 | MIT |
| pypi | attrs | 26.1.0 | MIT |
| pypi | bidict | 0.24.1 | MPL-2.0 |
| pypi | blinker | 1.9.0 | MIT |
| pypi | blis | 1.3.3 | BSD (see preserved license) |
| pypi | brotli | 1.2.0 | MIT |
| pypi | catalogue | 2.0.10 | MIT |
| pypi | certifi | 2026.7.22 | MPL-2.0 |
| pypi | cffi | 2.1.1 | MIT-0 |
| pypi | charset-normalizer | 3.5.2 | MIT |
| pypi | click | 8.5.0 | BSD-3-Clause |
| pypi | cloudpathlib | 0.26.0 | MIT |
| pypi | colorama | 0.4.6 | BSD (see preserved license) |
| pypi | confection | 1.3.3 | MIT |
| pypi | configargparse | 1.8.0 | MIT |
| pypi | cryptography | 46.0.7 | Apache-2.0 OR BSD-3-Clause |
| pypi | cymem | 2.0.13 | MIT |
| pypi | fastapi | 0.135.1 | MIT |
| pypi | fastapi | 0.142.2 | MIT |
| pypi | filelock | 4.0.9 | MIT |
| pypi | flask | 3.1.3 | BSD-3-Clause |
| pypi | flask-cors | 6.0.5 | MIT |
| pypi | flask-login | 0.6.3 | MIT |
| pypi | fsspec | 2026.9.0 | BSD-3-Clause |
| pypi | gevent | 26.9.0 | MIT |
| pypi | geventhttpclient | 2.5.1 | MIT |
| pypi | google-re2 | 1.1.20251105 | BSD (see preserved license) |
| pypi | greenlet | 3.5.6 | MIT AND PSF-2.0 |
| pypi | h11 | 0.16.0 | MIT |
| pypi | hf-xet | 1.6.0 | Apache-2.0 |
| pypi | httpcore | 1.0.9 | BSD-3-Clause |
| pypi | httptools | 0.8.0 | MIT |
| pypi | httpx | 0.28.1 | BSD (see preserved license) |
| pypi | httpx-sse | 0.4.3 | MIT |
| pypi | huggingface-hub | 1.33.0 | Apache-2.0 |
| pypi | hypothesis | 6.168.3 | MPL-2.0 |
| pypi | idna | 3.20 | BSD-3-Clause |
| pypi | iniconfig | 2.3.0 | MIT |
| pypi | itsdangerous | 2.2.0 | BSD (see preserved license) |
| pypi | jinja2 | 3.1.6 | BSD (see preserved license) |
| pypi | jsonschema | 4.26.0 | MIT |
| pypi | jsonschema-specifications | 2025.9.1 | MIT |
| pypi | locust | 2.46.6 | MIT |
| pypi | mako | 1.4.3 | MIT |
| pypi | markdown-it-py | 4.2.0 | MIT |
| pypi | markupsafe | 3.0.4 | BSD-3-Clause |
| pypi | mcp | 1.30.0 | MIT |
| pypi | mdurl | 0.1.2 | MIT |
| pypi | msgpack | 1.2.3 | Apache-2.0 |
| pypi | murmurhash | 1.0.15 | MIT |
| pypi | numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| pypi | opentelemetry-api | 1.45.0 | Apache-2.0 |
| pypi | opentelemetry-sdk | 1.45.0 | Apache-2.0 |
| pypi | opentelemetry-semantic-conventions | 0.66b0 | Apache-2.0 |
| pypi | packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pypi | phonenumbers | 9.0.40 | Apache-2.0 |
| pypi | pip-licenses | 5.5.5 | MIT |
| pypi | pluggy | 1.6.0 | MIT |
| pypi | preshed | 3.0.13 | MIT |
| pypi | presidio-analyzer | 2.2.364 | MIT |
| pypi | prettytable | 3.18.0 | BSD-3-Clause |
| pypi | prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause |
| pypi | psutil | 7.2.2 | BSD-3-Clause |
| pypi | psycopg | 3.3.6 | LGPL-3.0-only |
| pypi | psycopg-binary | 3.3.6 | LGPL-3.0-only |
| pypi | pycparser | 3.0 | BSD-3-Clause |
| pypi | pydantic | 2.13.5 | MIT |
| pypi | pydantic-core | 2.46.5 | MIT |
| pypi | pydantic-settings | 2.15.0 | MIT |
| pypi | pygments | 2.21.0 | BSD-2-Clause |
| pypi | pyjwt | 2.15.1 | MIT |
| pypi | pytest | 8.4.2 | MIT |
| pypi | pytest-asyncio | 1.4.0 | Apache-2.0 |
| pypi | python-dotenv | 1.2.4 | BSD-3-Clause |
| pypi | python-engineio | 4.14.0 | MIT |
| pypi | python-multipart | 0.0.32 | Apache-2.0 |
| pypi | python-socketio | 5.17.0 | MIT |
| pypi | pywin32 | 312 | PSF-2.0 |
| pypi | pyyaml | 6.0.3 | MIT |
| pypi | pyzmq | 27.2.0 | BSD-3-Clause |
| pypi | referencing | 0.37.0 | MIT |
| pypi | regex | 2026.9.29 | Apache-2.0 AND CNRI-Python |
| pypi | requests | 2.34.2 | Apache-2.0 |
| pypi | requests-file | 3.0.1 | Apache-2.0 |
| pypi | rich | 15.0.0 | MIT |
| pypi | rpds-py | 2026.6.3 | MIT |
| pypi | ruff | 0.16.10 | MIT |
| pypi | setuptools | 84.0.0 | MIT |
| pypi | shellingham | 1.5.4 | ISC |
| pypi | simple-websocket | 1.1.0 | MIT |
| pypi | smart-open | 8.0.2 | MIT |
| pypi | sortedcontainers | 2.4.0 | Apache-2.0 |
| pypi | spacy | 3.8.16 | MIT |
| pypi | spacy-legacy | 3.0.12 | MIT |
| pypi | spacy-loggers | 1.0.5 | MIT |
| pypi | sqlalchemy | 2.1.3 | MIT |
| pypi | srsly | 2.5.4 | MIT |
| pypi | sse-starlette | 3.5.0 | BSD-3-Clause |
| pypi | starlette | 1.7.0 | BSD-3-Clause |
| pypi | thinc | 8.3.13 | MIT |
| pypi | tldextract | 5.3.2 | BSD-3-Clause |
| pypi | tokenizers | 0.22.2 | Apache-2.0 |
| pypi | tokenizers | 0.23.2 | Apache-2.0 |
| pypi | tqdm | 4.70.1 | MPL-2.0 AND MIT |
| pypi | typer | 0.27.2 | MIT |
| pypi | typing-extensions | 4.16.0 | PSF-2.0 |
| pypi | typing-inspection | 0.4.4 | MIT |
| pypi | tzdata | 2026.5 | Apache-2.0 |
| pypi | urllib3 | 2.8.0 | MIT |
| pypi | uvicorn | 0.42.0 | BSD-3-Clause |
| pypi | uvicorn | 0.54.0 | BSD-3-Clause |
| pypi | uvloop | 0.23.0 | Apache-2.0 OR MIT |
| pypi | wasabi | 1.1.3 | MIT |
| pypi | watchfiles | 1.3.0 | MIT |
| pypi | wcwidth | 0.9.1 | MIT |
| pypi | weasel | 1.0.0 | MIT |
| pypi | websocket-client | 1.9.2 | Apache-2.0 |
| pypi | websockets | 17.1 | BSD-3-Clause |
| pypi | werkzeug | 3.1.9 | BSD-3-Clause |
| pypi | wrapt | 2.5.0 | BSD-2-Clause |
| pypi | wsproto | 1.3.2 | MIT |
| pypi | zope-event | 6.2 | ZPL-2.1 |
| pypi | zope-interface | 8.6 | ZPL-2.1 |

## Attribution and redistribution

Preserve upstream copyright, license and NOTICE files when redistributing dependencies. Dependencies declaring LGPL, GPL, MPL or other reciprocal terms retain those terms; inspect their preserved notices and corresponding upstream source before distributing modified binaries. This application inventory does not relicense third-party software.

Unknown declarations: none in the resolved application lockfiles.
