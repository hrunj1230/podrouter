"""Allow `python -m podrouter`, which the local scheduler uses for child commands."""

import sys

from podrouter.cli import main

sys.exit(main())
