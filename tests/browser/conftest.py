import os
import sys

# Make the framework root importable so `import browser.*` resolves
# regardless of pytest's import mode.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
