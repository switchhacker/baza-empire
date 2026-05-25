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


# ── next-app: Next.js 14 app-router minimal ───────────────────────────────────
def _next_app(project_id: str) -> list[tuple[str, str]]:
    pkg = '{\n  "name": "' + project_id + '",\n  "version": "0.1.0",\n  "private": true,\n  "scripts": {\n    "dev": "next dev -p ${PORT:-3000}",\n    "build": "next build",\n    "start": "next start -p ${PORT:-3000}"\n  },\n  "dependencies": {\n    "next": "^14.2.0",\n    "react": "^18.3.0",\n    "react-dom": "^18.3.0"\n  }\n}\n'
    return [
        ("package.json", pkg),
        ("app/layout.jsx",
            f'''export const metadata = {{ title: "{project_id}" }}

export default function RootLayout({{ children }}) {{
  return (
    <html lang="en">
      <body style={{{{fontFamily: "system-ui", padding: 24}}}}>{{children}}</body>
    </html>
  )
}}
'''),
        ("app/page.jsx",
            f'''export default function Page() {{
  return (
    <main>
      <h1>{project_id}</h1>
      <p>Next.js 14 app-router scaffold.</p>
    </main>
  )
}}
'''),
        ("README.md",
            f'''# {project_id} — Next.js 14

```
npm install
npm run dev
```
'''),
        (".gitignore", "node_modules/\n.next/\nout/\n.env*.local\n.preview.json\n.preview.log\n"),
    ]


register("next-app", type_="web-app", name="Next.js 14 app-router",
         description="Minimal Next.js 14 app using the app/ router.",
         files=_next_app)


# ── svelte-kit-min: SvelteKit minimal ─────────────────────────────────────────
def _svelte_kit_min(project_id: str) -> list[tuple[str, str]]:
    pkg = '{\n  "name": "' + project_id + '",\n  "version": "0.0.1",\n  "private": true,\n  "type": "module",\n  "scripts": {\n    "dev": "vite dev --port ${PORT:-5173}",\n    "build": "vite build",\n    "preview": "vite preview --port ${PORT:-5173}"\n  },\n  "devDependencies": {\n    "@sveltejs/adapter-auto": "^3.0.0",\n    "@sveltejs/kit": "^2.5.0",\n    "@sveltejs/vite-plugin-svelte": "^3.1.0",\n    "svelte": "^4.2.0",\n    "vite": "^5.4.0"\n  }\n}\n'
    return [
        ("package.json", pkg),
        ("svelte.config.js",
            '''import adapter from "@sveltejs/adapter-auto"

export default {
  kit: { adapter: adapter() },
}
'''),
        ("vite.config.js",
            '''import { sveltekit } from "@sveltejs/kit/vite"
import { defineConfig } from "vite"

export default defineConfig({ plugins: [sveltekit()] })
'''),
        ("src/routes/+page.svelte",
            f'''<script>
  let count = 0
</script>

<h1>{project_id}</h1>
<p>SvelteKit scaffold.</p>
<button on:click={{() => count++}}>clicked {{count}} times</button>
'''),
        ("README.md",
            f'''# {project_id} — SvelteKit

```
npm install
npm run dev
```
'''),
        (".gitignore", "node_modules/\n.svelte-kit/\nbuild/\n.env*.local\n.preview.json\n.preview.log\n"),
    ]


register("svelte-kit-min", type_="web-app", name="SvelteKit minimal",
         description="Minimal SvelteKit app with a single route.",
         files=_svelte_kit_min)


# ── htmx-flask: HTMX + Flask demo ─────────────────────────────────────────────
def _htmx_flask(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "Flask>=3.0\n"),
        ("app.py",
            f'''"""{project_id} — HTMX + Flask demo."""
from flask import Flask, render_template
import datetime, os

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", project={project_id!r})


@app.route("/api/time")
def now():
    return f"<span>server time: {{datetime.datetime.now().isoformat(timespec='seconds')}}</span>"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
'''),
        ("templates/index.html",
            f'''<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>{{{{ project }}}}</title>
    <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  </head>
  <body style="font-family: system-ui; padding: 24px">
    <h1>{{{{ project }}}}</h1>
    <button hx-get="/api/time" hx-target="#out" hx-swap="innerHTML">fetch time</button>
    <div id="out" style="margin-top: 12px"></div>
  </body>
</html>
'''),
        ("README.md",
            f'''# {project_id} — HTMX + Flask

```
pip install -r requirements.txt
python app.py
```
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.preview.json\n.preview.log\n"),
    ]


register("htmx-flask", type_="web-app", name="HTMX + Flask demo",
         description="Flask app rendering an HTMX-driven button that hits a server route.",
         files=_htmx_flask)


# ── vue-vite-min: Vue 3 + Vite ────────────────────────────────────────────────
def _vue_vite_min(project_id: str) -> list[tuple[str, str]]:
    pkg = '{\n  "name": "' + project_id + '",\n  "private": true,\n  "version": "0.0.1",\n  "type": "module",\n  "scripts": {\n    "dev": "vite --host 127.0.0.1 --port ${PORT:-5173}",\n    "build": "vite build",\n    "preview": "vite preview --port ${PORT:-5173}"\n  },\n  "dependencies": { "vue": "^3.4.0" },\n  "devDependencies": {\n    "@vitejs/plugin-vue": "^5.0.0",\n    "vite": "^5.4.0"\n  }\n}\n'
    return [
        ("package.json", pkg),
        ("vite.config.js",
            '''import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"

export default defineConfig({ plugins: [vue()] })
'''),
        ("index.html",
            f'''<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>{project_id}</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
'''),
        ("src/main.js",
            '''import { createApp } from "vue"
import App from "./App.vue"

createApp(App).mount("#app")
'''),
        ("src/App.vue",
            f'''<script setup>
import {{ ref }} from "vue"
const count = ref(0)
</script>

<template>
  <main style="padding: 24px; font-family: system-ui">
    <h1>{project_id}</h1>
    <p>Vue 3 + Vite scaffold.</p>
    <button @click="count++">clicked {{{{ count }}}} times</button>
  </main>
</template>
'''),
        (".gitignore", "node_modules/\ndist/\n.preview.json\n.preview.log\n"),
    ]


register("vue-vite-min", type_="web-app", name="Vue 3 + Vite minimal",
         description="Minimal Vue 3 single-file-component SPA powered by Vite.",
         files=_vue_vite_min)


# ── django-min: Django minimal ────────────────────────────────────────────────
def _django_min(project_id: str) -> list[tuple[str, str]]:
    pkg = project_id.replace("-", "_")
    return [
        ("requirements.txt", "Django>=5.0\n"),
        ("manage.py",
            f'''#!/usr/bin/env python
import os, sys

def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{pkg}.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()
'''),
        (f"{pkg}/__init__.py", ""),
        (f"{pkg}/settings.py",
            f'''import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
DEBUG = True
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "{pkg}.urls"
WSGI_APPLICATION = "{pkg}.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [{{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True, "OPTIONS": {{}}}}]
DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}}}
USE_TZ = True
STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
'''),
        (f"{pkg}/urls.py",
            f'''from django.http import JsonResponse
from django.urls import path


def index(request):
    return JsonResponse({{"message": "hello from {project_id}", "service": "{project_id}"}})


def healthz(request):
    return JsonResponse({{"ok": True}})


urlpatterns = [path("", index), path("healthz", healthz)]
'''),
        (f"{pkg}/wsgi.py",
            f'''import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{pkg}.settings")
application = get_wsgi_application()
'''),
        ("README.md",
            f'''# {project_id} — Django minimal

```
pip install -r requirements.txt
python manage.py runserver ${{PORT:-8000}}
```
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\ndb.sqlite3\n.preview.json\n.preview.log\n"),
    ]


register("django-min", type_="dashboard", name="Django minimal",
         description="Single-package Django project with /healthz and a JSON index.",
         files=_django_min)


# ── streamlit-min: Streamlit data app ─────────────────────────────────────────
def _streamlit_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "streamlit>=1.30\nnumpy>=1.26\n"),
        ("app.py",
            f'''"""{project_id} — Streamlit demo."""
import numpy as np
import streamlit as st

st.set_page_config(page_title={project_id!r})
st.title("{project_id}")

n = st.slider("samples", 10, 1000, 200)
data = np.cumsum(np.random.randn(n))
st.line_chart(data)
st.caption("Streamlit scaffold — edit app.py to customize.")
'''),
        ("README.md",
            f'''# {project_id} — Streamlit

```
pip install -r requirements.txt
streamlit run app.py --server.port ${{PORT:-8501}}
```
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.streamlit/\n.preview.json\n.preview.log\n"),
    ]


register("streamlit-min", type_="dashboard", name="Streamlit minimal",
         description="Streamlit app with a slider-driven line chart.",
         files=_streamlit_min)


# ── gradio-min: Gradio ML interface ───────────────────────────────────────────
def _gradio_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "gradio>=4.0\n"),
        ("app.py",
            f'''"""{project_id} — Gradio text demo."""
import os
import gradio as gr


def echo(text: str) -> str:
    return f"[{project_id}] {{text[::-1]}}"


demo = gr.Interface(fn=echo, inputs=gr.Textbox(label="input"), outputs=gr.Textbox(label="reversed"),
                    title={project_id!r}, description="Reverses your text.")

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=int(os.environ.get("PORT", 7860)))
'''),
        ("README.md",
            f'''# {project_id} — Gradio

```
pip install -r requirements.txt
python app.py
```
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\nflagged/\n.preview.json\n.preview.log\n"),
    ]


register("gradio-min", type_="dashboard", name="Gradio minimal",
         description="Gradio text→text Interface demo.",
         files=_gradio_min)


# ── starlette-min: Starlette async app ────────────────────────────────────────
def _starlette_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "starlette>=0.37\nuvicorn[standard]>=0.27\n"),
        ("app.py",
            f'''"""{project_id} — Starlette async app."""
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


async def healthz(request):
    return JSONResponse({{"ok": True, "service": {project_id!r}}})


async def index(request):
    return JSONResponse({{"message": "hello from {project_id}"}})


app = Starlette(debug=False, routes=[Route("/", index), Route("/healthz", healthz)])
'''),
        ("README.md",
            f'''# {project_id} — Starlette

```
pip install -r requirements.txt
uvicorn app:app --port ${{PORT:-8000}}
```
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.preview.json\n.preview.log\n"),
    ]


register("starlette-min", type_="dashboard", name="Starlette minimal",
         description="ASGI Starlette app with /healthz and JSON index.",
         files=_starlette_min)


# ── esp32-arduino-blink: PlatformIO + Arduino blink ───────────────────────────
def _esp32_arduino_blink(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id}
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
'''),
        ("src/main.cpp",
            '''#include <Arduino.h>

#ifndef LED_BUILTIN
#define LED_BUILTIN 2
#endif

void setup() {
  Serial.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  delay(500);
  digitalWrite(LED_BUILTIN, LOW);
  delay(500);
  Serial.println("blink");
}
'''),
        ("README.md",
            f'''# {project_id} — ESP32 Arduino blink

```
pio run -t upload
pio device monitor
```
'''),
        (".gitignore", ".pio/\n.vscode/\n.preview.json\n.preview.log\n"),
    ]


register("esp32-arduino-blink", type_="esp-firmware", name="ESP32 Arduino blink",
         description="PlatformIO + Arduino framework blink on GPIO 2.",
         files=_esp32_arduino_blink)


# ── esp32-wifi-sensor: WiFi + ADC sensor stub ─────────────────────────────────
def _esp32_wifi_sensor(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id}
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
'''),
        ("src/main.cpp",
            '''#include <Arduino.h>
#include <WiFi.h>

#ifndef WIFI_SSID
#define WIFI_SSID "your-ssid"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "your-pass"
#endif

#define ADC_PIN 34

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("connecting to %s...\\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; ++i) { delay(250); Serial.print("."); }
  Serial.println();
  Serial.println(WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "offline");
}

