from pathlib import Path
import sys

from bga_arknova.dashboard import run

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
run(root)

