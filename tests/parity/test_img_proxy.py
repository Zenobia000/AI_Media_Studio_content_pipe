"""/api/img-proxy against the TypeScript server: the signature checks word for word; the pictures by format and size (the two programs'
encoders do not give the same bytes)."""

from __future__ import annotations

import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from PIL import Image

from newsroom.engine.media.imgproxy import proxied_image


def _image(fmt: str, size=(1600, 900), mode="RGB", **kw) -> bytes:
    out = io.BytesIO()
    img = Image.effect_noise(size, 60).convert(mode) if mode != "P" else Image.new("P", size)
    img.save(out, fmt, **kw)
    return out.getvalue()


def _ico() -> bytes:
    png = _image("PNG", (64, 64), "RGBA")
    header = bytes([0, 0, 1, 0, 1, 0]) + bytes([64, 64, 0, 0, 1, 0, 32, 0]) + len(png).to_bytes(4, "little") + (22).to_bytes(4, "little")
    return header + png


FILES = {
    "/big.png": ("image/png", _image("PNG", (2000, 1000), "RGBA")),
    "/photo.jpg": ("image/jpeg", _image("JPEG", (2400, 1200))),
    "/small.jpg": ("image/jpeg", _image("JPEG", (200, 100))),
    "/pic.webp": ("image/webp", _image("WEBP", (1800, 600))),
    "/anim.gif": ("image/gif", _image("GIF", (120, 80), "P")),
    "/fav.ico": ("image/x-icon", _ico()),
    "/page.html": ("text/html", b"<html>no</html>"),
    "/noType.png": ("application/octet-stream", _image("PNG", (900, 300), "RGB")),
    "/square.png": ("image/png", _image("PNG", (500, 300), "RGB")),
}


@pytest.fixture(scope="module")
def origin():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hit = FILES.get(self.path.split("?")[0])
            if self.path.startswith("/gone"):
                self.send_response(404)
                self.end_headers()
                return
            if not hit:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", hit[0])
            self.send_header("Content-Length", str(len(hit[1])))
            self.end_headers()
            self.wfile.write(hit[1])

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def both(node_api, py_api, path):
    return httpx.get(node_api + path, timeout=30), py_api.get(path)


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?u=http://x/a.png",
        "?u=ftp://x/a.png&mode=full&exp=1999999999&sig=abc",
        "?u=http://x/a.png&mode=full&exp=1&sig=abcdabcdabcdabcd",
        "?u=http://x/a.png&mode=full&exp=1999999999&sig=0000000000000000",
        "?u=http://x/a.png&mode=full-avif&exp=1999999999&sig=0000000000000000",
        "?u=http://x/a.png&mode=bogus&exp=1999999999&sig=zz",
        "?u=http://x/a.png&mode=&exp=1999999999&sig=abcdabcdabcdabcd",
    ],
)
def test_refusals(compare, query):
    got = compare("/api/img-proxy" + query)
    assert got["status"] in (400, 403)


@pytest.mark.parametrize(
    ("path", "mode"),
    [
        ("/big.png", "full"),
        ("/big.png", "card"),
        ("/big.png", "avatar"),
        ("/photo.jpg", "thumb"),
        ("/photo.jpg", "image-1200"),
        ("/small.jpg", "full"),
        ("/pic.webp", "thumb"),
        ("/fav.ico", "avatar-48"),
        ("/noType.png", "card"),
        ("/square.png", "avatar"),
        ("/anim.gif", "card"),
    ],
)
def test_pictures(node_api, py_api, origin, path, mode):
    url = proxied_image(origin + path, mode)
    node, py = both(node_api, py_api, url)
    assert node.status_code == py.status_code == 200
    for header in ("content-type", "x-content-type-options", "content-security-policy", "x-img-proxy-sig"):
        assert node.headers.get(header) == py.headers.get(header), header
    a, b = Image.open(io.BytesIO(node.content)), Image.open(io.BytesIO(py.content))
    assert (a.format, a.size) == (b.format, b.size)
    again = py_api.get(url, headers={"If-None-Match": py.headers["etag"]})
    assert again.status_code == 304


@pytest.mark.parametrize("path", ["/page.html", "/gone/x.png", "/missing.png"])
def test_bad_upstream(node_api, py_api, origin, path):
    url = proxied_image(origin + path, "full")
    node, py = both(node_api, py_api, url)
    assert (node.status_code, node.text) == (py.status_code, py.text) == (502, "Upstream image unavailable")
    assert node.headers["cache-control"].split(",")[0] == py.headers["cache-control"].split(",")[0]
