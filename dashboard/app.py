#!/usr/bin/env python3
"""
Baza Empire Agent Dashboard
A lightweight Flask web app to monitor and manage agents.
"""
import os
import json
import yaml
import redis
import subprocess
from flask import Flask, render_template, request, jsonify, redirect, url_for

app = Flask(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'agents.yaml')

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

def get_redis():
    config = load_config()
    r = config.get('redis', {})
    return redis.Redis(host=r.get('host', 'localhost'), port=r.get('port', 6379), decode_responses=True)

def get_agent_status(agent_id):
    service_name = f"baza-agent-{agent_id.replace('_', '-')}"
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', service_name],
            capture_output=True, text=True, timeout=5
        )
        status = result.stdout.strip()
        return 'online' if status == 'active' else 'offline'
    except:
        return 'unknown'

def get_recent_messages(agent_id, limit=20):
    try:
        r = get_redis()
        keys = r.keys(f"chat:{agent_id}:*:history")
        all_messages = []
        for key in keys:
            chat_id = key.split(':')[2]
            msgs = r.lrange(key, -limit, -1)
            for m in msgs:
                try:
                    parsed = json.loads(m)
                    parsed['chat_id'] = chat_id
                    all_messages.append(parsed)
                except:
                    pass
        return all_messages[-limit:]
    except:
        return []

def restart_agent(agent_id):
    service_name = f"baza-agent-{agent_id.replace('_', '-')}"
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', service_name], timeout=10)
        return True
    except:
        return False

def stop_agent(agent_id):
    service_name = f"baza-agent-{agent_id.replace('_', '-')}"
    try:
        subprocess.run(['sudo', 'systemctl', 'stop', service_name], timeout=10)
        return True
    except:
        return False

def get_agent_logs(agent_id, lines=50):
    service_name = f"baza-agent-{agent_id.replace('_', '-')}"
    try:
        result = subprocess.run(
            ['journalctl', '-u', service_name, '-n', str(lines), '--no-pager', '--output=short'],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout
    except:
        return "Could not fetch logs."

# ─── Routes ──────────────────────────────────────────────────────────────────

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
            'name': agent_config['name'],
            'role': agent_config['role'],
            'model': agent_config['model'],
            'status': status,
            'recent_messages': messages
        })
    return render_template('index.html', agents=agent_data)

@app.route('/agent/<agent_id>')
def agent_detail(agent_id):
    config = load_config()
    agents = config.get('agents', {})
    if agent_id not in agents:
        return "Agent not found", 404
    agent_config = agents[agent_id]
    status = get_agent_status(agent_id)
    messages = get_recent_messages(agent_id, 30)
    logs = get_agent_logs(agent_id, 50)
    available_models = [
        "mistral-small:22b",
        "qwen2.5:14b",
        "deepseek-coder-v2:16b",
        "nemotron-3-nano:latest",
        "MHKetbi/nvidia_Llama-3.3-Nemotron-Super-49B-v1:latest",
        "llama3.1:8b"
    ]
    return render_template('agent.html',
        agent_id=agent_id,
        agent=agent_config,
        status=status,
        messages=messages,
        logs=logs,
        available_models=available_models
    )

@app.route('/agent/<agent_id>/edit', methods=['POST'])
def edit_agent(agent_id):
    config = load_config()
    if agent_id not in config['agents']:
        return jsonify({'error': 'Agent not found'}), 404
    data = request.json
    if 'model' in data:
        config['agents'][agent_id]['model'] = data['model']
    if 'system_prompt' in data:
        config['agents'][agent_id]['system_prompt'] = data['system_prompt']
    if 'role' in data:
        config['agents'][agent_id]['role'] = data['role']
    save_config(config)
    return jsonify({'success': True})

@app.route('/agent/<agent_id>/restart', methods=['POST'])
def restart(agent_id):
    success = restart_agent(agent_id)
    return jsonify({'success': success})

@app.route('/agent/<agent_id>/stop', methods=['POST'])
def stop(agent_id):
    success = stop_agent(agent_id)
    return jsonify({'success': success})

@app.route('/agent/<agent_id>/logs')
def logs(agent_id):
    lines = request.args.get('lines', 50, type=int)
    log_output = get_agent_logs(agent_id, lines)
    return jsonify({'logs': log_output})

@app.route('/api/status')
def api_status():
    config = load_config()
    agents = config.get('agents', {})
    result = {}
    for agent_id in agents:
        result[agent_id] = get_agent_status(agent_id)
    return jsonify(result)

@app.route('/api/messages/<agent_id>')
def api_messages(agent_id):
    limit = request.args.get('limit', 20, type=int)
    messages = get_recent_messages(agent_id, limit)
    return jsonify(messages)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888, debug=False)
