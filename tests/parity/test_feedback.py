"""POST /api/site/feedback: what the sender is told, against the TypeScript server. The happy path writes rows, so it is checked on its own."""

from __future__ import annotations

import io
import os

import psycopg
import pytest

BOUNDARY = "----parityboundary"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f0005000201a5f6458b0000000049454e44ae426082"
)


def form(fields: dict[str, str], file: tuple[str, bytes] | None = None) -> tuple[dict[str, str], bytes]:
    out = io.BytesIO()
    for key, value in fields.items():
        out.write(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    if file:
        out.write(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="screenshot"; filename="s.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
        out.write(file[1] + b"\r\n")
    out.write(f"--{BOUNDARY}--\r\n".encode())
    return {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}", "User-Agent": "Mozilla/5.0 Chrome Windows"}, out.getvalue()


@pytest.mark.parametrize(
    ("fields", "file"),
    [
        ({"content": ""}, None),
        ({"content": "x"}, None),
        ({"content": "y" * 5001}, None),
        ({"content": "hello there", "email": "not-an-email"}, None),
        ({"content": "hello there", "email": "a@" + "b" * 200 + ".com"}, None),
        ({"content": "hello there"}, ("s.png", b"not an image at all")),
        ({"content": "hello there"}, ("s.png", b"\x89PNG\r\n\x1a\nnoise")),
        ({"content": "hello there"}, ("s.png", b"GIF89a" + b"\0" * 5)),
    ],
)
def test_rejections(compare, fields, file):
    headers, body = form(fields, file)
    got = compare("/api/site/feedback", method="POST", headers=headers, content=body)
    assert got["status"] == 400


def test_not_multipart(compare):
    compare("/api/site/feedback", method="POST", headers={"Content-Type": "application/json"}, content="{}")


def test_saved_report(py_api):
    headers, body = form({"content": "A parity test report", "email": "t@example.com", "pageUrl": "javascript:alert(1)"}, ("s.png", PNG))
    res = py_api.post("/api/site/feedback", headers={**headers, "User-Agent": "Mozilla/5.0 Firefox Windows"}, content=body)
    assert res.status_code == 201 and res.headers["cache-control"] == "no-store"
    again = py_api.post("/api/site/feedback", headers={**headers, "User-Agent": "Mozilla/5.0 Firefox Windows"}, content=body)
    assert again.json() == res.json()  # an exact retry is the same report
    with psycopg.connect(os.environ["PARITY_DATABASE_URL"]) as conn:
        row = conn.execute(
            "SELECT content, email, page_url, screenshot_key, forward_error FROM feedback WHERE id = %s", (res.json()["id"],)
        ).fetchone()
    assert row[0] == "A parity test report" and row[1] == "t@example.com" and row[2] is None and row[3].startswith("local:") and row[4] == "pending"


def test_rate_limit_and_ban(py_api):
    import uuid

    ip = f"203.0.113.{uuid.uuid4().int % 250}-{uuid.uuid4().hex[:6]}"  # a source of its own for this run
    statuses = []
    for n in range(5):
        headers, body = form({"content": f"distinct report number {n} {uuid.uuid4()}"})
        res = py_api.post("/api/site/feedback", headers={**headers, "X-Real-IP": ip}, content=body)
        statuses.append((res.status_code, res.headers.get("retry-after")))
    assert [s for s, _ in statuses] == [201, 201, 201, 429, 429]
    assert statuses[3][1] == "3600" and statuses[4][1] is not None
