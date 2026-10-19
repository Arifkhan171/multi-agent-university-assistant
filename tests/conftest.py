import sys
from pathlib import Path

# Puts the project folder on the import path so tests can `import tools...`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
