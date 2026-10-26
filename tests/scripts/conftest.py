import sys
from pathlib import Path

# The carried-over scripts import their siblings as top-level modules.
sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "talos_bar" / "scripts"))
