"""Registry entry: concierge <magnet|.torrent>, always detached, folder via dialog."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from concierge.handlers import main

# I keep shell input after -- so a clicked source cannot become a worker option.
sys.exit(main(["--detach", "--", *sys.argv[1:2]]))
