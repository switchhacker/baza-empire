"""Schema-guided extraction: page markdown → local Ollama → validated JSON.
LOCAL ONLY (house hard rule) — OLLAMA_URL defaults to the AMD instance."""
import json
import os

import httpx

_TYPES = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict,
}


def validate(data, schema, path="$") -> list[str]:
    """Minimal JSON-schema subset: type / required / properties / items.

    null semantics match real JSON Schema's graceful-degradation intent: a
    required key must be *present*, but its value may legitimately be null
    (the model's honest "not found on page" signal) — so required-ness is a
    key-presence check only, and type validation is skipped for null values
    at any depth, since null is an acceptable value for any declared type.
    """
    errs: list[str] = []
    t = schema.get("type")
    if data is None:
        # Null is legitimate for property/item values ("not found on page"),
        # but the document root itself can never be "absent" — a bare null
        # reply from the model must fail, not report success with no data.
        if path == "$":
            return [f"{path}: expected {t or 'value'}, got null"]
        return errs
    if t in _TYPES and not isinstance(data, _TYPES[t]):
        if not (t == "number" and isinstance(data, int)):
            return [f"{path}: expected {t}, got {type(data).__name__}"]
    if t == "object" and isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                errs.append(f"{path}.{key}: required field missing")
        for key, sub in (schema.get("properties") or {}).items():
            if key in data:
                errs.extend(validate(data[key], sub, f"{path}.{key}"))
    if t == "array" and isinstance(data, list) and schema.get("items"):
        for i, item in enumerate(data):
            errs.extend(validate(item, schema["items"], f"{path}[{i}]"))
    return errs


async def extract(content: str, schema: dict, prompt: str | None = None,
                  model: str | None = None) -> dict:
    ollama = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = model or os.environ.get("PB_EXTRACT_MODEL", "glm-4.7-flash")
    system = (
        "Extract structured data from the provided web page content. "
        "Respond with JSON matching the requested schema exactly. "
        "Use null for values not present in the content — never invent data."
    )
    user = (
        f"{prompt or 'Extract the data described by the schema.'}\n\n"
        f"PAGE CONTENT:\n{content[:24000]}"
    )
    last_err = None
    for _ in range(2):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if last_err:
            messages.append({
                "role": "user",
                "content": f"Previous attempt failed validation: {last_err}. "
                           "Return corrected JSON only.",
            })
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(f"{ollama}/api/chat", json={
                "model": model, "messages": messages, "format": schema,
                "stream": False, "options": {"num_ctx": 16384, "temperature": 0},
            })
            resp.raise_for_status()
            raw = resp.json()["message"]["content"]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            last_err = f"invalid JSON: {e}"
            continue
        errs = validate(data, schema)
        if not errs:
            return {"success": True, "data": data, "model": model}
        last_err = "; ".join(errs[:5])
    return {"success": False, "error": f"validation failed after retry: {last_err}",
            "model": model}
