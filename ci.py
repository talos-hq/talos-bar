#!/usr/bin/env python3
"""Entry point for the local CI gate: ./ci.py {plan,run,verify,attest,check,install-hooks}."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ci.gate import main

if __name__ == "__main__":
    sys.exit(main())