void loop() {
  int raw = analogRead(ADC_PIN);
  Serial.printf("adc=%d wifi=%d\\n", raw, WiFi.status() == WL_CONNECTED);
  delay(2000);
}
'''),
        ("README.md",
            f'''# {project_id} — ESP32 WiFi + ADC sensor

Edit `WIFI_SSID` / `WIFI_PASS` (or override via build_flags) and:

```
pio run -t upload
pio device monitor
```

Reads GPIO34 (ADC) and prints every 2s.
'''),
        (".gitignore", ".pio/\n.vscode/\n.preview.json\n.preview.log\n"),
    ]


register("esp32-wifi-sensor", type_="esp-firmware", name="ESP32 WiFi sensor",
         description="Connects to WiFi and prints ADC reads over serial.",
         files=_esp32_wifi_sensor)


# ── esp32-cam-stream: ESP32-CAM HTTP stream skeleton ──────────────────────────
def _esp32_cam_stream(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id}
[env:esp32cam]
platform = espressif32
board = esp32cam
framework = arduino
monitor_speed = 115200
board_build.partitions = huge_app.csv
'''),
        ("src/main.cpp",
            '''#include <Arduino.h>
#include <WiFi.h>
#include <esp_camera.h>

// AI Thinker ESP32-CAM pinout
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

void setup() {
  Serial.begin(115200);
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0=Y2_GPIO_NUM; c.pin_d1=Y3_GPIO_NUM; c.pin_d2=Y4_GPIO_NUM; c.pin_d3=Y5_GPIO_NUM;
  c.pin_d4=Y6_GPIO_NUM; c.pin_d5=Y7_GPIO_NUM; c.pin_d6=Y8_GPIO_NUM; c.pin_d7=Y9_GPIO_NUM;
  c.pin_xclk=XCLK_GPIO_NUM; c.pin_pclk=PCLK_GPIO_NUM; c.pin_vsync=VSYNC_GPIO_NUM;
  c.pin_href=HREF_GPIO_NUM; c.pin_sccb_sda=SIOD_GPIO_NUM; c.pin_sccb_scl=SIOC_GPIO_NUM;
  c.pin_pwdn=PWDN_GPIO_NUM; c.pin_reset=RESET_GPIO_NUM;
  c.xclk_freq_hz=20000000; c.pixel_format=PIXFORMAT_JPEG;
  c.frame_size=FRAMESIZE_QVGA; c.jpeg_quality=12; c.fb_count=1;
  if (esp_camera_init(&c) != ESP_OK) { Serial.println("camera init failed"); }
  else { Serial.println("camera ready — wire WiFi + httpd next"); }
}

void loop() { delay(1000); }
'''),
        ("README.md",
            f'''# {project_id} — ESP32-CAM stream skeleton

Wiring: AI Thinker ESP32-CAM. Connect via FTDI (5V, GND, U0R↔TX, U0T↔RX, IO0↔GND for flashing).

```
pio run -t upload
pio device monitor
```

This is a skeleton — add WiFi connect + esp_http_server stream handler next.
'''),
        (".gitignore", ".pio/\n.vscode/\n.preview.json\n.preview.log\n"),
    ]


register("esp32-cam-stream", type_="esp-firmware", name="ESP32-CAM stream skeleton",
         description="ESP32-CAM (AI Thinker) camera init scaffold — add httpd stream handler.",
         files=_esp32_cam_stream)


# ── esp32-ble-beacon: BLE iBeacon advertise ───────────────────────────────────
def _esp32_ble_beacon(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id}
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
'''),
        ("src/main.cpp",
            f'''#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEServer.h>
#include <BLEBeacon.h>

void setup() {{
  Serial.begin(115200);
  BLEDevice::init({project_id!r});
  BLEServer *server = BLEDevice::createServer();
  BLEAdvertising *advertising = BLEDevice::getAdvertising();

  BLEBeacon beacon;
  beacon.setManufacturerId(0x004C);
  beacon.setProximityUUID(BLEUUID("8AEFB031-6C32-486F-825B-E26FA193487D"));
  beacon.setMajor(1);
  beacon.setMinor(1);

  BLEAdvertisementData data;
  data.setFlags(0x04);
  std::string md = "\\x4C\\x00\\x02\\x15" + std::string(beacon.getData().c_str(), 23);
  data.setManufacturerData(md);
  advertising->setAdvertisementData(data);
  advertising->start();
  Serial.println("ibeacon advertising");
}}

void loop() {{ delay(1000); }}
'''),
        ("README.md",
            f'''# {project_id} — ESP32 BLE iBeacon

```
pio run -t upload
pio device monitor
```

Advertises an iBeacon with a fixed UUID. Scan with nRF Connect.
'''),
        (".gitignore", ".pio/\n.vscode/\n.preview.json\n.preview.log\n"),
    ]


register("esp32-ble-beacon", type_="esp-firmware", name="ESP32 BLE iBeacon",
         description="Advertises a fixed iBeacon UUID over BLE.",
         files=_esp32_ble_beacon)


# ── esp32-mqtt-pub: MQTT publisher ────────────────────────────────────────────
def _esp32_mqtt_pub(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id}
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
lib_deps =
  knolleary/PubSubClient @ ^2.8
'''),
        ("src/main.cpp",
            f'''#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>

#ifndef WIFI_SSID
#define WIFI_SSID "your-ssid"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "your-pass"
#endif
#ifndef MQTT_HOST
#define MQTT_HOST "192.168.1.10"
#endif
#define MQTT_PORT 1883
#define MQTT_TOPIC "{project_id}/heartbeat"

WiFiClient wifi;
PubSubClient mqtt(wifi);

void setup() {{
  Serial.begin(115200);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i=0; i<40 && WiFi.status()!=WL_CONNECTED; ++i) {{ delay(250); }}
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
}}

void loop() {{
  if (!mqtt.connected()) {{ mqtt.connect({project_id!r}); }}
  mqtt.loop();
  char buf[64];
  snprintf(buf, sizeof(buf), "uptime=%lu", millis()/1000);
  mqtt.publish(MQTT_TOPIC, buf);
  Serial.printf("pub %s -> %s\\n", MQTT_TOPIC, buf);
  delay(5000);
}}
'''),
        ("README.md",
            f'''# {project_id} — ESP32 MQTT publisher

Override broker / WiFi via `build_flags` in platformio.ini:

```ini
build_flags =
  -DWIFI_SSID=\\"my-ssid\\"
  -DWIFI_PASS=\\"my-pass\\"
  -DMQTT_HOST=\\"10.0.0.5\\"
```

```
pio run -t upload
pio device monitor
```
'''),
        (".gitignore", ".pio/\n.vscode/\n.preview.json\n.preview.log\n"),
    ]


register("esp32-mqtt-pub", type_="esp-firmware", name="ESP32 MQTT publisher",
         description="WiFi + PubSubClient publishes a heartbeat every 5s.",
         files=_esp32_mqtt_pub)


# ── micropython-esp32: MicroPython boot+main blinky ───────────────────────────
def _micropython_esp32(project_id: str) -> list[tuple[str, str]]:
    return [
        ("boot.py",
            f'''# {project_id} — boot.py runs on every wake.
import gc
gc.collect()
'''),
        ("main.py",
            f'''"""{project_id} — MicroPython blink on GPIO 2."""
from machine import Pin
import time

led = Pin(2, Pin.OUT)
while True:
    led.value(not led.value())
    time.sleep_ms(500)
'''),
        ("README.md",
            f'''# {project_id} — MicroPython ESP32

Flash the MicroPython firmware first, then push files:

```
mpremote connect /dev/ttyUSB0 fs cp boot.py :
mpremote connect /dev/ttyUSB0 fs cp main.py :
mpremote connect /dev/ttyUSB0 reset
```

Or with ampy:

```
ampy -p /dev/ttyUSB0 put main.py
```
'''),
        (".gitignore", "__pycache__/\n*.pyc\n.preview.json\n.preview.log\n"),
    ]


register("micropython-esp32", type_="esp-firmware", name="MicroPython ESP32",
         description="MicroPython boot+main blinky for ESP32. Flash via mpremote/ampy.",
         files=_micropython_esp32)


# ── stm32-bluepill-blink: STM32F103 + libopencm3 blink ────────────────────────
def _stm32_bluepill_blink(project_id: str) -> list[tuple[str, str]]:
    return [
        ("Makefile",
            f'''# {project_id} — STM32F103 (BluePill) via libopencm3
PROJECT   = {project_id}
DEVICE    = stm32f103c8
OPENCM3_DIR ?= ./libopencm3

CFILES    = src/main.c
LDLIBS   += -lopencm3_stm32f1
LDFLAGS  += -T$(OPENCM3_DIR)/lib/stm32/f1/stm32f103x8.ld

include $(OPENCM3_DIR)/mk/genlink-config.mk
include $(OPENCM3_DIR)/mk/gcc-config.mk
include $(OPENCM3_DIR)/mk/genlink-rules.mk
include $(OPENCM3_DIR)/mk/gcc-rules.mk
'''),
        ("src/main.c",
            '''#include <libopencm3/stm32/rcc.h>
#include <libopencm3/stm32/gpio.h>

int main(void) {
    rcc_periph_clock_enable(RCC_GPIOC);
    gpio_set_mode(GPIOC, GPIO_MODE_OUTPUT_2_MHZ, GPIO_CNF_OUTPUT_PUSHPULL, GPIO13);
    for (;;) {
        gpio_toggle(GPIOC, GPIO13);
        for (volatile int i = 0; i < 800000; ++i) __asm__("nop");
    }
    return 0;
}
'''),
        ("README.md",
            f'''# {project_id} — STM32 BluePill blink

Requires libopencm3 cloned alongside this project:

```
git clone https://github.com/libopencm3/libopencm3.git
make -C libopencm3 TARGETS=stm32/f1
make
```

Flash with st-flash (e.g. `st-flash write {project_id}.bin 0x8000000`).
'''),
        (".gitignore", "*.o\n*.d\n*.elf\n*.bin\n*.hex\n*.map\n*.list\nlibopencm3/\n.preview.json\n.preview.log\n"),
    ]


register("stm32-bluepill-blink", type_="stm-firmware", name="STM32 BluePill blink",
         description="STM32F103C8 blink on PC13 via libopencm3.",
         files=_stm32_bluepill_blink)


# ── lora-mesh-node: LoRa SX1276 send/receive node ─────────────────────────────
def _lora_mesh_node(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id}
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
lib_deps =
  sandeepmistry/LoRa @ ^0.8.0
'''),
        ("src/main.cpp",
            f'''#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>

// Heltec / TTGO LoRa32 default pinout
#define LORA_SS    18
#define LORA_RST   14
#define LORA_DIO0  26
#define LORA_FREQ  915E6  // US 915 MHz; EU 868E6, AS 433E6

static uint32_t counter = 0;

void setup() {{
  Serial.begin(115200);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  if (!LoRa.begin(LORA_FREQ)) {{ Serial.println("LoRa init failed"); while (1) delay(1000); }}
  Serial.println("LoRa ready");
}}

void loop() {{
  // TX
  LoRa.beginPacket();
  LoRa.printf("{project_id} #%lu", counter++);
  LoRa.endPacket();

  // RX window
  uint32_t until = millis() + 3000;
  while (millis() < until) {{
    int sz = LoRa.parsePacket();
    if (sz) {{
      String msg;
      while (LoRa.available()) msg += (char)LoRa.read();
      Serial.printf("RX rssi=%d %s\\n", LoRa.packetRssi(), msg.c_str());
    }}
  }}
}}
'''),
        ("README.md",
            f'''# {project_id} — LoRa SX1276 mesh node

**Pick the right frequency for your region** (LORA_FREQ in src/main.cpp):
- US: 915E6
- EU: 868E6
- AS: 433E6

Wiring assumes a Heltec/TTGO ESP32 LoRa32 board. Adjust pins for SX1276 modules wired to a bare ESP32.

