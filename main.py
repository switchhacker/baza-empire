#!/usr/bin/env python3
"""
Baza Empire Agent Framework v3
Launches all agents as concurrent processes.
"""
import os
import sys
import yaml
import multiprocessing
from core.agent import BazaAgent

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'agents.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def run_agent(agent_id: str, agent_config: dict, global_config: dict):
    """Run a single agent in its own process."""
    agent = BazaAgent(agent_id, agent_config, global_config)
    agent.run()

def main():
    config = load_config()
    agents = config.get('agents', {})
    
    # Filter to specific agent if passed as argument
    target = sys.argv[1] if len(sys.argv) > 1 else None
    
    processes = []
    for agent_id, agent_config in agents.items():
        if target and agent_id != target:
            continue
        p = multiprocessing.Process(
            target=run_agent,
            args=(agent_id, agent_config, config),
            name=agent_id
        )
        p.start()
        processes.append(p)
        print(f"✅ Started {agent_config['name']}")

    for p in processes:
        p.join()

if __name__ == '__main__':
    main()
