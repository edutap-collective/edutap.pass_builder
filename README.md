# edutap.pass_builder

Stateless FastAPI service that builds Apple and Google wallet passes from
versioned templates.

## Installation

The package is not published to PyPI yet. Install it straight from the
source repository:

```console
uv pip install git+https://github.com/edutap-eu/edutap.pass_builder
```

## Development

Clone the repository and install it in editable mode with the development
extras:

```console
git clone https://github.com/edutap-eu/edutap.pass_builder
cd edutap.pass_builder
make install
make lint
make test-local
```

Configuration is read from the environment, prefixed with
`EDUTAP_PASS_BUILDER_` (see `src/edutap/pass_builder/settings.py`).
