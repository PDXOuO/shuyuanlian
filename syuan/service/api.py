"""Flask 服务：对外暴露链上状态与六幕演示接口，并托管演示台静态页面。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .. import config as C
from ..scenario import Demo

WEB_DIR = Path(__file__).parent / "web"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="/static")
    app.json.ensure_ascii = False
    app.config["JSON_SORT_KEYS"] = False

    demo = Demo()
    lock = threading.Lock()

    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/favicon.ico")
    def favicon():
        # 页面已用内联 SVG 图标；这里兜住直接访问 /favicon.ico 的请求，避免 500
        return ("", 204)

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "height": demo.engine.chain.height, "ts": time.time()})

    @app.get("/api/state")
    def state():
        with lock:
            return jsonify(demo.state())

    @app.post("/api/act/<int:n>")
    def act(n: int):
        if not 1 <= n <= 6:
            return jsonify({"error": "act 必须为 1..6"}), 400
        with lock:
            result = demo.run(n)
            payload = demo.state()
            payload["last_act"] = {"act": n, "logs": result["logs"]}
            return jsonify(payload)

    @app.post("/api/reset")
    def reset():
        with lock:
            demo.reset()
            return jsonify(demo.state())

    @app.get("/api/metrics")
    def metrics():
        path = C.RESULTS_DIR / "metrics.json"
        if not path.exists():
            return jsonify({"error": "尚未运行基准测试"}), 404
        return jsonify(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/verify")
    def verify():
        with lock:
            return jsonify({"integrity": demo.engine.chain.integrity(), "replay": demo.engine.replay()})

    @app.errorhandler(Exception)
    def on_error(exc):  # noqa: ANN001
        return jsonify({"error": type(exc).__name__, "message": str(exc)}), 500

    return app


def serve(host: str = "127.0.0.1", port: int = 8000, debug: bool = False) -> None:
    app = create_app()
    print(f"\n  数元链演示台已启动：http://{host}:{port}\n  按 Ctrl+C 停止\n")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    serve()
