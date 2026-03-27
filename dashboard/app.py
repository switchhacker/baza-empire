#!/usr/bin/env python3
"""
Baza Empire Agent Dashboard — v4
Full control center: agents, cron jobs, artifacts, settings, logs, infra
"""
import os, json, yaml, subprocess, re, datetime, sqlite3
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory

app = Flask(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
DASHBOARD_DIR  = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK_DIR  = os.path.dirname(DASHBOARD_DIR)
CONFIG_PATH    = os.path.join(FRAMEWORK_DIR, "config", "agents.yaml")
ARTIFACTS_DIR  = os.path.join(DASHBOARD_DIR, "artifacts")
LOGS_DIR       = os.path.join(FRAMEWORK_DIR, "logs")
SECRETS_PATH   = os.path.join(FRAMEWORK_DIR, "configs", "secrets.env")
VENV_PYTHON    = os.path.join(FRAMEWORK_DIR, "venv", "bin", "python")
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = "python3"

os.makedirs(ARTIFACTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# All file types are allowed — no whitelist

# ── Config helpers ────────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f) or {}

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

def load_secrets() -> dict:
    secrets = {}
    if not os.path.exists(SECRETS_PATH):
        return secrets
    with open(SECRETS_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                secrets[k.strip()] = v.strip().strip('"').strip("'")
    return secrets

def save_secrets(secrets: dict):
    lines = []
    for k, v in secrets.items():
        lines.append(f'{k}="{v}"')
    with open(SECRETS_PATH, 'w') as f:
        f.write("\n".join(lines) + "\n")

# ── Agent / service helpers ───────────────────────────────────────────────────

def svc_name(agent_id: str) -> str:
    return f"baza-agent-{agent_id.replace('_', '-')}"

def get_agent_status(agent_id: str) -> str:
    try:
        r = subprocess.run(['systemctl','is-active', svc_name(agent_id)],
                           capture_output=True, text=True, timeout=5)
        return 'online' if r.stdout.strip() == 'active' else 'offline'
    except:
        return 'unknown'

def get_agent_logs(agent_id: str, lines: int = 80) -> str:
    try:
        r = subprocess.run(
            ['journalctl','-u', svc_name(agent_id),'-n',str(lines),'--no-pager','--output=short'],
            capture_output=True, text=True, timeout=10)
        return r.stdout
    except:
        return "Could not fetch logs."

def get_recent_messages(agent_id: str, limit: int = 20) -> list:
    """Read from SQLite context DB."""
    db_path = os.path.join(FRAMEWORK_DIR, "data", "context.db")
    if not os.path.exists(db_path):
        db_path = os.path.join(FRAMEWORK_DIR, "context.db")
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT role, content, timestamp
            FROM messages
            WHERE agent_id = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (agent_id, limit))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return list(reversed(rows))
    except Exception as e:
        return []

# ── Cron helpers ──────────────────────────────────────────────────────────────

CRON_TAG = "# baza-empire-managed"

def list_crons() -> list:
    """Return all baza-managed cron jobs."""
    try:
        r = subprocess.run(['crontab','-l'], capture_output=True, text=True)
        lines = r.stdout.splitlines()
        jobs = []
        for i, line in enumerate(lines):
            if CRON_TAG in line:
                # Extract name from tag: # baza-empire-managed name=<name>
                name_m = re.search(r'name=([^\s]+)', line)
                name = name_m.group(1) if name_m else f"job_{i}"
                jobs.append({"id": name, "raw": line, "line_index": i})
            elif line.strip() and not line.startswith("#"):
                # Check if previous line was a tag
                if i > 0 and CRON_TAG in lines[i-1]:
                    name_m = re.search(r'name=([^\s]+)', lines[i-1])
                    name = name_m.group(1) if name_m else f"job_{i}"
                    # Parse cron fields
                    parts = line.split(None, 5)
                    jobs[-1]["schedule"] = " ".join(parts[:5]) if len(parts) >= 5 else line
                    jobs[-1]["command"]  = parts[5] if len(parts) > 5 else ""
                    jobs[-1]["enabled"]  = not line.startswith("#")
        return jobs
    except:
        return []

def get_raw_crontab() -> str:
    try:
        r = subprocess.run(['crontab','-l'], capture_output=True, text=True)
        return r.stdout
    except:
        return ""

def set_raw_crontab(content: str) -> bool:
    try:
        proc = subprocess.run(['crontab','-'], input=content, capture_output=True, text=True)
        return proc.returncode == 0
    except:
        return False

def add_cron_job(name: str, schedule: str, command: str) -> bool:
    raw = get_raw_crontab()
    # Remove existing job with same name
    lines = []
    skip_next = False
    for line in raw.splitlines():
        if CRON_TAG in line and f"name={name}" in line:
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        lines.append(line)
    # Add new job
    lines.append(f"{CRON_TAG} name={name}")
    lines.append(f"{schedule} {command}")
    return set_raw_crontab("\n".join(lines) + "\n")

def remove_cron_job(name: str) -> bool:
    raw = get_raw_crontab()
    lines = []
    skip_next = False
    for line in raw.splitlines():
        if CRON_TAG in line and f"name={name}" in line:
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        lines.append(line)
    return set_raw_crontab("\n".join(lines) + "\n")

def toggle_cron_job(name: str, enabled: bool) -> bool:
    raw = get_raw_crontab()
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if CRON_TAG in line and f"name={name}" in line:
            if i+1 < len(lines):
                cmd = lines[i+1].lstrip("#").strip()
                lines[i+1] = cmd if enabled else f"#{cmd}"
    return set_raw_crontab("\n".join(lines) + "\n")

# ── Artifact helpers ───────────────────────────────────────────────────────────

def scan_artifacts_dir(base_dir: str, project_id: str = "", agent_id: str = "") -> list:
    """Recursively scan a directory for artifacts, preserving all file types."""
    files = []
    for root, dirs, fnames in os.walk(base_dir):
        # Skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fname in sorted(fnames):
            fpath = os.path.join(root, fname)
            rel   = os.path.relpath(fpath, base_dir)
            # Derive agent_id from subdir if not provided
            parts = rel.split(os.sep)
            agent = agent_id
            proj  = project_id
            if not agent and len(parts) > 1:
                agent = parts[0]
            if not proj and len(parts) > 2:
                proj = parts[1]
            stat = os.stat(fpath)
            ext  = os.path.splitext(fname)[1].lower()
            files.append({
                "name":       fname,
                "rel_path":   rel,
                "abs_path":   fpath,
                "size":       stat.st_size,
                "modified":   datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "project_id": proj or "shared",
                "agent_id":   agent or "unknown",
                "ext":        ext,
                "file_type":  _ext_to_type(ext),
            })
    return files

def _ext_to_type(ext: str) -> str:
    img  = {'.png','.jpg','.jpeg','.gif','.svg','.webp','.ico'}
    code = {'.py','.sh','.bash','.js','.ts','.jsx','.tsx','.json','.yaml','.yml','.toml','.ini','.cfg','.conf','.sql','.html','.css'}
    doc  = {'.md','.txt','.rst','.csv','.log','.pdf','.docx'}
    arc  = {'.zip','.tar','.gz','.tgz','.bz2','.7z'}
    if ext in img:  return 'image'
    if ext in code: return 'code'
    if ext in doc:  return 'document'
    if ext in arc:  return 'archive'
    return 'file'

def artifacts_for_project(project_id: str) -> list:
    proj_dir = os.path.join(ARTIFACTS_DIR, project_id)
    if not os.path.exists(proj_dir):
        return []
    return scan_artifacts_dir(proj_dir, project_id=project_id)

def all_artifacts() -> list:
    if not os.path.exists(ARTIFACTS_DIR):
        return []
    return scan_artifacts_dir(ARTIFACTS_DIR)

# ── Routes — Pages ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    config = load_config()
    agents = config.get('agents', {})
    agent_data = []
    for agent_id, agent_config in agents.items():
        status = get_agent_status(agent_id)
        messages = get_recent_messages(agent_id, 5)
        agent_data.append({
            'id': agent_id,
            'name': agent_config.get('name', agent_id),
            'role': agent_config.get('role', ''),
            'model': agent_config.get('model', ''),
            'status': status,
            'recent_messages': messages,
        })
    crons = list_crons()
    return render_template('index.html', agents=agent_data, crons=crons)

@app.route('/agent/<agent_id>')
def agent_detail(agent_id):
    config = load_config()
    agents = config.get('agents', {})
    if agent_id not in agents:
        return "Agent not found", 404
    agent_config = agents[agent_id]
    status   = get_agent_status(agent_id)
    messages = get_recent_messages(agent_id, 40)
    logs     = get_agent_logs(agent_id, 80)
    crons    = [c for c in list_crons() if agent_id.replace('_','-') in c.get('command','') or agent_id in c.get('id','')]
    available_models = [
        "mistral-small:22b", "qwen2.5:14b", "deepseek-coder-v2:16b",
        "nemotron-3-nano:latest", "llama3.1:8b", "codellama:13b",
    ]
    return render_template('agent.html',
        agent_id=agent_id, agent=agent_config,
        status=status, messages=messages, logs=logs,
        crons=crons, available_models=available_models)

@app.route('/crons')
def crons_page():
    crons = list_crons()
    raw   = get_raw_crontab()
    return render_template('crons.html', crons=crons, raw_crontab=raw)

@app.route('/artifacts')
def artifacts_page():
    project_id = request.args.get('project_id', '')
    if project_id:
        arts = artifacts_for_project(project_id)
    else:
        arts = all_artifacts()
    projects = []
    if os.path.exists(ARTIFACTS_DIR):
        projects = [d for d in os.listdir(ARTIFACTS_DIR)
                    if os.path.isdir(os.path.join(ARTIFACTS_DIR, d))]
    return render_template('artifacts.html', artifacts=arts,
                           projects=sorted(projects), current_project=project_id)

@app.route('/settings')
def settings_page():
    config  = load_config()
    secrets = load_secrets()
    # Mask secret values
    masked = {k: ('●'*8 if v else '') for k, v in secrets.items()}
    return render_template('settings.html', config=config, secrets=masked,
                           secret_keys=list(secrets.keys()))

# ── Routes — Agent API ────────────────────────────────────────────────────────

@app.route('/agent/<agent_id>/restart', methods=['POST'])
def restart_agent_route(agent_id):
    try:
        subprocess.run(['sudo','systemctl','restart', svc_name(agent_id)], timeout=10)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/agent/<agent_id>/stop', methods=['POST'])
def stop_agent_route(agent_id):
    try:
        subprocess.run(['sudo','systemctl','stop', svc_name(agent_id)], timeout=10)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/agent/<agent_id>/start', methods=['POST'])
def start_agent_route(agent_id):
    try:
        subprocess.run(['sudo','systemctl','start', svc_name(agent_id)], timeout=10)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/agent/<agent_id>/edit', methods=['POST'])
def edit_agent(agent_id):
    config = load_config()
    if agent_id not in config.get('agents', {}):
        return jsonify({'error': 'Agent not found'}), 404
    data = request.json or {}
    for field in ['model','system_prompt','role','name']:
        if field in data:
            config['agents'][agent_id][field] = data[field]
    save_config(config)
    return jsonify({'success': True})

@app.route('/agent/<agent_id>/logs')
def agent_logs(agent_id):
    lines = request.args.get('lines', 80, type=int)
    return jsonify({'logs': get_agent_logs(agent_id, lines)})

@app.route('/api/status')
def api_status():
    config = load_config()
    result = {aid: get_agent_status(aid) for aid in config.get('agents', {})}
    return jsonify(result)

@app.route('/api/messages/<agent_id>')
def api_messages(agent_id):
    limit = request.args.get('limit', 20, type=int)
    return jsonify(get_recent_messages(agent_id, limit))

@app.route('/api/live')
def api_live():
    """Single endpoint: agent statuses + last messages + infra metrics."""
    import socket as _socket, shutil as _shutil
    config = load_config()
    agents_cfg = config.get('agents', {})

    def _run(cmd):
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=4)
            return r.stdout.strip()
        except:
            return ""

    def _port(host, port):
        try:
            with _socket.create_connection((host, port), timeout=1):
                return "up"
        except:
            return "down"

    statuses = {aid: get_agent_status(aid) for aid in agents_cfg}
    messages = {aid: get_recent_messages(aid, 3) for aid in agents_cfg}

    cpu_temp = _run("sensors 2>/dev/null | grep -i 'Package id 0' | head -1 | awk '{print $4}'")
    if not cpu_temp:
        cpu_temp = _run("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf \"%.0f°C\", $1/1000}'")

    online = sum(1 for s in statuses.values() if s == 'online')
    return jsonify({
        "statuses": statuses,
        "messages": messages,
        "online": online,
        "total": len(statuses),
        "metrics": {
            "cpu_temp":  cpu_temp or "N/A",
            "mem":       _run("free -h | awk '/^Mem:/{print $3\"/\"$2}'"),
            "disk":      _run("df -h / | tail -1 | awk '{print $5}'"),
            "ollama_amd": _port("localhost", 11434),
            "ollama_gpu": _port("localhost", 11435),
        }
    })

@app.route('/api/models')
def api_models():
    """All available models: local Ollama (both GPUs) + cloud via LiteLLM."""
    import socket as _socket
    def _port(host, port):
        try:
            with _socket.create_connection((host, port), timeout=2): return True
        except: return False

    def _ollama_models(base):
        try:
            r = subprocess.run(['curl','-s',f'{base}/api/tags'], capture_output=True, text=True, timeout=5)
            import json as _json
            data = _json.loads(r.stdout)
            return [{"name": m["name"], "size": m.get("size",0),
                     "params": m.get("details",{}).get("parameter_size",""),
                     "quant":  m.get("details",{}).get("quantization_level","")}
                    for m in data.get("models",[])]
        except: return []

    def _litellm_models():
        try:
            r = subprocess.run([
                'curl','-s','-H','Authorization: Bearer baza-litellm-internal',
                'http://localhost:4000/v1/models'
            ], capture_output=True, text=True, timeout=5)
            import json as _json
            return [m["id"] for m in _json.loads(r.stdout).get("data",[])]
        except: return []

    return jsonify({
        "amd":   {"url": "localhost:11434", "up": _port("localhost",11434), "models": _ollama_models("http://localhost:11434")},
        "cuda":  {"url": "localhost:11435", "up": _port("localhost",11435), "models": _ollama_models("http://localhost:11435")},
        "cloud": {"url": "localhost:4000",  "up": _port("localhost",4000),  "models": _litellm_models()},
    })

@app.route('/api/artifacts/project-list')
def api_artifact_project_list():
    """Return all existing project folders + known agent IDs for upload dropdowns."""
    config = load_config()
    agent_ids = list(config.get('agents', {}).keys())
    projects = []
    if os.path.exists(ARTIFACTS_DIR):
        projects = sorted([d for d in os.listdir(ARTIFACTS_DIR)
                           if os.path.isdir(os.path.join(ARTIFACTS_DIR, d))])
    return jsonify({"projects": projects, "agents": agent_ids})

# ── Routes — Cron API ─────────────────────────────────────────────────────────

@app.route('/api/crons', methods=['GET'])
def api_crons_list():
    return jsonify(list_crons())

@app.route('/api/crons/raw', methods=['GET'])
def api_crons_raw():
    return jsonify({'crontab': get_raw_crontab()})

@app.route('/api/crons/raw', methods=['POST'])
def api_crons_raw_save():
    data = request.json or {}
    content = data.get('content', '')
    ok = set_raw_crontab(content)
    return jsonify({'success': ok})

@app.route('/api/crons/add', methods=['POST'])
def api_crons_add():
    data     = request.json or {}
    name     = data.get('name', '').strip()
    schedule = data.get('schedule', '').strip()
    command  = data.get('command', '').strip()
    if not name or not schedule or not command:
        return jsonify({'success': False, 'error': 'name, schedule, command required'}), 400
    ok = add_cron_job(name, schedule, command)
    return jsonify({'success': ok})

@app.route('/api/crons/remove', methods=['POST'])
def api_crons_remove():
    data = request.json or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'name required'}), 400
    ok = remove_cron_job(name)
    return jsonify({'success': ok})

