"""Verify prepared GPU image in an isolated offline stack, leaving CPU unchanged.

Preparation: runtime_bootstrap.py compose -f compose.yaml -f deploy/compose.gpu.yaml build guard-worker
Proof: python scripts/gpu_preflight.py
"""
from pathlib import Path
import subprocess
import sys


if __name__=='__main__':
    raise SystemExit(subprocess.run([sys.executable,str(Path(__file__).with_name('clean_install.py')),
                                    '--runtime-profile','gpu','--port','18089',*sys.argv[1:]]).returncode)
