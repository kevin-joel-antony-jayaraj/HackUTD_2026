"""Command line for every job, handy locally and for cron:

    python -m sitesignal.jobs schema               # create/upgrade tables
    python -m sitesignal.jobs seed                 # load synthetic demo data (wipes demo tables)
    python -m sitesignal.jobs import assets FILE   # sites | assets | work_orders, CSV or XLSX
    python -m sitesignal.jobs weather              # refresh forecasts
    python -m sitesignal.jobs score                # rescore all assets (no agent)
    python -m sitesignal.jobs sweep [--send]       # agent sweep (texts only with --send)
    python -m sitesignal.jobs call [--send]        # call the service manager about the riskiest asset
    python -m sitesignal.jobs poll                 # Photon inbound poller (if not using the webhook)
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from . import importer, services
from .db import Database


def main(argv: list[str]) -> None:
    if not argv:
        print(__doc__)
        return
    cmd, db = argv[0], Database()
    if cmd == "schema":
        db.run_file(str(Path(__file__).resolve().parent.parent / "db" / "schema.sql"))
        out = {"ok": True}
    elif cmd == "seed":
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from seed import seed
        seed(db, date.today())
        out = {"seeded": True}
    elif cmd == "import":
        kind, path = argv[1], Path(argv[2])
        out = importer.import_file(db, kind, path.read_bytes(), path.name)
    elif cmd == "weather":
        out = services.refresh_weather(db)
    elif cmd == "score":
        out = {"scored": services.score_all(db)}
    elif cmd == "sweep":
        out = services.run_sweep(db, dry_run="--send" not in argv)
    elif cmd == "call":
        out = services.call_riskiest(db, dry_run="--send" not in argv)
    elif cmd == "poll":
        from .photon_inbound import poll_forever
        from .store import Store
        from .tools import Toolbox
        import os
        poll_forever(Toolbox(Store(db), dry_run=os.environ.get("PHOTON_SEND", "0") != "1"))
        return
    else:
        print(__doc__)
        return
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
