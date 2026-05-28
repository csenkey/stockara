"""Root conftest for backend tests.

Adds the workspace root to sys.path so that imports like
'from backend.src.module import ...' work when running pytest
from either the workspace root or the backend/ directory.
"""
import sys
from pathlib import Path

# Add the workspace root (parent of backend/) to the path
workspace_root = Path(__file__).parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))
