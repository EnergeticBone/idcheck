import os
from cvat_sdk import make_client

host = os.environ.get("CVAT_HOST", "http://localhost:8080")
user = os.environ["CVAT_USER"]
pwd = os.environ["CVAT_PASS"]

with make_client(host=host, credentials=(user, pwd)) as client:
    print("Kết nối OK, số task:", len(client.tasks.list()))