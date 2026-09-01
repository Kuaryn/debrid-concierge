"""Worker entry that resolves src/ itself, so registry spawns need no PYTHONPATH."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from concierge.worker import main

sys.exit(main())
