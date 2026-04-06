#!/usr/bin/env python3
"""
Baza Empire Agent Dashboard — v4
Full control center: agents, cron jobs, artifacts, settings, logs, infra
"""
import os, json, yaml, subprocess, re, datetime, sqlite3, uuid, secrets, functools
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory, make_response, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:
    from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
    import bcrypt
    CLOUD_ENABLED = True
except ImportError:
    CLOUD_ENABLED = False

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

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

CLOUD_STORAGE  = '/mnt/empirepool/cloud'
CLOUD_QUOTA_MB = 204800  # 200GB per user

os.makedirs(ARTIFACTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# ── AHB123 Business Hub — Schema Init ─────────────────────────────────────────

def init_ahb_tables():
    """Create AHB123 tables in baza_projects.db if they don't exist."""
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS ahb_clients (
            id TEXT PRIMARY KEY,
            name TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            city TEXT DEFAULT 'Philadelphia',
            source TEXT,
            status TEXT DEFAULT 'lead',
            notes TEXT,
            assigned_agent TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_projects (
            id TEXT PRIMARY KEY,
            client_id TEXT,
            title TEXT,
            address TEXT,
            scope TEXT,
            description TEXT,
            budget_low REAL,
            budget_high REAL,
            status TEXT DEFAULT 'estimate',
            start_date TEXT,
            end_date TEXT,
            assigned_agents TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_invoices (
            id TEXT PRIMARY KEY,
            client_id TEXT,
            project_id TEXT,
            invoice_number TEXT,
            line_items TEXT,
            subtotal REAL,
            tax REAL,
            total REAL,
            status TEXT DEFAULT 'draft',
            due_date TEXT,
            paid_date TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_receipts (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            vendor TEXT,
            amount REAL,
            category TEXT,
            description TEXT,
            receipt_date TEXT,
            file_path TEXT,
            ocr_text TEXT,
            created_by TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_payroll (
            id TEXT PRIMARY KEY,
            worker_name TEXT,
            role TEXT,
            hours REAL,
            rate REAL,
            total REAL,
            period_start TEXT,
            period_end TEXT,
            status TEXT DEFAULT 'pending',
            project_id TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_estimates (
            id TEXT PRIMARY KEY,
            client_id TEXT,
            project_id TEXT,
            title TEXT,
            description TEXT,
            scope TEXT,
            line_items TEXT,
            subtotal REAL,
            markup_pct REAL DEFAULT 15,
            total REAL,
            status TEXT DEFAULT 'draft',
            generated_by TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_chats (
            id TEXT PRIMARY KEY,
            visitor_name TEXT,
            visitor_email TEXT,
            visitor_phone TEXT,
            channel TEXT DEFAULT 'website',
            status TEXT DEFAULT 'active',
            lead_score TEXT,
            assigned_agent TEXT DEFAULT 'nova_sterling',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            role TEXT,
            content TEXT,
            agent_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_voice_configs (
            id TEXT PRIMARY KEY,
            name TEXT,
            voice TEXT DEFAULT 'en-US-GuyNeural',
            rate TEXT DEFAULT '+0%',
            pitch TEXT DEFAULT '+0Hz',
            volume TEXT DEFAULT '+0%',
            style TEXT DEFAULT 'friendly',
            pauses_enabled INTEGER DEFAULT 1,
            filler_words INTEGER DEFAULT 0,
            breathing_sounds INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_employees (
            id TEXT PRIMARY KEY,
            name TEXT,
            position TEXT,
            hourly_rate REAL,
            pay_type TEXT DEFAULT 'hourly',
            pay_method TEXT,
            phone TEXT,
            email TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_events (
            id TEXT PRIMARY KEY,
            title TEXT,
            details TEXT,
            date TEXT,
            time TEXT,
            end_time TEXT,
            category TEXT,
            all_day INTEGER DEFAULT 0,
            project_id TEXT,
            employee_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_notes (
            id TEXT PRIMARY KEY,
            title TEXT,
            content TEXT,
            is_list INTEGER DEFAULT 0,
            is_task INTEGER DEFAULT 0,
            tags TEXT,
            pinned INTEGER DEFAULT 0,
            project_id TEXT,
            due_date TEXT,
            checklist_items TEXT,
            author_employee_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_debts (
            id TEXT PRIMARY KEY,
            name TEXT,
            type TEXT,
            frequency TEXT DEFAULT 'Monthly',
            payment_amount REAL DEFAULT 0,
            due_date TEXT,
            payoff_date TEXT,
            balance REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_files (
            id TEXT PRIMARY KEY,
            name TEXT,
            file_type TEXT,
            file_path TEXT,
            size INTEGER,
            tags TEXT,
            category TEXT,
            year TEXT,
            project_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_project_phases (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            phase_number INTEGER,
            name TEXT,
            value REAL,
            start_date TEXT,
            end_date TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_tax_requirements (
            id TEXT PRIMARY KEY,
            title TEXT,
            details TEXT,
            due_date TEXT,
            completed INTEGER DEFAULT 0,
            category TEXT DEFAULT 'tax',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_timeclock (
            id TEXT PRIMARY KEY,
            employee_id TEXT,
            date TEXT,
            clock_in TEXT,
            clock_out TEXT,
            lunch_start TEXT,
            lunch_end TEXT,
            hours REAL,
            lunch_minutes INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_receipt_queue (
            id TEXT PRIMARY KEY,
            image_path TEXT,
            mode TEXT DEFAULT 'single',
            status TEXT DEFAULT 'pending',
            result_json TEXT,
            error TEXT,
            receipt_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_payments (
            id TEXT PRIMARY KEY,
            invoice_id TEXT,
            amount REAL,
            payment_method TEXT,
            payment_date TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_phase_tasks (
            id TEXT PRIMARY KEY,
            phase_id TEXT,
            project_id TEXT,
            title TEXT,
            status TEXT DEFAULT 'pending',
            assigned_to TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS baza_roadmap (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'planned',
            priority TEXT DEFAULT 'medium',
            category TEXT DEFAULT 'general',
            assigned_agent TEXT DEFAULT '',
            target_date TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS baza_dash_links (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            icon TEXT DEFAULT '&#128279;',
            category TEXT DEFAULT 'general',
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS baza_infra_notes (
            id TEXT PRIMARY KEY,
            section TEXT DEFAULT 'general',
            note TEXT NOT NULL,
            author TEXT DEFAULT 'system',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ahb_voice_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_name TEXT,
            caller_phone TEXT,
            direction TEXT DEFAULT 'inbound',
            duration_seconds INTEGER DEFAULT 0,
            transcript TEXT,
            audio_file TEXT,
            status TEXT DEFAULT 'completed',
            lead_created INTEGER DEFAULT 0,
            agent_notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    # Add new columns to existing tables (idempotent)
    alter_stmts = [
        "ALTER TABLE ahb_projects ADD COLUMN acquisition_type TEXT",
        "ALTER TABLE ahb_projects ADD COLUMN value REAL",
        "ALTER TABLE ahb_clients ADD COLUMN company TEXT",
        "ALTER TABLE ahb_clients ADD COLUMN tags TEXT",
        "ALTER TABLE ahb_payroll ADD COLUMN overtime_hours REAL DEFAULT 0",
        "ALTER TABLE ahb_payroll ADD COLUMN employee_id TEXT",
        "ALTER TABLE ahb_invoices ADD COLUMN terms TEXT",
        "ALTER TABLE ahb_invoices ADD COLUMN client_name TEXT",
        "ALTER TABLE ahb_invoices ADD COLUMN project_name TEXT",
        "ALTER TABLE ahb_receipts ADD COLUMN store_name TEXT",
        "ALTER TABLE ahb_receipts ADD COLUMN payment_method TEXT",
        "ALTER TABLE ahb_receipts ADD COLUMN total REAL",
        "ALTER TABLE ahb_receipts ADD COLUMN teller_name TEXT DEFAULT ''",
        "ALTER TABLE ahb_receipts ADD COLUMN store_location TEXT DEFAULT ''",
        "ALTER TABLE ahb_receipts ADD COLUMN purchase_time TEXT DEFAULT ''",
        "ALTER TABLE ahb_receipts ADD COLUMN image_path TEXT DEFAULT ''",
        "ALTER TABLE ahb_receipts ADD COLUMN tax_amount REAL DEFAULT 0",
        "ALTER TABLE ahb_receipts ADD COLUMN subtotal REAL DEFAULT 0",
        "ALTER TABLE ahb_receipts ADD COLUMN items_json TEXT DEFAULT '[]'",
        "ALTER TABLE ahb_receipts ADD COLUMN ocr_raw TEXT DEFAULT ''",
        "ALTER TABLE ahb_receipts ADD COLUMN ocr_structured TEXT DEFAULT '{}'",
        "ALTER TABLE ahb_receipts ADD COLUMN year TEXT DEFAULT ''",
        "ALTER TABLE ahb_invoices ADD COLUMN date TEXT DEFAULT ''",
        "ALTER TABLE ahb_invoices ADD COLUMN parent_invoice_id TEXT DEFAULT ''",
        "ALTER TABLE ahb_invoices ADD COLUMN is_change_order INTEGER DEFAULT 0",
        "ALTER TABLE ahb_invoices ADD COLUMN overdue_since TEXT DEFAULT ''",
        "ALTER TABLE ahb_invoices ADD COLUMN overdue_interest_per_week REAL DEFAULT 50",
        "ALTER TABLE ahb_invoices ADD COLUMN company_name TEXT DEFAULT 'All Home Building Co'",
        "ALTER TABLE ahb_invoices ADD COLUMN contractor_name TEXT DEFAULT 'Sergey Tkach'",
        "ALTER TABLE ahb_invoices ADD COLUMN client_address TEXT DEFAULT ''",
        "ALTER TABLE ahb_invoices ADD COLUMN client_email TEXT DEFAULT ''",
        "ALTER TABLE ahb_invoices ADD COLUMN client_phone TEXT DEFAULT ''",
        "ALTER TABLE ahb_invoices ADD COLUMN project_address TEXT DEFAULT ''",
        "ALTER TABLE ahb_projects ADD COLUMN client_name TEXT DEFAULT ''",
        "ALTER TABLE ahb_projects ADD COLUMN client_email TEXT DEFAULT ''",
        "ALTER TABLE ahb_projects ADD COLUMN contact_info TEXT DEFAULT ''",
        "ALTER TABLE ahb_projects ADD COLUMN location TEXT DEFAULT ''",
        "ALTER TABLE ahb_files ADD COLUMN photo_section TEXT DEFAULT ''",
        "ALTER TABLE ahb_files ADD COLUMN document_type TEXT DEFAULT ''",
        "ALTER TABLE ahb_invoices ADD COLUMN year TEXT DEFAULT ''",
        "ALTER TABLE ahb_projects ADD COLUMN year TEXT DEFAULT ''",
    ]
    for stmt in alter_stmts:
        try:
            c.execute(stmt)
        except Exception:
            pass
    conn.commit()
    conn.close()

init_ahb_tables()


def init_cloud_tables():
    """Create Baza Cloud tables."""
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS baza_cloud_users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            status TEXT DEFAULT 'invited',
            storage_used INTEGER DEFAULT 0,
            storage_limit INTEGER DEFAULT 5368709120,
            invite_code TEXT DEFAULT '',
            is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS baza_cloud_invites (
            id TEXT PRIMARY KEY,
            email TEXT DEFAULT '',
            code TEXT UNIQUE NOT NULL,
            created_by TEXT DEFAULT 'serge',
            used_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS baza_cloud_files (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            path TEXT NOT NULL,
            size INTEGER DEFAULT 0,
            mime_type TEXT DEFAULT '',
            category TEXT DEFAULT 'files',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()

init_cloud_tables()

# Cloud upload directory
CLOUD_UPLOAD_DIR = os.path.join(DASHBOARD_DIR, 'uploads', 'cloud')
os.makedirs(CLOUD_UPLOAD_DIR, exist_ok=True)


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

def _read_artifact_meta(fpath: str) -> dict:
    """Read sidecar .meta file for an artifact, returns {} if not found."""
    meta_path = fpath + ".meta"
    if os.path.exists(meta_path):
        try:
            import json as _j
            return _j.loads(open(meta_path).read())
        except Exception:
            pass
    return {}

def _infer_agent_from_filename(fname: str, known_agents: list) -> str:
    """Infer agent_id from filename prefix pattern: agent_id_timestamp_..."""
    fname_lower = fname.lower()
    for a in known_agents:
        if fname_lower.startswith(a + '_') or fname_lower.startswith(a + '.'):
            return a
    # Try two-part prefix (e.g. "claw_batto_...")
    parts = fname.split('_')
    if len(parts) >= 2:
        candidate = f"{parts[0]}_{parts[1]}"
        if candidate in known_agents:
            return candidate
    return ""

def scan_artifacts_dir(base_dir: str, project_id: str = "", agent_id: str = "") -> list:
    """Recursively scan a directory for artifacts, preserving all file types."""
    # Load known agent IDs for filename-based inference
    try:
        config = load_config()
        known_agents = list(config.get('agents', {}).keys())
    except Exception:
        known_agents = []

    files = []
    for root, dirs, fnames in os.walk(base_dir):
        # Skip hidden dirs and meta files
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fname in sorted(fnames):
            # Skip sidecar meta files
            if fname.endswith('.meta'):
                continue
            fpath = os.path.join(root, fname)
            rel   = os.path.relpath(fpath, base_dir)
            # Determine project_id from relative path structure: {project}/{file}
            parts = rel.split(os.sep)
            proj  = project_id or (parts[0] if len(parts) > 1 else "shared")

            # Determine agent_id: sidecar meta > filename inference
            meta  = _read_artifact_meta(fpath)
            agent = agent_id or meta.get('agent_id', '') or _infer_agent_from_filename(fname, known_agents) or "unknown"

            stat = os.stat(fpath)
            ext  = os.path.splitext(fname)[1].lower()
            files.append({
                "name":       fname,
                "rel_path":   rel,
                "abs_path":   fpath,
                "size":       stat.st_size,
                "modified":   datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "project_id": proj,
                "agent_id":   agent,
                "task_id":    meta.get('task_id', ''),
                "ext":        ext,
                "file_type":  _ext_to_type(ext),
            })
    return files

def _ext_to_type(ext: str) -> str:
    img   = {'.png','.jpg','.jpeg','.gif','.svg','.webp','.ico','.bmp','.tiff','.tif'}
    code  = {'.py','.sh','.bash','.js','.ts','.jsx','.tsx','.json','.yaml','.yml','.toml','.ini','.cfg','.conf','.sql','.html','.css','.xml'}
    doc   = {'.md','.txt','.rst','.csv','.log','.pdf','.docx','.doc','.xlsx','.xls','.pptx','.odt','.rtf'}
    arc   = {'.zip','.tar','.gz','.tgz','.bz2','.7z','.rar'}
    audio = {'.mp3','.wav','.ogg','.flac','.aac','.m4a','.wma','.opus'}
    video = {'.mp4','.mkv','.avi','.mov','.webm','.flv','.wmv'}
    if ext in img:   return 'image'
    if ext in code:  return 'code'
    if ext in doc:   return 'document'
    if ext in arc:   return 'archive'
    if ext in audio: return 'audio'
    if ext in video: return 'video'
    return 'other'

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
    def _fetch_ollama_models(port):
        try:
            import json as _j
            r = subprocess.run(['curl','-s',f'http://localhost:{port}/api/tags'],
                               capture_output=True, text=True, timeout=3)
            return sorted([m["name"] for m in _j.loads(r.stdout).get("models",[])])
        except:
            return []

    def _fetch_litellm_models():
        try:
            import json as _j
            r = subprocess.run([
                'curl','-s','-H','Authorization: Bearer baza-litellm-internal',
                'http://localhost:4000/v1/models'
            ], capture_output=True, text=True, timeout=3)
            return sorted([m["id"] for m in _j.loads(r.stdout).get("data",[])])
        except:
            return []

    amd_models  = _fetch_ollama_models(11434)
    cuda_models = _fetch_ollama_models(11435)
    cloud_models = _fetch_litellm_models()

    # Fallback so current model always appears even if Ollama is briefly offline
    current_model = agent_config.get("model","")
    if current_model:
        if current_model.startswith(("gpt-","claude-","gemini-","grok-","groq-","mistral-large","codestral","o1","o3")):
            if current_model not in cloud_models:
                cloud_models.insert(0, current_model)
        elif current_model not in amd_models and current_model not in cuda_models:
            amd_models.insert(0, current_model)

    available_models = {
        "Local — AMD GPU (11434)":   amd_models  or ["(offline)"],
        "Local — CUDA GPU (11435)":  cuda_models or ["(offline)"],
        "Cloud via LiteLLM (4000)":  cloud_models or ["(offline)"],
    }
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

@app.route('/api/agents/<agent_id>/conversations')
def api_agent_conversations(agent_id):
    try:
        from core.context_db import get_pool
        pool = get_pool()
        conn = pool.getconn()
        cur = conn.cursor()
        cur.execute("""
            SELECT chat_id,
                   MIN(created_at) as first_msg,
                   MAX(created_at) as last_msg,
                   COUNT(*) as msg_count,
                   (SELECT content FROM messages m2 WHERE m2.chat_id=m.chat_id AND m2.agent_id=%s ORDER BY created_at DESC LIMIT 1) as last_content
            FROM messages m
            WHERE agent_id = %s
            GROUP BY chat_id
            ORDER BY MAX(created_at) DESC
            LIMIT 50
        """, (agent_id, agent_id))
        rows = cur.fetchall()
        cur.close()
        pool.putconn(conn)
        return jsonify([{
            'chat_id': r[0], 'first_msg': str(r[1]), 'last_msg': str(r[2]),
            'msg_count': r[3], 'last_content': (r[4] or '')[:200]
        } for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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

    # ── Comprehensive temperature + hardware metrics ──────────────────────
    # CPU temp via k10temp (Ryzen)
    cpu_temp = _run("sensors -j 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(f'{d.get(\\\"k10temp-pci-00c3\\\",{}).get(\\\"Tctl\\\",{}).get(\\\"temp1_input\\\",0):.0f}°C')\" 2>/dev/null")
    if not cpu_temp or cpu_temp == '0°C':
        cpu_temp = _run("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf \"%.0f°C\", $1/1000}'") or "N/A"

    # Motherboard + chipset via ASUS EC
    mobo_temp = _run("sensors -j 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); ec=d.get('asusec-isa-0000',{}); mb=ec.get('Motherboard',{}); print(f'{mb.get(\\\"temp3_input\\\",0):.0f}°C')\" 2>/dev/null") or "N/A"
    chipset_temp = _run("sensors -j 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); ec=d.get('asusec-isa-0000',{}); cs=ec.get('Chipset',{}); [print(f'{v:.0f}°C') for k,v in cs.items() if 'input' in k and isinstance(v,(int,float)) and v>0]\" 2>/dev/null") or "N/A"

    # NVIDIA RTX 3070
    nvidia = _run("nvidia-smi --query-gpu=temperature.gpu,fan.speed,power.draw,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits 2>/dev/null")
    nvidia_temp = nvidia_fan = nvidia_power = nvidia_vram = nvidia_util = "N/A"
    if nvidia:
        parts = [p.strip() for p in nvidia.split(',')]
        if len(parts) >= 6:
            nvidia_temp = f"{parts[0]}°C"
            nvidia_fan = f"{parts[1]}%"
            nvidia_power = f"{parts[2]}W"
            nvidia_vram = f"{parts[3]}/{parts[4]}MB"
            nvidia_util = f"{parts[5]}%"

    # AMD RX 6700 XT — use rocm-smi first (sysfs locked by Vulkan), fall back to sysfs
    amd_temp = "N/A"
    amd_fan = "N/A"
    amd_power = "N/A"
    _rocm_temp_live = _run("rocm-smi --showtemp 2>/dev/null")
    if _rocm_temp_live:
        for _rl in _rocm_temp_live.splitlines():
            if "Temperature" in _rl and "edge" in _rl.lower():
                _tv = ''.join(c for c in _rl.split(":")[-1] if c.isdigit() or c == '.')
                if _tv:
                    amd_temp = f"{int(float(_tv))}°C"
        _rocm_use_live = _run("rocm-smi --showuse 2>/dev/null")
        for _rl in (_rocm_use_live or "").splitlines():
            if "GPU use" in _rl:
                _uv = ''.join(c for c in _rl.split(":")[-1] if c.isdigit())
                if _uv:
                    amd_power = f"{_uv}% util"
    if amd_temp == "N/A":
        # Fallback to sysfs
        for card in ["card0", "card1", "card2", "card3"]:
            t = _run(f"cat /sys/class/drm/{card}/device/hwmon/hwmon*/temp1_input 2>/dev/null")
            name = _run(f"cat /sys/class/drm/{card}/device/hwmon/hwmon*/name 2>/dev/null")
            if name == "amdgpu" and t and t.strip():
                val = int(t) // 1000
                if val > 0:
                    amd_temp = f"{val}°C"
                fan = _run(f"cat /sys/class/drm/{card}/device/hwmon/hwmon*/fan1_input 2>/dev/null")
                if fan and fan.strip() and fan.strip().isdigit() and int(fan) > 0:
                    amd_fan = f"{fan}RPM"
                break
    if amd_temp == "N/A":
        amd_temp = "idle"

    # NVMe SSD temps
    nvme0_temp = _run("sensors -j 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); n=d.get('nvme-pci-0400',d.get('nvme-pci-0e00',{})); c=n.get('Composite',{}); print(f'{c.get(\\\"temp1_input\\\",0):.0f}°C')\" 2>/dev/null") or "N/A"
    nvme1_temp = _run("sensors -j 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); n=d.get('nvme-pci-0e00',{}); c=n.get('Composite',{}); print(f'{c.get(\\\"temp1_input\\\",0):.0f}°C')\" 2>/dev/null") or "N/A"

    online = sum(1 for s in statuses.values() if s == 'online')
    return jsonify({
        "statuses": statuses,
        "messages": messages,
        "online": online,
        "total": len(statuses),
        "metrics": {
            "cpu_temp":     cpu_temp,
            "mobo_temp":    mobo_temp,
            "chipset_temp": chipset_temp,
            "nvidia_temp":  nvidia_temp,
            "nvidia_fan":   nvidia_fan,
            "nvidia_power": nvidia_power,
            "nvidia_vram":  nvidia_vram,
            "nvidia_util":  nvidia_util,
            "amd_temp":     amd_temp,
            "amd_fan":      amd_fan,
            "amd_power":    amd_power,
            "nvme0_temp":   nvme0_temp,
            "nvme1_temp":   nvme1_temp,
            "mem":          _run("free -h | awk '/^Mem:/{print $3\"/\"$2}'"),
            "disk":         _run("df -h / | tail -1 | awk '{print $5}'"),
            "ollama_vulkan": _port("localhost", 11434),
            "ollama_amd":   _port("localhost", 11434),
            "ollama_gpu":   _port("localhost", 11435),
            "litellm":      _port("localhost", 4000),
            "printer":      _run("lpstat -p 2>/dev/null | head -1 | grep -q 'idle\\|printing' && echo 'online' || echo 'offline'") or "offline",
            "printer_name": _run("lpstat -p 2>/dev/null | head -1 | awk '{print $2}'") or "—",
            "printer_jobs": _run("lpstat -o 2>/dev/null | wc -l") or "0",
        }
    })

@app.route('/api/models')
def api_models():
    """All available models: local Ollama (Vulkan + CUDA) + LiteLLM cloud + Ollama library."""
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
                     "quant":  m.get("details",{}).get("quantization_level",""),
                     "installed": True}
                    for m in data.get("models",[])]
        except: return []

    def _litellm_models():
        try:
            r = subprocess.run([
                'curl','-s','-H','Authorization: Bearer baza-litellm-internal',
                'http://localhost:4000/v1/models'
            ], capture_output=True, text=True, timeout=5)
            import json as _json
            return [{"name": m["id"], "provider": "litellm", "installed": True}
                    for m in _json.loads(r.stdout).get("data",[])]
        except: return []

    def _ollama_library():
        """Fetch popular models from Ollama library (pullable but not yet installed)."""
        try:
            import urllib.request as _ur
            import json as _json
            req = _ur.Request('https://ollama.com/api/tags', headers={'User-Agent': 'baza-dashboard'})
            with _ur.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read())
            installed = {m["name"].split(":")[0] for m in _ollama_models("http://localhost:11434")}
            library = []
            for m in data.get("models", [])[:100]:
                base_name = m["name"].split(":")[0]
                library.append({
                    "name": m["name"],
                    "size": m.get("size", 0),
                    "installed": base_name in installed,
                })
            return library
        except Exception:
            return []

    # Read LiteLLM config for cloud model list (even if proxy DB is down)
    def _litellm_config_models():
        """Read cloud models from litellm.yaml config file."""
        try:
            config_path = os.path.join(os.path.dirname(DASHBOARD_DIR), 'configs', 'litellm.yaml')
            import yaml as _yaml
            with open(config_path) as f:
                cfg = _yaml.safe_load(f)
            return [{"name": m.get("model_name",""), "provider": m.get("litellm_params",{}).get("model","").split("/")[0],
                     "model_id": m.get("litellm_params",{}).get("model",""), "installed": True}
                    for m in cfg.get("model_list", []) if m.get("model_name")]
        except Exception:
            return []

    return jsonify({
        "vulkan":  {"label": "Ollama Vulkan (RX 6700 XT)", "url": "localhost:11434", "up": _port("localhost",11434), "models": _ollama_models("http://localhost:11434")},
        "cuda":    {"label": "Ollama CUDA (RTX 3070)",      "url": "localhost:11435", "up": _port("localhost",11435), "models": _ollama_models("http://localhost:11435")},
        "cloud":   {"label": "LiteLLM Cloud Models",        "url": "localhost:4000",  "up": _port("localhost",4000),  "models": _litellm_models() or _litellm_config_models()},
        "library": {"label": "Ollama Library (pullable)",    "url": "ollama.com",      "up": True,                     "models": _ollama_library()},
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
    agent_id   = request.args.get('agent_id')
    if project_id:
        arts = artifacts_for_project(project_id)
    else:
        arts = all_artifacts()
    if agent_id:
        arts = [a for a in arts if a.get('agent_id') == agent_id]
    return jsonify(arts)

@app.route('/api/artifacts/save-text', methods=['POST'])
def api_artifact_save_text():
    import re as _re
    data       = request.json or {}
    project_id = data.get('project_id', 'shared')
    raw_name   = data.get('filename', f"artifact_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    content    = data.get('content', '')
    agent_id   = data.get('agent_id', '')
    task_id    = data.get('task_id', '')
    safe_name  = _re.sub(r'[^\w.\-]', '_', raw_name).strip('_') or 'artifact.txt'
    # All extensions accepted
    proj_dir   = os.path.join(ARTIFACTS_DIR, project_id)
    os.makedirs(proj_dir, exist_ok=True)
    fpath      = os.path.join(proj_dir, safe_name)
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(content)
    # Write sidecar meta file for reliable agent/task tracking
    if agent_id or task_id:
        try:
            meta = {'agent_id': agent_id, 'task_id': task_id,
                    'created_at': datetime.datetime.now().isoformat()}
            with open(fpath + '.meta', 'w') as mf:
                json.dump(meta, mf)
        except Exception:
            pass
    size = os.path.getsize(fpath)
    return jsonify({'success': True, 'name': safe_name, 'project_id': project_id,
                    'agent_id': agent_id, 'task_id': task_id,
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
    # Path traversal protection
    safe_filename = os.path.basename(filename)
    proj_dir = os.path.join(ARTIFACTS_DIR, project_id)
    fpath    = os.path.join(proj_dir, safe_filename)
    if not os.path.realpath(fpath).startswith(os.path.realpath(ARTIFACTS_DIR)):
        return jsonify({'error': 'Forbidden'}), 403
    if not os.path.exists(fpath):
        return jsonify({'error': 'File not found'}), 404
    ext = os.path.splitext(safe_filename)[1].lower()
    text_exts = {'.txt','.md','.py','.sh','.js','.ts','.jsx','.tsx','.html','.htm',
                 '.css','.json','.yaml','.yml','.toml','.ini','.cfg','.conf','.sql',
                 '.log','.env','.csv','.rst','.xml','.bash','.zsh','.rb','.go',
                 '.php','.svg','.rs','.c','.cpp','.h'}
    if ext in text_exts:
        try:
            content = open(fpath, 'r', errors='replace').read(500_000)
            return jsonify({'content': content, 'name': safe_filename, 'type': 'text'})
        except Exception:
            pass
    # Binary file — return metadata so JS can use the serve endpoint for inline preview
    return jsonify({
        'content': None, 'name': safe_filename, 'type': 'binary',
        'serve_url': f'/api/artifacts/serve/{project_id}/{safe_filename}'
    })

@app.route('/api/artifacts/serve/<project_id>/<filename>')
def api_artifact_serve(project_id, filename):
    """Serve file inline for browser preview (images, PDFs, etc)."""
    safe_filename = os.path.basename(filename)
    proj_dir = os.path.join(ARTIFACTS_DIR, project_id)
    fpath    = os.path.join(proj_dir, safe_filename)
    if not os.path.realpath(fpath).startswith(os.path.realpath(ARTIFACTS_DIR)):
        return jsonify({'error': 'Forbidden'}), 403
    return send_from_directory(proj_dir, safe_filename)

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


@app.route('/api/print', methods=['POST'])
def api_print():
    """Print a file/artifact via the HP Smart Tank 5101."""
    data = request.json or {}
    action = data.get('action', 'print')
    skill_path = os.path.join(os.path.dirname(DASHBOARD_DIR), 'skills', 'shared', 'print_document.py')

    # If printing an artifact by project_id + filename, resolve to absolute path
    if action == 'print' and data.get('project_id') and data.get('filename'):
        fpath = os.path.join(ARTIFACTS_DIR, data['project_id'], data['filename'])
        if os.path.exists(fpath):
            data['file_path'] = fpath

    env = os.environ.copy()
    env['SKILL_ARGS'] = json.dumps(data)
    try:
        result = subprocess.run([VENV_PYTHON, skill_path], capture_output=True, text=True, timeout=30, env=env)
        output = result.stdout.strip()
        # Try to get JSON from last line
        parsed = {}
        for line in reversed(output.split('\n')):
            if line.strip().startswith('{'):
                try:
                    parsed = json.loads(line.strip())
                    break
                except Exception:
                    pass
        if not parsed:
            parsed = {'success': result.returncode == 0, 'output': output}
        if result.returncode != 0 and result.stderr:
            parsed['stderr'] = result.stderr.strip()
        return jsonify(parsed)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/print/status')
def api_print_status():
    """Get printer status."""
    skill_path = os.path.join(os.path.dirname(DASHBOARD_DIR), 'skills', 'shared', 'print_document.py')
    env = os.environ.copy()
    env['SKILL_ARGS'] = json.dumps({'action': 'status'})
    try:
        result = subprocess.run([VENV_PYTHON, skill_path], capture_output=True, text=True, timeout=10, env=env)
        for line in reversed(result.stdout.strip().split('\n')):
            if line.strip().startswith('{'):
                return jsonify(json.loads(line.strip()))
        return jsonify({'success': True, 'output': result.stdout.strip()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


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

@app.route('/api/artifacts/send-to-agent', methods=['POST'])
def api_artifact_send_to_agent():
    data = request.json or {}
    project_id = data.get('project_id', '')
    filename = data.get('filename', '')
    agent_id = data.get('agent_id', '')
    prompt = data.get('prompt', '')
    if not all([agent_id, prompt]):
        return jsonify({'success': False, 'error': 'agent_id and prompt required'}), 400
    token_map = {
        'simon_bately': 'TELEGRAM_SIMON_BATELY',
        'claw_batto': 'TELEGRAM_CLAW_BATTO',
        'phil_hass': 'TELEGRAM_PHIL_HASS',
        'sam_axe': 'TELEGRAM_SAM_AXE',
        'rex_valor': 'TELEGRAM_REX_VALOR',
        'duke_harmon': 'TELEGRAM_DUKE_HARMON',
        'nova_sterling': 'TELEGRAM_NOVA_STERLING',
        'scout_reeves': 'TELEGRAM_SCOUT_REEVES',
    }
    token_env = token_map.get(agent_id)
    if not token_env:
        return jsonify({'success': False, 'error': f'Unknown agent: {agent_id}'}), 400
    token = os.environ.get(token_env, '')
    if not token:
        # Load from secrets file
        secrets_path = os.path.join(os.path.dirname(DASHBOARD_DIR), 'configs', 'secrets.env')
        if os.path.exists(secrets_path):
            with open(secrets_path) as sf:
                for line in sf:
                    line = line.strip()
                    if line.startswith(token_env + '='):
                        token = line.split('=', 1)[1].strip().strip('"').strip("'")
                        break
    if not token:
        return jsonify({'success': False, 'error': f'No token for {agent_id}'}), 500
    file_url = f"dashboard/artifacts/{project_id}/{filename}" if project_id and filename else ""
    message = f"\U0001f4ce Artifact Task from Dashboard\n\nFile: {filename}\nProject: {project_id}\nPath: {file_url}\n\nInstruction: {prompt}"
    import requests as _req
    try:
        resp = _req.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"limit": 5, "offset": -5}, timeout=10)
        updates = resp.json().get('result', [])
        chat_id = None
        for u in reversed(updates):
            msg = u.get('message', {})
            if msg.get('chat', {}).get('type') == 'private':
                chat_id = msg['chat']['id']
                break
        if not chat_id:
            return jsonify({'success': False, 'error': 'No chat found — message the agent on Telegram first'}), 400
        resp = _req.post(f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
        result = resp.json()
        if result.get('ok'):
            try:
                from core.context_db import journal_log
                journal_log(agent_id=agent_id, task_type="artifact_dispatch",
                    task_description=f"Dashboard sent {filename} to {agent_id}: {prompt[:200]}",
                    result="sent", success=True, input_data=data, chat_id=chat_id)
            except Exception:
                pass
            return jsonify({'success': True, 'message_id': result.get('result', {}).get('message_id')})
        return jsonify({'success': False, 'error': result.get('description', 'Send failed')})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ── Routes — Heartbeat API ───────────────────────────────────────────────────

@app.route('/api/heartbeats')
def api_heartbeats():
    """Return last heartbeat timestamp for all agents from Redis. TTL=120s per key."""
    try:
        import redis as _redis
        r = _redis.Redis(host='localhost', port=6379, decode_responses=True)
        keys = r.keys('baza:heartbeat:*')
        result = {}
        for key in keys:
            val = r.get(key)
            ttl = r.ttl(key)
            if val:
                try:
                    data = json.loads(val)
                    data['ttl']         = ttl
                    data['seconds_ago'] = max(0, 120 - ttl) if ttl > 0 else 999
                    result[data['agent_id']] = data
                except Exception:
                    pass
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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

    # CPU temp: try k10temp/Tctl (AMD Ryzen), then Package id 0 (Intel), then thermal_zone
    cpu_raw = _run("sensors 2>/dev/null | grep -i 'Tctl' | head -1 | awk '{print $2}'")
    if not cpu_raw:
        cpu_raw = _run("sensors 2>/dev/null | grep -i 'Package id 0' | head -1 | awk '{print $4}'")
    if not cpu_raw:
        # Try ASUS EC sensor for CPU temp
        cpu_raw = _run("sensors 2>/dev/null | grep -A1 'asusec' | grep -i 'CPU:' | head -1 | awk '{print $2}'")
    if not cpu_raw:
        cpu_raw = _run("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf \"%.1fC\", $1/1000}'")

    # GPU info
    gpus = []
    nvidia_smi = _run("nvidia-smi --query-gpu=name,temperature.gpu,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits 2>/dev/null")
    if nvidia_smi:
        parts = nvidia_smi.split(',')
        if len(parts) >= 5:
            gpus.append({"name": parts[0].strip(), "temp": parts[1].strip(), "memory_used": parts[2].strip(),
                         "memory_total": parts[3].strip(), "utilization": parts[4].strip(), "type": "NVIDIA"})
    # AMD — use rocm-smi (reliable even when Vulkan holds sysfs), fall back to sysfs
    # Try rocm-smi for utilization + VRAM (works when Vulkan locks sysfs)
    _rocm_use = _run("rocm-smi --showuse 2>/dev/null")
    _rocm_vram = _run("rocm-smi --showmeminfo vram 2>/dev/null")
    _amd_found = False
    if _rocm_use and "GPU use" in _rocm_use:
        gpu_entry = {"name": "Radeon RX 6700 XT", "type": "AMD"}
        # GPU utilization
        for _line in _rocm_use.splitlines():
            if "GPU use" in _line:
                _uval = ''.join(c for c in _line.split(":")[-1] if c.isdigit())
                if _uval:
                    gpu_entry["utilization"] = _uval + "%"
        # Temperature — try rocm-smi first
        _rocm_temp = _run("rocm-smi --showtemp 2>/dev/null")
        for _line in (_rocm_temp or "").splitlines():
            if "Temperature" in _line and ("edge" in _line.lower() or "GPU" in _line):
                _tval = ''.join(c for c in _line.split(":")[-1] if c.isdigit() or c == '.')
                if _tval:
                    gpu_entry["temp"] = str(int(float(_tval)))
        # VRAM from rocm-smi
        for _line in (_rocm_vram or "").splitlines():
            if "Total Memory" in _line:
                _v = ''.join(c for c in _line.split(":")[-1] if c.isdigit())
                if _v:
                    gpu_entry["memory_total"] = int(_v) // (1024*1024)
            elif "Used Memory" in _line:
                _v = ''.join(c for c in _line.split(":")[-1] if c.isdigit())
                if _v:
                    gpu_entry["memory_used"] = int(_v) // (1024*1024)
        # If temp still missing, try sysfs as last resort
        if "temp" not in gpu_entry:
            for _card in sorted(_run("ls -d /sys/class/drm/card[0-9]*/device/uevent 2>/dev/null").splitlines()):
                _card_dir = os.path.dirname(_card)
                if "amdgpu" in (_run(f"grep DRIVER {_card} 2>/dev/null") or ""):
                    _t = _run(f"cat {_card_dir}/hwmon/hwmon*/temp1_input 2>/dev/null")
                    if _t:
                        gpu_entry["temp"] = str(int(_t) // 1000)
                    break
        gpus.append(gpu_entry)
        _amd_found = True

    if not _amd_found:
        # Fallback: try sysfs directly
        for _card in sorted(_run("ls -d /sys/class/drm/card[0-9]*/device/uevent 2>/dev/null").splitlines()):
            _card_dir = os.path.dirname(_card)
            if "amdgpu" in (_run(f"grep DRIVER {_card} 2>/dev/null") or ""):
                gpu_entry = {"name": "Radeon RX 6700 XT", "type": "AMD"}
                _t = _run(f"cat {_card_dir}/hwmon/hwmon*/temp1_input 2>/dev/null")
                if _t:
                    gpu_entry["temp"] = str(int(_t) // 1000)
                _m = _run(f"cat {_card_dir}/mem_info_vram_total 2>/dev/null")
                if _m:
                    gpu_entry["memory_total"] = int(_m) // (1024*1024)
                _mu = _run(f"cat {_card_dir}/mem_info_vram_used 2>/dev/null")
                if _mu:
                    gpu_entry["memory_used"] = int(_mu) // (1024*1024)
                _b = _run(f"cat {_card_dir}/gpu_busy_percent 2>/dev/null")
                if _b:
                    gpu_entry["utilization"] = _b.strip() + "%"
                gpus.append(gpu_entry)
                break

    # Storage
    storage = []
    for line in _run("lsblk -bno NAME,SIZE,TYPE | grep disk").splitlines():
        parts = line.split()
        if len(parts) >= 3:
            sz = int(parts[1]) if parts[1].isdigit() else 0
            storage.append({"name": parts[0], "size_tb": round(sz / 1e12, 1), "type": parts[2]})

    # Tailscale
    ts_raw = _run("tailscale status --json 2>/dev/null")
    tailscale = {}
    try:
        import json as _json
        ts = _json.loads(ts_raw) if ts_raw else {}
        tailscale = {
            "self_ip": ts.get("Self", {}).get("TailscaleIPs", [""])[0] if ts.get("Self") else "",
            "hostname": ts.get("Self", {}).get("HostName", ""),
            "peers": [{
                "name": (p.get("DNSName","").split(".")[0] if p.get("HostName","") == "localhost" and p.get("DNSName") else p.get("HostName","")) or p.get("DNSName","").split(".")[0],
                "ip": p.get("TailscaleIPs", [""])[0] if p.get("TailscaleIPs") else "",
                "os": p.get("OS", ""),
                "online": p.get("Online", False),
                "last_seen": p.get("LastSeen", ""),
            } for p in ts.get("Peer", {}).values()]
        }
    except Exception:
        tailscale = {"self_ip": _run("tailscale ip -4 2>/dev/null"), "peers": []}

    # API endpoint count
    api_count = 0
    try:
        with open(os.path.join(DASHBOARD_DIR, 'app.py')) as _f:
            api_count = _f.read().count("@app.route")
    except Exception:
        pass

    # DB stats
    db_stats = {}
    try:
        conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
        for tbl in ['ahb_projects','ahb_invoices','ahb_receipts','ahb_clients','ahb_employees',
                     'ahb_events','ahb_debts','ahb_payroll','ahb_notes','ahb_files']:
            db_stats[tbl] = conn.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
        conn.close()
    except Exception:
        pass

    return jsonify({
        "hostname": _run("hostname"),
        "os": _run("uname -sr"),
        "kernel": _run("uname -r"),
        "cpu_model": _run("lscpu | grep 'Model name' | sed 's/.*: *//'"),
        "cpu_cores": _run("nproc"),
        "cpu_temp": cpu_raw or "N/A",
        "mem_total": _run("free -h | awk '/^Mem:/{print $2}'"),
        "mem_used": _run("free -h | awk '/^Mem:/{print $3}'"),
        "mem_usage": _run("free -h | awk '/^Mem:/{print $3\"/\"$2}'"),
        "disk_root": _run("df -h / | tail -1 | awk '{print $3\"/\"$2\" (\"$5\")}'"),
        "disk_usage": _run("df -h / | tail -1 | awk '{print $3\"/\"$2\" (\"$5\")}'"),
        "uptime": _run("uptime -p"),
        "load": _run("cat /proc/loadavg | awk '{print $1, $2, $3}'"),
        "gpus": gpus,
        "storage": storage,
        "total_storage_tb": round(sum(d["size_tb"] for d in storage), 1),
        "tailscale": tailscale,
        "api_routes": api_count,
        "db_stats": db_stats,
        "services": {
            "ollama_amd": _port("localhost", 11434),
            "ollama_nvidia": _port("localhost", 11435),
            "dashboard": "up",
            "sdwebui": _port("localhost", 7860),
            "litellm": _port("localhost", 4000),
            "tool_server": _port("localhost", 8000),
            "postgresql": _svc("postgresql"),
            "redis": _svc("redis-server"),
            "nginx": _svc("nginx"),
            "nextcloud": _run("docker ps --filter name=nextcloud --format '{{.Status}}' 2>/dev/null") or "stopped",
        },
        "agents": {
            name: _svc(f"baza-agent-{name.replace('_','-')}")
            for name in ["simon-bately","claw-batto","phil-hass","sam-axe",
                          "scout-reeves","duke-harmon","rex-valor","nova-sterling"]
        },
        "urls": {
            "dashboard": "http://100.127.118.103:8888",
            "mobile": "http://100.127.118.103:8888/mobile",
            "ahb123": "http://100.127.118.103:8888/ahb123",
            "portal": "http://100.127.118.103:8888/portal",
            "local_dashboard": "http://localhost:8888",
            "lan_dashboard": f"http://{_run('hostname -I').split()[0] if _run('hostname -I') else 'localhost'}:8888",
        },
        "nuc_mining": _svc("baza-nuc-mining"),
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


# ── Routes — AHB123 Business Hub ──────────────────────────────────────────────

def _ahb_db():
    """Get SQLite connection to baza_projects.db with row factory."""
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.row_factory = sqlite3.Row
    return conn


@app.route('/ahb123')
@app.route('/ahb123/<tab>')
def ahb123_page(tab='dashboard'):
    return render_template('ahb123.html', active_tab=tab)


@app.route('/mobile')
def mobile_page():
    return render_template('mobile.html')


@app.route('/mobile/manifest.json')
def mobile_manifest():
    manifest = {
        "name": "Baza Empire",
        "short_name": "Baza",
        "start_url": "/mobile",
        "display": "standalone",
        "background_color": "#07070f",
        "theme_color": "#07070f",
        "orientation": "portrait",
        "icons": [
            {"src": "/static/img/ahb_logo.jpeg", "sizes": "192x192", "type": "image/jpeg"},
            {"src": "/static/img/ahb_logo.jpeg", "sizes": "512x512", "type": "image/jpeg"}
        ]
    }
    return jsonify(manifest)


@app.route('/portal')
def portal_page():
    return render_template('portal.html')


# ── AHB123 — Clients ─────────────────────────────────────────────────────────

@app.route('/api/ahb/clients', methods=['GET'])
def api_ahb_clients_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_clients"
        params = []
        status = request.args.get('status')
        if status:
            q += " WHERE status = ?"
            params.append(status)
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/clients', methods=['POST'])
def api_ahb_clients_create():
    try:
        data = request.json or {}
        cid = uuid.uuid4().hex[:24]
        conn = _ahb_db()
        conn.execute(
            """INSERT INTO ahb_clients (id, name, phone, email, address, city, source, status, notes, assigned_agent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, data.get('name'), data.get('phone'), data.get('email'),
             data.get('address'), data.get('city', 'Philadelphia'),
             data.get('source'), data.get('status', 'lead'),
             data.get('notes'), data.get('assigned_agent'))
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': cid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/clients/<cid>', methods=['PUT'])
def api_ahb_clients_update(cid):
    try:
        data = request.json or {}
        fields = []
        vals = []
        for k in ('name', 'phone', 'email', 'address', 'city', 'source', 'status', 'notes', 'assigned_agent'):
            if k in data:
                fields.append(f"{k} = ?")
                vals.append(data[k])
        if not fields:
            return jsonify({'success': False, 'error': 'No fields to update'}), 400
        fields.append("updated_at = ?")
        vals.append(datetime.datetime.now().isoformat())
        vals.append(cid)
        conn = _ahb_db()
        conn.execute(f"UPDATE ahb_clients SET {', '.join(fields)} WHERE id = ?", vals)
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/clients/<cid>', methods=['DELETE'])
def api_ahb_clients_delete(cid):
    try:
        conn = _ahb_db()
        conn.execute("DELETE FROM ahb_clients WHERE id = ?", (cid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Projects ────────────────────────────────────────────────────────

@app.route('/api/ahb/projects', methods=['GET'])
def api_ahb_projects_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_projects WHERE 1=1"
        params = []
        if request.args.get('client_id'):
            q += " AND client_id = ?"
            params.append(request.args['client_id'])
        if request.args.get('status'):
            q += " AND status = ?"
            params.append(request.args['status'])
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/projects', methods=['POST'])
def api_ahb_projects_create():
    """Create a project and auto-generate a correlated invoice. Optionally create phases."""
    try:
        data = request.json or {}
        pid = uuid.uuid4().hex[:24]
        conn = _ahb_db()
        conn.execute(
            """INSERT INTO ahb_projects (id, client_id, title, address, scope, description,
               budget_low, budget_high, status, start_date, end_date, assigned_agents, notes,
               value, client_name, client_email, contact_info, location)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, data.get('client_id'), data.get('title'), data.get('address'),
             data.get('scope'), data.get('description'),
             data.get('budget_low'), data.get('budget_high'),
             data.get('status', 'estimate'), data.get('start_date'),
             data.get('end_date'), data.get('assigned_agents'), data.get('notes'),
             data.get('value'), data.get('client_name', ''),
             data.get('client_email', ''), data.get('contact_info', ''),
             data.get('location', ''))
        )

        # Create phases if provided
        phases = data.get('phases', [])
        line_items = []
        subtotal = 0
        for i, ph in enumerate(phases):
            phid = uuid.uuid4().hex[:24]
            phase_val = ph.get('value', 0) or 0
            conn.execute(
                """INSERT INTO ahb_project_phases (id, project_id, phase_number, name, value, start_date, end_date, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (phid, pid, ph.get('phase_number', i + 1), ph.get('name', f'Phase {i+1}'),
                 phase_val, ph.get('start_date', ''), ph.get('end_date', ''),
                 ph.get('status', 'pending')))
            line_items.append({'description': ph.get('name', f'Phase {i+1}'), 'quantity': 1, 'unit_price': phase_val})
            subtotal += phase_val

        # If no phases, use project value as single line item
        if not line_items:
            val = data.get('value') or data.get('budget_high') or 0
            line_items = [{'description': data.get('title', 'Project'), 'quantity': 1, 'unit_price': val}]
            subtotal = val

        # Auto-create correlated invoice
        iid = uuid.uuid4().hex[:24]
        inv_num = f"AHB-{datetime.datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:3].upper()}"
        total = subtotal  # no tax by default
        conn.execute(
            """INSERT INTO ahb_invoices (id, client_id, project_id, invoice_number, line_items,
               subtotal, tax, total, status, notes, client_name, project_name, terms,
               company_name, contractor_name, client_address, client_email, client_phone, project_address)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (iid, data.get('client_id', ''), pid, inv_num,
             json.dumps(line_items), subtotal, 0, total, 'draft',
             f"Auto-generated from project: {data.get('title', '')}",
             data.get('client_name', ''), data.get('title', ''), 'Net 30',
             'All Home Building Co', 'Sergey Tkach',
             data.get('address', ''), data.get('client_email', ''),
             data.get('contact_info', ''), data.get('address', '')))

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': pid, 'invoice_id': iid, 'invoice_number': inv_num})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/projects/<pid>', methods=['PUT'])
def api_ahb_projects_update(pid):
    try:
        data = request.json or {}
        fields = []
        vals = []
        for k in ('client_id', 'title', 'address', 'scope', 'description',
                   'budget_low', 'budget_high', 'status', 'start_date',
                   'end_date', 'assigned_agents', 'notes', 'value',
                   'client_name', 'client_email', 'contact_info', 'location',
                   'acquisition_type'):
            if k in data:
                fields.append(f"{k} = ?")
                vals.append(data[k])
        if not fields:
            return jsonify({'success': False, 'error': 'No fields to update'}), 400
        fields.append("updated_at = ?")
        vals.append(datetime.datetime.now().isoformat())
        vals.append(pid)
        conn = _ahb_db()
        conn.execute(f"UPDATE ahb_projects SET {', '.join(fields)} WHERE id = ?", vals)
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/projects/<pid>', methods=['DELETE'])
def api_ahb_projects_delete(pid):
    try:
        conn = _ahb_db()
        conn.execute("DELETE FROM ahb_projects WHERE id = ?", (pid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/projects/<pid>/status', methods=['POST'])
def api_ahb_project_status_sync(pid):
    """Update project status and auto-sync the linked invoice status + calendar events."""
    try:
        data = request.json or {}
        new_status = data.get('status')
        if not new_status:
            return jsonify({'success': False, 'error': 'status is required'}), 400

        conn = _ahb_db()
        project = conn.execute("SELECT * FROM ahb_projects WHERE id = ?", (pid,)).fetchone()
        if not project:
            conn.close()
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        project = dict(project)

        # Update project status
        now = datetime.datetime.now().isoformat()
        conn.execute("UPDATE ahb_projects SET status = ?, updated_at = ? WHERE id = ?", (new_status, now, pid))

        # Find linked invoice (first invoice for this project)
        inv = conn.execute(
            "SELECT * FROM ahb_invoices WHERE project_id = ? ORDER BY created_at ASC LIMIT 1", (pid,)
        ).fetchone()

        inv_status = None
        status_map = {
            'Planning': 'Sent',
            'planning': 'Sent',
            'In Progress': 'Approved',
            'in_progress': 'Approved',
            'in progress': 'Approved',
            'Completed': 'Approved',
            'completed': 'Approved',
            'Paid': 'Paid',
            'paid': 'Paid',
        }
        inv_status = status_map.get(new_status)

        invoice_result = None
        if inv and inv_status:
            inv = dict(inv)
            update_fields = {'status': inv_status, 'updated_at': now}
            # For completed projects, keep invoice as Approved (final bill due)
            if new_status.lower() == 'completed':
                # Calculate remaining balance: total minus payments received
                payments = conn.execute(
                    "SELECT COALESCE(sum(amount),0) as paid FROM ahb_payments WHERE invoice_id = ?", (inv['id'],)
                ).fetchone()
                paid_amount = payments['paid'] if payments else 0
                remaining = (inv['total'] or 0) - paid_amount
                update_fields['notes'] = f"Final bill due. Remaining balance: ${remaining:.2f}"

            if new_status.lower() == 'paid':
                update_fields['paid_date'] = now[:10]

            set_clause = ', '.join(f"{k} = ?" for k in update_fields)
            vals = list(update_fields.values()) + [inv['id']]
            conn.execute(f"UPDATE ahb_invoices SET {set_clause} WHERE id = ?", vals)
            invoice_result = {**inv, **update_fields}

        # If status is 'In Progress', create calendar events from project dates
        if new_status.lower().replace(' ', '_') in ('in_progress', 'in progress'):
            start_date = project.get('start_date')
            end_date = project.get('end_date')
            title = project.get('title', 'Project')
            if start_date:
                eid = uuid.uuid4().hex[:24]
                conn.execute(
                    """INSERT INTO ahb_events (id, title, details, date, category, all_day, project_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (eid, f"{title} - Start", f"Project start: {title}", start_date, 'project', 1, pid))
            if end_date:
                eid = uuid.uuid4().hex[:24]
                conn.execute(
                    """INSERT INTO ahb_events (id, title, details, date, category, all_day, project_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (eid, f"{title} - End", f"Project end: {title}", end_date, 'project', 1, pid))

        conn.commit()

        # Re-fetch updated project
        updated_project = dict(conn.execute("SELECT * FROM ahb_projects WHERE id = ?", (pid,)).fetchone())
        conn.close()
        return jsonify({'success': True, 'project': updated_project, 'invoice': invoice_result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Invoices ────────────────────────────────────────────────────────

@app.route('/api/ahb/invoices', methods=['GET'])
def api_ahb_invoices_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_invoices WHERE 1=1"
        params = []
        if request.args.get('status'):
            q += " AND status = ?"
            params.append(request.args['status'])
        if request.args.get('client_id'):
            q += " AND client_id = ?"
            params.append(request.args['client_id'])
        if request.args.get('is_change_order') is not None:
            q += " AND is_change_order = ?"; params.append(int(request.args['is_change_order']))
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/invoices', methods=['POST'])
def api_ahb_invoices_create():
    try:
        data = request.json or {}
        iid = uuid.uuid4().hex[:24]
        inv_num = f"AHB-{datetime.datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:3].upper()}"
        conn = _ahb_db()
        conn.execute(
            """INSERT INTO ahb_invoices (id, client_id, project_id, invoice_number, line_items,
               subtotal, tax, total, status, due_date, paid_date, notes,
               date, parent_invoice_id, is_change_order, overdue_since,
               overdue_interest_per_week, company_name, contractor_name,
               client_address, client_email, client_phone, project_address)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (iid, data.get('client_id'), data.get('project_id'), inv_num,
             json.dumps(data.get('line_items', [])) if isinstance(data.get('line_items'), list) else data.get('line_items'),
             data.get('subtotal'), data.get('tax'), data.get('total'),
             data.get('status', 'draft'), data.get('due_date'),
             data.get('paid_date'), data.get('notes'),
             data.get('date', ''), data.get('parent_invoice_id', ''),
             data.get('is_change_order', 0), data.get('overdue_since', ''),
             data.get('overdue_interest_per_week', 50),
             data.get('company_name', 'All Home Building Co'),
             data.get('contractor_name', 'Sergey Tkach'),
             data.get('client_address', ''), data.get('client_email', ''),
             data.get('client_phone', ''), data.get('project_address', ''))
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': iid, 'invoice_number': inv_num})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/invoices/<iid>', methods=['PUT'])
def api_ahb_invoices_update(iid):
    try:
        data = request.json or {}
        fields = []
        vals = []
        for k in ('client_id', 'project_id', 'line_items', 'subtotal', 'tax',
                   'total', 'status', 'due_date', 'paid_date', 'notes',
                   'date', 'parent_invoice_id', 'is_change_order', 'overdue_since',
                   'overdue_interest_per_week', 'company_name', 'contractor_name',
                   'client_address', 'client_email', 'client_phone', 'project_address'):
            if k in data:
                fields.append(f"{k} = ?")
                v = data[k]
                if k == 'line_items' and isinstance(v, list):
                    v = json.dumps(v)
                vals.append(v)
        if not fields:
            return jsonify({'success': False, 'error': 'No fields to update'}), 400
        fields.append("updated_at = ?")
        vals.append(datetime.datetime.now().isoformat())
        vals.append(iid)
        conn = _ahb_db()
        conn.execute(f"UPDATE ahb_invoices SET {', '.join(fields)} WHERE id = ?", vals)
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Receipts ────────────────────────────────────────────────────────

@app.route('/api/ahb/receipts', methods=['GET'])
def api_ahb_receipts_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_receipts WHERE 1=1"
        params = []
        if request.args.get('project_id'):
            q += " AND project_id = ?"
            params.append(request.args['project_id'])
        if request.args.get('category'):
            q += " AND category = ?"
            params.append(request.args['category'])
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/receipts', methods=['POST'])
def api_ahb_receipts_create():
    try:
        rid = uuid.uuid4().hex[:24]
        file_path = None
        if request.content_type and 'multipart' in request.content_type:
            data = request.form.to_dict()
            f = request.files.get('file')
            if f:
                upload_dir = os.path.join(ARTIFACTS_DIR, 'receipts')
                os.makedirs(upload_dir, exist_ok=True)
                file_path = os.path.join(upload_dir, f"{rid}_{f.filename}")
                f.save(file_path)
        else:
            data = request.json or {}
            file_path = data.get('file_path')
        conn = _ahb_db()
        amount = data.get('amount') or data.get('total') or 0
        conn.execute(
            """INSERT INTO ahb_receipts (id, project_id, vendor, amount, category,
               description, receipt_date, file_path, ocr_text, created_by,
               store_name, payment_method, total)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, data.get('project_id'), data.get('vendor') or data.get('store_name'),
             amount, data.get('category'), data.get('description'), data.get('receipt_date'),
             file_path, data.get('ocr_text'), data.get('created_by'),
             data.get('store_name') or data.get('vendor'), data.get('payment_method'), amount)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': rid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/receipts/<rid>', methods=['GET'])
def api_ahb_receipt_detail(rid):
    try:
        conn = _ahb_db()
        row = conn.execute("SELECT * FROM ahb_receipts WHERE id = ?", (rid,)).fetchone()
        conn.close()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(dict(row))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/receipts/<rid>', methods=['PUT'])
def api_ahb_receipt_update(rid):
    try:
        data = request.json or {}
        conn = _ahb_db()
        fields, vals = [], []
        for k in ['vendor', 'amount', 'category', 'description', 'receipt_date', 'store_name',
                   'payment_method', 'total', 'teller_name', 'store_location', 'purchase_time',
                   'tax_amount', 'subtotal', 'items_json', 'ocr_text', 'ocr_raw', 'ocr_structured',
                   'image_path', 'project_id', 'year']:
            if k in data:
                fields.append(f"{k} = ?"); vals.append(data[k])
        if fields:
            vals.append(rid)
            conn.execute(f"UPDATE ahb_receipts SET {', '.join(fields)} WHERE id = ?", vals)
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/receipts/<rid>/ocr', methods=['POST'])
def api_ahb_receipts_ocr(rid):
    """Run OCR on a receipt image using the receipt_ocr skill."""
    try:
        conn = _ahb_db()
        row = conn.execute("SELECT image_path FROM ahb_receipts WHERE id = ?", (rid,)).fetchone()
        if not row or not row['image_path']:
            conn.close()
            return jsonify({'success': False, 'error': 'No image attached to this receipt'}), 400

        skill_path = os.path.join(os.path.dirname(DASHBOARD_DIR), 'skills', 'shared', 'receipt_ocr.py')
        env = os.environ.copy()
        env['SKILL_ARGS'] = json.dumps({'image_path': row['image_path'], 'mode': 'full'})
        result = subprocess.run([VENV_PYTHON, skill_path], capture_output=True, text=True, timeout=120, env=env)

        if result.returncode != 0:
            conn.close()
            return jsonify({'success': False, 'error': result.stderr.strip() or 'OCR failed'})

        ocr_result = json.loads(result.stdout.strip())
        structured = ocr_result.get('structured', {})

        # Update receipt with OCR data
        update_fields = {
            'ocr_raw': ocr_result.get('ocr_raw', ''),
            'ocr_structured': json.dumps(structured),
            'ocr_text': ocr_result.get('ocr_raw', ''),
        }
        # Only overwrite empty fields with OCR data
        field_map = {
            'store_name': 'store_name', 'store_location': 'store_location',
            'teller_name': 'teller_name', 'purchase_time': 'purchase_time',
            'payment_method': 'payment_method', 'category': 'category',
            'total': 'total', 'tax_amount': 'tax_amount', 'subtotal': 'subtotal',
        }
        current = dict(conn.execute("SELECT * FROM ahb_receipts WHERE id = ?", (rid,)).fetchone())
        for ocr_key, db_key in field_map.items():
            val = structured.get(ocr_key)
            if val and not current.get(db_key):
                update_fields[db_key] = val
        if structured.get('items'):
            update_fields['items_json'] = json.dumps(structured['items'])
        if structured.get('purchase_date') and not current.get('receipt_date'):
            update_fields['receipt_date'] = structured['purchase_date']
        if not current.get('vendor') and structured.get('store_name'):
            update_fields['vendor'] = structured['store_name']
        if not current.get('amount') and structured.get('total'):
            update_fields['amount'] = structured['total']

        set_clause = ', '.join(f"{k} = ?" for k in update_fields)
        vals = list(update_fields.values()) + [rid]
        conn.execute(f"UPDATE ahb_receipts SET {set_clause} WHERE id = ?", vals)
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'ocr': ocr_result, 'updated_fields': list(update_fields.keys())})
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'OCR timed out (120s)'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/receipts/upload', methods=['POST'])
def api_ahb_receipts_upload():
    """Upload a receipt image, save to disk, create receipt record, and trigger OCR."""
    try:
        f = request.files.get('file') or request.files.get('image')
        if not f:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400

        rid = str(uuid.uuid4())
        upload_dir = os.path.join(DASHBOARD_DIR, 'uploads', 'ahb', 'receipts')
        os.makedirs(upload_dir, exist_ok=True)
        safe_name = re.sub(r'[^\w.\-]', '_', f.filename or 'receipt.jpg')
        file_path = os.path.join(upload_dir, f"{rid}_{safe_name}")
        f.save(file_path)

        # Get form data
        data = request.form.to_dict()
        now = datetime.datetime.now()
        year = data.get('year') or now.strftime('%Y')

        conn = _ahb_db()
        conn.execute(
            """INSERT INTO ahb_receipts (id, vendor, amount, category, description,
               receipt_date, store_name, payment_method, total, image_path, year, file_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, data.get('vendor', ''), float(data.get('amount', 0) or 0),
             data.get('category', ''), data.get('description', ''),
             data.get('receipt_date', now.strftime('%Y-%m-%d')),
             data.get('store_name', data.get('vendor', '')),
             data.get('payment_method', ''), float(data.get('amount', 0) or 0),
             file_path, year, file_path))
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'id': rid, 'image_path': file_path,
                        'image_url': f'/api/ahb/receipts/image/{rid}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/receipts/image/<rid>', methods=['GET'])
def api_ahb_receipt_image(rid):
    """Serve a receipt image by receipt ID."""
    try:
        conn = _ahb_db()
        row = conn.execute("SELECT image_path FROM ahb_receipts WHERE id = ?", (rid,)).fetchone()
        conn.close()
        if not row or not row['image_path']:
            return 'No image', 404
        img_path = row['image_path']
        if not os.path.exists(img_path):
            return 'Image file not found', 404
        return send_from_directory(os.path.dirname(img_path), os.path.basename(img_path))
    except Exception as e:
        return str(e), 500


# ── AHB123 — Payroll ─────────────────────────────────────────────────────────

@app.route('/api/ahb/payroll', methods=['GET'])
def api_ahb_payroll_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_payroll WHERE 1=1"
        params = []
        if request.args.get('status'):
            q += " AND status = ?"
            params.append(request.args['status'])
        if request.args.get('worker_name'):
            q += " AND worker_name = ?"
            params.append(request.args['worker_name'])
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/payroll', methods=['POST'])
def api_ahb_payroll_create():
    try:
        data = request.json or {}
        pid = uuid.uuid4().hex[:24]
        hours = float(data.get('hours', 0))
        rate = float(data.get('rate', 0))
        total = hours * rate
        conn = _ahb_db()
        conn.execute(
            """INSERT INTO ahb_payroll (id, worker_name, role, hours, rate, total,
               period_start, period_end, status, project_id, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, data.get('worker_name'), data.get('role'), hours, rate, total,
             data.get('period_start'), data.get('period_end'),
             data.get('status', 'pending'), data.get('project_id'), data.get('notes'))
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': pid, 'total': total})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/payroll/<pid>', methods=['PUT'])
def api_ahb_payroll_update(pid):
    try:
        data = request.json or {}
        fields = []
        vals = []
        for k in ('worker_name', 'role', 'hours', 'rate',
                   'period_start', 'period_end', 'status', 'project_id', 'notes'):
            if k in data:
                fields.append(f"{k} = ?")
                vals.append(data[k])
        # Auto-recalculate total if both hours and rate provided, otherwise allow explicit total
        if 'hours' in data and 'rate' in data:
            fields.append("total = ?")
            vals.append(float(data['hours']) * float(data['rate']))
        elif 'total' in data:
            fields.append("total = ?")
            vals.append(data['total'])
        if not fields:
            return jsonify({'success': False, 'error': 'No fields to update'}), 400
        vals.append(pid)
        conn = _ahb_db()
        conn.execute(f"UPDATE ahb_payroll SET {', '.join(fields)} WHERE id = ?", vals)
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Estimates ───────────────────────────────────────────────────────

@app.route('/api/ahb/estimates', methods=['GET'])
def api_ahb_estimates_list():
    try:
        conn = _ahb_db()
        rows = conn.execute("SELECT * FROM ahb_estimates ORDER BY created_at DESC").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/estimates', methods=['POST'])
def api_ahb_estimates_create():
    try:
        data = request.json or {}
        eid = uuid.uuid4().hex[:24]
        conn = _ahb_db()
        conn.execute(
            """INSERT INTO ahb_estimates (id, client_id, project_id, title, description, scope,
               line_items, subtotal, markup_pct, total, status, generated_by, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, data.get('client_id'), data.get('project_id'), data.get('title'),
             data.get('description'), data.get('scope'),
             json.dumps(data.get('line_items', [])) if isinstance(data.get('line_items'), list) else data.get('line_items'),
             data.get('subtotal'), data.get('markup_pct', 15), data.get('total'),
             data.get('status', 'draft'), data.get('generated_by'), data.get('notes'))
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': eid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/estimates/generate', methods=['POST'])
def api_ahb_estimates_generate():
    return jsonify({'status': 'queued', 'message': 'Estimate generation queued for agent processing'})


# ── AHB123 — Chats ───────────────────────────────────────────────────────────

@app.route('/api/ahb/chats', methods=['GET'])
def api_ahb_chats_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_chats"
        params = []
        if request.args.get('status'):
            q += " WHERE status = ?"
            params.append(request.args['status'])
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/chats/<chat_id>/messages', methods=['GET'])
def api_ahb_chat_messages(chat_id):
    try:
        conn = _ahb_db()
        rows = conn.execute(
            "SELECT * FROM ahb_chat_messages WHERE chat_id = ? ORDER BY created_at ASC",
            (chat_id,)
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/chats/<chat_id>/messages', methods=['POST'])
def api_ahb_chat_message_create(chat_id):
    try:
        data = request.json or {}
        conn = _ahb_db()
        conn.execute(
            "INSERT INTO ahb_chat_messages (chat_id, role, content, agent_id) VALUES (?, ?, ?, ?)",
            (chat_id, data.get('role', 'user'), data.get('content'), data.get('agent_id'))
        )
        conn.execute("UPDATE ahb_chats SET updated_at = ? WHERE id = ?",
                      (datetime.datetime.now().isoformat(), chat_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/chats/history')
def api_ahb_chats_history():
    db = _ahb_db()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    q = request.args.get('q', '')
    offset = (page - 1) * per_page
    if q:
        rows = db.execute("SELECT c.*, (SELECT COUNT(*) FROM ahb_chat_messages m WHERE m.chat_id=c.id) as msg_count FROM ahb_chats c WHERE c.visitor_name LIKE ? OR c.visitor_email LIKE ? ORDER BY c.updated_at DESC LIMIT ? OFFSET ?", (f'%{q}%', f'%{q}%', per_page, offset)).fetchall()
    else:
        rows = db.execute("SELECT c.*, (SELECT COUNT(*) FROM ahb_chat_messages m WHERE m.chat_id=c.id) as msg_count FROM ahb_chats c ORDER BY c.updated_at DESC LIMIT ? OFFSET ?", (per_page, offset)).fetchall()
    total = db.execute("SELECT COUNT(*) FROM ahb_chats").fetchone()[0]
    db.close()
    return jsonify({'chats': [dict(r) for r in rows], 'total': total, 'page': page, 'per_page': per_page})

@app.route('/api/ahb/chats/stats')
def api_ahb_chats_stats():
    db = _ahb_db()
    total = db.execute("SELECT COUNT(*) FROM ahb_chats").fetchone()[0]
    active = db.execute("SELECT COUNT(*) FROM ahb_chats WHERE status='active'").fetchone()[0]
    resolved = db.execute("SELECT COUNT(*) FROM ahb_chats WHERE status='resolved'").fetchone()[0]
    escalated = db.execute("SELECT COUNT(*) FROM ahb_chats WHERE status='escalated'").fetchone()[0]
    total_msgs = db.execute("SELECT COUNT(*) FROM ahb_chat_messages").fetchone()[0]
    db.close()
    return jsonify({'total': total, 'active': active, 'resolved': resolved, 'escalated': escalated, 'total_messages': total_msgs})

@app.route('/api/ahb/chats/<chat_id>/export')
def api_ahb_chats_export(chat_id):
    db = _ahb_db()
    chat = db.execute("SELECT * FROM ahb_chats WHERE id=?", (chat_id,)).fetchone()
    if not chat:
        db.close()
        return jsonify({'error': 'Not found'}), 404
    msgs = db.execute("SELECT * FROM ahb_chat_messages WHERE chat_id=? ORDER BY created_at", (chat_id,)).fetchall()
    db.close()
    lines = [f"Chat Export: {dict(chat).get('visitor_name','Unknown')}", f"Date: {dict(chat).get('created_at','')}", f"Status: {dict(chat).get('status','')}", "---"]
    for m in msgs:
        md = dict(m)
        lines.append(f"[{md.get('created_at','')}] {md.get('role','').upper()}: {md.get('content','')}")
    from flask import Response
    return Response('\n'.join(lines), mimetype='text/plain', headers={'Content-Disposition': f'attachment; filename=chat_{chat_id[:8]}.txt'})

@app.route('/api/ahb/chats/<chat_id>', methods=['PUT'])
def api_ahb_chat_update(chat_id):
    data = request.json or {}
    db = _ahb_db()
    fields = []
    vals = []
    for k in ['status', 'lead_score', 'assigned_agent', 'visitor_name', 'visitor_email', 'visitor_phone']:
        if k in data:
            fields.append(f"{k}=?")
            vals.append(data[k])
    if not fields:
        db.close()
        return jsonify({'success': False, 'error': 'No fields to update'}), 400
    fields.append("updated_at=datetime('now')")
    vals.append(chat_id)
    db.execute(f"UPDATE ahb_chats SET {','.join(fields)} WHERE id=?", vals)
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/ahb/chats/<chat_id>/escalate', methods=['POST'])
def api_ahb_chat_escalate(chat_id):
    db = _ahb_db()
    db.execute("UPDATE ahb_chats SET status='escalated', assigned_agent='simon_bately', updated_at=datetime('now') WHERE id=?", (chat_id,))
    db.commit()
    db.close()
    token = os.environ.get('TELEGRAM_SIMON_BATELY', '')
    if token:
        db2 = _ahb_db()
        chat = db2.execute("SELECT * FROM ahb_chats WHERE id=?", (chat_id,)).fetchone()
        db2.close()
        if chat:
            cd = dict(chat)
            try:
                import requests as _req
                resp = _req.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"limit": 5}, timeout=5)
                updates = resp.json().get('result', [])
                tg_chat = None
                for u in reversed(updates):
                    msg = u.get('message', {})
                    if msg.get('chat', {}).get('type') == 'private':
                        tg_chat = msg['chat']['id']
                        break
                if tg_chat:
                    _req.post(f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": tg_chat, "text": f"\U0001f6a8 Chat Escalated\nVisitor: {cd.get('visitor_name','Unknown')}\nEmail: {cd.get('visitor_email','')}\nLead Score: {cd.get('lead_score','')}"}, timeout=5)
            except Exception:
                pass
    return jsonify({'success': True})


# ── AHB123 — Voice ───────────────────────────────────────────────────────────

@app.route('/api/ahb/voice/voices', methods=['GET'])
def api_ahb_voice_list_voices():
    try:
        result = subprocess.run(
            [VENV_PYTHON, '-m', 'edge_tts', '--list-voices'], capture_output=True, text=True, timeout=15
        )
        voices = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith('Name') or line.startswith('---'):
                continue
            parts = line.split()
            if len(parts) >= 2:
                name = parts[0]
                gender = parts[1] if len(parts) > 1 else ''
                locale = name.rsplit('-', 1)[0] if '-' in name else ''
                voices.append({'name': name, 'gender': gender, 'locale': locale})
        return jsonify(voices)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/voice/configs', methods=['GET'])
def api_ahb_voice_configs_list():
    try:
        conn = _ahb_db()
        rows = conn.execute("SELECT * FROM ahb_voice_configs ORDER BY created_at DESC").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/voice/configs', methods=['POST'])
def api_ahb_voice_configs_create():
    try:
        data = request.json or {}
        vid = uuid.uuid4().hex[:24]
        conn = _ahb_db()
        conn.execute(
            """INSERT INTO ahb_voice_configs (id, name, voice, rate, pitch, volume, style,
               pauses_enabled, filler_words, breathing_sounds, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (vid, data.get('name'), data.get('voice', 'en-US-GuyNeural'),
             str(data.get('rate', '+0%')), str(data.get('pitch', '+0Hz')),
             str(data.get('volume', '+0%')), data.get('style', 'friendly'),
             1 if data.get('pauses_enabled') or data.get('natural_pauses') else 0,
             1 if data.get('filler_words') else 0,
             1 if data.get('breathing_sounds') or data.get('breathing') else 0,
             data.get('notes', ''))
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': vid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/voice/configs/<vid>', methods=['DELETE'])
def api_ahb_voice_configs_delete(vid):
    try:
        conn = _ahb_db()
        conn.execute("DELETE FROM ahb_voice_configs WHERE id = ?", (vid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/voice/synthesize', methods=['POST'])
def api_ahb_voice_synthesize():
    try:
        data = request.json or {}
        text = data.get('text', '')
        if not text:
            return jsonify({'success': False, 'error': 'text is required'}), 400
        voice = str(data.get('voice', 'en-US-GuyNeural'))
        # Normalize rate/pitch — accept numbers or strings
        raw_rate = data.get('rate', '+0%')
        raw_pitch = data.get('pitch', '+0Hz')
        raw_volume = data.get('volume', '+0%')
        # If numeric, format with % / Hz suffix
        if isinstance(raw_rate, (int, float)):
            rate = f"+{int(raw_rate)}%" if raw_rate >= 0 else f"{int(raw_rate)}%"
        else:
            rate = str(raw_rate) if raw_rate else '+0%'
        if isinstance(raw_pitch, (int, float)):
            pitch = f"+{int(raw_pitch)}Hz" if raw_pitch >= 0 else f"{int(raw_pitch)}Hz"
        else:
            pitch = str(raw_pitch) if raw_pitch else '+0Hz'
        if isinstance(raw_volume, (int, float)):
            volume = f"+{int(raw_volume)}%" if raw_volume >= 0 else f"{int(raw_volume)}%"
        else:
            volume = str(raw_volume) if raw_volume else '+0%'
        output_dir = os.path.join(DASHBOARD_DIR, 'artifacts', 'voice')
        os.makedirs(output_dir, exist_ok=True)
        filename = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        output = os.path.join(output_dir, filename)
        cmd = [VENV_PYTHON, '-m', 'edge_tts', f'--voice={voice}', f'--rate={rate}',
               f'--pitch={pitch}', f'--volume={volume}', f'--text={text}', f'--write-media={output}']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return jsonify({'success': False, 'error': result.stderr.strip() or 'TTS failed'}), 500
        return jsonify({'success': True, 'audio_url': f'/api/ahb/voice/audio/{filename}',
                        'voice': voice, 'rate': rate, 'pitch': pitch})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/voice/audio/<filename>', methods=['GET'])
def api_ahb_voice_audio(filename):
    voice_dir = os.path.join(DASHBOARD_DIR, 'artifacts', 'voice')
    return send_from_directory(voice_dir, filename, mimetype='audio/mpeg')


@app.route('/api/ahb/voice/logs')
def api_ahb_voice_logs():
    db = _ahb_db()
    status_filter = request.args.get('status', '')
    direction = request.args.get('direction', '')
    q = "SELECT * FROM ahb_voice_logs WHERE 1=1"
    params = []
    if status_filter:
        q += " AND status=?"
        params.append(status_filter)
    if direction:
        q += " AND direction=?"
        params.append(direction)
    q += " ORDER BY created_at DESC LIMIT 100"
    rows = db.execute(q, params).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/ahb/voice/logs', methods=['POST'])
def api_ahb_voice_log_create():
    data = request.json or {}
    db = _ahb_db()
    db.execute("INSERT INTO ahb_voice_logs (caller_name, caller_phone, direction, duration_seconds, transcript, audio_file, status, agent_notes) VALUES (?,?,?,?,?,?,?,?)",
        (data.get('caller_name',''), data.get('caller_phone',''), data.get('direction','inbound'),
         data.get('duration_seconds',0), data.get('transcript',''), data.get('audio_file',''),
         data.get('status','completed'), data.get('agent_notes','')))
    db.commit()
    log_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return jsonify({'success': True, 'id': log_id})

@app.route('/api/ahb/voice/logs/<int:log_id>')
def api_ahb_voice_log_detail(log_id):
    db = _ahb_db()
    row = db.execute("SELECT * FROM ahb_voice_logs WHERE id=?", (log_id,)).fetchone()
    db.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))

@app.route('/api/ahb/voice/stats')
def api_ahb_voice_stats():
    db = _ahb_db()
    total = db.execute("SELECT COUNT(*) FROM ahb_voice_logs").fetchone()[0]
    inbound = db.execute("SELECT COUNT(*) FROM ahb_voice_logs WHERE direction='inbound'").fetchone()[0]
    outbound = db.execute("SELECT COUNT(*) FROM ahb_voice_logs WHERE direction='outbound'").fetchone()[0]
    missed = db.execute("SELECT COUNT(*) FROM ahb_voice_logs WHERE status='missed'").fetchone()[0]
    leads = db.execute("SELECT COUNT(*) FROM ahb_voice_logs WHERE lead_created=1").fetchone()[0]
    avg_dur = db.execute("SELECT AVG(duration_seconds) FROM ahb_voice_logs WHERE duration_seconds>0").fetchone()[0] or 0
    db.close()
    return jsonify({'total': total, 'inbound': inbound, 'outbound': outbound, 'missed': missed, 'leads_generated': leads, 'avg_duration': round(avg_dur)})


# ── AHB123 — ArchiteCT ───────────────────────────────────────────────────────

@app.route('/api/ahb/architect/analyze', methods=['POST'])
def api_ahb_architect_analyze():
    try:
        f = request.files.get('image') or request.files.get('file')
        if not f:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        upload_dir = os.path.join(ARTIFACTS_DIR, 'proj-ahb123')
        os.makedirs(upload_dir, exist_ok=True)
        safe_name = re.sub(r'[^\w.\-]', '_', f.filename or 'upload.jpg')
        file_path = os.path.join(upload_dir, f"architect_{uuid.uuid4().hex[:8]}_{safe_name}")
        f.save(file_path)
        # Write .meta sidecar so artifact scanner knows the agent
        try:
            with open(file_path + '.meta', 'w') as mf:
                json.dump({'agent_id': 'sam_axe', 'task_id': '', 'created_at': datetime.datetime.now().isoformat()}, mf)
        except Exception:
            pass
        # Run cloud vision analysis via the analyze_image skill
        prompt = request.form.get('prompt',
            'Describe this image in exhaustive detail. Identify and label every object, surface, material, '
            'color, texture, and spatial relationship. Include estimated dimensions, placement positions, '
            'lighting, camera angle, condition of all elements, and any text or markings visible. '
            'The description must be detailed enough for an image generation model to recreate this image '
            'and for agents to understand every element in the scene.')
        mode = request.form.get('mode', 'describe_for_agents')
        skill_path = os.path.join(os.path.dirname(DASHBOARD_DIR), 'skills', 'shared', 'analyze_image.py')
        env = os.environ.copy()
        env['SKILL_ARGS'] = json.dumps({'image_path': file_path, 'prompt': prompt, 'mode': mode})
        result = subprocess.run([VENV_PYTHON, skill_path], capture_output=True, text=True, timeout=120, env=env)
        if result.returncode != 0:
            return jsonify({'success': False, 'error': result.stderr.strip() or 'Analysis failed', 'file_path': file_path})
        # Parse the output — skill prints text then JSON on last line
        output = result.stdout.strip()
        analysis = output
        try:
            # Try to extract JSON from last line
            lines = output.split('\n')
            for line in reversed(lines):
                if line.strip().startswith('{'):
                    parsed = json.loads(line.strip())
                    analysis = parsed.get('analysis', output)
                    break
        except Exception:
            pass
        return jsonify({'success': True, 'analysis': analysis, 'file_path': file_path})
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Analysis timed out (60s). Is LiteLLM running?'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/architect/generate', methods=['POST'])
def api_ahb_architect_generate():
    try:
        import requests as _req
        import base64
        data = request.json or {}
        resp = _req.post('http://localhost:7860/sdapi/v1/txt2img', json={
            'prompt': data['prompt'],
            'width': data.get('width', 1024),
            'height': data.get('height', 1024),
            'steps': data.get('steps', 30),
            'cfg_scale': data.get('cfg_scale', 7),
            'sampler_name': 'DPM++ 2M Karras',
        }, timeout=120)
        result = resp.json()
        images = result.get('images', [])
        saved = []
        out_dir = os.path.join(ARTIFACTS_DIR, 'proj-ahb123')
        os.makedirs(out_dir, exist_ok=True)
        for i, img_b64 in enumerate(images):
            fname = f"sam_axe_gen_{uuid.uuid4().hex[:8]}.png"
            fpath = os.path.join(out_dir, fname)
            with open(fpath, 'wb') as fp:
                fp.write(base64.b64decode(img_b64))
            # Write .meta sidecar
            try:
                with open(fpath + '.meta', 'w') as mf:
                    json.dump({'agent_id': 'sam_axe', 'task_id': '', 'created_at': datetime.datetime.now().isoformat()}, mf)
            except Exception:
                pass
            saved.append(f'/api/artifacts/serve/proj-ahb123/{fname}')
        image_url = saved[0] if saved else ''
        return jsonify({'success': True, 'images': saved, 'image_url': image_url})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/architect/transform', methods=['POST'])
@app.route('/api/ahb/architect/img2img', methods=['POST'])
def api_ahb_architect_img2img():
    try:
        import requests as _req
        import base64
        f = request.files.get('file') or request.files.get('image')
        if not f:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        img_b64 = base64.b64encode(f.read()).decode('utf-8')
        prompt = request.form.get('prompt', '')

        # Save original for before/after display
        out_dir = os.path.join(ARTIFACTS_DIR, 'proj-ahb123')
        os.makedirs(out_dir, exist_ok=True)
        orig_fname = f"sam_axe_original_{uuid.uuid4().hex[:8]}.png"
        orig_fpath = os.path.join(out_dir, orig_fname)
        with open(orig_fpath, 'wb') as fp:
            fp.write(base64.b64decode(img_b64))
        original_url = f'/api/artifacts/serve/proj-ahb123/{orig_fname}'

        # Build SD payload
        payload = {
            'init_images': [img_b64],
            'prompt': prompt,
            'width': int(request.form.get('width', 1024)),
            'height': int(request.form.get('height', 1024)),
            'steps': int(request.form.get('steps', 30)),
            'cfg_scale': float(request.form.get('cfg_scale', 7)),
            'denoising_strength': float(request.form.get('denoising_strength', 0.5)),
            'sampler_name': 'DPM++ 2M Karras',
        }

        # If a mask file is provided, use inpainting mode
        mask_file = request.files.get('mask')
        if mask_file:
            mask_b64 = base64.b64encode(mask_file.read()).decode('utf-8')
            payload['mask'] = mask_b64
            payload['inpainting_fill'] = int(request.form.get('inpaint_fill', 1))
            payload['inpaint_full_res'] = True
            payload['inpaint_full_res_padding'] = 32

        resp = _req.post('http://localhost:7860/sdapi/v1/img2img', json=payload, timeout=180)
        result = resp.json()
        images = result.get('images', [])
        saved = []
        for i, ib64 in enumerate(images):
            prefix = "sam_axe_inpaint_" if mask_file else "sam_axe_img2img_"
            fname = f"{prefix}{uuid.uuid4().hex[:8]}.png"
            fpath = os.path.join(out_dir, fname)
            with open(fpath, 'wb') as fp:
                fp.write(base64.b64decode(ib64))
            try:
                with open(fpath + '.meta', 'w') as mf:
                    json.dump({'agent_id': 'sam_axe', 'task_id': '', 'created_at': datetime.datetime.now().isoformat()}, mf)
            except Exception:
                pass
            saved.append(f'/api/artifacts/serve/proj-ahb123/{fname}')
        image_url = saved[0] if saved else ''
        return jsonify({'success': True, 'images': saved, 'image_url': image_url, 'original_url': original_url})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/architect/images/<filename>', methods=['GET'])
def api_ahb_architect_images(filename):
    """Serve architect images — check proj-ahb123 first, fall back to architect dir."""
    proj_dir = os.path.join(ARTIFACTS_DIR, 'proj-ahb123')
    if os.path.exists(os.path.join(proj_dir, filename)):
        return send_from_directory(proj_dir, filename)
    arch_dir = os.path.join(ARTIFACTS_DIR, 'architect')
    return send_from_directory(arch_dir, filename)


# ── AHB123 — Employees ─────────────────────────────────────────────────────────

@app.route('/api/ahb/employees', methods=['GET'])
def api_ahb_employees_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_employees WHERE 1=1"
        params = []
        if request.args.get('active'):
            q += " AND active = ?"; params.append(int(request.args['active']))
        rows = conn.execute(q + " ORDER BY name", params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ahb/employees', methods=['POST'])
def api_ahb_employees_create():
    try:
        data = request.json or {}
        conn = _ahb_db()
        eid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO ahb_employees (id, name, position, hourly_rate, pay_type, pay_method, phone, email, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, data.get('name',''), data.get('position',''), data.get('hourly_rate',0),
             data.get('pay_type','hourly'), data.get('pay_method',''), data.get('phone',''),
             data.get('email',''), 1 if data.get('active', True) else 0))
        conn.commit(); conn.close()
        return jsonify({'success': True, 'id': eid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/employees/<eid>', methods=['PUT'])
def api_ahb_employees_update(eid):
    try:
        data = request.json or {}
        conn = _ahb_db()
        fields, vals = [], []
        for k in ['name','position','hourly_rate','pay_type','pay_method','phone','email']:
            if k in data: fields.append(f"{k} = ?"); vals.append(data[k])
        if 'active' in data: fields.append("active = ?"); vals.append(1 if data['active'] else 0)
        if fields:
            fields.append("updated_at = datetime('now')")
            vals.append(eid)
            conn.execute(f"UPDATE ahb_employees SET {', '.join(fields)} WHERE id = ?", vals)
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/employees/<eid>', methods=['DELETE'])
def api_ahb_employees_delete(eid):
    try:
        conn = _ahb_db()
        conn.execute("UPDATE ahb_employees SET active = 0, updated_at = datetime('now') WHERE id = ?", (eid,))
        conn.commit(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Events/Schedule ───────────────────────────────────────────────────

@app.route('/api/ahb/events', methods=['GET'])
def api_ahb_events_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_events WHERE 1=1"
        params = []
        if request.args.get('category'):
            q += " AND category = ?"; params.append(request.args['category'])
        if request.args.get('date_from'):
            q += " AND date >= ?"; params.append(request.args['date_from'])
        if request.args.get('date_to'):
            q += " AND date <= ?"; params.append(request.args['date_to'])
        rows = conn.execute(q + " ORDER BY date, time", params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ahb/events', methods=['POST'])
def api_ahb_events_create():
    try:
        data = request.json or {}
        conn = _ahb_db()
        eid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO ahb_events (id, title, details, date, time, end_time, category, all_day, project_id, employee_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, data.get('title',''), data.get('details',''), data.get('date',''),
             data.get('time',''), data.get('end_time',''), data.get('category',''),
             1 if data.get('all_day') else 0, data.get('project_id',''), data.get('employee_id','')))
        conn.commit(); conn.close()
        return jsonify({'success': True, 'id': eid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/events/<eid>', methods=['PUT'])
def api_ahb_events_update(eid):
    try:
        data = request.json or {}
        conn = _ahb_db()
        fields, vals = [], []
        for k in ['title','details','date','time','end_time','category','project_id','employee_id']:
            if k in data: fields.append(f"{k} = ?"); vals.append(data[k])
        if 'all_day' in data: fields.append("all_day = ?"); vals.append(1 if data['all_day'] else 0)
        if fields:
            vals.append(eid)
            conn.execute(f"UPDATE ahb_events SET {', '.join(fields)} WHERE id = ?", vals)
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/events/<eid>', methods=['DELETE'])
def api_ahb_events_delete(eid):
    try:
        conn = _ahb_db()
        conn.execute("DELETE FROM ahb_events WHERE id = ?", (eid,))
        conn.commit(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/projects/<pid>/calendar', methods=['POST'])
def api_ahb_project_calendar_backfill(pid):
    """Backfill calendar events from project and phase dates. For importing past projects."""
    try:
        data = request.json or {}
        conn = _ahb_db()
        project = conn.execute("SELECT * FROM ahb_projects WHERE id = ?", (pid,)).fetchone()
        if not project:
            conn.close()
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        project = dict(project)

        title = data.get('title') or project.get('title', 'Project')
        start_date = data.get('start_date') or project.get('start_date')
        end_date = data.get('end_date') or project.get('end_date')
        events_created = []

        # Project start event
        if start_date:
            eid = uuid.uuid4().hex[:24]
            conn.execute(
                """INSERT INTO ahb_events (id, title, details, date, category, all_day, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (eid, f"{title} - Start", f"Project start: {title}", start_date, 'project', 1, pid))
            events_created.append({'id': eid, 'type': 'project_start', 'date': start_date})

        # Project end event
        if end_date:
            eid = uuid.uuid4().hex[:24]
            conn.execute(
                """INSERT INTO ahb_events (id, title, details, date, category, all_day, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (eid, f"{title} - End", f"Project end: {title}", end_date, 'project', 1, pid))
            events_created.append({'id': eid, 'type': 'project_end', 'date': end_date})

        # Phase start/end events
        phases = conn.execute(
            "SELECT * FROM ahb_project_phases WHERE project_id = ? ORDER BY phase_number", (pid,)
        ).fetchall()
        for ph in phases:
            ph = dict(ph)
            ph_name = ph.get('name', f"Phase {ph.get('phase_number', '?')}")
            if ph.get('start_date'):
                eid = uuid.uuid4().hex[:24]
                conn.execute(
                    """INSERT INTO ahb_events (id, title, details, date, category, all_day, project_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (eid, f"{ph_name} - Start", f"Phase start for {title}", ph['start_date'], 'phase', 1, pid))
                events_created.append({'id': eid, 'type': 'phase_start', 'phase': ph_name, 'date': ph['start_date']})
            if ph.get('end_date'):
                eid = uuid.uuid4().hex[:24]
                conn.execute(
                    """INSERT INTO ahb_events (id, title, details, date, category, all_day, project_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (eid, f"{ph_name} - End", f"Phase end for {title}", ph['end_date'], 'phase', 1, pid))
                events_created.append({'id': eid, 'type': 'phase_end', 'phase': ph_name, 'date': ph['end_date']})

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'events_created': len(events_created), 'events': events_created})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Notes ─────────────────────────────────────────────────────────────

@app.route('/api/ahb/notes', methods=['GET'])
def api_ahb_notes_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_notes WHERE 1=1"
        params = []
        if request.args.get('is_task'):
            q += " AND is_task = 1"
        if request.args.get('project_id'):
            q += " AND project_id = ?"; params.append(request.args['project_id'])
        rows = conn.execute(q + " ORDER BY pinned DESC, created_at DESC", params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ahb/notes', methods=['POST'])
def api_ahb_notes_create():
    try:
        data = request.json or {}
        conn = _ahb_db()
        nid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO ahb_notes (id, title, content, is_list, is_task, tags, pinned, project_id, due_date, checklist_items, author_employee_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (nid, data.get('title',''), data.get('content',''),
             1 if data.get('is_list') else 0, 1 if data.get('is_task') else 0,
             data.get('tags',''), 1 if data.get('pinned') else 0,
             data.get('project_id',''), data.get('due_date',''),
             json.dumps(data.get('checklist_items',[])) if isinstance(data.get('checklist_items'), list) else data.get('checklist_items','[]'),
             data.get('author_employee_id','')))
        conn.commit(); conn.close()
        return jsonify({'success': True, 'id': nid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/notes/<nid>', methods=['PUT'])
def api_ahb_notes_update(nid):
    try:
        data = request.json or {}
        conn = _ahb_db()
        fields, vals = [], []
        for k in ['title','content','tags','project_id','due_date','author_employee_id']:
            if k in data: fields.append(f"{k} = ?"); vals.append(data[k])
        for k in ['is_list','is_task','pinned']:
            if k in data: fields.append(f"{k} = ?"); vals.append(1 if data[k] else 0)
        if 'checklist_items' in data:
            fields.append("checklist_items = ?")
            vals.append(json.dumps(data['checklist_items']) if isinstance(data['checklist_items'], list) else data['checklist_items'])
        if fields:
            fields.append("updated_at = datetime('now')")
            vals.append(nid)
            conn.execute(f"UPDATE ahb_notes SET {', '.join(fields)} WHERE id = ?", vals)
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/notes/<nid>', methods=['DELETE'])
def api_ahb_notes_delete(nid):
    try:
        conn = _ahb_db()
        conn.execute("DELETE FROM ahb_notes WHERE id = ?", (nid,))
        conn.commit(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Debts (IPAY) ──────────────────────────────────────────────────────

@app.route('/api/ahb/debts', methods=['GET'])
def api_ahb_debts_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_debts WHERE 1=1"
        params = []
        if request.args.get('type'):
            q += " AND type = ?"; params.append(request.args['type'])
        rows = conn.execute(q + " ORDER BY due_date", params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ahb/debts', methods=['POST'])
def api_ahb_debts_create():
    try:
        data = request.json or {}
        conn = _ahb_db()
        did = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO ahb_debts (id, name, type, frequency, payment_amount, due_date, payoff_date, balance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (did, data.get('name',''), data.get('type','Bill'), data.get('frequency','Monthly'),
             data.get('payment_amount',0), data.get('due_date',''), data.get('payoff_date',''),
             data.get('balance',0)))
        conn.commit(); conn.close()
        return jsonify({'success': True, 'id': did})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/debts/<did>', methods=['PUT'])
def api_ahb_debts_update(did):
    try:
        data = request.json or {}
        conn = _ahb_db()
        fields, vals = [], []
        for k in ['name','type','frequency','payment_amount','due_date','payoff_date','balance']:
            if k in data: fields.append(f"{k} = ?"); vals.append(data[k])
        if fields:
            fields.append("updated_at = datetime('now')")
            vals.append(did)
            conn.execute(f"UPDATE ahb_debts SET {', '.join(fields)} WHERE id = ?", vals)
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/debts/<did>', methods=['DELETE'])
def api_ahb_debts_delete(did):
    try:
        conn = _ahb_db()
        conn.execute("DELETE FROM ahb_debts WHERE id = ?", (did,))
        conn.commit(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Files ─────────────────────────────────────────────────────────────

@app.route('/api/ahb/files', methods=['GET'])
def api_ahb_files_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_files WHERE 1=1"
        params = []
        if request.args.get('category'):
            q += " AND category = ?"; params.append(request.args['category'])
        if request.args.get('project_id'):
            q += " AND project_id = ?"; params.append(request.args['project_id'])
        if request.args.get('year'):
            q += " AND year = ?"; params.append(request.args['year'])
        rows = conn.execute(q + " ORDER BY created_at DESC", params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ahb/files', methods=['POST'])
def api_ahb_files_create():
    try:
        conn = _ahb_db()
        fid = str(uuid.uuid4())
        f = request.files.get('file')
        file_path = ''
        if f:
            upload_dir = os.path.join(DASHBOARD_DIR, 'uploads', 'ahb', 'files')
            os.makedirs(upload_dir, exist_ok=True)
            safe_name = re.sub(r'[^\w.\-]', '_', f.filename or 'file')
            file_path = os.path.join(upload_dir, f"{fid}_{safe_name}")
            f.save(file_path)
        data = request.form if f else (request.json or {})
        conn.execute(
            """INSERT INTO ahb_files (id, name, file_type, file_path, size, tags, category, year, project_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fid, data.get('name', f.filename if f else ''), data.get('file_type',''),
             file_path, data.get('size',0), data.get('tags',''), data.get('category',''),
             data.get('year',''), data.get('project_id','')))
        conn.commit(); conn.close()
        return jsonify({'success': True, 'id': fid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/files/<fid>', methods=['DELETE'])
def api_ahb_files_delete(fid):
    try:
        conn = _ahb_db()
        conn.execute("DELETE FROM ahb_files WHERE id = ?", (fid,))
        conn.commit(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/projects/<pid>/files', methods=['POST'])
def api_ahb_project_files_upload(pid):
    """Upload photos or documents to a project. Supports photo_section and document_type."""
    try:
        f = request.files.get('file')
        if not f:
            return jsonify({'success': False, 'error': 'No file provided'}), 400

        upload_dir = os.path.join(DASHBOARD_DIR, 'uploads', 'ahb', 'projects', pid)
        os.makedirs(upload_dir, exist_ok=True)
        fid = uuid.uuid4().hex[:24]
        safe_name = re.sub(r'[^\w.\-]', '_', f.filename or 'file')
        file_path = os.path.join(upload_dir, f"{fid}_{safe_name}")
        f.save(file_path)

        data = request.form
        photo_section = data.get('photo_section', '')  # before / during / after
        document_type = data.get('document_type', '')   # Permit, Contract, COI, etc.

        # Determine file_type and category from inputs
        ext = os.path.splitext(safe_name)[1].lower()
        is_image = ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic')
        file_type = 'photo' if is_image else 'document'
        category = document_type if document_type else ('photo' if is_image else 'document')

        conn = _ahb_db()
        conn.execute(
            """INSERT INTO ahb_files (id, name, file_type, file_path, size, tags, category, year, project_id, photo_section, document_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fid, f.filename or safe_name, file_type, file_path,
             os.path.getsize(file_path), data.get('tags', ''), category,
             str(datetime.datetime.now().year), pid, photo_section, document_type))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': fid, 'file_path': file_path})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Project Phases ────────────────────────────────────────────────────

def _rebuild_invoice_line_items(conn, project_id):
    """Rebuild the linked invoice's line_items JSON from all project phases."""
    phases = conn.execute(
        "SELECT * FROM ahb_project_phases WHERE project_id = ? ORDER BY phase_number",
        (project_id,)
    ).fetchall()
    line_items = []
    subtotal = 0
    for ph in phases:
        ph = dict(ph)
        val = ph.get('value', 0) or 0
        line_items.append({'description': ph.get('name', 'Phase'), 'quantity': 1, 'unit_price': val})
        subtotal += val
    # Update first linked invoice
    inv = conn.execute(
        "SELECT id FROM ahb_invoices WHERE project_id = ? ORDER BY created_at ASC LIMIT 1",
        (project_id,)
    ).fetchone()
    if inv:
        conn.execute(
            "UPDATE ahb_invoices SET line_items = ?, subtotal = ?, total = ?, updated_at = ? WHERE id = ?",
            (json.dumps(line_items), subtotal, subtotal, datetime.datetime.now().isoformat(), inv['id']))
    return inv['id'] if inv else None

@app.route('/api/ahb/phases', methods=['GET'])
def api_ahb_phases_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_project_phases WHERE 1=1"
        params = []
        if request.args.get('project_id'):
            q += " AND project_id = ?"; params.append(request.args['project_id'])
        rows = conn.execute(q + " ORDER BY phase_number", params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ahb/projects/<pid>/phases', methods=['POST'])
def api_ahb_project_phases_create(pid):
    """Add a phase to a project and sync to linked invoice line items."""
    try:
        data = request.json or {}
        conn = _ahb_db()
        phid = uuid.uuid4().hex[:24]
        conn.execute(
            """INSERT INTO ahb_project_phases (id, project_id, phase_number, name, value, start_date, end_date, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (phid, pid, data.get('phase_number', 1), data.get('name', ''),
             data.get('value', 0), data.get('start_date', ''), data.get('end_date', ''),
             data.get('status', 'pending')))
        conn.commit()
        _rebuild_invoice_line_items(conn, pid)
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': phid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/phases', methods=['POST'])
def api_ahb_phases_create():
    """Legacy: add a phase (also syncs invoice)."""
    try:
        data = request.json or {}
        conn = _ahb_db()
        phid = uuid.uuid4().hex[:24]
        project_id = data.get('project_id', '')
        conn.execute(
            """INSERT INTO ahb_project_phases (id, project_id, phase_number, name, value, start_date, end_date, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (phid, project_id, data.get('phase_number', 1), data.get('name', ''),
             data.get('value', 0), data.get('start_date', ''), data.get('end_date', ''),
             data.get('status', 'pending')))
        conn.commit()
        if project_id:
            _rebuild_invoice_line_items(conn, project_id)
            conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': phid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/phases/<phid>', methods=['PUT'])
def api_ahb_phases_update(phid):
    """Update a phase and sync to linked invoice line items."""
    try:
        data = request.json or {}
        conn = _ahb_db()
        fields, vals = [], []
        for k in ['project_id', 'phase_number', 'name', 'value', 'start_date', 'end_date', 'status']:
            if k in data: fields.append(f"{k} = ?"); vals.append(data[k])
        if fields:
            vals.append(phid)
            conn.execute(f"UPDATE ahb_project_phases SET {', '.join(fields)} WHERE id = ?", vals)
            conn.commit()
        # Get project_id to rebuild invoice
        phase = conn.execute("SELECT project_id FROM ahb_project_phases WHERE id = ?", (phid,)).fetchone()
        if phase and phase['project_id']:
            _rebuild_invoice_line_items(conn, phase['project_id'])
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/phases/<phid>', methods=['DELETE'])
def api_ahb_phases_delete(phid):
    """Delete a phase and rebuild the linked invoice line items."""
    try:
        conn = _ahb_db()
        phase = conn.execute("SELECT project_id FROM ahb_project_phases WHERE id = ?", (phid,)).fetchone()
        project_id = phase['project_id'] if phase else None
        conn.execute("DELETE FROM ahb_project_phases WHERE id = ?", (phid,))
        conn.execute("DELETE FROM ahb_phase_tasks WHERE phase_id = ?", (phid,))
        conn.commit()
        if project_id:
            _rebuild_invoice_line_items(conn, project_id)
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/phases/<phid>/tasks', methods=['GET'])
def api_ahb_phase_tasks_list(phid):
    """List tasks for a phase."""
    try:
        conn = _ahb_db()
        rows = conn.execute("SELECT * FROM ahb_phase_tasks WHERE phase_id = ? ORDER BY created_at", (phid,)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ahb/phases/<phid>/tasks', methods=['POST'])
def api_ahb_phase_tasks_create(phid):
    """Add a task to a phase."""
    try:
        data = request.json or {}
        conn = _ahb_db()
        tid = uuid.uuid4().hex[:24]
        # Get project_id from phase
        phase = conn.execute("SELECT project_id FROM ahb_project_phases WHERE id = ?", (phid,)).fetchone()
        project_id = phase['project_id'] if phase else ''
        conn.execute(
            """INSERT INTO ahb_phase_tasks (id, phase_id, project_id, title, status, assigned_to, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tid, phid, project_id, data.get('title', ''), data.get('status', 'pending'),
             data.get('assigned_to', ''), data.get('notes', '')))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': tid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Tax Requirements ──────────────────────────────────────────────────

@app.route('/api/ahb/tax', methods=['GET'])
def api_ahb_tax_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_tax_requirements WHERE 1=1"
        params = []
        if request.args.get('category'):
            q += " AND category = ?"; params.append(request.args['category'])
        if request.args.get('completed') is not None:
            q += " AND completed = ?"; params.append(int(request.args['completed']))
        rows = conn.execute(q + " ORDER BY due_date", params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ahb/tax', methods=['POST'])
def api_ahb_tax_create():
    try:
        data = request.json or {}
        conn = _ahb_db()
        tid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO ahb_tax_requirements (id, title, details, due_date, completed, category)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tid, data.get('title',''), data.get('details',''), data.get('due_date',''),
             1 if data.get('completed') else 0, data.get('category','tax')))
        conn.commit(); conn.close()
        return jsonify({'success': True, 'id': tid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/tax/<tid>', methods=['PUT'])
def api_ahb_tax_update(tid):
    try:
        data = request.json or {}
        conn = _ahb_db()
        fields, vals = [], []
        for k in ['title','details','due_date','category']:
            if k in data: fields.append(f"{k} = ?"); vals.append(data[k])
        if 'completed' in data: fields.append("completed = ?"); vals.append(1 if data['completed'] else 0)
        if fields:
            vals.append(tid)
            conn.execute(f"UPDATE ahb_tax_requirements SET {', '.join(fields)} WHERE id = ?", vals)
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ahb/tax/<tid>', methods=['DELETE'])
def api_ahb_tax_delete(tid):
    try:
        conn = _ahb_db()
        conn.execute("DELETE FROM ahb_tax_requirements WHERE id = ?", (tid,))
        conn.commit(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Invoice from Project ───────────────────────────────────────────────

@app.route('/api/ahb/invoices/from-project/<pid>', methods=['POST'])
def api_ahb_invoice_from_project(pid):
    """Generate an invoice from a project's phases and details."""
    try:
        conn = _ahb_db()
        project = conn.execute("SELECT * FROM ahb_projects WHERE id = ?", (pid,)).fetchone()
        if not project:
            conn.close()
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        project = dict(project)
        # Get phases as line items
        phases = conn.execute(
            "SELECT * FROM ahb_project_phases WHERE project_id = ? ORDER BY phase_number",
            (pid,)
        ).fetchall()
        line_items = []
        subtotal = 0
        if phases:
            for ph in phases:
                ph = dict(ph)
                item = {
                    'description': ph.get('name', 'Phase'),
                    'quantity': 1,
                    'unit_price': ph.get('value', 0) or 0
                }
                line_items.append(item)
                subtotal += item['unit_price']
        else:
            # Fallback: single line item from project value
            val = project.get('value') or project.get('budget_high') or 0
            line_items = [{'description': project.get('title', 'Project'), 'quantity': 1, 'unit_price': val}]
            subtotal = val

        inv_num = f"AHB-{datetime.datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:3].upper()}"
        iid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO ahb_invoices (id, client_id, project_id, invoice_number, line_items,
               subtotal, tax, total, status, notes, client_name, project_name, terms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (iid, project.get('client_id', ''), pid, inv_num,
             json.dumps(line_items), subtotal, 0, subtotal, 'draft',
             f"Generated from project: {project.get('title', '')}",
             project.get('client_name', ''), project.get('title', ''),
             'Net 30'))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': iid, 'invoice_number': inv_num,
                        'line_items': line_items, 'subtotal': subtotal})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Dashboard Stats ───────────────────────────────────────────────────

@app.route('/api/ahb/dashboard', methods=['GET'])
def api_ahb_dashboard():
    """Return aggregated stats for the overview dashboard. Supports ?year=2026 filter."""
    try:
        conn = _ahb_db()
        stats = {}
        year = request.args.get('year', '')

        # Year filter helpers
        def yf_proj(col='start_date'):
            fallback = 'created_at' if '.' not in col else col.split('.')[0] + '.created_at'
            return f" AND substr(COALESCE({col}, {fallback}), 1, 4) = '{year}'" if year else ''
        def yf(col='created_at'):
            return f" AND substr({col}, 1, 4) = '{year}'" if year else ''

        # Projects
        rows = conn.execute(f"SELECT status, count(*) as cnt FROM ahb_projects WHERE 1=1{yf_proj()} GROUP BY status").fetchall()
        stats['projects'] = {r['status']: r['cnt'] for r in rows}
        stats['projects_total'] = sum(r['cnt'] for r in rows)

        # Invoices — join with project to get real year from project start_date
        if year:
            inv_sql = """SELECT i.status, count(*) as cnt, COALESCE(sum(i.total),0) as total
                FROM ahb_invoices i LEFT JOIN ahb_projects p ON i.project_id = p.id
                WHERE substr(COALESCE(i.due_date, i.paid_date, p.start_date, i.created_at), 1, 4) = ?
                GROUP BY i.status"""
            rows = conn.execute(inv_sql, (year,)).fetchall()
        else:
            rows = conn.execute("SELECT status, count(*) as cnt, COALESCE(sum(total),0) as total FROM ahb_invoices GROUP BY status").fetchall()
        stats['invoices'] = {r['status']: {'count': r['cnt'], 'total': r['total']} for r in rows}
        stats['invoices_total'] = sum(r['total'] for r in rows)

        # Receipts
        rows = conn.execute(f"SELECT category, count(*) as cnt, COALESCE(sum(total),0) as total FROM ahb_receipts WHERE 1=1{yf('receipt_date')} GROUP BY category").fetchall()
        stats['receipts'] = {r['category']: {'count': r['cnt'], 'total': r['total']} for r in rows}
        stats['receipts_total'] = sum(r['total'] for r in rows)

        # Payroll
        row = conn.execute(f"SELECT COALESCE(sum(total),0) as total FROM ahb_payroll WHERE 1=1{yf('period_start')}").fetchone()
        stats['payroll_total'] = row['total']

        # Debts (debts are current, not year-filtered)
        row = conn.execute("SELECT COALESCE(sum(balance),0) as balance, COALESCE(sum(payment_amount),0) as monthly FROM ahb_debts").fetchone()
        stats['debt_balance'] = row['balance']
        stats['debt_monthly'] = row['monthly']
        rows = conn.execute("SELECT type, count(*) as cnt, COALESCE(sum(balance),0) as balance FROM ahb_debts GROUP BY type").fetchall()
        stats['debts'] = {r['type']: {'count': r['cnt'], 'balance': r['balance']} for r in rows}

        # Employees
        stats['employees_active'] = conn.execute("SELECT count(*) as cnt FROM ahb_employees WHERE active=1").fetchone()['cnt']

        # Clients
        stats['clients_total'] = conn.execute("SELECT count(*) as cnt FROM ahb_clients").fetchone()['cnt']

        # Events
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        week = (datetime.datetime.now() + datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        stats['events_this_week'] = conn.execute(
            "SELECT count(*) as cnt FROM ahb_events WHERE date >= ? AND date <= ?", (today, week)
        ).fetchone()['cnt']

        # Recent projects
        recent = conn.execute(f"SELECT id, title, status, value, address FROM ahb_projects WHERE 1=1{yf_proj()} ORDER BY updated_at DESC LIMIT 5").fetchall()
        stats['recent_projects'] = [dict(r) for r in recent]

        # All project locations for map — ALWAYS all time, value from invoices
        locations = conn.execute("""
            SELECT p.id, p.title, p.status, p.address,
                   COALESCE(SUM(i.total), p.value, 0) as value,
                   COUNT(i.id) as invoice_count
            FROM ahb_projects p
            LEFT JOIN ahb_invoices i ON i.project_id = p.id
            WHERE p.address IS NOT NULL AND p.address != ''
            GROUP BY p.id
        """).fetchall()
        stats['project_locations'] = [dict(r) for r in locations]

        # Upcoming debt due dates
        upcoming = conn.execute(
            "SELECT name, due_date, payment_amount, type FROM ahb_debts WHERE due_date >= ? AND due_date <= ? ORDER BY due_date LIMIT 10",
            (today, week)
        ).fetchall()
        stats['upcoming_debts'] = [dict(r) for r in upcoming]

        conn.close()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── AHB123 — Employee Time Clock ───────────────────────────────────────────────

@app.route('/api/ahb/timeclock', methods=['GET'])
def api_ahb_timeclock_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_timeclock WHERE 1=1"
        params = []
        if request.args.get('employee_id'):
            q += " AND employee_id = ?"; params.append(request.args['employee_id'])
        if request.args.get('date'):
            q += " AND date = ?"; params.append(request.args['date'])
        rows = conn.execute(q + " ORDER BY date DESC, clock_in DESC", params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/timeclock/punch', methods=['POST'])
def api_ahb_timeclock_punch():
    """Clock in, clock out, or lunch punch."""
    try:
        data = request.json or {}
        employee_id = data.get('employee_id', '')
        punch_type = data.get('type', 'clock_in')  # clock_in, clock_out, lunch_start, lunch_end
        now = datetime.datetime.now()
        conn = _ahb_db()

        if punch_type == 'clock_in':
            tid = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO ahb_timeclock (id, employee_id, date, clock_in, status)
                   VALUES (?, ?, ?, ?, 'active')""",
                (tid, employee_id, now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S')))
            conn.commit(); conn.close()
            return jsonify({'success': True, 'id': tid, 'punch': 'clock_in', 'time': now.strftime('%H:%M:%S')})

        # Find today's active record
        record = conn.execute(
            "SELECT * FROM ahb_timeclock WHERE employee_id = ? AND date = ? AND status = 'active' ORDER BY clock_in DESC LIMIT 1",
            (employee_id, now.strftime('%Y-%m-%d'))
        ).fetchone()
        if not record:
            conn.close()
            return jsonify({'success': False, 'error': 'No active clock-in found for today'}), 400

        rid = record['id']
        time_str = now.strftime('%H:%M:%S')

        if punch_type == 'lunch_start':
            conn.execute("UPDATE ahb_timeclock SET lunch_start = ? WHERE id = ?", (time_str, rid))
        elif punch_type == 'lunch_end':
            conn.execute("UPDATE ahb_timeclock SET lunch_end = ? WHERE id = ?", (time_str, rid))
        elif punch_type == 'clock_out':
            # Calculate hours
            clock_in = record['clock_in']
            cin = datetime.datetime.strptime(f"{record['date']} {clock_in}", '%Y-%m-%d %H:%M:%S')
            cout = now
            total_mins = (cout - cin).total_seconds() / 60
            # Subtract lunch
            lunch_mins = 0
            if record['lunch_start'] and record['lunch_end']:
                ls = datetime.datetime.strptime(f"{record['date']} {record['lunch_start']}", '%Y-%m-%d %H:%M:%S')
                le = datetime.datetime.strptime(f"{record['date']} {record['lunch_end']}", '%Y-%m-%d %H:%M:%S')
                lunch_mins = (le - ls).total_seconds() / 60
            hours = round((total_mins - lunch_mins) / 60, 2)
            conn.execute(
                "UPDATE ahb_timeclock SET clock_out = ?, hours = ?, lunch_minutes = ?, status = 'completed' WHERE id = ?",
                (time_str, hours, round(lunch_mins), rid))

        conn.commit(); conn.close()
        return jsonify({'success': True, 'punch': punch_type, 'time': time_str})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/timeclock/status/<employee_id>', methods=['GET'])
def api_ahb_timeclock_status(employee_id):
    """Get current clock status for an employee."""
    try:
        conn = _ahb_db()
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        record = conn.execute(
            "SELECT * FROM ahb_timeclock WHERE employee_id = ? AND date = ? ORDER BY clock_in DESC LIMIT 1",
            (employee_id, today)
        ).fetchone()
        conn.close()
        if not record:
            return jsonify({'status': 'not_clocked_in', 'record': None})
        return jsonify({'status': record['status'], 'record': dict(record)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── AHB123 — Project Detail ─────────────────────────────────────────────────

@app.route('/api/ahb/projects/<pid>/detail', methods=['GET'])
def api_ahb_project_detail(pid):
    """Return full project detail with invoices, phases, files, receipts, linked invoice, permits count, photo counts."""
    try:
        conn = _ahb_db()
        project = conn.execute("SELECT * FROM ahb_projects WHERE id = ?", (pid,)).fetchone()
        if not project:
            conn.close()
            return jsonify({'error': 'Not found'}), 404
        result = dict(project)
        result['invoices'] = [dict(r) for r in conn.execute(
            "SELECT * FROM ahb_invoices WHERE project_id = ? ORDER BY created_at DESC", (pid,)).fetchall()]
        result['phases'] = [dict(r) for r in conn.execute(
            "SELECT * FROM ahb_project_phases WHERE project_id = ? ORDER BY phase_number", (pid,)).fetchall()]
        result['files'] = [dict(r) for r in conn.execute(
            "SELECT * FROM ahb_files WHERE project_id = ? ORDER BY created_at DESC", (pid,)).fetchall()]
        result['receipts'] = [dict(r) for r in conn.execute(
            "SELECT * FROM ahb_receipts WHERE project_id = ? ORDER BY receipt_date DESC", (pid,)).fetchall()]
        result['events'] = [dict(r) for r in conn.execute(
            "SELECT * FROM ahb_events WHERE project_id = ? ORDER BY date", (pid,)).fetchall()]

        # Linked invoice (first invoice for this project)
        linked_inv = conn.execute(
            "SELECT * FROM ahb_invoices WHERE project_id = ? ORDER BY created_at ASC LIMIT 1", (pid,)
        ).fetchone()
        result['linked_invoice'] = dict(linked_inv) if linked_inv else None

        # Permits count
        permits_row = conn.execute(
            "SELECT count(*) as cnt FROM ahb_files WHERE project_id = ? AND document_type = 'Permit'", (pid,)
        ).fetchone()
        result['permits_count'] = permits_row['cnt'] if permits_row else 0

        # Photo counts per section
        photo_sections = conn.execute(
            "SELECT photo_section, count(*) as cnt FROM ahb_files WHERE project_id = ? AND photo_section != '' GROUP BY photo_section",
            (pid,)
        ).fetchall()
        result['photo_counts'] = {r['photo_section']: r['cnt'] for r in photo_sections}

        # Phase tasks
        for phase in result['phases']:
            tasks = conn.execute(
                "SELECT * FROM ahb_phase_tasks WHERE phase_id = ? ORDER BY created_at", (phase['id'],)
            ).fetchall()
            phase['tasks'] = [dict(t) for t in tasks]

        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── AHB123 — Invoice Detail + Interest ─────────────────────────────────────

@app.route('/api/ahb/invoices/<iid>/detail', methods=['GET'])
def api_ahb_invoice_detail(iid):
    try:
        conn = _ahb_db()
        inv = conn.execute("SELECT * FROM ahb_invoices WHERE id = ?", (iid,)).fetchone()
        if not inv:
            conn.close()
            return jsonify({'error': 'Not found'}), 404
        result = dict(inv)
        # Get change orders for this invoice
        result['change_orders'] = [dict(r) for r in conn.execute(
            "SELECT * FROM ahb_invoices WHERE parent_invoice_id = ?", (iid,)).fetchall()]
        # Get parent if this is a change order
        if inv['parent_invoice_id']:
            parent = conn.execute("SELECT * FROM ahb_invoices WHERE id = ?", (inv['parent_invoice_id'],)).fetchone()
            result['parent_invoice'] = dict(parent) if parent else None
        # Get payments
        result['payments'] = [dict(r) for r in conn.execute(
            "SELECT * FROM ahb_payments WHERE invoice_id = ? ORDER BY payment_date DESC", (iid,)).fetchall()]
        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/invoices/<iid>/interest', methods=['GET'])
def api_ahb_invoice_interest(iid):
    try:
        conn = _ahb_db()
        inv = conn.execute("SELECT * FROM ahb_invoices WHERE id = ?", (iid,)).fetchone()
        if not inv:
            conn.close()
            return jsonify({'error': 'Not found'}), 404
        inv = dict(inv)
        overdue_since = inv.get('overdue_since') or inv.get('due_date')
        if not overdue_since:
            conn.close()
            return jsonify({'weeks': 0, 'interest': 0, 'total_with_interest': inv.get('total', 0)})
        from datetime import datetime as dt
        start = dt.strptime(overdue_since[:10], '%Y-%m-%d')
        weeks = max(0, (dt.now() - start).days // 7)
        rate = inv.get('overdue_interest_per_week') or 50
        interest = weeks * rate
        conn.close()
        return jsonify({'weeks': weeks, 'interest': interest, 'rate_per_week': rate,
                        'total_with_interest': (inv.get('total') or 0) + interest})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/invoices/<iid>/pdf', methods=['GET'])
def api_ahb_invoice_pdf(iid):
    """Generate professional invoice PDF matching Shaski template."""
    try:
        conn = _ahb_db()
        inv = conn.execute("SELECT * FROM ahb_invoices WHERE id = ?", (iid,)).fetchone()
        if not inv:
            conn.close()
            return jsonify({'error': 'Invoice not found'}), 404
        inv = dict(inv)
        # Get project details if linked
        project = None
        if inv.get('project_id'):
            p = conn.execute("SELECT * FROM ahb_projects WHERE id = ?", (inv['project_id'],)).fetchone()
            if p:
                project = dict(p)
        # Get phases for this project
        phases = []
        if inv.get('project_id'):
            phases = [dict(r) for r in conn.execute(
                "SELECT * FROM ahb_project_phases WHERE project_id = ? ORDER BY phase_number", (inv['project_id'],)).fetchall()]
        conn.close()

        # Parse line items
        line_items = []
        if inv.get('line_items'):
            try:
                line_items = json.loads(inv['line_items']) if isinstance(inv['line_items'], str) else inv['line_items']
            except (json.JSONDecodeError, TypeError):
                line_items = []

        # Build line items HTML — group by phase if available
        items_html = ''
        for i, item in enumerate(line_items, 1):
            desc = item.get('description', '')
            qty = item.get('quantity', 1)
            price = item.get('unit_price', 0) or 0
            total_item = qty * price
            items_html += f'''<tr>
                <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#333;">{i}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#333;font-weight:500;">{desc}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:center;color:#333;">{qty}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;color:#333;">${price:,.2f}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;color:#333;font-weight:600;">${total_item:,.2f}</td>
            </tr>'''

        subtotal = inv.get('subtotal') or inv.get('total') or 0
        tax = inv.get('tax') or 0
        total = inv.get('total') or 0
        inv_date = inv.get('date') or (inv.get('created_at', '')[:10] if inv.get('created_at') else '')
        project_location = inv.get('project_address') or (project.get('address', '') if project else '') or ''
        interest_rate = inv.get('overdue_interest_per_week') or 50

        # Logo as base64 for embedding in PDF
        logo_b64 = ''
        logo_path = os.path.join(DASHBOARD_DIR, 'static', 'img', 'ahb_logo.jpeg')
        if os.path.exists(logo_path):
            import base64
            with open(logo_path, 'rb') as lf:
                logo_b64 = base64.b64encode(lf.read()).decode('utf-8')

        # Page 1: Invoice
        html = f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Invoice {inv.get('invoice_number','')}</title>
<style>
@media print {{ body {{ margin:0; }} @page {{ margin:40px 50px; }} }}
body {{ font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;max-width:780px;margin:30px auto;color:#333;font-size:14px;line-height:1.5; }}
.page-break {{ page-break-before:always; }}
</style></head>
<body>
<!-- PAGE 1: INVOICE -->
<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;">
    <div style="display:flex;align-items:center;gap:12px;">
        {f'<img src="data:image/jpeg;base64,{logo_b64}" style="width:50px;height:50px;object-fit:contain;">' if logo_b64 else '<div style="width:50px;height:50px;background:#2563eb;border-radius:8px;"></div>'}
        <div>
            <div style="font-size:20px;font-weight:700;color:#1a1a1a;">All Home Building CO LLC</div>
            <div style="font-size:12px;color:#888;">2725 Colmar ave</div>
            <div style="font-size:12px;color:#888;">800-484-6404</div>
            <div style="font-size:12px;color:#888;">AHB123.com</div>
        </div>
    </div>
    <div style="text-align:right;">
        <div style="font-size:28px;font-weight:300;color:#333;letter-spacing:2px;">INVOICE</div>
        <div style="font-size:14px;color:#555;">#{inv.get('invoice_number','')}</div>
        <div style="display:inline-block;padding:3px 12px;border-radius:12px;font-size:11px;font-weight:700;background:{'#dcfce7;color:#16a34a' if inv.get('status')=='Paid' else '#dbeafe;color:#2563eb' if inv.get('status')=='Approved' else '#fef3c7;color:#d97706' if inv.get('status')=='In Progress' else '#fee2e2;color:#dc2626' if inv.get('status')=='Overdue' else '#f3f4f6;color:#6b7280'};margin-top:4px;">{inv.get('status','Draft')}</div>
    </div>
</div>

<div style="display:flex;justify-content:space-between;margin:16px 0 24px;padding:12px 0;border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb;">
    <div>
        <div style="font-size:11px;color:#999;text-transform:uppercase;font-weight:700;margin-bottom:4px;">Bill To:</div>
        <div style="font-weight:600;">{inv.get('client_name','')}</div>
        <div style="color:#666;">{inv.get('client_address','')}</div>
    </div>
    <div style="text-align:center;">
        <div style="font-size:11px;color:#999;text-transform:uppercase;font-weight:700;margin-bottom:4px;">Project Location:</div>
        <div style="color:#666;">{project_location}</div>
    </div>
    <div style="text-align:right;">
        <div style="font-size:11px;color:#999;text-transform:uppercase;font-weight:700;margin-bottom:4px;">Date:</div>
        <div>{inv_date}</div>
    </div>
</div>

<table style="width:100%;border-collapse:collapse;margin:0 0 20px;">
    <thead>
        <tr style="background:#f8fafc;">
            <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;font-size:12px;color:#64748b;font-weight:700;">#</th>
            <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;font-size:12px;color:#64748b;font-weight:700;">Description</th>
            <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #e2e8f0;font-size:12px;color:#64748b;font-weight:700;">Qty</th>
            <th style="padding:10px 12px;text-align:right;border-bottom:2px solid #e2e8f0;font-size:12px;color:#64748b;font-weight:700;">Price</th>
            <th style="padding:10px 12px;text-align:right;border-bottom:2px solid #e2e8f0;font-size:12px;color:#64748b;font-weight:700;">Total</th>
        </tr>
    </thead>
    <tbody>{items_html}</tbody>
</table>

<div style="display:flex;justify-content:flex-end;">
    <div style="width:250px;border-top:2px solid #333;padding-top:8px;">
        <div style="display:flex;justify-content:space-between;padding:6px 0;font-size:20px;font-weight:700;color:#2563eb;">
            <span>Total:</span><span>${total:,.2f}</span>
        </div>
    </div>
</div>

{f'<p style="margin-top:20px;font-size:12px;color:#888;font-style:italic;">{inv.get("notes","")}</p>' if inv.get('notes') else ''}

<!-- PAGE 2: CONTRACTOR AGREEMENT -->
<div class="page-break"></div>

<div style="display:flex;align-items:center;gap:12px;margin-bottom:24px;">
    {f'<img src="data:image/jpeg;base64,{logo_b64}" style="width:40px;height:40px;object-fit:contain;">' if logo_b64 else ''}
    <h1 style="margin:0;font-size:24px;font-weight:400;color:#333;">CONTRACTOR AGREEMENT</h1>
</div>

<div style="margin-bottom:20px;">
    <h3 style="font-size:14px;color:#333;margin:0 0 10px;">Parties</h3>
    <div style="display:flex;justify-content:space-between;">
        <div>
            <div style="font-size:12px;color:#999;font-weight:700;">Contractor:</div>
            <div style="font-weight:600;">Sergey Tkach</div>
            <div>All Home Building CO LLC</div>
            <div style="color:#666;font-size:13px;">2725 Colmar ave</div>
            <div style="color:#666;font-size:13px;">800-484-6404</div>
            <div style="color:#666;font-size:13px;">AHB123.com</div>
        </div>
        <div style="text-align:right;">
            <div style="font-size:12px;color:#999;font-weight:700;">Client:</div>
            <div style="font-weight:600;">{inv.get('client_name','')}</div>
            <div style="color:#666;font-size:13px;">{inv.get('client_address','')}</div>
        </div>
    </div>
</div>

<div style="margin-bottom:20px;padding:12px;background:#f8fafc;border-radius:6px;">
    <h3 style="font-size:14px;color:#333;margin:0 0 8px;">Project Information</h3>
    <div style="font-size:13px;color:#555;">
        <strong>Invoice Number:</strong> {inv.get('invoice_number','')}<br>
        <strong>Date:</strong> {inv_date}<br>
        <strong>Project Location:</strong> {project_location}
    </div>
</div>

<div style="margin-bottom:24px;">
    <h3 style="font-size:14px;color:#333;margin:0 0 12px;">Terms & Conditions</h3>
    <div style="font-size:13px;color:#444;line-height:1.6;">
        <p style="margin:0 0 8px;"><strong>1.</strong> A Deposit is due before commencement of the project.</p>
        <p style="margin:0 0 8px;"><strong>2.</strong> Total due upon completion.</p>
        <p style="margin:0 0 8px;"><strong>3.</strong> Project will take approx {(project or {}).get('notes','') or 'TBD'} days to complete.</p>
        <p style="margin:0 0 8px;"><strong>4.</strong> Project description is final unless a change order is requested.</p>
        <p style="margin:0 0 8px;"><strong>5.</strong> Make checks payable to ALL HOME BUILDING CO.</p>
        <p style="margin:0 0 8px;"><strong>6.</strong> Late Payment: Payment is due by the date specified on this invoice. If payment is not received by the due date, this invoice shall be deemed overdue. Interest shall accrue at the rate of fifty dollars (${interest_rate:.0f}.00) per week on the unpaid balance. An overdue interest sheet will be attached reflecting all accrued charges.</p>
    </div>
</div>

<div style="display:flex;justify-content:space-between;margin-top:40px;padding-top:20px;">
    <div style="width:45%;">
        <div style="font-size:12px;color:#999;margin-bottom:4px;">Contractor Signature:</div>
        <div style="border-bottom:1px solid #333;height:40px;margin-bottom:4px;"></div>
        <div style="font-size:12px;color:#555;">Sergey Tkach</div>
    </div>
    <div style="width:45%;">
        <div style="font-size:12px;color:#999;margin-bottom:4px;">Client Signature:</div>
        <div style="border-bottom:1px solid #333;height:40px;margin-bottom:4px;"></div>
        <div style="font-size:12px;color:#555;">{inv.get('client_name','')}</div>
    </div>
</div>

</body>
</html>'''

        # Try to use weasyprint for real PDF, fall back to HTML
        download = request.args.get('download', '0') == '1'
        try:
            from weasyprint import HTML as WeasyHTML
            pdf_bytes = WeasyHTML(string=html).write_pdf()
            response = make_response(pdf_bytes)
            response.headers['Content-Type'] = 'application/pdf'
            disposition = 'attachment' if download else 'inline'
            response.headers['Content-Disposition'] = f'{disposition}; filename="invoice_{inv.get("invoice_number","")}.pdf"'
            return response
        except ImportError:
            # weasyprint not installed — return HTML with PDF-like content type hint
            response = make_response(html)
            response.headers['Content-Type'] = 'text/html; charset=utf-8'
            response.headers['Content-Disposition'] = f'inline; filename="invoice_{inv.get("invoice_number","")}.html"'
            return response

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── AHB123 — Payments ───────────────────────────────────────────────────────

@app.route('/api/ahb/payments', methods=['GET'])
def api_ahb_payments_list():
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_payments WHERE 1=1"
        params = []
        if request.args.get('invoice_id'):
            q += " AND invoice_id = ?"; params.append(request.args['invoice_id'])
        rows = conn.execute(q + " ORDER BY payment_date DESC", params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/payments', methods=['POST'])
def api_ahb_payments_create():
    try:
        data = request.json or {}
        conn = _ahb_db()
        pid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO ahb_payments (id, invoice_id, amount, payment_method, payment_date, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pid, data.get('invoice_id',''), data.get('amount',0), data.get('payment_method',''),
             data.get('payment_date',''), data.get('notes','')))
        conn.commit(); conn.close()
        return jsonify({'success': True, 'id': pid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AHB123 — Billing Summary ────────────────────────────────────────────────

@app.route('/api/ahb/billing/summary', methods=['GET'])
def api_ahb_billing_summary():
    try:
        conn = _ahb_db()
        stats = {}
        # Unpaid invoices
        for status in ['Sent', 'Approved', 'In Progress', 'Overdue']:
            row = conn.execute(
                "SELECT count(*) as cnt, COALESCE(sum(total),0) as total FROM ahb_invoices WHERE status = ? AND is_change_order = 0",
                (status,)).fetchone()
            stats[status.lower().replace(' ','_')] = {'count': row['cnt'], 'total': row['total']}
        # Paid
        row = conn.execute("SELECT count(*) as cnt, COALESCE(sum(total),0) as total FROM ahb_invoices WHERE status = 'Paid' AND is_change_order = 0").fetchone()
        stats['paid'] = {'count': row['cnt'], 'total': row['total']}
        # Total receivable (all non-paid)
        row = conn.execute("SELECT COALESCE(sum(total),0) as total FROM ahb_invoices WHERE status != 'Paid' AND is_change_order = 0").fetchone()
        stats['total_receivable'] = row['total']
        # Total payments received
        row = conn.execute("SELECT COALESCE(sum(amount),0) as total FROM ahb_payments").fetchone()
        stats['total_payments'] = row['total']
        # Overdue invoices with interest
        overdue = conn.execute("SELECT * FROM ahb_invoices WHERE status = 'Overdue' AND is_change_order = 0").fetchall()
        overdue_details = []
        for inv in overdue:
            inv = dict(inv)
            overdue_since = inv.get('overdue_since') or inv.get('due_date') or inv.get('created_at','')[:10]
            try:
                from datetime import datetime as dt
                start = dt.strptime(overdue_since[:10], '%Y-%m-%d')
                weeks = max(0, (dt.now() - start).days // 7)
            except:
                weeks = 0
            rate = inv.get('overdue_interest_per_week') or 50
            interest = weeks * rate
            inv['weeks_overdue'] = weeks
            inv['interest_accrued'] = interest
            inv['total_with_interest'] = (inv.get('total') or 0) + interest
            overdue_details.append(inv)
        stats['overdue_details'] = overdue_details
        # Active billing items (unpaid invoices)
        active = conn.execute(
            "SELECT * FROM ahb_invoices WHERE status != 'Paid' AND is_change_order = 0 ORDER BY status, created_at DESC"
        ).fetchall()
        stats['active_items'] = [dict(r) for r in active]
        # Change orders summary
        row = conn.execute("SELECT count(*) as cnt, COALESCE(sum(total),0) as total FROM ahb_invoices WHERE is_change_order = 1").fetchone()
        stats['change_orders'] = {'count': row['cnt'], 'total': row['total']}
        conn.close()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/files/serve/<fid>', methods=['GET'])
def api_ahb_files_serve(fid):
    try:
        conn = _ahb_db()
        row = conn.execute("SELECT file_path FROM ahb_files WHERE id = ?", (fid,)).fetchone()
        conn.close()
        if not row or not row['file_path']:
            return 'Not found', 404
        path = row['file_path']
        if not os.path.exists(path):
            return 'File not found', 404
        return send_from_directory(os.path.dirname(path), os.path.basename(path))
    except Exception as e:
        return str(e), 500


# ── AHB123 — Receipt Processing Queue ───────────────────────────────────────

@app.route('/api/ahb/receipts/process', methods=['POST'])
def api_ahb_receipts_process():
    """Upload receipt images for processing. Modes: single, dual, bulk."""
    try:
        mode = request.form.get('mode', 'single')
        files = request.files.getlist('files') or [request.files.get('file')]
        files = [f for f in files if f]
        if not files:
            return jsonify({'success': False, 'error': 'No files uploaded'}), 400

        queue_dir = os.path.join(DASHBOARD_DIR, 'uploads', 'ahb', 'receipts', 'queue')
        os.makedirs(queue_dir, exist_ok=True)
        conn = _ahb_db()
        queue_ids = []

        for f in files:
            if mode == 'dual':
                # Split image in half — left and right
                from PIL import Image
                import io as _io
                img = Image.open(f.stream)
                w, h = img.size
                mid = w // 2
                for side, crop_box in [('left', (0, 0, mid, h)), ('right', (mid, 0, w, h))]:
                    qid = str(uuid.uuid4())
                    cropped = img.crop(crop_box)
                    fname = f"{qid}_{side}.jpg"
                    fpath = os.path.join(queue_dir, fname)
                    cropped.save(fpath, 'JPEG', quality=85)
                    conn.execute(
                        "INSERT INTO ahb_receipt_queue (id, image_path, mode, status) VALUES (?, ?, ?, 'pending')",
                        (qid, fpath, 'dual'))
                    queue_ids.append(qid)
            else:
                # Single or bulk — each file is one receipt
                qid = str(uuid.uuid4())
                safe_name = re.sub(r'[^\w.\-]', '_', f.filename or 'receipt.jpg')
                fpath = os.path.join(queue_dir, f"{qid}_{safe_name}")
                f.save(fpath)
                conn.execute(
                    "INSERT INTO ahb_receipt_queue (id, image_path, mode, status) VALUES (?, ?, ?, 'pending')",
                    (qid, fpath, mode))
                queue_ids.append(qid)

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'queue_ids': queue_ids, 'count': len(queue_ids)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/receipts/queue', methods=['GET'])
def api_ahb_receipts_queue_list():
    """List queue items. ?status=pending|processing|done|error"""
    try:
        conn = _ahb_db()
        q = "SELECT * FROM ahb_receipt_queue WHERE 1=1"
        params = []
        if request.args.get('status'):
            q += " AND status = ?"; params.append(request.args['status'])
        rows = conn.execute(q + " ORDER BY created_at DESC", params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ahb/receipts/queue/run', methods=['POST'])
def api_ahb_receipts_queue_run():
    """Process pending queue items through OCR. Runs synchronously for up to ?limit=10 items."""
    try:
        limit = int(request.args.get('limit', 10))
        conn = _ahb_db()
        pending = conn.execute(
            "SELECT * FROM ahb_receipt_queue WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
            (limit,)).fetchall()

        if not pending:
            conn.close()
            return jsonify({'success': True, 'processed': 0, 'message': 'No pending items'})

        skill_path = os.path.join(os.path.dirname(DASHBOARD_DIR), 'skills', 'shared', 'receipt_ocr.py')
        processed = 0
        for item in pending:
            qid = item['id']
            conn.execute("UPDATE ahb_receipt_queue SET status = 'processing' WHERE id = ?", (qid,))
            conn.commit()
            try:
                env = os.environ.copy()
                env['SKILL_ARGS'] = json.dumps({'image_path': item['image_path'], 'mode': 'full'})
                result = subprocess.run([VENV_PYTHON, skill_path], capture_output=True, text=True, timeout=120, env=env)
                if result.returncode == 0:
                    conn.execute("UPDATE ahb_receipt_queue SET status = 'done', result_json = ? WHERE id = ?",
                                 (result.stdout.strip(), qid))
                else:
                    conn.execute("UPDATE ahb_receipt_queue SET status = 'error', error = ? WHERE id = ?",
                                 (result.stderr.strip()[:500], qid))
            except subprocess.TimeoutExpired:
                conn.execute("UPDATE ahb_receipt_queue SET status = 'error', error = 'Timeout (120s)' WHERE id = ?", (qid,))
            except Exception as e:
                conn.execute("UPDATE ahb_receipt_queue SET status = 'error', error = ? WHERE id = ?", (str(e)[:500], qid))
            conn.commit()
            processed += 1

        conn.close()
        return jsonify({'success': True, 'processed': processed})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/receipts/queue/<qid>/confirm', methods=['POST'])
def api_ahb_receipts_queue_confirm(qid):
    """Confirm a processed receipt — creates actual ahb_receipt record from queue data + user edits."""
    try:
        data = request.json or {}
        conn = _ahb_db()
        item = conn.execute("SELECT * FROM ahb_receipt_queue WHERE id = ?", (qid,)).fetchone()
        if not item:
            conn.close()
            return jsonify({'success': False, 'error': 'Queue item not found'}), 404

        # Create actual receipt from confirmed data
        rid = str(uuid.uuid4())
        now = datetime.datetime.now()
        image_path = item['image_path']

        # Move image from queue to permanent storage
        perm_dir = os.path.join(DASHBOARD_DIR, 'uploads', 'ahb', 'receipts')
        os.makedirs(perm_dir, exist_ok=True)
        perm_path = os.path.join(perm_dir, f"{rid}.jpg")
        if os.path.exists(image_path):
            import shutil
            shutil.move(image_path, perm_path)
            image_path = perm_path

        conn.execute(
            """INSERT INTO ahb_receipts (id, vendor, store_name, amount, total, category, description,
               receipt_date, payment_method, teller_name, store_location, purchase_time,
               tax_amount, subtotal, items_json, ocr_text, ocr_raw, ocr_structured,
               image_path, file_path, year, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, data.get('store_name', data.get('vendor', '')),
             data.get('store_name', ''), data.get('total', 0), data.get('total', 0),
             data.get('category', ''), data.get('description', ''),
             data.get('receipt_date', now.strftime('%Y-%m-%d')),
             data.get('payment_method', ''), data.get('teller_name', ''),
             data.get('store_location', ''), data.get('purchase_time', ''),
             data.get('tax_amount', 0), data.get('subtotal', 0),
             json.dumps(data.get('items', [])), data.get('ocr_text', ''),
             data.get('ocr_raw', ''), json.dumps(data.get('structured', {})),
             image_path, image_path,
             (data.get('receipt_date', '') or '')[:4] or now.strftime('%Y'),
             now.isoformat()))

        # Mark queue item as confirmed
        conn.execute("UPDATE ahb_receipt_queue SET status = 'confirmed', receipt_id = ? WHERE id = ?", (rid, qid))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'receipt_id': rid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/receipts/queue/<qid>/reject', methods=['POST'])
def api_ahb_receipts_queue_reject(qid):
    """Reject/discard a queue item."""
    try:
        conn = _ahb_db()
        conn.execute("UPDATE ahb_receipt_queue SET status = 'rejected' WHERE id = ?", (qid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ahb/receipts/queue/image/<qid>', methods=['GET'])
def api_ahb_receipts_queue_image(qid):
    """Serve a queue item's image."""
    try:
        conn = _ahb_db()
        row = conn.execute("SELECT image_path FROM ahb_receipt_queue WHERE id = ?", (qid,)).fetchone()
        conn.close()
        if not row or not row['image_path'] or not os.path.exists(row['image_path']):
            return 'Not found', 404
        return send_from_directory(os.path.dirname(row['image_path']), os.path.basename(row['image_path']))
    except Exception as e:
        return str(e), 500


@app.route('/api/ahb/receipts/scan-existing', methods=['POST'])
def api_ahb_receipts_scan_existing():
    """Queue existing receipts for OCR re-scan. Does NOT alter existing data — results go to queue for review."""
    try:
        data = request.json or {}
        category = data.get('category', '')  # empty = all
        conn = _ahb_db()
        q = "SELECT id, image_path, store_name, total FROM ahb_receipts WHERE image_path IS NOT NULL AND image_path != ''"
        params = []
        if category:
            q += " AND category = ?"; params.append(category)
        receipts = conn.execute(q, params).fetchall()

        queued = 0
        for r in receipts:
            if not os.path.exists(r['image_path']):
                continue
            qid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO ahb_receipt_queue (id, image_path, mode, status, receipt_id) VALUES (?, ?, 'rescan', 'pending', ?)",
                (qid, r['image_path'], r['id']))
            queued += 1

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'queued': queued})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Explore Lab ──────────────────────────────────────────────────────────────
_explore_servers = {}  # session_id -> {pid, port, type, started}
EXPLORE_PORT_START = 9100

def _find_free_port():
    import socket as _s
    with _s.socket(_s.AF_INET, _s.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def _get_device_catalog():
    return {
        "common": [
            {"id":"iphone-16-pro","name":"iPhone 16 Pro","w":393,"h":852,"type":"phone","os":"ios","notch":"dynamic-island"},
            {"id":"iphone-se","name":"iPhone SE","w":375,"h":667,"type":"phone","os":"ios","notch":"none"},
            {"id":"galaxy-s25","name":"Galaxy S25","w":360,"h":780,"type":"phone","os":"android","notch":"punch-hole"},
            {"id":"pixel-9","name":"Pixel 9","w":412,"h":915,"type":"phone","os":"android","notch":"punch-hole"},
            {"id":"ipad-pro-13","name":"iPad Pro 13\"","w":1024,"h":1366,"type":"tablet","os":"ios"},
            {"id":"galaxy-tab-s10","name":"Galaxy Tab S10","w":800,"h":1280,"type":"tablet","os":"android"},
            {"id":"desktop-1080p","name":"Desktop 1080p","w":1920,"h":1080,"type":"desktop","os":"any"},
            {"id":"desktop-1440","name":"Desktop 1440p","w":2560,"h":1440,"type":"desktop","os":"any"},
            {"id":"macbook-pro","name":"MacBook Pro 14\"","w":1512,"h":982,"type":"desktop","os":"macos"},
        ],
        "uncommon": [
            {"id":"rpi-touch","name":"Raspberry Pi Touch","w":800,"h":480,"type":"embedded","os":"linux"},
            {"id":"rpi-zero","name":"Raspberry Pi Zero","w":640,"h":480,"type":"embedded","os":"linux"},
            {"id":"arduino-tft","name":"Arduino TFT 2.8\"","w":320,"h":240,"type":"embedded","os":"bare"},
            {"id":"stm32-oled","name":"STM32 OLED 0.96\"","w":128,"h":64,"type":"embedded","os":"bare"},
            {"id":"stm32-tft","name":"STM32 TFT 3.5\"","w":480,"h":320,"type":"embedded","os":"bare"},
            {"id":"esp32-tft","name":"ESP32 TFT 1.8\"","w":160,"h":128,"type":"embedded","os":"bare"},
            {"id":"surface-pro","name":"Surface Pro","w":2880,"h":1920,"type":"tablet","os":"windows"},
            {"id":"steam-deck","name":"Steam Deck","w":1280,"h":800,"type":"handheld","os":"linux"},
        ],
        "browsers": [
            {"id":"chrome-desktop","name":"Chrome Desktop","w":1440,"h":900,"type":"browser","os":"any","ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36","chrome":"131"},
            {"id":"chrome-mobile","name":"Chrome Mobile","w":412,"h":915,"type":"browser","os":"android","ua":"Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36","chrome":"131"},
            {"id":"safari-desktop","name":"Safari Desktop","w":1440,"h":900,"type":"browser","os":"macos","ua":"Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15","safari":"18"},
            {"id":"safari-mobile","name":"Safari iOS","w":393,"h":852,"type":"browser","os":"ios","ua":"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1","safari":"18"},
            {"id":"firefox-desktop","name":"Firefox Desktop","w":1440,"h":900,"type":"browser","os":"any","ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0","firefox":"133"},
            {"id":"firefox-mobile","name":"Firefox Android","w":412,"h":915,"type":"browser","os":"android","ua":"Mozilla/5.0 (Android 15; Mobile; rv:133.0) Gecko/133.0 Firefox/133.0","firefox":"133"},
            {"id":"edge-desktop","name":"Edge Desktop","w":1440,"h":900,"type":"browser","os":"windows","ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0","edge":"131"},
            {"id":"brave-desktop","name":"Brave Desktop","w":1440,"h":900,"type":"browser","os":"any","ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36","brave":"131"},
            {"id":"opera-desktop","name":"Opera Desktop","w":1440,"h":900,"type":"browser","os":"any","ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/117.0.0.0","opera":"117"},
            {"id":"samsung-internet","name":"Samsung Internet","w":360,"h":780,"type":"browser","os":"android","ua":"Mozilla/5.0 (Linux; Android 15; SAMSUNG SM-S926B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/27.0 Chrome/131.0.0.0 Mobile Safari/537.36","samsung":"27"},
        ]
    }

@app.route('/explore')
def explore_lab():
    projects = []
    if os.path.exists(ARTIFACTS_DIR):
        projects = sorted([d for d in os.listdir(ARTIFACTS_DIR)
                           if os.path.isdir(os.path.join(ARTIFACTS_DIR, d))])
    return render_template('explore.html', projects=projects, page='explore')

@app.route('/api/explore/devices')
def api_explore_devices():
    return jsonify(_get_device_catalog())

@app.route('/api/explore/static/<project_id>/<path:filepath>')
def api_explore_static(project_id, filepath):
    proj_dir = os.path.join(ARTIFACTS_DIR, project_id)
    full = os.path.realpath(os.path.join(proj_dir, filepath))
    if not full.startswith(os.path.realpath(ARTIFACTS_DIR)):
        return jsonify({'error': 'Forbidden'}), 403
    parent = os.path.dirname(full)
    fname  = os.path.basename(full)
    return send_from_directory(parent, fname)

@app.route('/api/explore/serve', methods=['POST'])
def api_explore_serve():
    data = request.json or {}
    src  = data.get('type', 'artifact')
    if src == 'url':
        return jsonify({'serve_url': data.get('url', ''), 'type': 'url'})
    project_id = data.get('project_id', '')
    filename   = data.get('filename', '')
    if not project_id or not filename:
        return jsonify({'error': 'project_id and filename required'}), 400
    serve_url = f'/api/explore/static/{project_id}/{filename}'
    return jsonify({'serve_url': serve_url, 'type': 'artifact'})

@app.route('/api/explore/build', methods=['POST'])
def api_explore_build():
    data        = request.json or {}
    project_id  = data.get('project_id', '')
    entry_point = data.get('entry_point', 'index.html')
    build_type  = data.get('type', 'static')
    proj_dir    = os.path.join(ARTIFACTS_DIR, project_id)
    if not os.path.isdir(proj_dir):
        return jsonify({'error': 'Project not found'}), 404

    session_id = str(uuid.uuid4())[:8]

    if build_type == 'static':
        serve_url = f'/api/explore/static/{project_id}/{entry_point}'
        return jsonify({'session_id': session_id, 'serve_url': serve_url, 'port': None, 'pid': None})

    port = _find_free_port()
    if build_type == 'flask':
        proc = subprocess.Popen(
            [VENV_PYTHON, entry_point],
            cwd=proj_dir,
            env={**os.environ, 'PORT': str(port), 'FLASK_RUN_PORT': str(port)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif build_type == 'node':
        proc = subprocess.Popen(
            ['npx', 'serve', '-l', str(port), '.'],
            cwd=proj_dir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        return jsonify({'error': f'Unknown build type: {build_type}'}), 400

    _explore_servers[session_id] = {
        'pid': proc.pid, 'port': port, 'type': build_type,
        'started': datetime.datetime.now().isoformat(), 'project_id': project_id
    }
    return jsonify({'session_id': session_id, 'serve_url': f'http://localhost:{port}', 'port': port, 'pid': proc.pid})

@app.route('/api/explore/kill', methods=['POST'])
def api_explore_kill():
    data       = request.json or {}
    session_id = data.get('session_id', '')
    info = _explore_servers.pop(session_id, None)
    if not info:
        return jsonify({'error': 'Session not found'}), 404
    try:
        os.kill(info['pid'], 9)
    except ProcessLookupError:
        pass
    return jsonify({'success': True, 'killed': session_id})

@app.route('/api/explore/sessions')
def api_explore_sessions():
    return jsonify(_explore_servers)

@app.route('/api/explore/upload', methods=['POST'])
def api_explore_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    upload_id  = str(uuid.uuid4())[:8]
    upload_dir = os.path.join(DASHBOARD_DIR, 'explore-sessions', upload_id)
    os.makedirs(upload_dir, exist_ok=True)
    saved = []
    for f in request.files.getlist('file'):
        safe = os.path.basename(f.filename)
        f.save(os.path.join(upload_dir, safe))
        saved.append(safe)
    entry = saved[0] if saved else ''
    return jsonify({'session_id': upload_id, 'serve_url': f'/api/explore/static-upload/{upload_id}/{entry}', 'files': saved})

@app.route('/api/explore/static-upload/<session_id>/<path:filepath>')
def api_explore_static_upload(session_id, filepath):
    upload_dir = os.path.join(DASHBOARD_DIR, 'explore-sessions', session_id)
    full = os.path.realpath(os.path.join(upload_dir, filepath))
    if not full.startswith(os.path.realpath(os.path.join(DASHBOARD_DIR, 'explore-sessions'))):
        return jsonify({'error': 'Forbidden'}), 403
    parent = os.path.dirname(full)
    fname  = os.path.basename(full)
    return send_from_directory(parent, fname)


# (SQLite cloud section removed — replaced by CLOUD_ENABLED PostgreSQL block below)


# ── Baza Roadmap API ─────────────────────────────────────────────────────────

@app.route('/api/roadmap', methods=['GET'])
def api_roadmap_list():
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM baza_roadmap ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'planned' THEN 1 WHEN 'future' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END, created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/roadmap', methods=['POST'])
def api_roadmap_add():
    d = request.json or {}
    rid = str(uuid.uuid4())
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.execute(
        "INSERT INTO baza_roadmap (id, title, description, status, priority, category, assigned_agent, target_date, notes) VALUES (?,?,?,?,?,?,?,?,?)",
        (rid, d.get('title',''), d.get('description',''), d.get('status','planned'),
         d.get('priority','medium'), d.get('category','general'),
         d.get('assigned_agent',''), d.get('target_date',''), d.get('notes','')))
    conn.commit()
    conn.close()
    return jsonify({"id": rid, "status": "created"})

@app.route('/api/roadmap/<rid>', methods=['PUT'])
def api_roadmap_update(rid):
    d = request.json or {}
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    fields, vals = [], []
    for k in ['title','description','status','priority','category','assigned_agent','target_date','started_at','completed_at','notes']:
        if k in d:
            fields.append(f"{k}=?")
            vals.append(d[k])
    if not fields:
        return jsonify({"error": "No fields to update"}), 400
    fields.append("updated_at=datetime('now')")
    if d.get('status') == 'in_progress':
        row = conn.execute("SELECT started_at FROM baza_roadmap WHERE id=?", (rid,)).fetchone()
        if row and not row[0]:
            fields.append("started_at=datetime('now')")
    if d.get('status') == 'completed':
        fields.append("completed_at=datetime('now')")
    vals.append(rid)
    conn.execute(f"UPDATE baza_roadmap SET {','.join(fields)} WHERE id=?", vals)
    conn.commit()
    conn.close()
    return jsonify({"id": rid, "status": "updated"})

@app.route('/api/roadmap/<rid>/start', methods=['POST'])
def api_roadmap_start(rid):
    """Start a roadmap item: set status to in_progress + create a task for the right agent."""
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.row_factory = sqlite3.Row
    item = conn.execute("SELECT * FROM baza_roadmap WHERE id=?", (rid,)).fetchone()
    if not item:
        conn.close()
        return jsonify({"error": "Roadmap item not found"}), 404

    # Update roadmap status
    conn.execute("UPDATE baza_roadmap SET status='in_progress', started_at=datetime('now'), updated_at=datetime('now') WHERE id=?", (rid,))

    # Determine best agent based on category
    cat = item['category'] or 'general'
    agent_map = {
        'business': 'simon_bately',
        'infrastructure': 'claw_batto',
        'development': 'claw_batto',
        'ai_agents': 'claw_batto',
        'general': 'simon_bately',
    }
    assigned = item['assigned_agent'] or agent_map.get(cat, 'simon_bately')

    # Check if task already exists for this roadmap item
    existing = conn.execute("SELECT id FROM tasks WHERE title LIKE ?", (f"%{item['title'][:30]}%",)).fetchone()
    task_id = None
    if not existing:
        task_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO tasks (id, title, description, status, priority, assigned_to, created_at, notes) VALUES (?,?,?,?,?,?,?,?)",
            (task_id, f"Roadmap: {item['title']}", f"{item['description']}\n\nCategory: {cat}\nFrom roadmap, started manually.",
             'pending', item['priority'] or 'medium', assigned,
             datetime.datetime.now().isoformat(), "[Duke] Created from roadmap — started by Serge"))

        # Update roadmap with assigned agent
        conn.execute("UPDATE baza_roadmap SET assigned_agent=? WHERE id=? AND (assigned_agent IS NULL OR assigned_agent='')", (assigned, rid))
    else:
        task_id = existing[0]

    conn.commit()
    conn.close()

    return jsonify({"id": rid, "status": "started", "task_id": task_id, "assigned_to": assigned})


@app.route('/api/roadmap/<rid>', methods=['DELETE'])
def api_roadmap_delete(rid):
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.execute("DELETE FROM baza_roadmap WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return jsonify({"id": rid, "status": "deleted"})

# ── Dashboard Links API ──────────────────────────────────────────────────────

@app.route('/api/dash-links', methods=['GET'])
def api_dash_links_list():
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM baza_dash_links ORDER BY sort_order, created_at").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/dash-links', methods=['POST'])
def api_dash_links_add():
    d = request.json or {}
    lid = str(uuid.uuid4())
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.execute(
        "INSERT INTO baza_dash_links (id, title, url, icon, category, sort_order) VALUES (?,?,?,?,?,?)",
        (lid, d.get('title',''), d.get('url',''), d.get('icon','&#128279;'),
         d.get('category','general'), d.get('sort_order', 0)))
    conn.commit()
    conn.close()
    return jsonify({"id": lid, "status": "created"})

@app.route('/api/dash-links/<lid>', methods=['PUT'])
def api_dash_links_update(lid):
    d = request.json or {}
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    fields, vals = [], []
    for k in ['title','url','icon','category','sort_order']:
        if k in d:
            fields.append(f"{k}=?")
            vals.append(d[k])
    if not fields:
        return jsonify({"error": "No fields"}), 400
    vals.append(lid)
    conn.execute(f"UPDATE baza_dash_links SET {','.join(fields)} WHERE id=?", vals)
    conn.commit()
    conn.close()
    return jsonify({"id": lid, "status": "updated"})

@app.route('/api/dash-links/<lid>', methods=['DELETE'])
def api_dash_links_delete(lid):
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.execute("DELETE FROM baza_dash_links WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    return jsonify({"id": lid, "status": "deleted"})

# ── Infra Notes API ──────────────────────────────────────────────────────────

@app.route('/api/infra/notes', methods=['GET'])
def api_infra_notes_list():
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.row_factory = sqlite3.Row
    section = request.args.get('section')
    if section:
        rows = conn.execute("SELECT * FROM baza_infra_notes WHERE section=? ORDER BY created_at DESC", (section,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM baza_infra_notes ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/infra/notes', methods=['POST'])
def api_infra_notes_add():
    d = request.json or {}
    nid = str(uuid.uuid4())
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.execute(
        "INSERT INTO baza_infra_notes (id, section, note, author) VALUES (?,?,?,?)",
        (nid, d.get('section','general'), d.get('note',''), d.get('author','system')))
    conn.commit()
    conn.close()
    return jsonify({"id": nid, "status": "created"})

@app.route('/api/infra/notes/<nid>', methods=['DELETE'])
def api_infra_notes_delete(nid):
    conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'))
    conn.execute("DELETE FROM baza_infra_notes WHERE id=?", (nid,))
    conn.commit()
    conn.close()
    return jsonify({"id": nid, "status": "deleted"})

# ── Agent Usage API ──────────────────────────────────────────────────────────

@app.route('/api/usage')
def api_usage():
    agent_id = request.args.get('agent_id')
    days = int(request.args.get('days', 7))
    try:
        import psycopg2
        pg = psycopg2.connect(host="localhost", port=5432, dbname="baza_agents",
                              user="switchhacker",
                              password=os.environ.get("DB_PASSWORD", "baza2026"))
        cur = pg.cursor()
        cur.execute("""
            SELECT agent_id, COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0),
                   COALESCE(SUM(total_tokens),0), COUNT(*), COALESCE(SUM(duration_ms),0), COALESCE(SUM(cost),0)
            FROM agent_usage WHERE created_at > NOW() - INTERVAL '%s days'
            """ + (" AND agent_id = %s" if agent_id else "") + """
            GROUP BY agent_id ORDER BY SUM(total_tokens) DESC
        """, (days, agent_id) if agent_id else (days,))
        by_agent = [{"agent_id": r[0], "prompt_tokens": r[1], "completion_tokens": r[2],
                      "total_tokens": r[3], "call_count": r[4], "duration_ms": r[5], "cost": float(r[6])} for r in cur.fetchall()]
        cur.execute("SELECT provider, COALESCE(SUM(total_tokens),0), COUNT(*) FROM agent_usage WHERE created_at > NOW() - INTERVAL '%s days' GROUP BY provider", (days,))
        by_provider = [{"provider": r[0], "total_tokens": r[1], "call_count": r[2]} for r in cur.fetchall()]
        cur.execute("SELECT DATE(created_at), COALESCE(SUM(total_tokens),0), COUNT(*) FROM agent_usage WHERE created_at > NOW() - INTERVAL '%s days' GROUP BY DATE(created_at) ORDER BY DATE(created_at)", (days,))
        by_day = [{"day": r[0].isoformat() if r[0] else None, "total_tokens": r[1], "call_count": r[2]} for r in cur.fetchall()]
        total_tokens = sum(a["total_tokens"] for a in by_agent)
        total_calls = sum(a["call_count"] for a in by_agent)
        cur.close(); pg.close()
        return jsonify({"days": days, "total_tokens": total_tokens, "total_calls": total_calls,
                        "by_agent": by_agent, "by_provider": by_provider, "by_day": by_day})
    except Exception as e:
        return jsonify({"error": str(e), "by_agent": [], "by_provider": [], "by_day": [],
                        "total_tokens": 0, "total_calls": 0})

# ── AHB123 Chat Widget API (public-facing, Nova Sterling) ────────────────────

@app.route('/api/ahb/widget/chat', methods=['POST'])
def api_ahb_widget_chat():
    """Public chat widget endpoint — visitor sends message, Nova responds via LLM."""
    data = request.json or {}
    message = (data.get('message') or '').strip()
    chat_id = data.get('chat_id', '')
    visitor_name = data.get('visitor_name', '')
    visitor_email = data.get('visitor_email', '')
    visitor_phone = data.get('visitor_phone', '')

    if not message:
        return jsonify({'error': 'Message required'}), 400

    conn = _ahb_db()

    # Create or get chat
    if not chat_id:
        chat_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO ahb_chats (id, visitor_name, visitor_email, visitor_phone, channel, status, assigned_agent) VALUES (?,?,?,?,?,?,?)",
            (chat_id, visitor_name, visitor_email, visitor_phone, 'website', 'active', 'nova_sterling'))
        conn.commit()
    else:
        # Update visitor info if provided
        if visitor_name or visitor_email or visitor_phone:
            updates = []
            params = []
            if visitor_name: updates.append("visitor_name=?"); params.append(visitor_name)
            if visitor_email: updates.append("visitor_email=?"); params.append(visitor_email)
            if visitor_phone: updates.append("visitor_phone=?"); params.append(visitor_phone)
            if updates:
                params.append(chat_id)
                conn.execute(f"UPDATE ahb_chats SET {','.join(updates)} WHERE id=?", params)
                conn.commit()

    # Save user message
    conn.execute(
        "INSERT INTO ahb_chat_messages (chat_id, role, content, agent_id) VALUES (?,?,?,?)",
        (chat_id, 'user', message, None))
    conn.commit()

    # Get conversation history for context
    history = conn.execute(
        "SELECT role, content FROM ahb_chat_messages WHERE chat_id=? ORDER BY created_at ASC",
        (chat_id,)).fetchall()
    conn.close()

    # Build messages for Nova
    system_prompt = """You are Nova Sterling — Director of Client Relations at All Home Building Co LLC (AHBCO).
You are the live chat assistant on ahb123.com. You are warm, professional, and helpful.

ABOUT THE COMPANY:
- All Home Building Co LLC, licensed PA HIC residential general contractor
- President: Sergey Tkach | Phone: 800-484-6404
- Address: 2725 Colmar Ave, Bensalem PA 19020
- Website: ahb123.com
- Services: Kitchen & bathroom remodeling, basement finishing, home additions, new construction, commercial build-outs
- Service area: Philadelphia, Bucks County, Montgomery County, Delaware County, Chester County PA

YOUR GOALS:
1. Greet warmly and ask how you can help
2. Answer questions about services, process, and service areas
3. Qualify leads: ask for name, project type, location, timeline, and budget range
4. When you have enough info, say you'll have someone reach out within 24 hours
5. Keep responses concise (2-4 sentences max), friendly, professional

RULES:
- Never make up pricing — say "every project is unique, we provide free estimates"
- Never schedule appointments directly — say the team will reach out
- If asked about something outside your scope, say you'll connect them with the right person
- Use plain text only, no markdown
- Be conversational, not robotic"""

    messages = [{"role": r[0], "content": r[1]} for r in history[-20:]]  # Last 20 messages

    # Call Ollama for Nova's response
    try:
        import urllib.request as _urllib
        ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        payload = json.dumps({
            "model": "qwen2.5:14b",
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "stream": False,
            "options": {"num_predict": 300, "temperature": 0.7}
        }).encode()
        req = _urllib.Request(f"{ollama_url}/api/chat", data=payload,
                              headers={"Content-Type": "application/json"})
        with _urllib.urlopen(req, timeout=120) as r:
            result = json.loads(r.read())
            reply = result.get("message", {}).get("content", "")
    except Exception as e:
        reply = "Thanks for reaching out! I'm having a brief technical moment. Please call us at 800-484-6404 or try again in a minute."

    if not reply.strip():
        reply = "Thanks for your message! How can I help you with your home improvement project today?"

    # Save Nova's response
    conn2 = _ahb_db()
    conn2.execute(
        "INSERT INTO ahb_chat_messages (chat_id, role, content, agent_id) VALUES (?,?,?,?)",
        (chat_id, 'assistant', reply.strip(), 'nova_sterling'))
    conn2.execute("UPDATE ahb_chats SET updated_at=? WHERE id=?",
                  (datetime.datetime.now().isoformat(), chat_id))
    conn2.commit()
    conn2.close()

    return jsonify({
        'chat_id': chat_id,
        'reply': reply.strip(),
        'agent': 'Nova Sterling'
    })


@app.route('/api/ahb/widget/history', methods=['GET'])
def api_ahb_widget_history():
    """Get chat history for a widget session."""
    chat_id = request.args.get('chat_id', '')
    if not chat_id:
        return jsonify([])
    conn = _ahb_db()
    rows = conn.execute(
        "SELECT role, content, created_at FROM ahb_chat_messages WHERE chat_id=? ORDER BY created_at ASC",
        (chat_id,)).fetchall()
    conn.close()
    return jsonify([{"role": r[0], "content": r[1], "time": r[2]} for r in rows])


@app.route('/ahb-chat-widget.js')
def ahb_chat_widget_js():
    """Serve the embeddable chat widget script."""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'static'), 'ahb-chat-widget.js',
                               mimetype='application/javascript')


# ── Baza Cloud ────────────────────────────────────────────────────────────────

if CLOUD_ENABLED:
    app.secret_key = os.environ.get('FLASK_SECRET', 'baza-cloud-secret-change-me')
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'cloud_login_page'

    class CloudUser(UserMixin):
        def __init__(self, id, email, display_name, storage_quota_mb, storage_used_mb, is_admin):
            self.id = id
            self.email = email
            self.display_name = display_name
            self.storage_quota_mb = storage_quota_mb
            self.storage_used_mb = storage_used_mb
            self.is_admin = is_admin

    @login_manager.user_loader
    def load_user(user_id):
        try:
            from core.context_db import get_pool
            pool = get_pool()
            conn = pool.getconn()
            cur = conn.cursor()
            cur.execute("SELECT id, email, display_name, storage_quota_mb, storage_used_mb, is_admin FROM cloud_users WHERE id=%s AND is_active=TRUE", (int(user_id),))
            row = cur.fetchone()
            cur.close()
            pool.putconn(conn)
            if row:
                return CloudUser(*row)
        except Exception:
            pass
        return None

    @app.route('/cloud')
    def cloud_page():
        if not current_user.is_authenticated:
            return redirect('/cloud/login')
        return render_template('cloud.html', user=current_user)

    @app.route('/cloud/login')
    def cloud_login_page():
        return render_template('cloud_login.html')

    @app.route('/api/cloud/register', methods=['POST'])
    def api_cloud_register():
        data = request.json or {}
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        name = data.get('display_name', '').strip()
        if not email or not password:
            return jsonify({'success': False, 'error': 'Email and password required'}), 400
        if len(password) < 6:
            return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        try:
            from core.context_db import get_pool
            pool = get_pool()
            conn = pool.getconn()
            cur = conn.cursor()
            cur.execute("INSERT INTO cloud_users (email, password_hash, display_name) VALUES (%s, %s, %s) RETURNING id", (email, pw_hash, name or email.split('@')[0]))
            user_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            pool.putconn(conn)
            # Create user storage directory
            user_dir = os.path.join(CLOUD_STORAGE, str(user_id))
            os.makedirs(user_dir, exist_ok=True)
            return jsonify({'success': True, 'user_id': user_id})
        except Exception as e:
            if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                return jsonify({'success': False, 'error': 'Email already registered'}), 409
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/cloud/login', methods=['POST'])
    def api_cloud_login():
        data = request.json or {}
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        try:
            from core.context_db import get_pool
            pool = get_pool()
            conn = pool.getconn()
            cur = conn.cursor()
            cur.execute("SELECT id, email, password_hash, display_name, storage_quota_mb, storage_used_mb, is_admin FROM cloud_users WHERE email=%s AND is_active=TRUE", (email,))
            row = cur.fetchone()
            if not row:
                cur.close(); pool.putconn(conn)
                return jsonify({'success': False, 'error': 'Invalid email or password'}), 401
            if not bcrypt.checkpw(password.encode(), row[2].encode()):
                cur.close(); pool.putconn(conn)
                return jsonify({'success': False, 'error': 'Invalid email or password'}), 401
            cur.execute("UPDATE cloud_users SET last_login=NOW() WHERE id=%s", (row[0],))
            conn.commit()
            cur.close()
            pool.putconn(conn)
            user = CloudUser(row[0], row[1], row[3], row[4], row[5], row[6])
            login_user(user)
            return jsonify({'success': True, 'user': {'id': user.id, 'email': user.email, 'name': user.display_name}})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/cloud/logout', methods=['POST'])
    def api_cloud_logout():
        logout_user()
        return jsonify({'success': True})

    @app.route('/api/cloud/me')
    @login_required
    def api_cloud_me():
        u = current_user
        return jsonify({'id': u.id, 'email': u.email, 'name': u.display_name,
                        'storage_quota_mb': u.storage_quota_mb, 'storage_used_mb': u.storage_used_mb,
                        'is_admin': u.is_admin})

    # ── Cloud File Manager ──
    @app.route('/api/cloud/files')
    @login_required
    def api_cloud_files():
        user_dir = os.path.join(CLOUD_STORAGE, str(current_user.id))
        os.makedirs(user_dir, exist_ok=True)
        subdir = request.args.get('path', '')
        target = os.path.realpath(os.path.join(user_dir, subdir))
        if not target.startswith(os.path.realpath(user_dir)):
            return jsonify({'error': 'Invalid path'}), 403
        if not os.path.isdir(target):
            return jsonify({'error': 'Not a directory'}), 404
        items = []
        for name in sorted(os.listdir(target)):
            full = os.path.join(target, name)
            is_dir = os.path.isdir(full)
            items.append({'name': name, 'is_dir': is_dir,
                          'size': os.path.getsize(full) if not is_dir else 0,
                          'modified': os.path.getmtime(full)})
        return jsonify({'path': subdir, 'items': items})

    @app.route('/api/cloud/files/upload', methods=['POST'])
    @login_required
    def api_cloud_upload():
        user_dir = os.path.join(CLOUD_STORAGE, str(current_user.id))
        subdir = request.form.get('path', '')
        target = os.path.realpath(os.path.join(user_dir, subdir))
        if not target.startswith(os.path.realpath(user_dir)):
            return jsonify({'error': 'Invalid path'}), 403
        os.makedirs(target, exist_ok=True)
        saved = []
        for f in request.files.getlist('files'):
            safe = re.sub(r'[^\w.\-]', '_', f.filename or 'upload')
            f.save(os.path.join(target, safe))
            saved.append(safe)
        return jsonify({'success': True, 'files': saved})

    @app.route('/api/cloud/files/download/<path:filepath>')
    @login_required
    def api_cloud_download(filepath):
        user_dir = os.path.join(CLOUD_STORAGE, str(current_user.id))
        target = os.path.realpath(os.path.join(user_dir, filepath))
        if not target.startswith(os.path.realpath(user_dir)):
            return jsonify({'error': 'Invalid path'}), 403
        return send_from_directory(os.path.dirname(target), os.path.basename(target), as_attachment=True)

    @app.route('/api/cloud/files/mkdir', methods=['POST'])
    @login_required
    def api_cloud_mkdir():
        data = request.json or {}
        user_dir = os.path.join(CLOUD_STORAGE, str(current_user.id))
        new_dir = os.path.realpath(os.path.join(user_dir, data.get('path', '')))
        if not new_dir.startswith(os.path.realpath(user_dir)):
            return jsonify({'error': 'Invalid path'}), 403
        os.makedirs(new_dir, exist_ok=True)
        return jsonify({'success': True})

    @app.route('/api/cloud/files/delete', methods=['POST'])
    @login_required
    def api_cloud_delete():
        data = request.json or {}
        user_dir = os.path.join(CLOUD_STORAGE, str(current_user.id))
        target = os.path.realpath(os.path.join(user_dir, data.get('path', '')))
        if not target.startswith(os.path.realpath(user_dir)):
            return jsonify({'error': 'Invalid path'}), 403
        if os.path.isdir(target):
            import shutil; shutil.rmtree(target)
        elif os.path.isfile(target):
            os.remove(target)
        return jsonify({'success': True})

    # ── Cloud Agent Chat ──
    @app.route('/api/cloud/chat/<agent_id>', methods=['POST'])
    @login_required
    def api_cloud_chat(agent_id):
        """Per-user agent chat -- runs inference via Ollama, stores in per-user context."""
        data = request.json or {}
        message = data.get('message', '').strip()
        if not message:
            return jsonify({'error': 'Message required'}), 400

        # Load agent config
        config = load_config()
        agent_cfg = config.get('agents', {}).get(agent_id)
        if not agent_cfg:
            return jsonify({'error': f'Unknown agent: {agent_id}'}), 404

        user_id = current_user.id

        try:
            from core.context_db import get_pool
            pool = get_pool()
            conn = pool.getconn()
            cur = conn.cursor()

            # Save user message
            cur.execute("INSERT INTO cloud_conversations (user_id, agent_id, role, content) VALUES (%s, %s, 'user', %s)", (user_id, agent_id, message))

            # Get conversation history (last 20 messages)
            cur.execute("SELECT role, content FROM cloud_conversations WHERE user_id=%s AND agent_id=%s ORDER BY created_at DESC LIMIT 20", (user_id, agent_id))
            history = [{'role': r[0], 'content': r[1]} for r in reversed(cur.fetchall())]

            # Get per-user agent memory
            cur.execute("SELECT key, value FROM cloud_agent_memory WHERE user_id=%s AND agent_id=%s", (user_id, agent_id))
            memories = {r[0]: r[1] for r in cur.fetchall()}

            conn.commit()
            cur.close()
            pool.putconn(conn)

            # Build system prompt
            system = agent_cfg.get('system_prompt', f'You are {agent_cfg.get("name", agent_id)}.')
            if memories:
                mem_str = '\n'.join(f'- {k}: {v}' for k, v in list(memories.items())[:20])
                system += f'\n\nUser context:\n{mem_str}'

            # Run inference via Ollama
            import urllib.request
            model = agent_cfg.get('model', 'qwen2.5:14b')
            messages = [{'role': 'system', 'content': system}] + history

            payload = json.dumps({
                'model': model,
                'messages': messages,
                'stream': False,
                'options': {'num_predict': 1024, 'temperature': 0.7}
            }).encode()

            # Try both Ollama instances
            response_text = None
            for port in [11434, 11435]:
                try:
                    req = urllib.request.Request(f'http://localhost:{port}/api/chat',
                        data=payload, headers={'Content-Type': 'application/json'}, method='POST')
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        result = json.loads(resp.read())
                        response_text = result.get('message', {}).get('content', '')
                        if response_text:
                            break
                except Exception:
                    continue

            if not response_text:
                return jsonify({'error': 'All inference backends unavailable'}), 503

            # Save assistant response
            pool = get_pool()
            conn = pool.getconn()
            cur = conn.cursor()
            cur.execute("INSERT INTO cloud_conversations (user_id, agent_id, role, content) VALUES (%s, %s, 'assistant', %s)", (user_id, agent_id, response_text))
            conn.commit()
            cur.close()
            pool.putconn(conn)

            return jsonify({'success': True, 'response': response_text, 'agent': agent_cfg.get('name', agent_id)})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/cloud/chat/<agent_id>/history')
    @login_required
    def api_cloud_chat_history(agent_id):
        try:
            from core.context_db import get_pool
            pool = get_pool()
            conn = pool.getconn()
            cur = conn.cursor()
            cur.execute("SELECT role, content, created_at FROM cloud_conversations WHERE user_id=%s AND agent_id=%s ORDER BY created_at ASC LIMIT 100", (current_user.id, agent_id))
            msgs = [{'role': r[0], 'content': r[1], 'created_at': str(r[2])} for r in cur.fetchall()]
            cur.close()
            pool.putconn(conn)
            return jsonify(msgs)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/cloud/chat/<agent_id>/clear', methods=['POST'])
    @login_required
    def api_cloud_chat_clear(agent_id):
        try:
            from core.context_db import get_pool
            pool = get_pool()
            conn = pool.getconn()
            cur = conn.cursor()
            cur.execute("DELETE FROM cloud_conversations WHERE user_id=%s AND agent_id=%s", (current_user.id, agent_id))
            conn.commit()
            cur.close()
            pool.putconn(conn)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/cloud/memory/<agent_id>', methods=['POST'])
    @login_required
    def api_cloud_memory_set(agent_id):
        data = request.json or {}
        key = data.get('key', '')
        value = data.get('value', '')
        if not key:
            return jsonify({'error': 'Key required'}), 400
        try:
            from core.context_db import get_pool
            pool = get_pool()
            conn = pool.getconn()
            cur = conn.cursor()
            cur.execute("""INSERT INTO cloud_agent_memory (user_id, agent_id, key, value, category, updated_at)
                VALUES (%s,%s,%s,%s,%s,NOW()) ON CONFLICT (user_id, agent_id, key) DO UPDATE SET value=%s, updated_at=NOW()""",
                (current_user.id, agent_id, key, value, data.get('category','general'), value))
            conn.commit()
            cur.close()
            pool.putconn(conn)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ── Admin: user management ──
    @app.route('/api/cloud/admin/users')
    @login_required
    def api_cloud_admin_users():
        if not current_user.is_admin:
            return jsonify({'error': 'Admin only'}), 403
        try:
            from core.context_db import get_pool
            pool = get_pool()
            conn = pool.getconn()
            cur = conn.cursor()
            cur.execute("SELECT id, email, display_name, storage_quota_mb, storage_used_mb, is_active, is_admin, created_at, last_login FROM cloud_users ORDER BY created_at DESC")
            users = [{'id':r[0],'email':r[1],'name':r[2],'quota_mb':r[3],'used_mb':r[4],'active':r[5],'admin':r[6],'created':str(r[7]),'last_login':str(r[8]) if r[8] else None} for r in cur.fetchall()]
            cur.close()
            pool.putconn(conn)
            return jsonify(users)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/cloud/storage/usage')
    @login_required
    def api_cloud_storage_usage():
        user_dir = os.path.join(CLOUD_STORAGE, str(current_user.id))
        if not os.path.isdir(user_dir):
            return jsonify({'used_mb': 0, 'quota_mb': current_user.storage_quota_mb, 'percent': 0})
        total = 0
        for root, dirs, files in os.walk(user_dir):
            for f in files:
                total += os.path.getsize(os.path.join(root, f))
        used_mb = round(total / 1024 / 1024, 1)
        return jsonify({'used_mb': used_mb, 'quota_mb': current_user.storage_quota_mb,
                        'percent': round(used_mb / current_user.storage_quota_mb * 100, 1) if current_user.storage_quota_mb else 0})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888, debug=False)