@app.route('/api/crons/toggle', methods=['POST'])
def api_crons_toggle():
    data    = request.json or {}
    name    = data.get('name', '').strip()
    enabled = data.get('enabled', True)
    ok = toggle_cron_job(name, enabled)
    return jsonify({'success': ok})

@app.route('/api/crons/run-now', methods=['POST'])
def api_crons_run_now():
    """Immediately run a cron job's command in background."""
    data    = request.json or {}
    name    = data.get('name', '').strip()
    crons   = list_crons()
    job     = next((c for c in crons if c.get('id') == name), None)
    if not job or not job.get('command'):
        return jsonify({'success': False, 'error': 'Job not found or no command'}), 404
    try:
        subprocess.Popen(job['command'], shell=True,
                         stdout=open(os.path.join(LOGS_DIR,'cron_manual.log'),'a'),
                         stderr=subprocess.STDOUT)
        return jsonify({'success': True, 'message': f'Running: {job["command"][:80]}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ── Routes — Artifacts API ────────────────────────────────────────────────────

@app.route('/api/artifacts')
def api_artifacts():
    project_id = request.args.get('project_id')
    if project_id:
        return jsonify(artifacts_for_project(project_id))
    return jsonify(all_artifacts())

@app.route('/api/artifacts/save-text', methods=['POST'])
def api_artifact_save_text():
    import re as _re
    data       = request.json or {}
    project_id = data.get('project_id', 'shared')
    raw_name   = data.get('filename', f"artifact_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    content    = data.get('content', '')
    safe_name  = _re.sub(r'[^\w.\-]', '_', raw_name).strip('_') or 'artifact.txt'
    # All extensions accepted
    proj_dir   = os.path.join(ARTIFACTS_DIR, project_id)
    os.makedirs(proj_dir, exist_ok=True)
    fpath      = os.path.join(proj_dir, safe_name)
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(content)
    size = os.path.getsize(fpath)
    return jsonify({'success': True, 'name': safe_name, 'project_id': project_id,
                    'size': size, 'download_url': f'/api/artifacts/download/{project_id}/{safe_name}'})

@app.route('/api/artifacts/upload', methods=['POST'])
def api_artifact_upload():
    import re as _re
    project_id = request.form.get('project_id', 'shared')
    subfolder  = request.form.get('subfolder', '').strip('/')
    files      = request.files.getlist('file')
    if not files:
        return jsonify({'success': False, 'error': 'No files provided'}), 400
    proj_dir = os.path.join(ARTIFACTS_DIR, project_id, subfolder) if subfolder else os.path.join(ARTIFACTS_DIR, project_id)
    os.makedirs(proj_dir, exist_ok=True)
    saved = []
    errors = []
    for f in files:
        if not f or not f.filename:
            continue
        # Preserve original filename, sanitise only dangerous chars
        safe_name = _re.sub(r'[^\w.\-_ ()]', '_', f.filename).strip() or f'upload_{len(saved)}'
        fpath = os.path.join(proj_dir, safe_name)
        try:
            f.save(fpath)
            saved.append({'name': safe_name, 'size': os.path.getsize(fpath),
                          'project_id': project_id,
                          'download_url': f'/api/artifacts/download/{project_id}/{safe_name}'})
        except Exception as e:
            errors.append({'name': f.filename, 'error': str(e)})
    return jsonify({'success': len(saved) > 0, 'files': saved, 'errors': errors,
                    'count': len(saved)})

@app.route('/api/artifacts/download/<project_id>/<path:filename>')
def api_artifact_download(project_id, filename):
    proj_dir = os.path.join(ARTIFACTS_DIR, project_id)
    return send_from_directory(proj_dir, filename, as_attachment=True)

@app.route('/api/artifacts/view/<project_id>/<filename>')
def api_artifact_view(project_id, filename):
    proj_dir = os.path.join(ARTIFACTS_DIR, project_id)
    fpath    = os.path.join(proj_dir, filename)
    if not os.path.exists(fpath):
        return jsonify({'error': 'File not found'}), 404
    ext = os.path.splitext(filename)[1].lower()
    # Serve images and PDFs directly
    binary_serve = {'.png','image/png','.jpg','.jpeg','.gif','.webp','.svg',
                    '.pdf','.mp4','.mp3','.wav','.ogg','.zip','.gz','.tar'}
    text_exts = {'.txt','.md','.py','.sh','.js','.ts','.jsx','.tsx','.html','.htm',
                 '.css','.json','.yaml','.yml','.toml','.ini','.cfg','.conf','.sql',
                 '.log','.env','.csv','.rst','.xml','.bash','.zsh'}
    if ext in text_exts or os.path.getsize(fpath) < 2_000_000:
        try:
            content = open(fpath, 'r', errors='replace').read(500_000)
            return jsonify({'content': content, 'name': filename, 'type': 'text'})
        except Exception as e:
            pass
    return send_from_directory(proj_dir, filename)

@app.route('/api/artifacts/serve/<project_id>/<filename>')
def api_artifact_serve(project_id, filename):
    """Serve file inline for browser preview (images, PDFs, etc)."""
    proj_dir = os.path.join(ARTIFACTS_DIR, project_id)
    return send_from_directory(proj_dir, filename)

@app.route('/api/artifacts/delete', methods=['POST'])
def api_artifact_delete():
    data       = request.json or {}
    project_id = data.get('project_id', '')
    filename   = data.get('name', '')
    if not project_id or not filename:
        return jsonify({'success': False}), 400
    fpath = os.path.join(ARTIFACTS_DIR, project_id, filename)
    if os.path.exists(fpath):
        os.remove(fpath)
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Not found'}), 404


@app.route('/api/artifacts/delete-bulk', methods=['POST'])
def api_artifact_delete_bulk():
    data  = request.json or {}
    files = data.get('files', [])  # [{project_id, name}]
    deleted = 0
    for f in files:
        fpath = os.path.join(ARTIFACTS_DIR, f.get('project_id',''), f.get('name',''))
        if os.path.exists(fpath):
            os.remove(fpath)
            deleted += 1
    return jsonify({'success': True, 'deleted': deleted})

@app.route('/api/artifacts/rename', methods=['POST'])
def api_artifact_rename():
    import re as _re
    data       = request.json or {}
    project_id = data.get('project_id','')
    old_name   = data.get('old_name','')
    new_name   = _re.sub(r'[^\w.\-_ ()]','_', data.get('new_name','')).strip()
    if not all([project_id, old_name, new_name]):
        return jsonify({'success': False, 'error': 'Missing fields'})
    old_path = os.path.join(ARTIFACTS_DIR, project_id, old_name)
    new_path = os.path.join(ARTIFACTS_DIR, project_id, new_name)
    if not os.path.exists(old_path):
        return jsonify({'success': False, 'error': 'File not found'})
    os.rename(old_path, new_path)
    return jsonify({'success': True, 'new_name': new_name})

@app.route('/api/artifacts/move', methods=['POST'])
def api_artifact_move():
    data       = request.json or {}
    from_proj  = data.get('from_project','')
    to_proj    = data.get('to_project','')
    filename   = data.get('name','')
    if not all([from_proj, to_proj, filename]):
        return jsonify({'success': False, 'error': 'Missing fields'})
    src  = os.path.join(ARTIFACTS_DIR, from_proj, filename)
    dest_dir = os.path.join(ARTIFACTS_DIR, to_proj)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    if not os.path.exists(src):
        return jsonify({'success': False, 'error': 'File not found'})
    import shutil
    shutil.move(src, dest)
    return jsonify({'success': True})

# ── Routes — Settings API ─────────────────────────────────────────────────────

@app.route('/api/settings/secret', methods=['POST'])
def api_set_secret():
    data    = request.json or {}
    key     = data.get('key', '').strip()
    value   = data.get('value', '').strip()
    if not key:
        return jsonify({'success': False, 'error': 'key required'}), 400
    secrets      = load_secrets()
    secrets[key] = value
    save_secrets(secrets)
    return jsonify({'success': True})

@app.route('/api/settings/secret/delete', methods=['POST'])
def api_delete_secret():
    data    = request.json or {}
    key     = data.get('key', '').strip()
    secrets = load_secrets()
    if key in secrets:
        del secrets[key]
        save_secrets(secrets)
    return jsonify({'success': True})

# ── Routes — Infra Map ────────────────────────────────────────────────────────

@app.route('/infra')
def infra_page():
    return render_template('infra.html')

@app.route('/api/infra/metrics')
def api_infra_metrics():
    import socket as _socket, shutil as _shutil

    def _run(cmd):
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            return r.stdout.strip()
        except:
            return ""

    def _svc(name):
        out = _run("systemctl is-active " + name)
        return "active" if out == "active" else (out or "inactive")

    def _port(host, port):
        try:
            with _socket.create_connection((host, port), timeout=2):
                return "up"
        except:
            return "down"

    cpu_raw = _run("sensors 2>/dev/null | grep -i 'Package id 0' | head -1 | awk '{print $4}'")
    if not cpu_raw:
        cpu_raw = _run("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf \"%.1fC\", $1/1000}'")

    return jsonify({
        "cpu_temp":   cpu_raw or "N/A",
        "mem_usage":  _run("free -h | awk '/^Mem:/{print $3\"/\"$2}'"),
        "disk_usage": _run("df -h / | tail -1 | awk '{print $3\"/\"$2\" (\"$5\")'\"'\"'}'"),
        "uptime":     _run("uptime -p"),
        "nuc_mining": _svc("baza-nuc-mining"),
        "services": {
            "ollama":     _port("localhost", 11434),
            "dashboard":  "up",
            "sdwebui":    _port("localhost", 7860),
            "mosquitto":  _svc("mosquitto"),
            "postgresql": _svc("postgresql"),
            "nextcloud":  _svc("nextcloud"),
            "docker":     _svc("docker"),
            "mining":     _svc("baza-mining"),
        }
    })


# ── Email — local SQLite (context.db on baza) ─────────────────────────────────

EMAIL_DB_PATH = os.path.join(FRAMEWORK_DIR, "context.db")

def get_email_db():
    conn = sqlite3.connect(EMAIL_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def email_db_exists():
    return os.path.exists(EMAIL_DB_PATH)

def rows_to_list(rows):
    return [dict(r) for r in rows]

@app.route('/email')
def email_page():
    return render_template('email.html')

@app.route('/api/email/queue')
def api_email_queue():
    if not email_db_exists():
        return jsonify({"records": [], "error": "context.db not found — email pipeline not initialised yet"})
    status = request.args.get("status", "")
    limit  = int(request.args.get("limit", 50))
    try:
        conn = get_email_db()
        if status:
            rows = conn.execute(
                "SELECT * FROM email_queue WHERE status=? ORDER BY received_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM email_queue ORDER BY received_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        conn.close()
        # Normalise field names for the frontend (from -> sender, body_snippet -> snippet)
        records = []
        for r in rows:
            d = dict(r)
            d.setdefault("from",         d.get("sender", ""))
            d.setdefault("body_snippet", d.get("snippet", ""))
            records.append(d)
        return jsonify({"records": records})
    except Exception as e:
        return jsonify({"records": [], "error": str(e)})

@app.route('/api/email/stats')
def api_email_stats():
    if not email_db_exists():
        return jsonify({"total":0,"pending":0,"approved":0,"ignored":0,"sent":0,"high_priority":0})
    try:
        conn = get_email_db()
        rows = conn.execute("SELECT status, priority FROM email_queue").fetchall()
        conn.close()
        stats = {"total": len(rows), "pending": 0, "approved": 0,
                 "ignored": 0, "sent": 0, "high_priority": 0}
        for r in rows:
            s = r["status"] or ""
            if s == "awaiting_confirmation": stats["pending"] += 1
            elif s in stats: stats[s] += 1
            if (r["priority"] or "").lower() in ("high","urgent"): stats["high_priority"] += 1
        return jsonify(stats)
    except Exception as e:
        return jsonify({"total":0,"error":str(e)})

@app.route('/api/email/action', methods=['POST'])
def api_email_action():
    """approve / ignore / restore an email by its local DB id or gmail_id."""
    if not email_db_exists():
        return jsonify({"success": False, "error": "context.db not found"})
    body     = request.get_json() or {}
    gmail_id = body.get("gmail_id")
    action   = body.get("action", "")
    reply    = body.get("reply_text", "")

    status_map = {
        "approve":  "approved",
        "ignore":   "ignored",
        "restore":  "awaiting_confirmation",
    }
    new_status = status_map.get(action, action)

    try:
        conn = get_email_db()
        if reply:
            conn.execute(
                "UPDATE email_queue SET status=?, suggested_reply=? WHERE gmail_id=?",
                (new_status, reply, gmail_id)
            )
        else:
            conn.execute(
                "UPDATE email_queue SET status=? WHERE gmail_id=?",
                (new_status, gmail_id)
            )
        conn.commit()

        # If approving — run email_send.py via subprocess
        if action == "approve":
            row = conn.execute(
                "SELECT id FROM email_queue WHERE gmail_id=?", (gmail_id,)
            ).fetchone()
            conn.close()
            if row:
                local_id = row["id"]
                send_cmd = [
                    VENV_PYTHON,
                    os.path.join(FRAMEWORK_DIR, "skills", "shared", "email_send.py"),
                    "approve", str(local_id)
                ]
                if reply:
                    send_cmd += ["send", str(local_id), reply]
                subprocess.Popen(send_cmd, cwd=FRAMEWORK_DIR,
                                 stdout=open(os.path.join(LOGS_DIR, "email_send.log"), "a"),
                                 stderr=subprocess.STDOUT)
        else:
            conn.close()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/email/fetch', methods=['POST'])
def api_email_fetch():
    """Manually trigger email_fetch.py to pull new emails from Gmail."""
    try:
        proc = subprocess.Popen(
            [VENV_PYTHON, os.path.join(FRAMEWORK_DIR, "skills", "shared", "email_fetch.py")],
            cwd=FRAMEWORK_DIR,
            stdout=open(os.path.join(LOGS_DIR, "email_fetch.log"), "a"),
            stderr=subprocess.STDOUT
        )
        return jsonify({"success": True, "pid": proc.pid})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ── Routes — Tasks (SQLite baza_projects.db) ─────────────────────────────────

DB_PATH = os.path.join(DASHBOARD_DIR, "baza_projects.db")

def get_tasks_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/tasks')
def tasks_page():
    return render_template('tasks.html')

@app.route('/api/tasks')
def api_tasks_list():
    if not os.path.exists(DB_PATH):
        return jsonify([])
    status   = request.args.get('status', '')
    project  = request.args.get('project_id', '')
    agent    = request.args.get('assigned_to', '')
    limit    = int(request.args.get('limit', 100))
    try:
        conn = get_tasks_db()
        sql = "SELECT * FROM tasks WHERE 1=1"
        params = []
        if status:   sql += " AND status=?";      params.append(status)
        if project:  sql += " AND project_id=?";  params.append(project)
        if agent:    sql += " AND assigned_to=?"; params.append(agent)
        sql += " ORDER BY CASE status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 WHEN 'blocked' THEN 3 ELSE 4 END, priority DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/tasks/<task_id>', methods=['GET'])
def api_task_get(task_id):
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'DB not found'}), 404
    conn = get_tasks_db()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify(dict(row))

@app.route('/api/tasks/<task_id>', methods=['PATCH'])
def api_task_update(task_id):
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'DB not found'}), 404
    data = request.json or {}
    allowed = {'title','description','status','priority','assigned_to','project_id','notes','due_date'}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({'error': 'no valid fields'}), 400
    fields['updated_at'] = datetime.datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [task_id]
    conn = get_tasks_db()
    conn.execute(f"UPDATE tasks SET {set_clause} WHERE id=?", values)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/tasks', methods=['POST'])
def api_task_create():
    import uuid as _uuid
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'DB not found'}), 404
    data = request.json or {}
    task_id = data.get('id') or str(_uuid.uuid4())[:8]
    now = datetime.datetime.utcnow().isoformat()
    conn = get_tasks_db()
    try:
        conn.execute("""
            INSERT INTO tasks (id, project_id, title, description, assigned_to, status, priority, notes, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            task_id,
            data.get('project_id', 'proj-baza-empire'),
            data.get('title', 'Untitled Task'),
            data.get('description', ''),
            data.get('assigned_to', ''),
            data.get('status', 'pending'),
            data.get('priority', 'medium'),
            data.get('notes', ''),
            now, now
        ))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': task_id})
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500

@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def api_task_delete(task_id):
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'DB not found'}), 404
    conn = get_tasks_db()
    conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/tasks/stats', methods=['GET'])
def api_task_stats():
    if not os.path.exists(DB_PATH):
        return jsonify({})
    conn = get_tasks_db()
    rows = conn.execute("SELECT status, project_id, assigned_to FROM tasks").fetchall()
    conn.close()
    stats = {'total': len(rows), 'by_status': {}, 'by_project': {}, 'by_agent': {}}
    for r in rows:
        s = r['status'] or 'unknown'
        stats['by_status'][s] = stats['by_status'].get(s, 0) + 1
        p = r['project_id'] or 'unknown'
        stats['by_project'][p] = stats['by_project'].get(p, 0) + 1
        a = r['assigned_to'] or 'unassigned'
        stats['by_agent'][a] = stats['by_agent'].get(a, 0) + 1
    return jsonify(stats)

@app.route('/api/projects')
def api_projects_list():
    if not os.path.exists(DB_PATH):
        return jsonify([])
    conn = get_tasks_db()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()]
    except Exception:
        rows = []
    conn.close()
    return jsonify(rows)


# ── Routes — Skills Lab ────────────────────────────────────────────────────────

@app.route('/skills')
def skills_page():
    return render_template('skills.html')

@app.route('/api/skills/list')
def api_skills_list():
    skills = []
    shared_dir = os.path.join(FRAMEWORK_DIR, "skills", "shared")
    if os.path.isdir(shared_dir):
        for f in sorted(Path(shared_dir).glob("*.py")):
            stat = os.stat(f)
            # Read first docstring line
            desc = ""
            try:
                for line in open(f).readlines()[:12]:
                    line = line.strip()
                    if line.startswith('"""') or line.startswith("'''"):
                        desc = line.strip('"\' ')
                        if len(desc) < 5:
                            continue
                        break
                    if line and not line.startswith('#') and not line.startswith('import') and not line.startswith('def'):
                        continue
            except Exception:
                pass
            skills.append({'name': f.stem, 'path': str(f), 'scope': 'shared',
                           'size': stat.st_size, 'modified': datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                           'description': desc})
    # Per-agent skills
    agents_dir = os.path.join(FRAMEWORK_DIR, "agents")
    for agent_dir in sorted(Path(agents_dir).iterdir()):
        skill_dir = agent_dir / "skills"
        if skill_dir.is_dir():
            for f in sorted(skill_dir.glob("*.py")):
                stat = os.stat(f)
                skills.append({'name': f.stem, 'path': str(f), 'scope': agent_dir.name,
                               'size': stat.st_size, 'modified': datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                               'description': ''})
    return jsonify(skills)

@app.route('/api/skills/read/<skill_name>')
def api_skill_read(skill_name):
    shared_dir = os.path.join(FRAMEWORK_DIR, "skills", "shared")
    path = os.path.join(shared_dir, f"{skill_name}.py")
    if not os.path.exists(path):
        return jsonify({'error': 'not found'}), 404
    return jsonify({'name': skill_name, 'code': open(path).read(), 'path': path})

@app.route('/api/skills/save', methods=['POST'])
def api_skill_save():
    import re as _re
    data = request.json or {}
    name = data.get('name', '').strip()
    code = data.get('code', '').strip()
    if not name or not code:
        return jsonify({'error': 'name and code required'}), 400
    if not _re.match(r'^[a-z][a-z0-9_]{1,49}$', name):
        return jsonify({'error': 'invalid name'}), 400
    path = os.path.join(FRAMEWORK_DIR, "skills", "shared", f"{name}.py")
    import stat as _stat
    with open(path, 'w') as f:
        f.write(code)
    os.chmod(path, os.stat(path).st_mode | _stat.S_IXUSR)
    return jsonify({'success': True, 'path': path})

@app.route('/api/skills/run', methods=['POST'])
def api_skill_run():
    data = request.json or {}
    name = data.get('name', '').strip()
    args = data.get('args', {})
    if not name:
        return jsonify({'error': 'name required'}), 400
    shared_dir = os.path.join(FRAMEWORK_DIR, "skills", "shared")
    path = os.path.join(shared_dir, f"{name}.py")
    if not os.path.exists(path):
        return jsonify({'error': f'skill not found: {name}'}), 404
    import time as _time
    env = os.environ.copy()
    env['SKILL_ARGS'] = json.dumps(args)
    env['AGENT_ID'] = 'dashboard'
    t0 = _time.time()
    try:
        result = subprocess.run([VENV_PYTHON, path], capture_output=True, text=True, timeout=30, env=env)
        elapsed = int((_time.time() - t0) * 1000)
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout[:8000],
            'error': result.stderr[:2000] if result.returncode != 0 else '',
            'duration_ms': elapsed,
            'exit_code': result.returncode
        })
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'timeout (30s)', 'output': ''})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'output': ''})

