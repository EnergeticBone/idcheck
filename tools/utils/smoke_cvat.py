import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os
from cvat_sdk import make_client

host = os.environ.get("CVAT_HOST", "http://localhost:8080")
user = os.environ["CVAT_USER"]
pwd = os.environ["CVAT_PASS"]

with make_client(host=host, credentials=(user, pwd)) as client:
    print("Kết nối OK, số task:", len(client.tasks.list()))