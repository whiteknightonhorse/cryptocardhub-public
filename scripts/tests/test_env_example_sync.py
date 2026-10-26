"""T-0142-07 (ruling-5): every ${VAR} the affiliates config reads must be
declared in the env template, or the loader silently substitutes empty (Y56)."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_env_example_sync():
    affiliates_yaml = (REPO / "config" / "affiliates.yaml").read_text()
    # Strip YAML comments first — the file's own header uses "${VAR}" as
    # illustrative prose (not a real reference), which would otherwise
    # false-positive into `used`.
    affiliates_code = "\n".join(
        line.split("#", 1)[0] for line in affiliates_yaml.splitlines()
    )
    env_example = (REPO / ".env.example").read_text()
    used = set(re.findall(r"\$\{([A-Z0-9_]+)\}", affiliates_code))
    declared = set(re.findall(r"^([A-Z0-9_]+)=", env_example, re.M))
    assert "PLASMA_REF_URL" in used
    assert used <= declared, used - declared