```
pio run -t upload
pio device monitor
```
'''),
        (".gitignore", ".pio/\n.vscode/\n.preview.json\n.preview.log\n"),
    ]


register("lora-mesh-node", type_="lora-test", name="LoRa SX1276 mesh node",
         description="ESP32 + SX1276 LoRa node — TX a counter, RX for 3s, repeat.",
         files=_lora_mesh_node)


# ── python-cli-click: Click-based CLI ─────────────────────────────────────────
def _python_cli_click(project_id: str) -> list[tuple[str, str]]:
    pkg = project_id.replace("-", "_")
    return [
        ("pyproject.toml",
            f'''[project]
name = "{project_id}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["click>=8.1"]

[project.scripts]
{project_id} = "{pkg}.cli:main"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
'''),
        (f"src/{pkg}/__init__.py", '__version__ = "0.1.0"\n'),
        (f"src/{pkg}/cli.py",
            f'''import click


@click.command()
@click.option("--name", default="world", help="who to greet")
def main(name: str) -> None:
    """{project_id} CLI."""
    click.echo(f"hello, {{name}} — from {project_id}")


if __name__ == "__main__":
    main()
'''),
        ("tests/test_cli.py",
            f'''from click.testing import CliRunner
from {pkg}.cli import main


def test_default():
    r = CliRunner().invoke(main, [])
    assert r.exit_code == 0
    assert "world" in r.output


def test_named():
    r = CliRunner().invoke(main, ["--name", "baza"])
    assert r.exit_code == 0
    assert "baza" in r.output
'''),
        ("README.md",
            f'''# {project_id} — Click CLI

```
pip install -e .
{project_id} --name baza
pytest
```
'''),
        (".gitignore", "venv/\n__pycache__/\n*.egg-info/\n.pytest_cache/\nbuild/\ndist/\n"),
    ]


register("python-cli-click", type_="library", name="Python CLI (Click)",
         description="Click-based CLI with installable entrypoint and pytest+CliRunner.",
         files=_python_cli_click)


# ── python-typed-min: typed lib with ruff+mypy+pytest ─────────────────────────
def _python_typed_min(project_id: str) -> list[tuple[str, str]]:
    pkg = project_id.replace("-", "_")
    return [
        ("pyproject.toml",
            f'''[project]
name = "{project_id}"
version = "0.1.0"
requires-python = ">=3.10"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.mypy]
strict = true
python_version = "3.10"
files = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
'''),
        (f"src/{pkg}/__init__.py", 'from .core import add, greet\n\n__all__ = ["add", "greet"]\n'),
        (f"src/{pkg}/core.py",
            '''"""Typed helpers."""
from __future__ import annotations


def add(a: int, b: int) -> int:
    return a + b


def greet(name: str = "world") -> str:
    return f"hello, {name}"
'''),
        ("tests/test_core.py",
            f'''from {pkg} import add, greet


def test_add():
    assert add(2, 3) == 5


def test_greet():
    assert greet("baza") == "hello, baza"
'''),
        ("README.md",
            f'''# {project_id} — typed Python lib

```
pip install -e .[dev]
ruff check src tests
mypy
pytest
```
'''),
        (".gitignore", "venv/\n__pycache__/\n*.egg-info/\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\nbuild/\ndist/\n"),
    ]


register("python-typed-min", type_="library", name="Python typed (ruff+mypy+pytest)",
         description="src/ layout with ruff, mypy --strict, and pytest preconfigured.",
         files=_python_typed_min)


# ── go-cli-min: Go CLI ────────────────────────────────────────────────────────
def _go_cli_min(project_id: str) -> list[tuple[str, str]]:
    mod = project_id
    return [
        ("go.mod", f"module {mod}\n\ngo 1.22\n"),
        ("main.go",
            f'''package main

import (
\t"flag"
\t"fmt"
)

func greet(name string) string {{
\treturn fmt.Sprintf("hello, %s — from {project_id}", name)
}}

func main() {{
\tname := flag.String("name", "world", "who to greet")
\tflag.Parse()
\tfmt.Println(greet(*name))
}}
'''),
        ("main_test.go",
            '''package main

import "testing"

func TestGreet(t *testing.T) {
\tif got := greet("baza"); got == "" || got[:5] != "hello" {
\t\tt.Fatalf("unexpected: %q", got)
\t}
}
'''),
        ("README.md",
            f'''# {project_id} — Go CLI

```
go run . --name baza
go test ./...
go build -o {project_id}
```
'''),
        (".gitignore", f"{project_id}\n*.test\n*.out\n.preview.json\n.preview.log\n"),
    ]


register("go-cli-min", type_="library", name="Go CLI minimal",
         description="Tiny Go CLI with flag parsing and a unit test.",
         files=_go_cli_min)


# ── rust-cli-min: Rust CLI with clap ──────────────────────────────────────────
def _rust_cli_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("Cargo.toml",
            f'''[package]
name = "{project_id.replace('-', '_')}"
version = "0.1.0"
edition = "2021"

[dependencies]
clap = {{ version = "4.5", features = ["derive"] }}
'''),
        ("src/main.rs",
            f'''use clap::Parser;

#[derive(Parser, Debug)]
#[command(name = {project_id!r}, about = "tiny {project_id} CLI")]
struct Args {{
    /// Who to greet
    #[arg(short, long, default_value = "world")]
    name: String,
}}

fn main() {{
    let args = Args::parse();
    println!("hello, {{}} — from {project_id}", args.name);
}}
'''),
        ("README.md",
            f'''# {project_id} — Rust CLI

```
cargo run -- --name baza
cargo build --release
```
'''),
        (".gitignore", "target/\nCargo.lock\n.preview.json\n.preview.log\n"),
    ]


register("rust-cli-min", type_="library", name="Rust CLI (clap)",
         description="Rust CLI with clap derive parser.",
         files=_rust_cli_min)


# ── discord-bot-py: discord.py skeleton ───────────────────────────────────────
def _discord_bot_py(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "discord.py>=2.3\npython-dotenv>=1.0\n"),
        ("bot.py",
            f'''"""{project_id} — discord.py bot."""
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ.get("DISCORD_TOKEN", "")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"[{project_id}] logged in as {{bot.user}}")


@bot.command()
async def ping(ctx):
    await ctx.send("pong")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("set DISCORD_TOKEN in .env")
    bot.run(TOKEN)
'''),
        (".env.example", "DISCORD_TOKEN=your-bot-token-here\n"),
        ("README.md",
            f'''# {project_id} — Discord bot

```
cp .env.example .env  # then fill in DISCORD_TOKEN
pip install -r requirements.txt
python bot.py
```

Try `!ping` in any channel the bot can see.
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.env\n.preview.json\n.preview.log\n"),
    ]


register("discord-bot-py", type_="other", name="Discord bot (Python)",
         description="discord.py bot with !ping. Reads DISCORD_TOKEN from .env.",
         files=_discord_bot_py)


# ── telegram-bot-py: python-telegram-bot skeleton ─────────────────────────────
def _telegram_bot_py(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "python-telegram-bot>=21.0\npython-dotenv>=1.0\n"),
        ("bot.py",
            f'''"""{project_id} — python-telegram-bot."""
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"hi from {project_id} — try /ping")


async def ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong")


def main():
    if not TOKEN:
        raise SystemExit("set TELEGRAM_TOKEN in .env")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.run_polling()


if __name__ == "__main__":
    main()
'''),
        (".env.example", "TELEGRAM_TOKEN=your-bot-token-here\n"),
        ("README.md",
            f'''# {project_id} — Telegram bot

```
cp .env.example .env  # fill TELEGRAM_TOKEN
pip install -r requirements.txt
python bot.py
```

Send `/start` to the bot, then `/ping`.
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.env\n.preview.json\n.preview.log\n"),
    ]


register("telegram-bot-py", type_="other", name="Telegram bot (Python)",
         description="python-telegram-bot skeleton with /start and /ping handlers.",
         files=_telegram_bot_py)


# ── pygame-min: Pygame window template ────────────────────────────────────────
def _pygame_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "pygame>=2.5\n"),
        ("main.py",
            f'''"""{project_id} — Pygame window. ESC to quit."""
import pygame

pygame.init()
screen = pygame.display.set_mode((640, 480))
pygame.display.set_caption({project_id!r})
clock = pygame.time.Clock()

running = True
while running:
    for ev in pygame.event.get():
        if ev.type == pygame.QUIT:
            running = False
        elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
            running = False
    screen.fill((20, 20, 32))
    pygame.draw.circle(screen, (200, 220, 255), (320, 240), 60)
    pygame.display.flip()
    clock.tick(60)

pygame.quit()
'''),
        ("README.md",
            f'''# {project_id} — Pygame

```
pip install -r requirements.txt
python main.py
```

ESC or close-button to quit.
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.preview.json\n.preview.log\n"),
    ]


register("pygame-min", type_="other", name="Pygame minimal",
         description="640x480 Pygame window, ESC to quit, 60 FPS loop.",
         files=_pygame_min)


# ── electron-app-min: Electron desktop app ────────────────────────────────────
def _electron_app_min(project_id: str) -> list[tuple[str, str]]:
    pkg = '{\n  "name": "' + project_id + '",\n  "version": "0.1.0",\n  "main": "main.js",\n  "scripts": {\n    "start": "electron ."\n  },\n  "devDependencies": {\n    "electron": "^31.0.0"\n  }\n}\n'
    return [
        ("package.json", pkg),
        ("main.js",
            f'''const {{ app, BrowserWindow }} = require("electron")

function createWindow() {{
  const win = new BrowserWindow({{
    width: 800,
    height: 600,
    title: {project_id!r},
  }})
  win.loadFile("index.html")
}}

app.whenReady().then(createWindow)
app.on("window-all-closed", () => {{ if (process.platform !== "darwin") app.quit() }})
'''),
        ("index.html",
            f'''<!doctype html>
<html>
  <head><meta charset="utf-8" /><title>{project_id}</title></head>
  <body style="font-family: system-ui; padding: 24px">
    <h1>{project_id}</h1>
    <p>Electron scaffold ready.</p>
  </body>
</html>
'''),
        ("README.md",
            f'''# {project_id} — Electron

```
npm install
npm start
```
'''),
        (".gitignore", "node_modules/\ndist/\nout/\n.preview.json\n.preview.log\n"),
    ]


register("electron-app-min", type_="other", name="Electron minimal",
         description="Smallest viable Electron desktop app — main process + index.html.",
         files=_electron_app_min)


# ── browser-ext-mv3: Chrome MV3 extension ─────────────────────────────────────
def _browser_ext_mv3(project_id: str) -> list[tuple[str, str]]:
    manifest = '{\n  "manifest_version": 3,\n  "name": "' + project_id + '",\n  "version": "0.1.0",\n  "description": "' + project_id + ' Chrome extension.",\n  "action": {\n    "default_popup": "popup.html",\n    "default_title": "' + project_id + '"\n  },\n  "permissions": ["storage"]\n}\n'
    return [
        ("manifest.json", manifest),
        ("popup.html",
            f'''<!doctype html>
<html>
  <head><meta charset="utf-8" /><title>{project_id}</title></head>
  <body style="font-family: system-ui; padding: 12px; width: 240px">
    <h3>{project_id}</h3>
    <button id="go">click me</button>
    <p id="out"></p>
    <script src="popup.js"></script>
  </body>
</html>
'''),
        ("popup.js",
            f'''document.getElementById("go").addEventListener("click", () => {{
  document.getElementById("out").textContent = "clicked at " + new Date().toLocaleTimeString()
}})
'''),
        ("README.md",
            f'''# {project_id} — Chrome MV3 extension

1. Open `chrome://extensions`
2. Enable "Developer mode"
3. Click "Load unpacked" and pick this folder
'''),
        (".gitignore", "node_modules/\ndist/\n.preview.json\n.preview.log\n"),
    ]


register("browser-ext-mv3", type_="other", name="Chrome MV3 extension",
         description="Manifest V3 Chrome extension with a popup and click handler.",
         files=_browser_ext_mv3)


