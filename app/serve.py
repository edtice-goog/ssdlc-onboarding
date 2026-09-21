#!/usr/bin/env python3
"""Entry point.  python -m app.serve [--port 8080] [--db ssdlc.db]"""

import argparse

from .api import serve


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--db", default="ssdlc.db")
    a = ap.parse_args()
    serve(a.host, a.port, a.db)


if __name__ == "__main__":
    main()
