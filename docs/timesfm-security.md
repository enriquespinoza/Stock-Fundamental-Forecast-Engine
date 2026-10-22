# TimesFM research environment security

## Scope and boundary

TimesFM is an optional, research-only dependency. Its forecast files are
features for the shadow evaluation; they are not optimizer inputs, portfolio
targets, or execution instructions. Do not place broker credentials, API
tokens, or order permissions in the TimesFM environment.

A Python virtual environment keeps Python packages separate from other Python
environments. It is **not** an operating-system sandbox: code installed or run
inside it can use files, network access, and permissions available to the
Windows account. For stronger isolation, run research under a separate Windows
account or VM with only the required read-only input snapshots and a dedicated
output folder. Never mount execution-engine secrets or writable broker/order
directories into that environment.

Package installation is a software supply-chain trust decision. Install from
the expected package index, inspect dependency changes, and retain an exact
environment inventory. Keep optional TimesFM and training dependencies out of
the default SEC-ingestion and execution environments.

## Windows Git Bash setup

From the repository root, create a dedicated virtual environment:

```bash
python -m venv .venv-timesfm
source .venv-timesfm/Scripts/activate
python -m pip install -e ".[timesfm,research]"
python -m pip check
python -m pip freeze --all > timesfm-environment.txt
```

The project pins its direct TimesFM and research dependencies. The generated
`timesfm-environment.txt` records the complete resolved package versions for
this machine. Treat it as an environment inventory, not a portable lockfile:
editable installs can include a machine-specific project path. Review it and
replace that entry with the exact source commit before using it to recreate the
environment elsewhere. A version inventory improves reproducibility but is
not a cryptographic package-signature check.

## Pin and record the model

Resolve the Hub repository to a commit SHA once, record it with the experiment,
and pass that exact 40-character lowercase SHA on every feature build:

```bash
TIMESFM_REVISION=$(python -c "from huggingface_hub import model_info; print(model_info('google/timesfm-2.5-200m-pytorch').sha)")
echo "$TIMESFM_REVISION"
```

```bash
python scripts/build_timesfm_forecast_features.py \
  --events "$EVENTS" \
  --prices "$PRICES" \
  --price-manifest "$PRICE_MANIFEST" \
  --checkpoint-revision "$TIMESFM_REVISION"
```

The CLI rejects branch names and abbreviated revisions. It records the pinned
revision in the feature manifest, and downstream model training independently
requires the same full SHA. A changed model revision starts a new experiment.

The TimesFM adapter uses the installed `timesfm` package API and does not enable
Hugging Face `trust_remote_code`. Do not add a remote-code execution option
without reviewing the exact repository code and creating a new security review
and experiment version. Prefer the model's `safetensors` weights when available;
this does not make the surrounding Python package or its dependencies
automatically safe.

## Agent and data handling

TimesFM inference returns numeric forecast values; it has no tool or order
interface in this project. If an AI agent is used to operate the workflow,
restrict its shell, file, and network tools at the operating-system boundary.
Treat filing text and downloaded artifacts as untrusted data, not instructions.
Keep any agent in the same research-only boundary, with no broker credentials
and no ability to modify optimizer targets or submit orders.

Before sharing a run, verify that the manifests identify the exact package
inventory, checkpoint SHA, input hashes, and code revision. Forecast and scoring
artifacts remain shadow research and must not be wired into portfolio or
execution systems by this framework.