# ── docker-compose-app: app+postgres+redis stack ──────────────────────────────
def _docker_compose_app(project_id: str) -> list[tuple[str, str]]:
    return [
        ("docker-compose.yml",
            f'''services:
  app:
    build: .
    container_name: {project_id}-app
    environment:
      DATABASE_URL: postgres://postgres:postgres@db:5432/{project_id.replace('-', '_')}
      REDIS_URL: redis://cache:6379/0
    ports:
      - "8000:8000"
    depends_on: [db, cache]

  db:
    image: postgres:16-alpine
    container_name: {project_id}-db
    environment:
      POSTGRES_DB: {project_id.replace('-', '_')}
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - dbdata:/var/lib/postgresql/data

  cache:
    image: redis:7-alpine
    container_name: {project_id}-cache

volumes:
  dbdata:
'''),
        ("Dockerfile",
            '''FROM python:3.12-slim
WORKDIR /app
COPY app.py .
RUN pip install --no-cache-dir flask
EXPOSE 8000
CMD ["python", "app.py"]
'''),
        ("app.py",
            f'''"""{project_id} — minimal app for the compose stack."""
import os
from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/")
def index():
    return jsonify(
        service={project_id!r},
        db=os.environ.get("DATABASE_URL", ""),
        redis=os.environ.get("REDIS_URL", ""),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
'''),
        ("README.md",
            f'''# {project_id} — Docker Compose

```
docker compose up --build
curl http://localhost:8000/
```

Includes Postgres 16 and Redis 7.
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.env\n.preview.json\n.preview.log\n"),
    ]


register("docker-compose-app", type_="other", name="Docker Compose app",
         description="Compose stack with Flask app + Postgres 16 + Redis 7.",
         files=_docker_compose_app)


# ── hugo-site-min: Hugo static site ───────────────────────────────────────────
def _hugo_site_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("config.toml",
            f'''baseURL = "/"
languageCode = "en-us"
title = "{project_id}"
'''),
        ("content/_index.md",
            f'''---
title: "{project_id}"
---

Welcome to **{project_id}** — a Hugo site scaffold.
'''),
        ("layouts/index.html",
            '''<!doctype html>
<html>
  <head><meta charset="utf-8" /><title>{{ .Site.Title }}</title></head>
  <body style="font-family: system-ui; padding: 24px">
    <h1>{{ .Site.Title }}</h1>
    {{ .Content }}
  </body>
</html>
'''),
        ("README.md",
            f'''# {project_id} — Hugo site

```
hugo server -D -p ${{PORT:-1313}}
hugo  # builds to public/
```
'''),
        (".gitignore", "public/\nresources/\n.hugo_build.lock\n.preview.json\n.preview.log\n"),
    ]


register("hugo-site-min", type_="other", name="Hugo static site",
         description="Smallest viable Hugo site with one layout and one content page.",
         files=_hugo_site_min)


# ── mkdocs-site: MkDocs docs ──────────────────────────────────────────────────
def _mkdocs_site(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "mkdocs>=1.6\nmkdocs-material>=9.5\n"),
        ("mkdocs.yml",
            f'''site_name: {project_id}
theme:
  name: material
nav:
  - Home: index.md
  - Getting started: getting-started.md
'''),
        ("docs/index.md",
            f'''# {project_id}

Documentation for **{project_id}**.

See the [getting started](getting-started.md) page.
'''),
        ("docs/getting-started.md",
            f'''# Getting started

```
pip install -r requirements.txt
mkdocs serve -a 127.0.0.1:${{PORT:-8000}}
```

To build a static site:

```
mkdocs build  # outputs to site/
```
'''),
        ("README.md",
            f'''# {project_id} — MkDocs

Material-themed MkDocs site. Edit pages in `docs/`.
'''),
        (".gitignore", "venv/\nsite/\n__pycache__/\n.preview.json\n.preview.log\n"),
    ]


register("mkdocs-site", type_="other", name="MkDocs site",
         description="MkDocs Material documentation site with two pages.",
         files=_mkdocs_site)


# ── raspi-python-gpio: Raspberry Pi GPIO blink ────────────────────────────────
def _raspi_python_gpio(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "RPi.GPIO>=0.7\n"),
        ("blink.py",
            f'''"""{project_id} — blink an LED on BCM pin 17."""
import time
import RPi.GPIO as GPIO

LED = 17  # BCM numbering

GPIO.setmode(GPIO.BCM)
GPIO.setup(LED, GPIO.OUT)

try:
    while True:
        GPIO.output(LED, GPIO.HIGH)
        time.sleep(0.5)
        GPIO.output(LED, GPIO.LOW)
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
finally:
    GPIO.cleanup()
'''),
        ("README.md",
            f'''# {project_id} — Raspberry Pi GPIO blink

Wiring (BCM numbering):
- LED anode → 220Ω resistor → GPIO17 (physical pin 11)
- LED cathode → GND (physical pin 9)

```
sudo pip install -r requirements.txt
sudo python blink.py
```

`sudo` is needed for /dev/gpiomem access on most Pi OS setups.
'''),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.preview.json\n.preview.log\n"),
    ]


register("raspi-python-gpio", type_="other", name="Raspberry Pi GPIO blink",
         description="RPi.GPIO blink on BCM pin 17.",
         files=_raspi_python_gpio)


# ── trash-bot-esp32: Rubbish-Taxi inspired obstacle-avoidance bot ─────────────
def _trash_bot_esp32(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id}
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
'''),
        ("src/main.cpp",
            '''#include <Arduino.h>

// HC-SR04 ultrasonic
#define TRIG_PIN 5
#define ECHO_PIN 18

// L298N motor driver (left + right)
#define L_IN1 14
#define L_IN2 27
#define L_EN  26  // PWM
#define R_IN3 25
#define R_IN4 33
#define R_EN  32  // PWM

#define PWM_FREQ 1000
#define PWM_RES  8
#define CH_L     0
#define CH_R     1

static long readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW); delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  long dur = pulseIn(ECHO_PIN, HIGH, 30000);
  if (dur == 0) return 999;
  return dur / 58;
}

static void drive(int leftSpeed, int rightSpeed) {
  digitalWrite(L_IN1, leftSpeed >= 0); digitalWrite(L_IN2, leftSpeed < 0);
  digitalWrite(R_IN3, rightSpeed >= 0); digitalWrite(R_IN4, rightSpeed < 0);
  ledcWrite(CH_L, abs(leftSpeed));
  ledcWrite(CH_R, abs(rightSpeed));
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT); pinMode(ECHO_PIN, INPUT);
  pinMode(L_IN1, OUTPUT); pinMode(L_IN2, OUTPUT);
  pinMode(R_IN3, OUTPUT); pinMode(R_IN4, OUTPUT);
  ledcSetup(CH_L, PWM_FREQ, PWM_RES); ledcAttachPin(L_EN, CH_L);
  ledcSetup(CH_R, PWM_FREQ, PWM_RES); ledcAttachPin(R_EN, CH_R);
}

void loop() {
  long cm = readDistanceCm();
  Serial.printf("dist=%ld cm\\n", cm);
  if (cm < 25) {
    drive(-160, 160);  // spin in place
    delay(400);
  } else {
    drive(180, 180);   // forward
  }
  delay(50);
}
'''),
        ("README.md",
            f'''# {project_id} — Rubbish-Taxi inspired bot

Parts:
- ESP32 dev board
- HC-SR04 ultrasonic sensor
- L298N dual H-bridge motor driver
- 2× DC gear motors + chassis
- Battery pack (6–12V for motors, USB or 5V reg for ESP32 logic)

Wiring:
- HC-SR04: TRIG=GPIO5, ECHO=GPIO18, VCC=5V, GND=GND
- L298N left motor: IN1=GPIO14, IN2=GPIO27, ENA(PWM)=GPIO26
- L298N right motor: IN3=GPIO25, IN4=GPIO33, ENB(PWM)=GPIO32
- L298N logic GND tied to ESP32 GND; motor supply separate

```
pio run -t upload
pio device monitor
```

Behavior: drives forward, spins in place when an obstacle is <25 cm away.
'''),
        (".gitignore", ".pio/\n.vscode/\n.preview.json\n.preview.log\n"),
    ]


register("trash-bot-esp32", type_="esp-firmware", name="Trash bot (ESP32 + HC-SR04 + L298N)",
         description="Rubbish-Taxi inspired ESP32 obstacle-avoidance bot — ultrasonic + dual-motor H-bridge.",
         files=_trash_bot_esp32)


# ── nuxt-min ──────────────────────────────────────────────────────────────────
def _nuxt_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "private": true,
  "scripts": {{
    "dev": "nuxt dev",
    "build": "nuxt build",
    "preview": "nuxt preview"
  }},
  "devDependencies": {{
    "nuxt": "^3.11.0"
  }}
}}
'''),
        ("nuxt.config.ts",
            f'''// {project_id} — Nuxt 3 config
export default defineNuxtConfig({{
  devtools: {{ enabled: true }},
  app: {{
    head: {{ title: "{project_id}" }}
  }}
}})
'''),
        ("app.vue",
            f'''<template>
  <div class="app">
    <h1>{{{{ title }}}}</h1>
    <p>Nuxt 3 scaffold for <code>{project_id}</code></p>
  </div>
</template>

<script setup lang="ts">
const title = ref("{project_id}")
</script>

<style>
.app {{ font-family: system-ui, sans-serif; padding: 2rem; }}
</style>
'''),
        ("README.md", f"# {project_id}\n\nNuxt 3 scaffold.\n\n```\nnpm install\nnpm run dev\n```\n"),
        (".gitignore", "node_modules/\n.nuxt/\n.output/\ndist/\n.env\n"),
    ]


register("nuxt-min", type_="web-app", name="Nuxt 3 minimal",
         description="Minimal Nuxt 3 single-page scaffold with TypeScript config.",
         files=_nuxt_min)


# ── astro-min ─────────────────────────────────────────────────────────────────
def _astro_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "type": "module",
  "scripts": {{
    "dev": "astro dev",
    "build": "astro build",
    "preview": "astro preview"
  }},
  "devDependencies": {{
    "astro": "^4.5.0"
  }}
}}
'''),
        ("astro.config.mjs",
            f'''// {project_id} — Astro config
import {{ defineConfig }} from 'astro/config';

export default defineConfig({{
  site: 'https://example.com',
}});
'''),
        ("src/pages/index.astro",
            f'''---
const title = "{project_id}";
---
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{{title}}</title>
  </head>
  <body>
    <h1>{{title}}</h1>
    <p>Astro static scaffold.</p>
  </body>
</html>
'''),
        ("README.md", f"# {project_id}\n\nAstro static-site scaffold.\n\n```\nnpm install\nnpm run dev\n```\n"),
        (".gitignore", "node_modules/\ndist/\n.astro/\n.env\n"),
    ]


register("astro-min", type_="web-app", name="Astro minimal",
         description="Static Astro scaffold with a single index page.",
         files=_astro_min)


# ── solidjs-min ───────────────────────────────────────────────────────────────
def _solidjs_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "private": true,
  "type": "module",
  "scripts": {{
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  }},
  "dependencies": {{
    "solid-js": "^1.8.0"
  }},
  "devDependencies": {{
    "vite": "^5.0.0",
    "vite-plugin-solid": "^2.10.0"
  }}
}}
'''),
        ("vite.config.js",
            f'''import {{ defineConfig }} from 'vite';
import solid from 'vite-plugin-solid';

// {project_id} — SolidJS + Vite
export default defineConfig({{
  plugins: [solid()],
}});
'''),
        ("index.html",
            f'''<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>{project_id}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/index.jsx"></script>
  </body>
</html>
'''),
        ("src/index.jsx",
            f'''import {{ render }} from 'solid-js/web';
import App from './App';

render(() => <App />, document.getElementById('root'));
'''),
        ("src/App.jsx",
            f'''import {{ createSignal }} from 'solid-js';

