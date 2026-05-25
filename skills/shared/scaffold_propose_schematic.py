"""Skill: Rex proposes a schematic for a project, auto-populated from its BOM.

Reads `{project_id, node_id, bom_ids?, description?}`, fetches BOM rows from
`project_bom`, matches each row to a component in the Baza components library,
lays the components out in a 4-column grid, and synthesizes a small set of
sensible wires (power/ground rails to the first MCU, plus the first sensor's
signal pin to a free GPIO). The resulting schematic payload is merged into the
target node's `payload_json`.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


# Layout constants
GRID_COLS = 4
X_SPACING = 250
Y_SPACING = 250
X_ORIGIN = 60
Y_ORIGIN = 60
MAX_COMPONENTS = 16

# Sequence of GPIOs we hand out for sensor signal hookups
_GPIO_SEQUENCE = ["GPIO2", "GPIO4", "GPIO5", "GPIO12", "GPIO13", "GPIO14"]


def _fetch_bom_rows(con, project_id, bom_ids):
    """Return list of project_bom rows (dicts) for the project, optionally
    filtered by an explicit list of bom row ids."""
    con.row_factory = sqlite3.Row
    if bom_ids:
        placeholders = ",".join("?" for _ in bom_ids)
        q = (f"SELECT * FROM project_bom WHERE project_id=? "
             f"AND id IN ({placeholders}) ORDER BY id")
        rows = con.execute(q, (project_id, *bom_ids)).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM project_bom WHERE project_id=? ORDER BY id",
            (project_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def _layout(idx):
    """Return (x, y) for the idx-th component in the grid."""
    col = idx % GRID_COLS
    row = idx // GRID_COLS
    return X_ORIGIN + col * X_SPACING, Y_ORIGIN + row * Y_SPACING


def _first_pin(component, kinds):
    """Return the first pin on `component` whose kind is in `kinds`, or None."""
    if not component:
        return None
    if isinstance(kinds, str):
        kinds = [kinds]
    for pin in component.get("pins", []):
        if pin["kind"] in kinds:
            return pin
    return None


def _pin_exists(component, pin_name):
    if not component:
        return False
    return any(p["name"] == pin_name for p in component.get("pins", []))


def _build_schematic(matched, library_get):
    """Given list of (instance_id, component_dict, label) tuples, build a
    schematic payload (components + wires)."""
    components_out = []
    for idx, (inst_id, comp, label) in enumerate(matched):
        x, y = _layout(idx)
        components_out.append({
            "instance_id": inst_id,
            "component_id": comp["id"],
            "x": x,
            "y": y,
            "label": label,
        })

    wires = []

    # Find the first MCU instance — it owns the power rails + GPIO bus
    mcu_entry = None
    for inst_id, comp, _label in matched:
        if comp["category"] == "mcu":
            mcu_entry = (inst_id, comp)
            break

    if mcu_entry is not None:
        mcu_id, mcu_comp = mcu_entry
        mcu_power_pin = _first_pin(mcu_comp, ["power"])
        mcu_gnd_pin = _first_pin(mcu_comp, ["ground"])

        # Connect every non-MCU component's power/ground pins to the MCU rails
        for inst_id, comp, _label in matched:
            if inst_id == mcu_id:
                continue

            other_power = _first_pin(comp, ["power"])
            if other_power and mcu_power_pin:
                wires.append({
                    "from": f"{mcu_id}.{mcu_power_pin['name']}",
                    "to": f"{inst_id}.{other_power['name']}",
                    "color": "power",
                })

            other_gnd = _first_pin(comp, ["ground"])
            if other_gnd and mcu_gnd_pin:
                wires.append({
                    "from": f"{mcu_id}.{mcu_gnd_pin['name']}",
                    "to": f"{inst_id}.{other_gnd['name']}",
                    "color": "ground",
                })

        # Hand the first sensor's signal/gpio pin to a free MCU GPIO
        first_sensor = None
        for inst_id, comp, _label in matched:
            if comp["category"] == "sensor":
                first_sensor = (inst_id, comp)
                break

        if first_sensor is not None:
            s_id, s_comp = first_sensor
            s_signal = _first_pin(s_comp, ["signal", "gpio"])
            if s_signal:
                for gpio_name in _GPIO_SEQUENCE:
                    if _pin_exists(mcu_comp, gpio_name):
                        wires.append({
                            "from": f"{mcu_id}.{gpio_name}",
                            "to": f"{s_id}.{s_signal['name']}",
                            "color": "signal",
                        })
                        break

    notes = "Auto-proposed from BOM. Drag to rearrange; click pins to wire."

    return components_out, wires, notes


def main():
    args = json.loads(os.environ.get("SKILL_ARGS") or "{}")
    db_path = args.get("_db_path") or os.environ.get("BAZA_PROJECTS_DB")
    if not db_path:
        print(json.dumps({"error": "no db path"})); sys.exit(1)

    project_id = args.get("project_id")
    node_id = args.get("node_id")
    if not project_id or node_id is None:
        print(json.dumps({"error": "project_id and node_id required"}))
        sys.exit(1)

    bom_ids = args.get("bom_ids") or []
    description = args.get("description") or ""

    # Fetch BOM rows
    con = sqlite3.connect(db_path)
    try:
        rows = _fetch_bom_rows(con, project_id, bom_ids)
    finally:
        con.close()

    from core.baza_components_library import match_component, get_component
    from core.scaffold_engine import ScaffoldEngine

    # Match each BOM row to a component
    matched = []  # list of (instance_id, component_dict, label)
    truncated_from = 0
    for row in rows:
        comp = match_component(row.get("name") or "")
        if not comp:
            continue
        if len(matched) >= MAX_COMPONENTS:
            truncated_from = len(rows)
            break
        # Stable instance ids based on category prefix + ordinal
        prefix = {
            "mcu": "u",
            "sensor": "s",
            "actuator": "a",
            "display": "d",
            "power": "p",
            "passive": "r",
            "module": "m",
            "communication": "c",
        }.get(comp["category"], "x")
        ordinal = sum(1 for (_i, c, _l) in matched if c["category"] == comp["category"]) + 1
        inst_id = f"{prefix}{ordinal}"
        label = row.get("name") or comp["name"]
        matched.append((inst_id, comp, label))

    components_out, wires, notes = _build_schematic(matched, get_component)

    if truncated_from:
        notes = (f"Showing first {MAX_COMPONENTS} of {truncated_from} parts; "
                 f"edit to add more.")

    schematic = {
        "components": components_out,
        "wires": wires,
        "notes": notes,
    }
    if description:
        schematic["description"] = description

    # Merge into the node's payload
    eng = ScaffoldEngine(db_path)
    existing = eng.get_node(node_id)
    if not existing:
        print(json.dumps({"error": "node not found"})); sys.exit(1)

    payload = {}
    if existing.get("payload_json"):
        try:
            payload = json.loads(existing["payload_json"])
        except Exception:
            payload = {}
    payload["schematic"] = schematic

    eng.update_node(node_id, payload_json=json.dumps(payload, default=str))
    eng.emit_event(
        project_id,
        node_id=node_id,
        event_type="schematic_proposed",
        actor="rex_smasher",
        payload={"component_count": len(components_out),
                 "wire_count": len(wires)},
    )

    print(json.dumps({
        "ok": True,
        "component_count": len(components_out),
        "wire_count": len(wires),
    }))


if __name__ == "__main__":
    main()
