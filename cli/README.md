# kcia CLI

This package is the CLI half of kcia. It is **not installable on its own** — it reads
`control-plane/` from the repository root at runtime, so it must be installed editable
from a clone:

```bash
python3 -m venv .venv
.venv/bin/pip install -e "./cli[dev]"
```

See the repository README for installation, usage, and current status.
