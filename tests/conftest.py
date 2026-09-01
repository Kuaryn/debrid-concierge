import sys
from pathlib import Path

# repo-root scripts (repoint, win_* entries) are test targets too
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
