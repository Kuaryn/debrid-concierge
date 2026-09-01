"""Registry entry for the tray at login."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from concierge.tray import run

run()
