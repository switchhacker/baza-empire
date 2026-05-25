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
