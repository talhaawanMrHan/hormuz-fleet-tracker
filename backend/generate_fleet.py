"""Validate the canonical fleet.json supplied with the hackathon brief.

This project deliberately does not regenerate fleet.json: the brief fixes the
scenario data for grading. Run `python generate_fleet.py` to validate that the
canonical JSON still contains the expected 15 ships, 10 ports, bbox and units.
"""
import json
from pathlib import Path

FLEET_PATH = Path(__file__).with_name("fleet.json")


def validate():
    data = json.loads(FLEET_PATH.read_text(encoding="utf-8"))
    assert data["coordinateFormat"] == "[lat, lng]"
    assert data["units"] == {
        "speed": "knots",
        "fuel": "tons",
        "heading": "degrees from true north (0-360)",
    }
    assert data["boundingBox"] == {"north": 30.5, "south": 22.0, "east": 60.0, "west": 47.5}
    assert len(data["ports"]) == 10
    assert len(data["fleet"]) == 15
    assert [s["shipId"] for s in data["fleet"]] == [f"MV-{i}" for i in range(1, 16)]
    assert all(len(s["position"]) == 2 for s in data["fleet"])
    print(f"OK: canonical fleet.json — {len(data['fleet'])} ships, {len(data['ports'])} ports")


if __name__ == "__main__":
    validate()
