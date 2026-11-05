"""Usage: python tests/tools/s3_backup_check.py <repository root> <work folder>   (not with -I: Pillow is in the user's packages)

Backs up a database to an S3-compatible store with the Python port and with the TypeScript engine, then lists what the store holds. The backup code forces HTTPS and
puts the bucket in the host or, with DB_BACKUP_STORE_DOMAIN, uses `https://<domain>/<daily|weekly|monthly>/<file>`, so a local store works in path style with a
self-signed certificate. What was used on 2026-11-05 (MinIO's images need a login now; SeaweedFS pulls from Docker Hub):

    <work folder>/minio-certs/: public.crt and private.key (openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=localhost
        -addext subjectAltName=DNS:localhost,IP:127.0.0.1), and s3.json with one identity (TESTACCESSKEY / TESTSECRETKEY0123456789abcdef0123456789, all actions)
    docker run -d --name s3-test -p 9443:8333 -v <work folder>/minio-certs:/certs:ro chrislusf/seaweedfs:latest server -dir=/data -s3 -s3.port=8333
        -s3.config=/certs/s3.json -s3.cert.file=/certs/public.crt -s3.key.file=/certs/private.key -volume.max=3

The script creates the three buckets, backs up the `dev` database of start_dev.py (pg_dump 17 must be at the path below), and lists the buckets.
"""

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

root, sw = Path(sys.argv[1]), Path(sys.argv[2])
cert = sw / "minio-certs" / "public.crt"
STORE = {
    "DB_BACKUP_STORE_SECRET_ID": "TESTACCESSKEY",
    "DB_BACKUP_STORE_SECRET_KEY": "TESTSECRETKEY0123456789abcdef0123456789",
    "DB_BACKUP_STORE_BUCKET": "test",
    "DB_BACKUP_STORE_REGION": "us-east-1",
    "DB_BACKUP_STORE_DOMAIN": "localhost:9443",
}
PG = "C:/Program Files/PostgreSQL/17/bin"
env = {
    **os.environ,
    **STORE,
    "SSL_CERT_FILE": str(cert),
    "NODE_EXTRA_CA_CERTS": str(cert),
    "PATH": PG + os.pathsep + os.environ["PATH"],
    "PYTHONUTF8": "1",
    "DATABASE_URL": "postgres://postgres:ci@127.0.0.1:55432/dev",
    "SESSION_SECRET": "ci-session-secret-0123456789abcdef",
    "IMG_PROXY_SIGN_SECRET": "ci-img-secret-0123456789abcdef",
    "ADMIN_PASSWORD": "ci-admin-password-123",
    "INGEST_TOKEN": "ci-ingest-token-0123456789abcdef",
    "SITE_URL": "http://127.0.0.1:3000",
    "API_BASE_URL": "http://127.0.0.1:3101",
    "COLLECT_ENABLED": "false",
    "MODEL_CALLS_ENABLED": "false",
    "LOG_LEVEL": "error",
    "TZ": "UTC",
}
os.environ.update(env)
sys.path.insert(0, str(root / "src"))
from newsroom.engine import operations_backup as B  # noqa: E402


def signed(method, url, payload_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"):
    h = B.sign_v4(
        method, url, "us-east-1", STORE["DB_BACKUP_STORE_SECRET_ID"], STORE["DB_BACKUP_STORE_SECRET_KEY"], payload_hash, {}, datetime.now(UTC)
    )
    return httpx.request(method, url, headers=h, timeout=60)


# the three buckets the backup writes to (its keys start with the retention class)
for bucket in ("daily", "weekly", "monthly"):
    r = signed("PUT", f"https://localhost:9443/{bucket}")
    print("create bucket", bucket, r.status_code)

# the Python port
env_py = {**env, "AIHOT_DATA_DIR": str(sw / "bk-py")}
os.environ["AIHOT_DATA_DIR"] = env_py["AIHOT_DATA_DIR"]
with ConnectionPool(env["DATABASE_URL"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, open=True) as pool:
    try:
        res = B.run_backup(pool, datetime(2026, 11, 5, 4, 10, tzinfo=UTC))
        print("PYTHON backup ->", json.dumps({k: res[k] for k in res if k in ("uploaded", "filesError")}), [o["key"] for o in res.get("objects", [])])
    except Exception as error:
        print("PYTHON backup FAILED:", error)

# the TypeScript engine, same store
(sw / "bk-in.json").write_text(json.dumps({"calls": [["runBackup", "2026-11-06T04:10:00Z"]]}), encoding="utf-8")
env_node = {**env, "AIHOT_DATA_DIR": str(sw / "bk-node")}
out = subprocess.run(["node", "scripts/ops-run.ts", str(sw / "bk-in.json")], cwd=root / "AIHOT", env=env_node, capture_output=True, timeout=600)
line = next((line for line in reversed(out.stdout.decode(errors="replace").splitlines()) if line.startswith("[")), "[]")
node = json.loads(line)[0]
print(
    "NODE backup ->",
    json.dumps({k: node[k] for k in node if k in ("uploaded", "filesError", "error")}) if isinstance(node, dict) else node,
    [o["key"] for o in node.get("objects", [])] if isinstance(node, dict) else "",
)

# what the store holds
for bucket in ("daily", "weekly", "monthly"):
    r = signed("GET", f"https://localhost:9443/{bucket}?list-type=2")
    items = re.findall(r"<Key>([^<]+)</Key>.*?<Size>(\d+)</Size>", r.text, re.S)
    print("store", bucket, r.status_code, items)
