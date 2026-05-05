"""
Baza Empire — Project Templates (R3)

Pre-fills new Baza projects with working scaffolds so they're testable from
the moment they're created. Each template is a list of (relpath, content)
tuples + the project type to apply.

Usage:
    from core.baza_project_templates import apply_template, list_templates
    files = apply_template("flask-min", proj_dir="/path/to/proj", project_id="my-app")
"""
from __future__ import annotations

import os
from typing import Any

# id → metadata + files generator
TEMPLATES: dict[str, dict[str, Any]] = {}


def register(template_id: str, *, type_: str, name: str, description: str, files):
    TEMPLATES[template_id] = {
        "id": template_id, "type": type_, "name": name,
        "description": description, "_files": files,
    }


# ── flask-min: minimal Flask app with /healthz and a test ─────────────────────
def _flask_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "Flask>=3.0\npytest>=7.0\n"),
        ("app.py",
            f'''"""{project_id} — minimal Flask app scaffold."""
from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/healthz")
def healthz():
    return jsonify(ok=True, service={project_id!r})


@app.route("/")
def index():
    return jsonify(message=f"hello from {project_id}", endpoints=["/", "/healthz"])


if __name__ == "__main__":
    import os
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
'''),
        ("tests/test_app.py",
            '''import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app


@pytest.fixture()
def client():
    return app.test_client()


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_json()
    assert "message" in body
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.pytest_cache/\n.preview.json\n.preview.log\n"),
    ]


register("flask-min", type_="dashboard", name="Flask minimal",
         description="Tiny Flask app with /healthz, an index, and a pytest smoke test.",
         files=_flask_min)


# ── fastapi-min: FastAPI app with /healthz ────────────────────────────────────
def _fastapi_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "fastapi>=0.110\nuvicorn[standard]>=0.27\nhttpx>=0.27\npytest>=7.0\n"),
        ("main.py",
            f'''"""{project_id} — minimal FastAPI app scaffold."""
from fastapi import FastAPI

app = FastAPI(title={project_id!r})


@app.get("/healthz")
def healthz():
    return {{"ok": True, "service": {project_id!r}}}


@app.get("/")
def root():
    return {{"message": f"hello from {project_id}", "docs": "/docs"}}
'''),
        ("tests/test_main.py",
            '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from main import app

c = TestClient(app)


def test_healthz():
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.pytest_cache/\n.preview.json\n.preview.log\n"),
    ]


register("fastapi-min", type_="dashboard", name="FastAPI minimal",
         description="FastAPI app with /healthz and a TestClient smoke test. Run via `uvicorn main:app --port $PORT`.",
         files=_fastapi_min)


# ── react-vite-min: minimal React+Vite app ────────────────────────────────────
def _react_vite_min(project_id: str) -> list[tuple[str, str]]:
    pkg = '''{
  "name": "''' + project_id + '''",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {
    "dev": "vite --host 127.0.0.1 --port ${PORT:-5173}",
    "preview": "vite preview --host 127.0.0.1 --port ${PORT:-5173}",
    "build": "vite build",
    "test": "echo 'no tests yet — drop a vitest config in to add them' && exit 0"
  },
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.0",
    "vite": "^5.4.0"
  }
}
'''
    return [
        ("package.json", pkg),
        ("vite.config.js",
            '''import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig({
  plugins: [react()],
})
'''),
        ("index.html",
            f'''<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>{project_id}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
'''),
        ("src/main.jsx",
            '''import React from "react"
import { createRoot } from "react-dom/client"
import App from "./App.jsx"

createRoot(document.getElementById("root")).render(<App />)
'''),
        ("src/App.jsx",
            f'''import {{ useState }} from "react"