@app.route('/api/skills/delete', methods=['POST'])
def api_skill_delete():
    import re as _re
    data = request.json or {}
    name = data.get('name', '').strip()
    protected = {'create_skill', 'save_artifact', 'artifact_save', 'update_task'}
    if not name or name in protected:
        return jsonify({'error': 'cannot delete protected skill'}), 400
    path = os.path.join(FRAMEWORK_DIR, "skills", "shared", f"{name}.py")
    if not os.path.exists(path):
        return jsonify({'error': 'not found'}), 404
    os.remove(path)
    return jsonify({'success': True})


# ── Routes — Journal (PostgreSQL task_journal) ────────────────────────────────

@app.route('/journal')
def journal_page():
    return render_template('journal.html')

@app.route('/api/journal')
def api_journal():
    agent_id = request.args.get('agent_id', '')
    task_type = request.args.get('task_type', '')
    limit = int(request.args.get('limit', 100))
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="baza_agents",
            user="switchhacker", password=os.environ.get("DB_PASSWORD","baza2026")
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        sql = "SELECT * FROM task_journal WHERE 1=1"
        params = []
        if agent_id:   sql += " AND agent_id=%s";   params.append(agent_id)
        if task_type:  sql += " AND task_type=%s";  params.append(task_type)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        # Serialise datetime objects
        for r in rows:
            for k, v in r.items():
                if hasattr(v, 'isoformat'):
                    r[k] = v.isoformat()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e), 'rows': []})


