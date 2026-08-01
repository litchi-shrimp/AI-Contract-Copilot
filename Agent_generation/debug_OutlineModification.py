#!/usr/bin/env python3
"""
调试 大纲修改 Agent
"""
import json
from agent.agents.OutlineModificationAgent import OutlineModificationAgent
from pathlib import Path

if __name__ == "__main__":
    CURRENT_FILE = Path(__file__).resolve() # /Agent_generation/main.py
    BASE_DIR = CURRENT_FILE.parent   # /Agent_generation
    match_result = []
    outline = json.load(open(BASE_DIR / "agent"/ "data"/ "initial_outline.json", "r", encoding="utf-8"))
    outline_modification_agent = OutlineModificationAgent("modification_agent", data=outline, match_data=match_result)
    user_confirmed, outline = outline_modification_agent.interactive_run()
    print(json.dumps(outline, ensure_ascii=False, indent=2))