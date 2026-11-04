"""Background media preparation: the structural similarity measure, the proxied renditions an article names, the preparation job on a local site, and the animation and AVIF
conversions. Pixels and bytes of re-encoded images are not the same as sharp's, so those are held to what they must achieve rather than to equality."""

from __future__ import annotations

import base64
import io
import json
import os
import random
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest
from PIL import Image, ImageDraw
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin


def node(tmp_path, payload: dict, env_extra: dict | None = None):
    path = tmp_path / "in.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": "postgres://x", **(env_extra or {})}
    out = subprocess.run(["node", "scripts/media-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line[:1] in "[{0123456789-n"))


def png_bytes(w=48, h=40, seed=1) -> bytes:
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(w * h)])
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def test_ssim_and_luma(settings_env, tmp_path):
    import numpy as np

    from newsroom.engine.media.similarity import Luma, luma, ssim

    rnd = random.Random(5)
    w, h = 40, 33
    a = [float(rnd.randrange(256)) for _ in range(w * h)]
    for noise in (0, 3, 40):
        b = [min(255.0, max(0.0, v + rnd.randint(-noise, noise))) for v in a]
        want = node(tmp_path, {"op": "ssim", "a": a, "b": b, "width": w, "height": h})
        got = ssim(Luma(np.array(a, dtype=np.float32).reshape(h, w), w, h), Luma(np.array(b, dtype=np.float32).reshape(h, w), w, h))
        assert got == pytest.approx(want, abs=1e-9), (noise, got, want)
    assert ssim(Luma(np.zeros((4, 4), dtype=np.float32), 4, 4), Luma(np.zeros((4, 4), dtype=np.float32), 4, 4)) is None
    assert ssim(Luma(np.zeros((8, 9), dtype=np.float32), 9, 8), Luma(np.zeros((8, 8), dtype=np.float32), 8, 8)) is None
    data = png_bytes()
    want = node(tmp_path, {"op": "luma", "png": base64.b64encode(data).decode()})
    got = luma(data)
    assert (got.width, got.height) == (want["width"], want["height"])
    assert np.allclose(got.values.reshape(-1), np.array(want["values"], dtype=np.float32), atol=1e-3)


def test_proxied_renditions(settings_env, tmp_path):
    from newsroom.engine.media.prepare import proxied_renditions

    answers = [
        {"cover": "/api/img-proxy?u=https%3A%2F%2Fa.example%2Fx.png&amp;mode=card-800&exp=1&sig=a", "again": "/api/img-proxy?u=https%3A%2F%2Fa.example%2Fx.png&mode=card-800&exp=2"},
        [{"src": "https://site/api/img-proxy?u=https%3A%2F%2Fb.example%2Fy.jpg&mode=body-1200"}, "/api/img-proxy?u=https%3A%2F%2Fb.example%2Fy.jpg&mode=body-1200-avif"],
        "nothing here", {"a": "/api/img-proxy?u=https%3A%2F%2Fc.example%2Fz.gif&mode=avatar"},
    ]
    assert proxied_renditions(answers) == node(tmp_path, {"op": "renditions", "answers": answers})


def test_animation_conversion(settings_env, tmp_path, monkeypatch):
    from newsroom.engine.media import images as I

    monkeypatch.setenv("AIHOT_DATA_DIR", str(tmp_path))
    frames = []
    for i in range(6):
        img = Image.new("RGB", (300, 200), (255, 255, 255))
        ImageDraw.Draw(img).rectangle((i * 40, 20, i * 40 + 60, 120), fill=(220, 30, 30))
        frames.append(img)
    out = io.BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=[80, 90, 100, 110, 120, 130], loop=3)
    body = out.getvalue()
    url, mode = "https://a.example/anim.gif", "card-800"
    file = I.cache_file(url, mode)
    file.parent.mkdir(parents=True, exist_ok=True)
    I._save(file, body, "image/gif")
    saved = I.convert_animated(url, mode)
    assert saved > 0
    webp = file.read_bytes()
    assert webp[:4] == b"RIFF" and webp[8:12] == b"WEBP" and file.with_name(file.name + ".type").read_text() == "image/webp"
    with Image.open(io.BytesIO(webp)) as w:
        assert w.n_frames == 6 and w.info.get("loop") == 3 and w.width == 300
    # a GIF that does not shrink enough is kept and recorded, so it is not tried again
    flat = io.BytesIO()
    Image.new("P", (4, 4)).save(flat, "GIF")
    other = I.cache_file("https://a.example/tiny.gif", mode)
    other.parent.mkdir(parents=True, exist_ok=True)
    I._save(other, flat.getvalue(), "image/gif")
    assert I.convert_animated("https://a.example/tiny.gif", mode) == 0 and other.with_name(other.name + ".prepared").exists()
    assert I.convert_animated("https://a.example/never-cached.gif", mode) == 0


def serve(origin_box: list):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path.startswith("/photo"):
                rnd = random.Random(int(path[6:].split(".")[0] or 0))
                img = Image.new("RGB", (1200, 800))
                px = []
                for y in range(800):
                    for x in range(1200):
                        px.append(((x // 6 * 7 + y // 5) % 256, (x // 7 + y // 6 * 3) % 256, (x // 9 + y // 4 + rnd.randrange(3)) % 256))
                img.putdata(px)
                out = io.BytesIO()
                img.save(out, "JPEG", quality=95)
                body, ctype = out.getvalue(), "image/jpeg"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_avif_conversion(settings_env, tmp_path, monkeypatch):
    from newsroom.engine.media import images as I

    monkeypatch.setenv("AIHOT_DATA_DIR", str(tmp_path))
    server = serve([])
    try:
        url, mode = f"http://127.0.0.1:{server.server_port}/photo1.jpg", "card-800"
        rendition = I.produce_image(url, mode)
        assert rendition.type == "image/jpeg"
        saved = I.convert_avif(url, mode)
        file = I.cache_file(url, f"{mode}{I.AVIF_MODE_SUFFIX}")
        if saved:  # a clearly smaller variant of at least the same sharpness
            avif = file.read_bytes()
            assert saved == len(rendition.body) - len(avif) and len(avif) <= len(rendition.body) * 0.9
            assert I.produce_avif(url, mode).type == "image/avif"
        else:  # the rendition stays, and that is recorded
            assert file.with_name(file.name + ".prepared").exists() and I.produce_avif(url, mode).type == "image/jpeg"
        assert I.convert_avif(url, mode) == 0  # decided once
    finally:
        server.shutdown()


def test_preparation_job_on_a_local_site(database_url, settings_env, tmp_path, monkeypatch):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.media.prepare import prepare_article_media

    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["mp_node", "mp_py"])
    admin.close()
    server = serve([])
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        ids = []
        for name, url in urls.items():
            with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
                rows = c.execute("SELECT a.id FROM articles a JOIN publications p ON p.article_id = a.id WHERE p.visibility = 'public' ORDER BY a.identity_key LIMIT 4").fetchall()
                ids = [r["id"] for r in rows]
                for i, r in enumerate(rows):
                    media = [{"kind": "image", "url": f"{origin}/photo{i}.jpg", "width": 1200, "height": 800}] if i < 3 else [{"kind": "image", "url": f"{origin}/missing.jpg"}]
                    body = f'<p>{"A paragraph of the article body. " * 12}</p><img src="{media[0]["url"]}" width="1200" height="800"><p>{"More text follows the picture. " * 12}</p>'
                    c.execute("UPDATE articles SET media = %s, body_html = %s, body_text = %s, body_status = 'ok' WHERE id = %s", (Jsonb(media), body, "text " * 100, r["id"]))
                    c.execute("UPDATE sources SET site_fulltext = true WHERE id = (SELECT source_id FROM articles WHERE id = %s)", (r["id"],))
                c.execute("SELECT 1")
        # both sides publish from the media they were given
        from newsroom.engine.publication.publish import publish_article

        def configure(conn):
            conn.adapters.register_loader("numeric", _FloatNumeric)

        with ConnectionPool(urls["mp_py"], min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
            for article_id in ids:
                publish_article(pool, article_id)
        env = {**os.environ, **ENV, "DATABASE_URL": urls["mp_node"], "AIHOT_DATA_DIR": str(tmp_path / "node-data")}
        path = tmp_path / "in.json"
        path.write_text(json.dumps({"op": "prepare", "ids": ids}), encoding="utf-8")
        subprocess.run(["node", "scripts/republish-all.ts"], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
        out = subprocess.run(["node", "scripts/media-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=600)
        want = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))
        monkeypatch.setenv("AIHOT_DATA_DIR", str(tmp_path / "py-data"))
        with ConnectionPool(urls["mp_py"], min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
            got = [prepare_article_media(pool, i) for i in ids]
    finally:
        server.shutdown()
    assert [(g["renditions"], g["failed"]) for g in got] == [(w["renditions"], w["failed"]) for w in want]
    assert any(w["renditions"] > 0 for w in want) and any(w["failed"] > 0 for w in want)
