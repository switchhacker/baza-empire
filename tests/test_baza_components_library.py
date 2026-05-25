import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_list_components_nonempty():
    from core.baza_components_library import list_components
    items = list_components()
    assert len(items) >= 40


def test_get_component_known():
    from core.baza_components_library import get_component
    c = get_component("esp32-devkit")
    assert c is not None
    assert c["category"] == "mcu"
    assert any(p["kind"] == "ground" for p in c["pins"])


def test_match_component_keyword():
    from core.baza_components_library import match_component
    assert match_component("HC-SR04 Ultrasonic").get("id") == "hc-sr04"
    assert match_component("ESP32 DevKit 30 pin").get("id") == "esp32-devkit"
    assert match_component("ssd1306 oled 128x64").get("id") == "ssd1306-oled-i2c"
    assert match_component("unknown widget xyz") is None
