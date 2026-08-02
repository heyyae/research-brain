import os
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]

# Read .env directly rather than merging into os.environ: tools like Claude Code can
# already have ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY set in the ambient shell (pointed at
# the real Anthropic API), and load_dotenv() would never override those. This project's
# own .env must always win for its own config.
_env_file = dotenv_values(REPO_ROOT / ".env")


def _get(*names: str, default: str | None = None) -> str | None:
    for name in names:
        if _env_file.get(name):
            return _env_file[name]
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    return default


# Support both the generic ANTHROPIC_* names (see .env.example) and Pair Foundry's own
# PAIR_BASE_URL / PAIR_API_KEY naming, in case .env was filled in with the latter.
ANTHROPIC_BASE_URL = _get("ANTHROPIC_BASE_URL", "PAIR_BASE_URL")
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY", "PAIR_API_KEY")
MODEL_NAME = _get("RESEARCH_BRAIN_MODEL", default="claude-sonnet-4-20250514-v1:rsn")

DB_PATH = Path(_get("RESEARCH_BRAIN_DB_PATH") or (REPO_ROOT / "data" / "research_brain.db"))
TRANSCRIPTS_DIR = REPO_ROOT / "transcripts"
OUTPUTS_DIR = REPO_ROOT / "outputs"

HYPOTHESIS_TOP_N = 3
