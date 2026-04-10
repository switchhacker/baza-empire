#!/usr/bin/env python3
"""Generate a change order from description + amount."""
import os, json
from datetime import datetime

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
project = args.get("project", "")
client = args.get("client", "")
description = args.get("description", "")
amount = float(args.get("amount", 0))
co_number = args.get("number", "CO-001")

change_order = {
    "change_order_number": co_number,
    "date": datetime.now().strftime("%Y-%m-%d"),
    "project": project,
    "client": client,
    "description": description,
    "amount": amount,
    "tax": round(amount * 0.06, 2),
    "total": round(amount * 1.06, 2),
    "status": "pending_approval",
    "text": (
        f"CHANGE ORDER {co_number}\n"
        f"Date: {datetime.now().strftime('%B %d, %Y')}\n"
        f"Project: {project}\nClient: {client}\n\n"
        f"Description of Change:\n{description}\n\n"
        f"Additional Cost: ${amount:,.2f}\n"
        f"Tax (6%): ${amount * 0.06:,.2f}\n"
        f"Total: ${amount * 1.06:,.2f}\n\n"
        f"Signature: ________________  Date: ________"
    )
}
print(json.dumps(change_order))
