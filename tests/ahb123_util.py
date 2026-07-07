# tests/ahb123_util.py
import importlib.util, os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "web", "ahb123")

def load(name):
    """Load web/ahb123/<name>.py as a module by path."""
    path = os.path.join(SRC, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
