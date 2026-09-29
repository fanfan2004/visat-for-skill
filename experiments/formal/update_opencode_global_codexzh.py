"""Safely merge the documented CodexZH Responses provider into OpenCode config.

The existing API key is preserved in memory and is never printed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


CONFIG = Path(
    os.environ.get(
        "OPENCODE_GLOBAL_CONFIG",
        Path.home() / ".config" / "opencode" / "opencode.json",
    )
).expanduser()


def main() -> int:
    if not CONFIG.is_file():
        raise FileNotFoundError(f"OpenCode config does not exist: {CONFIG}")
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    providers = payload.setdefault("provider", {})
    provider = providers.setdefault("openai", {})
    provider["npm"] = "@ai-sdk/openai"
    provider["name"] = "codexzh"

    options = provider.setdefault("options", {})
    options["baseURL"] = "https://api.codexzh.com/v1"
    if not options.get("apiKey"):
        options["apiKey"] = "{env:OPENAI_API_KEY}"
    options["setCachekey"] = True
    generation = options.setdefault("options", {})
    generation.update(
        {
            "reasoningEffort": "high",
            "textVerbosity": "low",
            "reasoningSummary": "auto",
        }
    )

    models = provider.setdefault("models", {})
    models["gpt-5.6-terra"] = {"name": "gpt-5.6-terra"}

    temporary = CONFIG.with_suffix(".json.tmp-vista")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, CONFIG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
