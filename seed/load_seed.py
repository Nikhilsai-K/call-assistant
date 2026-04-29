"""
One-shot seed loader. Creates a demo org, three agent templates, and loads the
HVAC KB. Run `python seed/load_seed.py` with the API env configured.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
API = "http://localhost:8000"
ORG = "demo-org-000001"


def main() -> int:
    templates = json.loads((ROOT / "agent_templates.json").read_text())
    hvac_kb = (ROOT / "kb_hvac.md").read_text()

    with httpx.Client(timeout=30.0, headers={"X-Dev-Org": ORG}) as c:
        for t in templates:
            resp = c.post(
                f"{API}/v1/agents",
                json={
                    "name": t["name"],
                    "plain_instructions": t["plain_instructions"],
                    "voice_id": t["voice_id"],
                    "voice_provider": t["voice_provider"],
                    "tools_enabled": t["tools_enabled"],
                    "emergency_keywords": t["emergency_keywords"],
                    "personality": t["personality"],
                    "business_hours": t.get("business_hours"),
                },
            )
            resp.raise_for_status()
            print(f"created agent {t['name']} -> {resp.json()['id']}")

        kb = c.post(
            f"{API}/v1/kb",
            json={"name": "HVAC Starter KB", "source_type": "upload"},
        ).json()
        c.post(
            f"{API}/v1/kb/{kb['id']}/documents",
            json={"title": "Mike's HVAC Starter", "content": hvac_kb},
        ).raise_for_status()
        print(f"loaded KB {kb['id']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