# ── Routes — Agent Memory (PostgreSQL agent_memory) ───────────────────────────

@app.route('/memory')
def memory_page():
    return render_template('memory.html')

@app.route('/api/memory')
def api_memory_list():
    agent_id = request.args.get('agent_id', '')
    category = request.args.get('category', '')
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="baza_agents",
            user="switchhacker", password=os.environ.get("DB_PASSWORD","baza2026")
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        sql = "SELECT * FROM agent_memory WHERE 1=1"
        params = []
        if agent_id: sql += " AND agent_id=%s"; params.append(agent_id)
        if category: sql += " AND category=%s"; params.append(category)
        sql += " ORDER BY agent_id, category, key"
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            for k, v in r.items():
                if hasattr(v, 'isoformat'): r[k] = v.isoformat()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e), 'rows': []})

@app.route('/api/memory', methods=['POST'])
def api_memory_set():
    data = request.json or {}
    agent_id = data.get('agent_id','').strip()
    key      = data.get('key','').strip()
    value    = data.get('value','').strip()
    category = data.get('category','general').strip()
    if not agent_id or not key:
        return jsonify({'error': 'agent_id and key required'}), 400
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="baza_agents",
            user="switchhacker", password=os.environ.get("DB_PASSWORD","baza2026")
        )
        conn.cursor().execute("""
            INSERT INTO agent_memory (agent_id, key, value, category, updated_at)
            VALUES (%s,%s,%s,%s,NOW())
            ON CONFLICT (agent_id, key) DO UPDATE
            SET value=EXCLUDED.value, category=EXCLUDED.category, updated_at=NOW()
        """, (agent_id, key, value, category))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/memory/delete', methods=['POST'])
