#!/usr/bin/env python3
"""Compare two texts and show differences."""
import os, json, difflib
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
text1 = args.get("text1", ""); text2 = args.get("text2", "")
diff = list(difflib.unified_diff(text1.splitlines(), text2.splitlines(), lineterm=""))
print(json.dumps({"diff": "\n".join(diff[:100]), "changes": len(diff)}))