export default function App() {{
  const [count, setCount] = createSignal(0);
  return (
    <div style={{{{ "font-family": "system-ui", padding: "2rem" }}}}>
      <h1>{project_id}</h1>
      <button onClick={{() => setCount(count() + 1)}}>clicks: {{count()}}</button>
    </div>
  );
}}
'''),
        (".gitignore", "node_modules/\ndist/\n.env\n"),
    ]


register("solidjs-min", type_="web-app", name="SolidJS + Vite minimal",
         description="SolidJS counter app with Vite bundler.",
         files=_solidjs_min)


# ── remix-min ─────────────────────────────────────────────────────────────────
def _remix_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "private": true,
  "type": "module",
  "scripts": {{
    "dev": "remix dev",
    "build": "remix build",
    "start": "remix-serve build"
  }},
  "dependencies": {{
    "@remix-run/node": "^2.8.0",
    "@remix-run/react": "^2.8.0",
    "@remix-run/serve": "^2.8.0",
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  }},
  "devDependencies": {{
    "typescript": "^5.3.0"
  }}
}}
'''),
        ("remix.config.js",
            f'''/** @type {{import('@remix-run/dev').AppConfig}} */
export default {{
  ignoredRouteFiles: ["**/.*"],
  // {project_id} Remix config
}};
'''),
        ("app/root.tsx",
            f'''import {{ Links, Meta, Outlet, Scripts }} from "@remix-run/react";

export default function App() {{
  return (
    <html lang="en">
      <head>
        <title>{project_id}</title>
        <Meta />
        <Links />
      </head>
      <body>
        <Outlet />
        <Scripts />
      </body>
    </html>
  );
}}
'''),
        ("app/routes/_index.tsx",
            f'''export default function Index() {{
  return (
    <main style={{{{ fontFamily: "system-ui", padding: "2rem" }}}}>
      <h1>{project_id}</h1>
      <p>Remix scaffold.</p>
    </main>
  );
}}
'''),
        ("README.md", f"# {project_id}\n\nRemix minimal scaffold.\n\n```\nnpm install\nnpm run dev\n```\n"),
        (".gitignore", "node_modules/\n.cache/\nbuild/\npublic/build/\n.env\n"),
    ]


register("remix-min", type_="web-app", name="Remix minimal",
         description="Minimal Remix app with a single index route.",
         files=_remix_min)


# ── nextjs-tailwind ───────────────────────────────────────────────────────────
def _nextjs_tailwind(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "private": true,
  "scripts": {{
    "dev": "next dev",
    "build": "next build",
    "start": "next start"
  }},
  "dependencies": {{
    "next": "^14.1.0",
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  }},
  "devDependencies": {{
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0"
  }}
}}
'''),
        ("tailwind.config.js",
            f'''/** @type {{import('tailwindcss').Config}} */
// {project_id} — Tailwind config
module.exports = {{
  content: ["./app/**/*.{{js,jsx,ts,tsx}}"],
  theme: {{ extend: {{}} }},
  plugins: [],
}};
'''),
        ("postcss.config.js",
            '''module.exports = {
  plugins: { tailwindcss: {}, autoprefixer: {} },
};
'''),
        ("app/layout.jsx",
            f'''import "./globals.css";

export const metadata = {{ title: "{project_id}" }};

export default function RootLayout({{ children }}) {{
  return (
    <html lang="en">
      <body className="bg-gray-50 text-gray-900">{{children}}</body>
    </html>
  );
}}
'''),
        ("app/page.jsx",
            f'''export default function Page() {{
  return (
    <main className="p-8">
      <h1 className="text-3xl font-bold">{project_id}</h1>
      <p className="mt-2 text-gray-600">Next.js 14 + Tailwind scaffold.</p>
    </main>
  );
}}
'''),
        ("app/globals.css", "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n"),
        ("README.md", f"# {project_id}\n\nNext.js 14 with Tailwind CSS.\n\n```\nnpm install\nnpm run dev\n```\n"),
        (".gitignore", "node_modules/\n.next/\nout/\n.env*\n"),
    ]


register("nextjs-tailwind", type_="web-app", name="Next.js 14 + Tailwind",
         description="Next.js 14 app-router scaffold with Tailwind CSS preconfigured.",
         files=_nextjs_tailwind)


# ── dash-plotly ───────────────────────────────────────────────────────────────
def _dash_plotly(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "dash>=2.16\nplotly>=5.20\n"),
        ("app.py",
            f'''"""{project_id} — Plotly Dash demo."""
from dash import Dash, dcc, html, Input, Output
import plotly.express as px

app = Dash(__name__)
app.title = "{project_id}"

app.layout = html.Div([
    html.H1("{project_id}"),
    dcc.Slider(1, 20, 1, value=5, id="n"),
    dcc.Graph(id="g"),
])


@app.callback(Output("g", "figure"), Input("n", "value"))
def update(n):
    return px.line(x=list(range(n)), y=[i * i for i in range(n)],
                   title=f"y = x^2 (n={{n}})")


if __name__ == "__main__":
    app.run(debug=False, port=8050)
'''),
        ("README.md", f"# {project_id}\n\nPlotly Dash slider + line graph demo.\n"),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n"),
    ]


register("dash-plotly", type_="dashboard", name="Plotly Dash demo",
         description="Plotly Dash app with a slider-driven line graph.",
         files=_dash_plotly)


# ── nicegui-min ───────────────────────────────────────────────────────────────
def _nicegui_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "nicegui>=1.4\n"),
        ("app.py",
            f'''"""{project_id} — NiceGUI button + label demo."""
from nicegui import ui

count = {{"n": 0}}

ui.label(f"{project_id}").style("font-size: 1.5rem; font-weight: 700;")
counter = ui.label("clicks: 0")


def bump():
    count["n"] += 1
    counter.text = f"clicks: {{count['n']}}"


ui.button("click me", on_click=bump)
ui.run(title="{project_id}", port=8080, reload=False)
'''),
        ("README.md", f"# {project_id}\n\nNiceGUI button + label counter.\n"),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n"),
    ]


register("nicegui-min", type_="dashboard", name="NiceGUI minimal",
         description="NiceGUI button/label counter demo.",
         files=_nicegui_min)


# ── nodejs-express ────────────────────────────────────────────────────────────
def _nodejs_express(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "private": true,
  "main": "app.js",
  "scripts": {{
    "start": "node app.js",
    "test": "node --test test/"
  }},
  "dependencies": {{
    "express": "^4.19.0"
  }},
  "devDependencies": {{
    "supertest": "^6.3.0"
  }}
}}
'''),
        ("app.js",
            f'''const express = require("express");
const app = express();

app.get("/healthz", (req, res) => res.json({{ ok: true, service: "{project_id}" }}));
app.get("/", (req, res) => res.json({{ message: "hello from {project_id}" }}));

if (require.main === module) {{
  const port = process.env.PORT || 3000;
  app.listen(port, () => console.log(`{project_id} on :${{port}}`));
}}
module.exports = app;
'''),
        ("test/app.test.js",
            f'''const test = require("node:test");
const assert = require("node:assert");
const request = require("supertest");
const app = require("../app");

test("{project_id} /healthz", async () => {{
  const r = await request(app).get("/healthz");
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.ok, true);
}});
'''),
        ("README.md", f"# {project_id}\n\nExpress minimal with /healthz + supertest.\n"),
        (".gitignore", "node_modules/\n.env\nnpm-debug.log\n"),
    ]


register("nodejs-express", type_="dashboard", name="Node.js Express minimal",
         description="Express server with /healthz and a supertest smoke test.",
         files=_nodejs_express)


# ── nestjs-min ────────────────────────────────────────────────────────────────
def _nestjs_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "private": true,
  "scripts": {{
    "start": "ts-node src/main.ts",
    "build": "tsc"
  }},
  "dependencies": {{
    "@nestjs/common": "^10.3.0",
    "@nestjs/core": "^10.3.0",
    "@nestjs/platform-express": "^10.3.0",
    "reflect-metadata": "^0.2.0",
    "rxjs": "^7.8.0"
  }},
  "devDependencies": {{
    "ts-node": "^10.9.0",
    "typescript": "^5.3.0"
  }}
}}
'''),
        ("tsconfig.json",
            '''{
  "compilerOptions": {
    "target": "ES2021",
    "module": "commonjs",
    "experimentalDecorators": true,
    "emitDecoratorMetadata": true,
    "esModuleInterop": true,
    "strict": true,
    "outDir": "dist"
  }
}
'''),
        ("src/main.ts",
            f'''import "reflect-metadata";
import {{ NestFactory }} from "@nestjs/core";
import {{ AppModule }} from "./app.module";

async function bootstrap() {{
  const app = await NestFactory.create(AppModule);
  await app.listen(3000);
  console.log("{project_id} listening on :3000");
}}
bootstrap();
'''),
        ("src/app.module.ts",
            f'''import {{ Module }} from "@nestjs/common";
import {{ AppController }} from "./app.controller";

@Module({{ controllers: [AppController] }})
export class AppModule {{}}
'''),
        ("src/app.controller.ts",
            f'''import {{ Controller, Get }} from "@nestjs/common";

@Controller()
export class AppController {{
  @Get("healthz")
  healthz() {{ return {{ ok: true, service: "{project_id}" }}; }}
}}
'''),
        ("README.md", f"# {project_id}\n\nNestJS minimal with /healthz.\n"),
        (".gitignore", "node_modules/\ndist/\n.env\n"),
    ]


register("nestjs-min", type_="dashboard", name="NestJS minimal",
         description="NestJS scaffold with a single /healthz controller.",
         files=_nestjs_min)


# ── hono-cloudflare ───────────────────────────────────────────────────────────
def _hono_cloudflare(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "private": true,
  "scripts": {{
    "dev": "wrangler dev",
    "deploy": "wrangler deploy"
  }},
  "dependencies": {{
    "hono": "^4.0.0"
  }},
  "devDependencies": {{
    "wrangler": "^3.30.0",
    "typescript": "^5.3.0"
  }}
}}
'''),
        ("wrangler.toml",
            f'''name = "{project_id}"
main = "src/index.ts"
compatibility_date = "2024-04-01"
'''),
        ("src/index.ts",
            f'''import {{ Hono }} from "hono";

const app = new Hono();
app.get("/", (c) => c.json({{ service: "{project_id}", ok: true }}));
app.get("/healthz", (c) => c.json({{ ok: true }}));

export default app;
'''),
        ("README.md", f"# {project_id}\n\nHono on Cloudflare Workers.\n\n```\nnpm install\nnpm run dev\n```\n"),
        (".gitignore", "node_modules/\n.wrangler/\ndist/\n.env\n"),
    ]


register("hono-cloudflare", type_="dashboard", name="Hono on Cloudflare Workers",
         description="Hono framework targeting Cloudflare Workers via Wrangler.",
         files=_hono_cloudflare)


# ── gin-go ────────────────────────────────────────────────────────────────────
def _gin_go(project_id: str) -> list[tuple[str, str]]:
    safe_mod = project_id.replace("_", "-")
    return [
        ("go.mod",
            f'''module {safe_mod}

go 1.21

require github.com/gin-gonic/gin v1.9.1
'''),
        ("main.go",
            f'''package main

import (
\t"net/http"

\t"github.com/gin-gonic/gin"
)

func newRouter() *gin.Engine {{
\tr := gin.Default()
\tr.GET("/healthz", func(c *gin.Context) {{
\t\tc.JSON(http.StatusOK, gin.H{{"ok": true, "service": "{project_id}"}})
\t}})
\treturn r
}}

func main() {{
\tnewRouter().Run(":8080")
}}
'''),
        ("main_test.go",
            f'''package main

import (
\t"net/http"
\t"net/http/httptest"
\t"testing"
)

func TestHealthz(t *testing.T) {{
\tw := httptest.NewRecorder()
\treq, _ := http.NewRequest("GET", "/healthz", nil)
\tnewRouter().ServeHTTP(w, req)
\tif w.Code != 200 {{
\t\tt.Fatalf("{project_id}: expected 200, got %d", w.Code)
\t}}
}}
'''),
        ("README.md", f"# {project_id}\n\nGin HTTP server with /healthz.\n\n```\ngo mod tidy && go run .\n```\n"),
        (".gitignore", "bin/\n*.test\n*.out\n.env\n"),
    ]


register("gin-go", type_="dashboard", name="Gin (Go) HTTP server",
         description="Go Gin server exposing /healthz, with a unit test.",
         files=_gin_go)