def api_memory_delete():
    data = request.json or {}
    agent_id = data.get('agent_id','').strip()
    key      = data.get('key','').strip()
    if not agent_id or not key:
        return jsonify({'error': 'agent_id and key required'}), 400
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="baza_agents",
            user="switchhacker", password=os.environ.get("DB_PASSWORD","baza2026")
        )
        conn.cursor().execute("DELETE FROM agent_memory WHERE agent_id=%s AND key=%s", (agent_id, key))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/empire')
def api_empire_list():
    category = request.args.get('category','')
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="baza_agents",
            user="switchhacker", password=os.environ.get("DB_PASSWORD","baza2026")
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if category:
            cur.execute("SELECT * FROM empire_knowledge WHERE category=%s ORDER BY key", (category,))
        else:
            cur.execute("SELECT * FROM empire_knowledge ORDER BY category, key")
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            for k, v in r.items():
                if hasattr(v, 'isoformat'): r[k] = v.isoformat()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e), 'rows': []})

@app.route('/api/empire', methods=['POST'])
def api_empire_set():
    data = request.json or {}
    key      = data.get('key','').strip()
    value    = data.get('value','').strip()
    category = data.get('category','general').strip()
    if not key:
        return jsonify({'error': 'key required'}), 400
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="baza_agents",
            user="switchhacker", password=os.environ.get("DB_PASSWORD","baza2026")
        )
        conn.cursor().execute("""
            INSERT INTO empire_knowledge (key, value, category, updated_at, updated_by)
            VALUES (%s,%s,%s,NOW(),'dashboard')
            ON CONFLICT (key) DO UPDATE
            SET value=EXCLUDED.value, category=EXCLUDED.category, updated_at=NOW(), updated_by='dashboard'
        """, (key, value, category))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Routes — Ollama model management ─────────────────────────────────────────

