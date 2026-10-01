import json
import pathlib

import pytest
from jsonschema import Draft202012Validator

SCHEMA = json.loads((pathlib.Path(__file__).parent.parent / "schemas" / "log-events.schema.json").read_text())


@pytest.fixture(scope="session")
def validate():
    """validate(envelope, 'log_normalized') -> raise nếu sai schema."""
    def _v(envelope, name):
        v = Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]})
        errs = sorted(v.iter_errors(envelope), key=lambda e: list(e.path))
        assert not errs, "\n".join(f"{list(e.path)}: {e.message}" for e in errs[:5])
    return _v