# ── actix-rust ────────────────────────────────────────────────────────────────
def _actix_rust(project_id: str) -> list[tuple[str, str]]:
    safe_name = project_id.replace("-", "_")
    return [
        ("Cargo.toml",
            f'''[package]
name = "{safe_name}"
version = "0.1.0"
edition = "2021"

[dependencies]
actix-web = "4"
'''),
        ("src/main.rs",
            f'''use actix_web::{{get, App, HttpServer, Responder, HttpResponse}};

#[get("/healthz")]
async fn healthz() -> impl Responder {{
    HttpResponse::Ok().json(serde_json::json!({{ "ok": true, "service": "{project_id}" }}))
}}

#[actix_web::main]
async fn main() -> std::io::Result<()> {{
    HttpServer::new(|| App::new().service(healthz))
        .bind(("127.0.0.1", 8080))?
        .run()
        .await
}}
'''),
        ("README.md", f"# {project_id}\n\nActix-web /healthz server.\n\n```\ncargo run\n```\n"),
        (".gitignore", "target/\nCargo.lock\n.env\n"),
    ]


register("actix-rust", type_="dashboard", name="Actix-web (Rust) server",
         description="Actix-web /healthz server in Rust.",
         files=_actix_rust)


# ── rails-min ─────────────────────────────────────────────────────────────────
def _rails_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("Gemfile",
            f'''source "https://rubygems.org"

ruby "~> 3.2"

# {project_id} — Rails 7 scaffold
gem "rails", "~> 7.1"
'''),
        ("config.ru",
            f'''# {project_id} — Rack config
require_relative "config/environment"
run Rails.application
Rails.application.load_server
'''),
        ("README.md",
            f"# {project_id}\n\nRails 7 placeholder. To bootstrap:\n\n```\nbundle install\nbundle exec rails new . --force --skip-bundle\nbundle install\nbin/rails server\n```\n"),
        (".gitignore", "tmp/\nlog/\n*.sqlite3\n.bundle/\nvendor/bundle/\n.env\n"),
    ]


register("rails-min", type_="dashboard", name="Ruby on Rails minimal",
         description="Rails 7 Gemfile + config.ru placeholder (run `rails new .` to expand).",
         files=_rails_min)


# ── esp32-deep-sleep ──────────────────────────────────────────────────────────
def _esp32_deep_sleep(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id} — ESP32 deep sleep
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
'''),
        ("src/main.cpp",
            f'''// {project_id} — wake on timer, log, deep-sleep again
#include <Arduino.h>

#define SLEEP_SECONDS 10ULL

RTC_DATA_ATTR int boot_count = 0;

void setup() {{
  Serial.begin(115200);
  delay(200);
  ++boot_count;
  Serial.printf("[{project_id}] boot #%d — sleeping %llu s\\n", boot_count, SLEEP_SECONDS);
  esp_sleep_enable_timer_wakeup(SLEEP_SECONDS * 1000000ULL);
  Serial.flush();
  esp_deep_sleep_start();
}}

void loop() {{}}
'''),
        ("README.md", f"# {project_id}\n\nESP32 deep-sleep cycle every 10s. Boot count persists in RTC memory.\n"),
        (".gitignore", ".pio/\n.vscode/\n"),
    ]


register("esp32-deep-sleep", type_="esp-firmware", name="ESP32 deep sleep",
         description="ESP32 deep-sleep wake-on-timer with RTC-backed boot counter.",
         files=_esp32_deep_sleep)


# ── esp32-ota-update ──────────────────────────────────────────────────────────
def _esp32_ota_update(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id} — ESP32 ArduinoOTA
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
lib_deps =
'''),
        ("src/main.cpp",
            f'''// {project_id} — WiFi + ArduinoOTA
#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoOTA.h>

const char* SSID = "your-ssid";
const char* PASS = "your-pass";

void setup() {{
  Serial.begin(115200);
  WiFi.begin(SSID, PASS);
  while (WiFi.status() != WL_CONNECTED) {{ delay(200); Serial.print("."); }}
  Serial.printf("\\n[{project_id}] IP %s\\n", WiFi.localIP().toString().c_str());

  ArduinoOTA.setHostname("{project_id}");
  ArduinoOTA.begin();
}}

void loop() {{
  ArduinoOTA.handle();
}}
'''),
        ("README.md",
            f"# {project_id}\n\nESP32 OTA via ArduinoOTA.\n\nWorkflow:\n1. Flash once over USB\n2. Set SSID/PASS\n3. Subsequent uploads: `pio run -t upload --upload-port {project_id}.local`\n"),
        (".gitignore", ".pio/\n.vscode/\n"),
    ]


register("esp32-ota-update", type_="esp-firmware", name="ESP32 OTA (ArduinoOTA)",
         description="ESP32 firmware with WiFi + ArduinoOTA mDNS-discoverable updates.",
         files=_esp32_ota_update)


# ── esp32-i2c-oled ────────────────────────────────────────────────────────────
def _esp32_i2c_oled(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id} — ESP32 + SSD1306 OLED
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
lib_deps =
  adafruit/Adafruit SSD1306 @ ^2.5.0
  adafruit/Adafruit GFX Library @ ^1.11.0
'''),
        ("src/main.cpp",
            f'''// {project_id} — SSD1306 0.96" OLED text demo
#include <Wire.h>
#include <Adafruit_SSD1306.h>

Adafruit_SSD1306 display(128, 64, &Wire, -1);

void setup() {{
  Serial.begin(115200);
  Wire.begin();
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {{
    Serial.println("OLED init failed");
    while (true) delay(1000);
  }}
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println("{project_id}");
  display.println("SSD1306 OK");
  display.display();
}}

void loop() {{ delay(1000); }}
'''),
        ("README.md",
            f"# {project_id}\n\nESP32 + SSD1306 OLED.\n\nWiring (I2C, addr 0x3C):\n- SDA → GPIO 21\n- SCL → GPIO 22\n- VCC → 3V3, GND → GND\n"),
        (".gitignore", ".pio/\n.vscode/\n"),
    ]


register("esp32-i2c-oled", type_="esp-firmware", name="ESP32 + SSD1306 OLED",
         description="ESP32 I2C OLED text demo using Adafruit SSD1306.",
         files=_esp32_i2c_oled)


# ── esp32-rfid-rc522 ──────────────────────────────────────────────────────────
def _esp32_rfid_rc522(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id} — ESP32 + MFRC522 RFID
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
lib_deps =
  miguelbalboa/MFRC522 @ ^1.4.10
'''),
        ("src/main.cpp",
            f'''// {project_id} — RFID UID reader
#include <SPI.h>
#include <MFRC522.h>

#define SS_PIN 5
#define RST_PIN 22

MFRC522 rfid(SS_PIN, RST_PIN);

void setup() {{
  Serial.begin(115200);
  SPI.begin();
  rfid.PCD_Init();
  Serial.println("[{project_id}] tap a card...");
}}

void loop() {{
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;
  Serial.print("UID:");
  for (byte i = 0; i < rfid.uid.size; i++) Serial.printf(" %02X", rfid.uid.uidByte[i]);
  Serial.println();
  rfid.PICC_HaltA();
}}
'''),
        ("README.md",
            f"# {project_id}\n\nESP32 + MFRC522 RFID UID reader.\n\nWiring (SPI):\n- SDA → 5, SCK → 18, MOSI → 23, MISO → 19\n- RST → 22, 3V3, GND\n"),
        (".gitignore", ".pio/\n.vscode/\n"),
    ]


register("esp32-rfid-rc522", type_="esp-firmware", name="ESP32 + MFRC522 RFID",
         description="ESP32 MFRC522 RFID reader logging UIDs over serial.",
         files=_esp32_rfid_rc522)


# ── esp32-relay-control ───────────────────────────────────────────────────────
def _esp32_relay_control(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id} — ESP32 4-channel relay (serial commands)
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
'''),
        ("src/main.cpp",
            f'''// {project_id} — 4-relay control via serial: "1 on", "3 off"
#include <Arduino.h>

const int RELAYS[4] = {{16, 17, 18, 19}};

void setup() {{
  Serial.begin(115200);
  for (int i = 0; i < 4; i++) {{ pinMode(RELAYS[i], OUTPUT); digitalWrite(RELAYS[i], HIGH); }}
  Serial.println("[{project_id}] commands: <1-4> <on|off>");
}}

void loop() {{
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\\n');
  int ch = line.substring(0, 1).toInt();
  String state = line.substring(2);
  state.trim();
  if (ch < 1 || ch > 4) return;
  digitalWrite(RELAYS[ch - 1], state == "on" ? LOW : HIGH);
  Serial.printf("relay %d -> %s\\n", ch, state.c_str());
}}
'''),
        ("README.md", f"# {project_id}\n\nESP32 4-channel relay via serial. Active-low module assumed.\n"),
        (".gitignore", ".pio/\n.vscode/\n"),
    ]


register("esp32-relay-control", type_="esp-firmware", name="ESP32 4-channel relay",
         description="ESP32 4-channel relay control via serial commands.",
         files=_esp32_relay_control)


# ── esp32-stepper-motor ───────────────────────────────────────────────────────
def _esp32_stepper_motor(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id} — ESP32 + A4988 stepper
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
lib_deps =
  waspinator/AccelStepper @ ^1.64
'''),
        ("src/main.cpp",
            f'''// {project_id} — A4988 stepper sweep with AccelStepper
#include <AccelStepper.h>

#define STEP_PIN 14
#define DIR_PIN  27

AccelStepper stepper(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

void setup() {{
  Serial.begin(115200);
  stepper.setMaxSpeed(800);
  stepper.setAcceleration(400);
  Serial.println("[{project_id}] sweeping");
}}

void loop() {{
  if (stepper.distanceToGo() == 0) {{
    stepper.moveTo(stepper.currentPosition() == 0 ? 800 : 0);
  }}
  stepper.run();
}}
'''),
        ("README.md",
            f"# {project_id}\n\nESP32 + A4988 stepper driver sweep.\n\nWiring:\n- STEP → GPIO 14, DIR → GPIO 27\n- A4988 VMOT (8-35V), GND; logic VDD → 3V3\n- MS1/MS2/MS3 left floating = full step\n"),
        (".gitignore", ".pio/\n.vscode/\n"),
    ]


register("esp32-stepper-motor", type_="esp-firmware", name="ESP32 + A4988 stepper",
         description="ESP32 stepper sweep via AccelStepper and A4988 driver.",
         files=_esp32_stepper_motor)


# ── esp8266-pio-blink ─────────────────────────────────────────────────────────
def _esp8266_pio_blink(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id} — ESP8266 blink
[env:nodemcuv2]
platform = espressif8266
board = nodemcuv2
framework = arduino
monitor_speed = 115200
'''),
        ("src/main.cpp",
            f'''// {project_id} — ESP8266 onboard LED blink (active-low)
#include <Arduino.h>

void setup() {{
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  Serial.println("[{project_id}] blinking");
}}

void loop() {{
  digitalWrite(LED_BUILTIN, LOW);
  delay(500);
  digitalWrite(LED_BUILTIN, HIGH);
  delay(500);
}}
'''),
        ("README.md", f"# {project_id}\n\nESP8266 NodeMCU blink (PlatformIO).\n"),
        (".gitignore", ".pio/\n.vscode/\n"),
    ]


register("esp8266-pio-blink", type_="esp-firmware", name="ESP8266 PlatformIO blink",
         description="ESP8266 NodeMCU onboard-LED blink using PlatformIO + Arduino.",
         files=_esp8266_pio_blink)


# ── stm32f4-hal-blink ─────────────────────────────────────────────────────────
def _stm32f4_hal_blink(project_id: str) -> list[tuple[str, str]]:
    return [
        ("Makefile",
            f'''# {project_id} — STM32F4 Discovery blink (LD3 on PD12)
TARGET = {project_id}
PREFIX = arm-none-eabi-
CC     = $(PREFIX)gcc
SRCS   = src/main.c src/system_init.c
CFLAGS = -mcpu=cortex-m4 -mthumb -O2 -Wall -DSTM32F407xx

