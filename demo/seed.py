#!/usr/bin/env python3
"""Run the whole demo workflow non-interactively and print a narrative.

    python -m demo.seed [--db demo.db] [--keep]

Same steps the UI walkthrough runs one at a time (demo/steps.py), so there is
one implementation rather than two that drift. Use this to produce a populated
database; use the UI walkthrough to show an audience the application filling up.
"""

import argparse
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):          # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app import views                                        # noqa: E402
from app.core import connect, one                            # noqa: E402
from demo import steps                                       # noqa: E402


def wrap(text, width=70, indent="    "):
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(indent + line); line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(indent + line)
    return "\n".join(out)


def main(db_path, keep):
    p = pathlib.Path(db_path)
    if p.exists() and not keep:
        p.unlink()
    con = connect(db_path)

    for i, step in enumerate(steps.STEPS, 1):
        print(f"\n{'-' * 72}\n{i}. {step['title']}\n{'-' * 72}")
        if step["done"](con):
            print("    (already done)")
            continue
        r = steps.run(con, step["key"])
        print(f"    {r['headline']}")
        for f in r.get("facts", []):
            if f:
                print(f"      {f}")
        if r.get("note"):
            print()
            print(wrap(r["note"]))

    print(f"\n{'-' * 72}\nWhere that leaves us\n{'-' * 72}")
    c = views.coverage(con)
    a, i = c["applications"], c["inventory"]
    print(f"    applications   {a['total']:>3}   federated {a['by_origin']['federated']}, "
          f"inferred {a['by_origin']['inferred']}")
    print(f"    inventory      {i['records_total']:>3}   managed {i['managed']}, "
          f"rejected {i['rejected']}, undecided {i['undecided']}, stale {i['stale_decisions']}")
    print(f"    repositories   {c['code']['repositories_total']:>3}   "
          f"mapped {c['code']['mapped']}, unmapped {c['code']['unmapped']}")
    print(f"    artifacts      {c['build']['artifacts_total']:>3}   "
          f"governed {c['build']['governed']}, ungoverned {c['build']['ungoverned']}")

    app = one(con, "SELECT id FROM application WHERE name='Billing Platform'")
    if app:
        print()
        print(wrap(views.lineage(con, app["id"])["narrative"]))

    con.close()
    print(f"\n{'-' * 72}")
    print(f"Database written to {db_path}. Start the application with:")
    print(f"    python -m app.serve --db {db_path}")
    print(f"\nTo show it filling up from empty instead:")
    print(f"    python -m app.serve --demo --db fresh.db")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="demo.db")
    ap.add_argument("--keep", action="store_true",
                    help="continue an existing database instead of starting over")
    a = ap.parse_args()
    main(a.db, a.keep)
