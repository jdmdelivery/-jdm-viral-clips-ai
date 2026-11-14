import os, sys, tempfile
from pathlib import Path
_tmp = tempfile.mkdtemp(prefix="jdm-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "worker-windows"))