all:
\t@echo "stub — configure HAL/CubeMX include paths and link script before building"
clean:
\trm -f *.elf *.o
'''),
        ("src/main.c",
            f'''/* {project_id} — STM32F407 HAL blink stub (PD12 LD3) */
#include "stm32f4xx_hal.h"

void SystemClock_Config(void);

int main(void) {{
  HAL_Init();
  SystemClock_Config();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  GPIO_InitTypeDef g = {{0}};
  g.Pin = GPIO_PIN_12;
  g.Mode = GPIO_MODE_OUTPUT_PP;
  g.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOD, &g);
  while (1) {{
    HAL_GPIO_TogglePin(GPIOD, GPIO_PIN_12);
    HAL_Delay(500);
  }}
}}
'''),
        ("src/system_init.c",
            f'''/* {project_id} — clock config stub. Replace with CubeMX-generated SystemClock_Config(). */
#include "stm32f4xx_hal.h"

void SystemClock_Config(void) {{
  /* Generated by STM32CubeMX in real projects. */
}}

void SysTick_Handler(void) {{ HAL_IncTick(); }}
'''),
        ("README.md",
            f"# {project_id}\n\nSTM32F4 Discovery (LD3) blink via HAL.\n\nGenerate HAL drivers + linker script via STM32CubeMX, then build with `make`.\n"),
        (".gitignore", "*.o\n*.elf\n*.bin\nbuild/\n"),
    ]


register("stm32f4-hal-blink", type_="stm-firmware", name="STM32F4 HAL blink",
         description="STM32F407 Discovery LD3 blink using HAL (CubeMX scaffold).",
         files=_stm32f4_hal_blink)


# ── stm32-freertos-min ────────────────────────────────────────────────────────
def _stm32_freertos_min(project_id: str) -> list[tuple[str, str]]:
    return [
        ("Makefile",
            f'''# {project_id} — STM32F4 + FreeRTOS
TARGET = {project_id}
PREFIX = arm-none-eabi-
CFLAGS = -mcpu=cortex-m4 -mthumb -O2 -Wall -DSTM32F407xx

all:
\t@echo "stub — wire FreeRTOS source + HAL via CubeMX, then build"
clean:
\trm -f *.elf *.o
'''),
        ("src/main.c",
            f'''/* {project_id} — FreeRTOS single-task blink on STM32F4 */
#include "FreeRTOS.h"
#include "task.h"
#include "stm32f4xx_hal.h"

static void blink_task(void *arg) {{
  (void)arg;
  for (;;) {{
    HAL_GPIO_TogglePin(GPIOD, GPIO_PIN_12);
    vTaskDelay(pdMS_TO_TICKS(500));
  }}
}}

int main(void) {{
  HAL_Init();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  GPIO_InitTypeDef g = {{ .Pin = GPIO_PIN_12, .Mode = GPIO_MODE_OUTPUT_PP }};
  HAL_GPIO_Init(GPIOD, &g);
  xTaskCreate(blink_task, "blink", 128, NULL, 1, NULL);
  vTaskStartScheduler();
  for (;;) {{}}
}}
'''),
        ("src/freertos_hooks.c",
            f'''/* {project_id} — FreeRTOS hooks */
#include "FreeRTOS.h"
#include "task.h"

void vApplicationStackOverflowHook(TaskHandle_t xTask, char *name) {{ (void)xTask; (void)name; for (;;) {{}} }}
void vApplicationMallocFailedHook(void) {{ for (;;) {{}} }}
'''),
        ("README.md",
            f"# {project_id}\n\nFreeRTOS on STM32F4 with one blink task. Bring in FreeRTOS kernel + HAL via CubeMX or a submodule before building.\n"),
        (".gitignore", "*.o\n*.elf\nbuild/\n"),
    ]


register("stm32-freertos-min", type_="stm-firmware", name="STM32F4 + FreeRTOS",
         description="STM32F407 FreeRTOS scaffold with a single blink task.",
         files=_stm32_freertos_min)


# ── meshtastic-node-stub ──────────────────────────────────────────────────────
def _meshtastic_node_stub(project_id: str) -> list[tuple[str, str]]:
    return [
        ("README.md",
            f"# {project_id}\n\nMeshtastic firmware fork stub.\n\n1. `git clone --recurse-submodules https://github.com/meshtastic/firmware.git`\n2. Apply your `platformio_overlay.ini` over the upstream config\n3. Tweak `userPrefs.h` (region, role) per `build_instructions.md`\n"),
        ("build_instructions.md",
            f"# Build {project_id}\n\n```\n# from inside firmware/\npio run -e tbeam -t upload\n```\n\nEdit `src/mesh/RadioInterface.h` then re-flash.\n"),
        ("platformio_overlay.ini",
            f'''; {project_id} — overlay for Meshtastic upstream build
[env:tbeam]
build_flags =
  -DMESHTASTIC_PROJECT="{project_id}"
'''),
        (".gitignore", "firmware/\n.pio/\n*.bin\n"),
    ]


register("meshtastic-node-stub", type_="lora-test", name="Meshtastic node stub",
         description="Pointer + overlay for forking and rebuilding meshtastic/firmware.",
         files=_meshtastic_node_stub)


# ── ttn-uplink-node ───────────────────────────────────────────────────────────
def _ttn_uplink_node(project_id: str) -> list[tuple[str, str]]:
    return [
        ("platformio.ini",
            f'''; {project_id} — TTN OTAA uplink (LMIC)
[env:ttgo-lora32-v1]
platform = espressif32
board = ttgo-lora32-v1
framework = arduino
monitor_speed = 115200
lib_deps =
  mcci-catena/MCCI LoRaWAN LMIC library @ ^4.1.1
build_flags =
  -D CFG_us915=1
  -D CFG_sx1276_radio=1
'''),
        ("src/main.cpp",
            f'''// {project_id} — TTN OTAA join + uplink every 60s
#include <lmic.h>
#include <hal/hal.h>
#include <SPI.h>

// ZERO these out for compile-time scaffolding; provision real values from TTN console.
static const u1_t PROGMEM APPEUI[8] = {{0}};
static const u1_t PROGMEM DEVEUI[8] = {{0}};
static const u1_t PROGMEM APPKEY[16] = {{0}};

void os_getArtEui(u1_t* buf) {{ memcpy_P(buf, APPEUI, 8); }}
void os_getDevEui(u1_t* buf) {{ memcpy_P(buf, DEVEUI, 8); }}
void os_getDevKey(u1_t* buf) {{ memcpy_P(buf, APPKEY, 16); }}

static osjob_t sendjob;

void do_send(osjob_t* j) {{
  uint8_t payload[3] = {{ 0xA1, 0xB2, 0xC3 }};
  LMIC_setTxData2(1, payload, sizeof(payload), 0);
}}

void onEvent(ev_t ev) {{
  if (ev == EV_TXCOMPLETE) os_setTimedCallback(&sendjob, os_getTime() + sec2osticks(60), do_send);
}}

void setup() {{
  Serial.begin(115200);
  Serial.println("[{project_id}] LMIC init");
  os_init();
  LMIC_reset();
  do_send(&sendjob);
}}

void loop() {{ os_runloop_once(); }}
'''),
        ("README.md",
            f"# {project_id}\n\nTTN LoRaWAN OTAA uplink for ESP32 + SX1276 (TTGO LoRa32 v1).\n\nSet correct region in `platformio.ini` (`CFG_us915` / `CFG_eu868` / `CFG_au915`).\nFill in `APPEUI`, `DEVEUI`, `APPKEY` from your TTN application.\n"),
        (".gitignore", ".pio/\n.vscode/\n"),
    ]


register("ttn-uplink-node", type_="lora-test", name="TTN LoRaWAN uplink node",
         description="ESP32 + SX1276 LoRaWAN OTAA uplink to The Things Network.",
         files=_ttn_uplink_node)


# ── node-typescript-lib ───────────────────────────────────────────────────────
def _node_typescript_lib(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "version": "0.1.0",
  "type": "module",
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "scripts": {{
    "build": "tsc",
    "test": "vitest run"
  }},
  "devDependencies": {{
    "typescript": "^5.3.0",
    "vitest": "^1.4.0"
  }}
}}
'''),
        ("tsconfig.json",
            '''{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "declaration": true,
    "strict": true,
    "outDir": "dist"
  },
  "include": ["src"]
}
'''),
        ("src/index.ts",
            f'''/** {project_id} — typed library entry point */
export const NAME = "{project_id}";
export function greet(name: string): string {{
  return `hello ${{name}} from ${{NAME}}`;
}}
'''),
        ("tests/index.test.ts",
            f'''import {{ describe, it, expect }} from "vitest";
import {{ greet, NAME }} from "../src/index";

describe("{project_id}", () => {{
  it("greets", () => {{
    expect(greet("world")).toBe(`hello world from ${{NAME}}`);
  }});
}});
'''),
        ("README.md", f"# {project_id}\n\nTypeScript library with vitest.\n"),
        (".gitignore", "node_modules/\ndist/\n.vitest/\n"),
    ]


register("node-typescript-lib", type_="library", name="Node TypeScript library",
         description="TypeScript library skeleton with vitest tests.",
         files=_node_typescript_lib)


# ── cpp-cmake-lib ─────────────────────────────────────────────────────────────
def _cpp_cmake_lib(project_id: str) -> list[tuple[str, str]]:
    safe_name = project_id.replace("-", "_")
    return [
        ("CMakeLists.txt",
            f'''cmake_minimum_required(VERSION 3.20)
project({safe_name} CXX)
set(CMAKE_CXX_STANDARD 20)

add_library({safe_name} STATIC src/lib.cpp)
target_include_directories({safe_name} PUBLIC include)

enable_testing()
include(FetchContent)
FetchContent_Declare(googletest URL https://github.com/google/googletest/archive/refs/tags/v1.14.0.zip)
FetchContent_MakeAvailable(googletest)

add_executable(test_{safe_name} tests/test_lib.cpp)
target_link_libraries(test_{safe_name} PRIVATE {safe_name} GTest::gtest_main)
add_test(NAME test_{safe_name} COMMAND test_{safe_name})
'''),
        ("include/lib.hpp",
            f'''#pragma once
#include <string>

namespace {safe_name} {{
inline constexpr const char* NAME = "{project_id}";
int add(int a, int b);
std::string banner();
}}
'''),
        ("src/lib.cpp",
            f'''#include "lib.hpp"

namespace {safe_name} {{
int add(int a, int b) {{ return a + b; }}
std::string banner() {{ return std::string("hello from ") + NAME; }}
}}
'''),
        ("tests/test_lib.cpp",
            f'''#include <gtest/gtest.h>
#include "lib.hpp"

TEST({safe_name}, Add) {{
  EXPECT_EQ({safe_name}::add(2, 3), 5);
}}

TEST({safe_name}, Banner) {{
  EXPECT_NE({safe_name}::banner().find("{project_id}"), std::string::npos);
}}
'''),
        ("README.md", f"# {project_id}\n\nC++20 static library with GoogleTest.\n\n```\ncmake -S . -B build\ncmake --build build\nctest --test-dir build\n```\n"),
        (".gitignore", "build/\n*.o\n*.a\n.cache/\n"),
    ]


register("cpp-cmake-lib", type_="library", name="C++ CMake library",
         description="Modern C++20 static library with GoogleTest via FetchContent.",
         files=_cpp_cmake_lib)


# ── deno-module ───────────────────────────────────────────────────────────────
def _deno_module(project_id: str) -> list[tuple[str, str]]:
    return [
        ("deno.json",
            f'''{{
  "name": "{project_id}",
  "tasks": {{
    "test": "deno test",
    "fmt": "deno fmt"
  }}
}}
'''),
        ("mod.ts",
            f'''/** {project_id} — Deno module */
export const NAME = "{project_id}";

export function greet(name: string): string {{
  return `hello ${{name}} from ${{NAME}}`;
}}
'''),
        ("mod_test.ts",
            f'''import {{ assertEquals }} from "https://deno.land/std@0.220.0/assert/mod.ts";
import {{ greet }} from "./mod.ts";

Deno.test("{project_id} greet", () => {{
  assertEquals(greet("world"), "hello world from {project_id}");
}});
'''),
        ("README.md", f"# {project_id}\n\nDeno module.\n\n```\ndeno task test\n```\n"),
        (".gitignore", ".deno/\ncoverage/\n"),
    ]


