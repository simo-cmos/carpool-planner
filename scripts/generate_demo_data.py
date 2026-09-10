"""Generate the demo workspace shipped in demo/demo-workspace.json.

Run this instead of hand-editing the JSON: it drives the app's own service layer,
so computed fields, schema version and relationships stay valid as the schema
evolves.

    python scripts/generate_demo_data.py

Every person here is fictional and every coordinate is a public landmark — a
station, a piazza, a park. Never regenerate this file from a real workspace.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Point the app at a throwaway database before importing anything that opens it.
_scratch = tempfile.mkdtemp(prefix="dmproject-demo-")
os.environ["DMPROJECT_DATA_DIR"] = _scratch

from app.database import init_db  # noqa: E402
from app.services.destinations import save_destination_favorite  # noqa: E402
from app.services.participants import save_participant  # noqa: E402
from app.services.workspace import export_workspace_backup  # noqa: E402

# name, place label, lat, lon, seats (None = no car), fuel, l/100km, habit score
PEOPLE = [
    ("Giulia",  "Modena, Piazza Grande",        44.6462, 10.9252, 5,    "gasoline", 6.7, 8.0),
    ("Marco",   "Reggio Emilia, Stazione",      44.7009, 10.6297, 4,    "diesel",   4.9, 6.0),
    ("Sofia",   "Carpi, Piazza dei Martiri",    44.7836, 10.8853, 4,    "electric", 0.0, 7.0),
    ("Luca",    "Sassuolo, Parco Ducale",       44.5416, 10.7844, 2,    "gasoline", 6.2, 3.0),
    ("Elena",   "Casalecchio di Reno, Municipio", 44.4757, 11.2769, None, None,     None, 5.0),
    ("Davide",  "Budrio, Stazione",             44.5372, 11.5347, None, None,      None, 4.0),
    ("Chiara",  "Ferrara, Stazione",            44.8449, 11.5989, None, None,      None, 6.0),
    ("Paolo",   "Imola, Piazza Matteotti",      44.3531, 11.7147, None, None,      None, 2.0),
]

DESTINATION = ("DumBO, Bologna", 44.5055, 11.3247, "21:00")


def main() -> None:
    init_db()

    for name, place, lat, lon, seats, fuel, consumption, habit in PEOPLE:
        save_participant(
            participant_id=None,
            name=name,
            address_text=place,
            location_name=place,
            latitude=lat,
            longitude=lon,
            has_car=seats is not None,
            fuel_type=fuel,
            consumption_l_per_100km=consumption,
            total_seats=seats,
            habit_score=habit,
        )

    label, lat, lon, arrival = DESTINATION
    save_destination_favorite(
        name=label,
        latitude=lat,
        longitude=lon,
        address_text=label,
        target_arrival_time=arrival,
    )

    out = REPO_ROOT / "demo" / "demo-workspace.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(export_workspace_backup(), encoding="utf-8")
    print(f"wrote {out.relative_to(REPO_ROOT)} ({len(PEOPLE)} participants)")


if __name__ == "__main__":
    main()