@app.route('/api/ollama/models')
def api_ollama_models():
    import urllib.request as _ur
    results = {}
    for label, port in [('amd', 11434), ('nvidia', 11435)]:
        try:
            with _ur.urlopen(f"http://localhost:{port}/api/tags", timeout=3) as r:
                data = json.loads(r.read())
                results[label] = [m['name'] for m in data.get('models', [])]
        except Exception:
            results[label] = []
    return jsonify(results)

@app.route('/api/ollama/running')
def api_ollama_running():
    import urllib.request as _ur
    results = {}
    for label, port in [('amd', 11434), ('nvidia', 11435)]:
        try:
            with _ur.urlopen(f"http://localhost:{port}/api/ps", timeout=3) as r:
                data = json.loads(r.read())
                results[label] = data.get('models', [])
        except Exception:
            results[label] = None
    return jsonify(results)


# ── Routes — System health (live) ─────────────────────────────────────────────

@app.route('/api/syshealth')
def api_syshealth():
    def _run(cmd):
        try:
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            return ""

    # CPU load
    load = _run("cat /proc/loadavg").split()
    cpu_load = f"{load[0]}/{load[1]}/{load[2]}" if len(load) >= 3 else "?"

    # Memory
    mem_out = _run("free -h | awk '/^Mem:/{print $3\"/\"$2}'")

    # Disk
    disk_out = _run("df -h / | tail -1 | awk '{print $3\"/\"$2\" (\"$5\")\"}'")

    # GPU Nvidia
    nv = _run("nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null | head -1")
    nv_data = {}
    if nv and "," in nv:
        parts = [x.strip() for x in nv.split(",")]
        if len(parts) >= 4:
            nv_data = {"temp": parts[0], "util": parts[1], "mem_used": parts[2], "mem_total": parts[3]}

    # GPU AMD via sysfs
    amd_data = {}
    try:
        temp = _run("cat /sys/class/hwmon/hwmon*/temp1_input 2>/dev/null | head -1")
        if temp.strip().isdigit():
            amd_data["temp"] = str(int(temp.strip())//1000)
    except Exception:
        pass

    # Mining
    mining_data = {}
    try:
        import urllib.request as _ur
        with _ur.urlopen("http://localhost:4067/2/summary", timeout=3) as r:
            xmr = json.loads(r.read())
            hr = xmr.get("hashrate", {}).get("total", [0, 0, 0])
            hr_val = hr[2] or hr[1] or hr[0]
            if hr_val >= 1000:
                mining_data["hashrate"] = f"{hr_val/1000:.2f} kH/s"
            else:
                mining_data["hashrate"] = f"{hr_val:.0f} H/s"
            mining_data["pool"] = xmr.get("connection", {}).get("pool", "?")
            mining_data["shares"] = xmr.get("results", {}).get("shares_good", 0)
    except Exception:
        mining_data = {"hashrate": "offline", "pool": "?", "shares": 0}

    return jsonify({
        "cpu_load": cpu_load,
        "memory": mem_out,
        "disk": disk_out,
        "nvidia": nv_data,
        "amd": amd_data,
        "mining": mining_data,
        "timestamp": datetime.datetime.utcnow().isoformat()
    })


# ── Routes — Task Runner control ──────────────────────────────────────────────

@app.route('/api/taskrunner/run', methods=['POST'])
def api_taskrunner_run():
    """Manually trigger the task runner."""
    data = request.json or {}
    agent = data.get('agent', '')
    cmd = [VENV_PYTHON, os.path.join(FRAMEWORK_DIR, "core", "task_runner.py")]
    if agent:
        cmd += ["--agent", agent]
    log_path = os.path.join(LOGS_DIR, "task_runner_manual.log")
    try:
        proc = subprocess.Popen(cmd, cwd=FRAMEWORK_DIR,
                                stdout=open(log_path, 'a'), stderr=subprocess.STDOUT)
        return jsonify({'success': True, 'pid': proc.pid, 'log': log_path})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/taskrunner/logs')
def api_taskrunner_logs():
    log_path = os.path.join(LOGS_DIR, "task_runner_manual.log")
    if not os.path.exists(log_path):
        return jsonify({'logs': '(no logs yet)'})
    try:
        lines = open(log_path).readlines()
        return jsonify({'logs': ''.join(lines[-100:])})
    except Exception as e:
        return jsonify({'logs': str(e)})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888, debug=False)