register("deno-module", type_="library", name="Deno module",
         description="Deno TypeScript module with built-in test task.",
         files=_deno_module)


# ── ruby-gem ──────────────────────────────────────────────────────────────────
def _ruby_gem(project_id: str) -> list[tuple[str, str]]:
    safe_name = project_id.replace("-", "_")
    return [
        (f"{safe_name}.gemspec",
            f'''require_relative "lib/{safe_name}/version"

Gem::Specification.new do |spec|
  spec.name          = "{project_id}"
  spec.version       = {safe_name.capitalize()}::VERSION
  spec.authors       = ["Baza Empire"]
  spec.summary       = "{project_id} ruby gem skeleton"
  spec.files         = Dir["lib/**/*.rb"]
  spec.require_paths = ["lib"]
  spec.add_development_dependency "rspec", "~> 3.12"
end
'''),
        (f"lib/{safe_name}.rb",
            f'''require_relative "{safe_name}/version"

module {safe_name.capitalize()}
  NAME = "{project_id}".freeze

  def self.greet(name)
    "hello #{{name}} from #{{NAME}}"
  end
end
'''),
        (f"lib/{safe_name}/version.rb",
            f'''module {safe_name.capitalize()}
  VERSION = "0.1.0".freeze
end
'''),
        (f"spec/{safe_name}_spec.rb",
            f'''require "{safe_name}"

RSpec.describe {safe_name.capitalize()} do
  it "greets" do
    expect({safe_name.capitalize()}.greet("world")).to eq("hello world from {project_id}")
  end
end
'''),
        ("README.md", f"# {project_id}\n\nRuby gem skeleton.\n\n```\nbundle exec rspec\ngem build {safe_name}.gemspec\n```\n"),
        (".gitignore", "*.gem\nGemfile.lock\npkg/\n.rspec_status\n"),
    ]


register("ruby-gem", type_="library", name="Ruby gem skeleton",
         description="Ruby gem skeleton with version module and RSpec test.",
         files=_ruby_gem)


# ── aws-lambda-py ─────────────────────────────────────────────────────────────
def _aws_lambda_py(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "# add runtime deps here\n"),
        ("lambda_function.py",
            f'''"""{project_id} — AWS Lambda handler."""
import json


def lambda_handler(event, context):
    return {{
        "statusCode": 200,
        "body": json.dumps({{"ok": True, "service": "{project_id}", "event": event}}),
    }}
'''),
        ("template.yaml",
            f'''AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: {project_id}

Resources:
  {project_id.replace("-", "")}Fn:
    Type: AWS::Serverless::Function
    Properties:
      Handler: lambda_function.lambda_handler
      Runtime: python3.12
      CodeUri: .
      Timeout: 10
      Events:
        Api:
          Type: HttpApi
          Properties:
            Path: /
            Method: get
'''),
        ("README.md", f"# {project_id}\n\nAWS Lambda function packaged with SAM.\n\n```\nsam build && sam deploy --guided\n```\n"),
        (".gitignore", ".aws-sam/\n__pycache__/\n*.pyc\n.env\n"),
    ]


register("aws-lambda-py", type_="other", name="AWS Lambda (Python)",
         description="Python AWS Lambda function with SAM template for HTTP API.",
         files=_aws_lambda_py)


# ── cloudflare-worker-ts ──────────────────────────────────────────────────────
def _cloudflare_worker_ts(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "private": true,
  "scripts": {{
    "dev": "wrangler dev",
    "deploy": "wrangler deploy"
  }},
  "devDependencies": {{
    "wrangler": "^3.30.0",
    "typescript": "^5.3.0"
  }}
}}
'''),
        ("wrangler.toml",
            f'''name = "{project_id}"
main = "src/index.ts"
compatibility_date = "2024-04-01"
'''),
        ("src/index.ts",
            f'''export default {{
  async fetch(request: Request): Promise<Response> {{
    const url = new URL(request.url);
    return new Response(JSON.stringify({{
      service: "{project_id}",
      path: url.pathname,
      ok: true,
    }}), {{ headers: {{ "content-type": "application/json" }} }});
  }},
}};
'''),
        ("README.md", f"# {project_id}\n\nCloudflare Worker (TypeScript).\n\n```\nnpm install\nnpm run dev\n```\n"),
        (".gitignore", "node_modules/\n.wrangler/\ndist/\n.env\n"),
    ]


register("cloudflare-worker-ts", type_="other", name="Cloudflare Worker (TS)",
         description="TypeScript Cloudflare Worker with a JSON fetch handler.",
         files=_cloudflare_worker_ts)


# ── github-action-py ──────────────────────────────────────────────────────────
def _github_action_py(project_id: str) -> list[tuple[str, str]]:
    return [
        ("action.yml",
            f'''name: "{project_id}"
description: "Custom composite GitHub Action — {project_id}"
inputs:
  who:
    description: "Name to greet"
    required: false
    default: "world"
outputs:
  greeting:
    description: "Greeting string"
    value: ${{{{ steps.run.outputs.greeting }}}}
runs:
  using: "composite"
  steps:
    - id: run
      shell: bash
      run: python ${{{{ github.action_path }}}}/scripts/main.py "${{{{ inputs.who }}}}"
'''),
        ("scripts/main.py",
            f'''"""{project_id} — GitHub Action entrypoint."""
import os
import sys

who = sys.argv[1] if len(sys.argv) > 1 else "world"
msg = f"hello {{who}} from {project_id}"
print(msg)
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a") as f:
        f.write(f"greeting={{msg}}\\n")
'''),
        ("README.md", f"# {project_id}\n\nComposite GitHub Action.\n\nUse with:\n\n```yaml\n- uses: ./\n  with:\n    who: serge\n```\n"),
        (".gitignore", "__pycache__/\n*.pyc\n.env\n"),
    ]


register("github-action-py", type_="other", name="GitHub Action (Python)",
         description="Composite GitHub Action with a Python entrypoint.",
         files=_github_action_py)


# ── langchain-agent-py ────────────────────────────────────────────────────────
def _langchain_agent_py(project_id: str) -> list[tuple[str, str]]:
    return [
        ("requirements.txt", "langchain>=0.1\nlangchain-openai>=0.1\npython-dotenv>=1.0\n"),
        ("agent.py",
            f'''"""{project_id} — LangChain agent skeleton."""
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()


def build_agent():
    return ChatOpenAI(
        model="gpt-4o-mini",
        api_key=os.environ.get("OPENAI_API_KEY"),
        temperature=0.2,
    )


def main():
    llm = build_agent()
    prompt = "In one sentence, describe the project {project_id}."
    resp = llm.invoke(prompt)
    print(resp.content)


if __name__ == "__main__":
    main()
'''),
        (".env.example", "OPENAI_API_KEY=sk-...\n"),
        ("README.md", f"# {project_id}\n\nLangChain agent skeleton.\n\n```\npip install -r requirements.txt\ncp .env.example .env\npython agent.py\n```\n"),
        (".gitignore", "venv/\n__pycache__/\n*.pyc\n.env\n"),
    ]


register("langchain-agent-py", type_="other", name="LangChain agent (Python)",
         description="LangChain agent skeleton driven by an OpenAI key.",
         files=_langchain_agent_py)


# ── playwright-e2e ────────────────────────────────────────────────────────────
def _playwright_e2e(project_id: str) -> list[tuple[str, str]]:
    return [
        ("package.json",
            f'''{{
  "name": "{project_id}",
  "private": true,
  "scripts": {{
    "test": "playwright test",
    "report": "playwright show-report"
  }},
  "devDependencies": {{
    "@playwright/test": "^1.42.0"
  }}
}}
'''),
        ("playwright.config.ts",
            f'''import {{ defineConfig, devices }} from "@playwright/test";

// {project_id} — Playwright E2E config
export default defineConfig({{
  testDir: "./tests",
  retries: 1,
  use: {{ baseURL: "https://example.com" }},
  projects: [
    {{ name: "chromium", use: {{ ...devices["Desktop Chrome"] }} }},
  ],
}});
'''),
        ("tests/example.spec.ts",
            f'''import {{ test, expect }} from "@playwright/test";

test("{project_id} — homepage loads", async ({{ page }}) => {{
  await page.goto("/");
  await expect(page).toHaveTitle(/Example/);
}});
'''),
        ("README.md", f"# {project_id}\n\nPlaywright E2E suite.\n\n```\nnpm install\nnpx playwright install\nnpm test\n```\n"),
        (".gitignore", "node_modules/\ntest-results/\nplaywright-report/\n.env\n"),
    ]


register("playwright-e2e", type_="other", name="Playwright E2E suite",
         description="Playwright E2E test suite targeting Chromium.",
         files=_playwright_e2e)


# ── flutter-app-min ───────────────────────────────────────────────────────────
def _flutter_app_min(project_id: str) -> list[tuple[str, str]]:
    safe_name = project_id.replace("-", "_")
    return [
        ("pubspec.yaml",
            f'''name: {safe_name}
description: {project_id} Flutter starter
publish_to: 'none'
version: 0.1.0

environment:
  sdk: ">=3.0.0 <4.0.0"

dependencies:
  flutter:
    sdk: flutter

flutter:
  uses-material-design: true
'''),
        ("lib/main.dart",
            f'''import 'package:flutter/material.dart';

void main() => runApp(const {safe_name.capitalize()}App());

class {safe_name.capitalize()}App extends StatelessWidget {{
  const {safe_name.capitalize()}App({{super.key}});

  @override
  Widget build(BuildContext context) {{
    return MaterialApp(
      title: '{project_id}',
      home: const Counter(),
    );
  }}
}}

class Counter extends StatefulWidget {{
  const Counter({{super.key}});
  @override
  State<Counter> createState() => _CounterState();
}}

class _CounterState extends State<Counter> {{
  int n = 0;
  @override
  Widget build(BuildContext context) {{
    return Scaffold(
      appBar: AppBar(title: const Text('{project_id}')),
      body: Center(child: Text('clicks: $n', style: const TextStyle(fontSize: 24))),
      floatingActionButton: FloatingActionButton(
        onPressed: () => setState(() => n++),
        child: const Icon(Icons.add),
      ),
    );
  }}
}}
'''),
        ("README.md", f"# {project_id}\n\nFlutter counter starter.\n\n```\nflutter pub get\nflutter run\n```\n"),
        (".gitignore", ".dart_tool/\n.packages\nbuild/\n.flutter-plugins\n.flutter-plugins-dependencies\n"),
    ]


register("flutter-app-min", type_="other", name="Flutter starter",
         description="Flutter counter app with MaterialApp scaffold.",
         files=_flutter_app_min)


# ── godot-4-game ──────────────────────────────────────────────────────────────
def _godot_4_game(project_id: str) -> list[tuple[str, str]]:
    return [
        ("project.godot",
            f'''; Godot 4 project — {project_id}
config_version=5

[application]
config/name="{project_id}"
run/main_scene="res://scenes/main.tscn"
config/features=PackedStringArray("4.2")

[rendering]
renderer/rendering_method="gl_compatibility"
'''),
        ("scenes/main.tscn",
            f'''[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/main.gd" id="1"]

[node name="Main" type="Node2D"]
script = ExtResource("1")
'''),
        ("scripts/main.gd",
            f'''extends Node2D

func _ready() -> void:
\tprint("{project_id} — Godot 4 main scene loaded")
'''),
        ("README.md", f"# {project_id}\n\nGodot 4 starter project. Open `project.godot` in the Godot editor.\n"),
        (".gitignore", ".godot/\n.import/\nexport_presets.cfg\n*.translation\n"),
    ]


register("godot-4-game", type_="other", name="Godot 4 starter",
         description="Godot 4 starter project with a Node2D main scene.",
         files=_godot_4_game)


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
