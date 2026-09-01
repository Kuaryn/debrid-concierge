"""Registry entry: concierge <magnet|.torrent>, always detached, folder via dialog."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from concierge.handlers import main

sys.exit(main([*sys.argv[1:], "--detach"]))
