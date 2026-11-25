import os

# Tests must never write the default on-disk checkpoint file (stale runs would leak between test runs).
os.environ.setdefault("OBSERVA_CHECKPOINT_DB", ":memory:")