export default function App() {{
  const [count, setCount] = useState(0)
  return (
    <main style={{{{padding: 24, fontFamily: "system-ui"}}}}>
      <h1>{project_id}</h1>
      <p>Vite + React scaffold ready.</p>
      <button onClick={{() => setCount(count + 1)}}>clicked {{count}} times</button>
    </main>
  )
}}
'''),
        (".gitignore", "node_modules/\ndist/\n.preview.json\n.preview.log\n"),
    ]


register("react-vite-min", type_="web-app", name="React + Vite minimal",
         description="Vite-powered React SPA. Dev server reads PORT env. `test` is a no-op until you add vitest.",
         files=_react_vite_min)


# ── esp-idf-blink: minimal ESP-IDF blinky scaffold ────────────────────────────
def _esp_idf_blink(project_id: str) -> list[tuple[str, str]]:
    cmake_root = '''cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(''' + project_id + ''')
'''
    main_cmake = '''idf_component_register(
  SRCS "main.c"
  INCLUDE_DIRS ".")
'''
    return [
        ("CMakeLists.txt", cmake_root),
        ("main/CMakeLists.txt", main_cmake),
        ("main/main.c",
            '''#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"

#ifndef BLINK_GPIO
#define BLINK_GPIO 2
#endif

void app_main(void) {
    gpio_reset_pin(BLINK_GPIO);
    gpio_set_direction(BLINK_GPIO, GPIO_MODE_OUTPUT);
    int level = 0;
    while (1) {
        gpio_set_level(BLINK_GPIO, level);
        level = !level;
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}
'''),
        ("README.md",
            f'''# {project_id} — ESP-IDF blink

Build:    `idf.py build`
Flash:    use the dashboard Flash tab (gated) or `idf.py -p /dev/ttyUSB0 flash`
Monitor:  `idf.py -p /dev/ttyUSB0 monitor`
'''),
        (".gitignore", "build/\nsdkconfig.old\nmanaged_components/\n.preview.json\n.preview.log\n"),
    ]


register("esp-idf-blink", type_="esp-firmware", name="ESP-IDF blink",
         description="Bare-minimum ESP-IDF blink program (GPIO 2). Build with idf.py, flash via dashboard.",
         files=_esp_idf_blink)


# ── library-min: bare Python lib + pytest ─────────────────────────────────────
def _library_min(project_id: str) -> list[tuple[str, str]]:
    pkg_dir = project_id.replace("-", "_")
    return [
        ("pyproject.toml",
            f'''[project]
name = "{project_id}"
version = "0.1.0"
description = ""
requires-python = ">=3.10"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
'''),
        (f"src/{pkg_dir}/__init__.py", '__version__ = "0.1.0"\n'),
        (f"src/{pkg_dir}/core.py",
            '''"""Library entry point."""


def hello(name: str = "world") -> str:
    return f"hello, {name}"
'''),
        ("tests/test_core.py",
            f'''from {pkg_dir}.core import hello


def test_hello_default():
    assert hello() == "hello, world"


def test_hello_named():
    assert hello("baza") == "hello, baza"
'''),
        (".gitignore", "__pycache__/\n*.egg-info/\n.pytest_cache/\nbuild/\ndist/\n"),
    ]


register("library-min", type_="library", name="Python library minimal",
         description="Modern src/ layout Python package with pytest.",
         files=_library_min)


# ── Public API ────────────────────────────────────────────────────────────────

def list_templates() -> list[dict[str, Any]]:
    return [
        {"id": t["id"], "type": t["type"], "name": t["name"], "description": t["description"]}
        for t in TEMPLATES.values()
    ]


def apply_template(template_id: str, proj_dir: str, project_id: str) -> list[str]:
    """Materialize the template files inside `proj_dir`. Returns list of paths
    written. Skips files that already exist (so this is safe to layer on top
    of an existing scaffold)."""
    if template_id not in TEMPLATES:
        raise ValueError(f"unknown template: {template_id}")
    spec = TEMPLATES[template_id]
    pairs = spec["_files"](project_id)
    written: list[str] = []
    for relpath, content in pairs:
        dest = os.path.join(proj_dir, relpath)
        if os.path.exists(dest):
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(relpath)
    return written


def template_type(template_id: str) -> str | None:
    spec = TEMPLATES.get(template_id)
    return spec["type"] if spec else None
