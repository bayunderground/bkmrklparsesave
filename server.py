#!/usr/bin/env python3
"""bkmrklparsesave server: append extracted items to Markdown files.

Stdlib only. Binds to 127.0.0.1; NGINX proxies to it. No auth.
"""
import os
from pathlib import Path

HOST = os.environ.get("BKMRKLPARSESAVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("BKMRKLPARSESAVE_PORT", "8765"))
DATA_DIR = Path(os.environ.get("BKMRKLPARSESAVE_DATA", "./data")).resolve()


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"bkmrklparsesave listening on http://{HOST}:{PORT} data={DATA_DIR}")


if __name__ == "__main__":
    main()
