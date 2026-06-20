"""Baza Components Library.

Lookup table of common electronics components used by Rex / Phil for
auto-proposing schematics from a BOM. Each component exposes a small,
opinionated subset of its real pins (just enough to draw a usable wiring
diagram in the dashboard's schematic editor) plus rendering hints.

Categories:
    mcu | sensor | actuator | display | power | passive | module | communication
"""
from __future__ import annotations

from typing import Optional


# Sensible defaults for SVG rendering
_DEFAULT_W = 120
_DEFAULT_H = 80


def _c(component_id, name, category, pins, *, width=None, height=None,
       match_keywords=None):
    """Build a normalized component dict."""
    return {
        "id": component_id,
        "name": name,
        "category": category,
        "pins": pins,
        "width": width if width is not None else _DEFAULT_W,
        "height": height if height is not None else _DEFAULT_H,
        "match_keywords": [k.lower() for k in (match_keywords or [])],
    }


def _pin(name, kind, position):
    return {"name": name, "kind": kind, "position": position}


# --------------------------------------------------------------------------
# Component definitions
# --------------------------------------------------------------------------

_COMPONENT_LIST = [
    # ---------------- MCUs (8) ----------------
    _c("esp32-devkit", "ESP32 DevKit (30-pin)", "mcu",
       width=140, height=280,
       match_keywords=["esp32", "esp32-devkit", "esp32 dev", "esp32-wroom",
                       "wemos d1 mini", "esp32 devkit", "esp32 wroom"],
       pins=[
           _pin("3V3", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("GPIO15", "gpio", "left"),
           _pin("GPIO2", "gpio", "left"),
           _pin("GPIO4", "gpio", "left"),
           _pin("GPIO5", "gpio", "left"),
           _pin("VIN", "power", "right"),
           _pin("GND", "ground", "right"),
           _pin("GPIO13", "gpio", "right"),
           _pin("GPIO12", "gpio", "right"),
           _pin("GPIO14", "gpio", "right"),
           _pin("GPIO21", "i2c_sda", "right"),
           _pin("GPIO22", "i2c_scl", "right"),
       ]),
    _c("esp8266-nodemcu", "ESP8266 NodeMCU", "mcu",
       width=140, height=240,
       match_keywords=["esp8266", "nodemcu", "esp8266-nodemcu", "esp-12"],
       pins=[
           _pin("3V3", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("D1", "i2c_scl", "left"),
           _pin("D2", "i2c_sda", "left"),
           _pin("D5", "spi_sck", "left"),
           _pin("VIN", "power", "right"),
           _pin("GND", "ground", "right"),
           _pin("D6", "spi_miso", "right"),
           _pin("D7", "spi_mosi", "right"),
           _pin("D8", "spi_cs", "right"),
           _pin("A0", "analog", "right"),
       ]),
    _c("arduino-uno", "Arduino Uno", "mcu",
       width=180, height=260,
       match_keywords=["arduino uno", "arduino-uno", "uno r3", "atmega328"],
       pins=[
           _pin("5V", "power", "left"),
           _pin("3V3", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("A0", "analog", "left"),
           _pin("A4", "i2c_sda", "left"),
           _pin("A5", "i2c_scl", "left"),
           _pin("D2", "gpio", "right"),
           _pin("D3", "pwm", "right"),
           _pin("D5", "pwm", "right"),
           _pin("D9", "pwm", "right"),
           _pin("D10", "spi_cs", "right"),
           _pin("D11", "spi_mosi", "right"),
           _pin("D12", "spi_miso", "right"),
           _pin("D13", "spi_sck", "right"),
       ]),
    _c("arduino-nano", "Arduino Nano", "mcu",
       width=120, height=260,
       match_keywords=["arduino nano", "arduino-nano", "nano v3"],
       pins=[
           _pin("5V", "power", "left"),
           _pin("3V3", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("A4", "i2c_sda", "left"),
           _pin("A5", "i2c_scl", "left"),
           _pin("D2", "gpio", "right"),
           _pin("D3", "pwm", "right"),
           _pin("D5", "pwm", "right"),
           _pin("D9", "pwm", "right"),
           _pin("D11", "spi_mosi", "right"),
           _pin("D12", "spi_miso", "right"),
           _pin("D13", "spi_sck", "right"),
       ]),
    _c("raspberry-pi-pico", "Raspberry Pi Pico", "mcu",
       width=140, height=260,
       match_keywords=["raspberry pi pico", "rpi pico", "pico", "rp2040",
                       "raspberry-pi-pico"],
       pins=[
           _pin("3V3", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("GP0", "uart_tx", "left"),
           _pin("GP1", "uart_rx", "left"),
           _pin("GP2", "gpio", "left"),
           _pin("VBUS", "power", "right"),
           _pin("VSYS", "power", "right"),
           _pin("GND", "ground", "right"),
           _pin("GP4", "i2c_sda", "right"),
           _pin("GP5", "i2c_scl", "right"),
           _pin("GP16", "spi_miso", "right"),
           _pin("GP18", "spi_sck", "right"),
           _pin("GP19", "spi_mosi", "right"),
       ]),
    _c("stm32-bluepill", "STM32 Blue Pill (F103C8)", "mcu",
       width=140, height=260,
       match_keywords=["stm32", "bluepill", "blue pill", "stm32f103",
                       "stm32-bluepill"],
       pins=[
           _pin("3V3", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("PA0", "analog", "left"),
           _pin("PA1", "analog", "left"),
           _pin("PA9", "uart_tx", "left"),
           _pin("PA10", "uart_rx", "left"),
           _pin("5V", "power", "right"),
           _pin("GND", "ground", "right"),
           _pin("PB6", "i2c_scl", "right"),
           _pin("PB7", "i2c_sda", "right"),
           _pin("PA5", "spi_sck", "right"),
           _pin("PA6", "spi_miso", "right"),
           _pin("PA7", "spi_mosi", "right"),
       ]),
    _c("esp32-cam", "ESP32-CAM", "mcu",
       width=160, height=200,
       match_keywords=["esp32-cam", "esp32 cam", "ai-thinker cam"],
       pins=[
           _pin("5V", "power", "left"),
           _pin("3V3", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("U0T", "uart_tx", "left"),
           _pin("U0R", "uart_rx", "left"),
           _pin("GPIO12", "gpio", "right"),
           _pin("GPIO13", "gpio", "right"),
           _pin("GPIO14", "gpio", "right"),
           _pin("GPIO15", "gpio", "right"),
           _pin("GPIO2", "gpio", "right"),
       ]),
    _c("esp32-s3-devkit", "ESP32-S3 DevKitC-1 / WROOM", "mcu",
       width=170, height=360,
       match_keywords=["esp32-s3", "esp32 s3", "esp32s3", "s3 devkit",
                       "esp32-s3-wroom", "esp32-s3 wroom", "s3-devkit",
                       "esp32-s3 board", "freenove esp32-s3", "wroom cam",
                       "esp32-s3 cam"],
       pins=[
           # Left rail: power in + low GPIO bank
           _pin("5V", "power", "left"),
           _pin("3V3", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("GPIO4", "gpio", "left"),
           _pin("GPIO5", "gpio", "left"),
           _pin("GPIO6", "gpio", "left"),
           _pin("GPIO7", "gpio", "left"),
           _pin("GPIO15", "gpio", "left"),
           _pin("GPIO16", "gpio", "left"),
           _pin("GPIO17", "gpio", "left"),
           # Right rail: VIN + buses + high GPIO bank
           _pin("VIN", "power", "right"),
           _pin("GND", "ground", "right"),
           _pin("GPIO8", "i2c_sda", "right"),
           _pin("GPIO9", "i2c_scl", "right"),
           _pin("GPIO10", "spi_cs", "right"),
           _pin("GPIO11", "spi_mosi", "right"),
           _pin("GPIO12", "spi_sck", "right"),
           _pin("GPIO13", "spi_miso", "right"),
           _pin("GPIO43", "uart_tx", "right"),
           _pin("GPIO44", "uart_rx", "right"),
       ]),
    _c("micro-bit", "BBC micro:bit", "mcu",
       width=180, height=140,
       match_keywords=["microbit", "micro:bit", "micro-bit", "bbc microbit"],
       pins=[
           _pin("3V", "power", "bottom"),
           _pin("GND", "ground", "bottom"),
           _pin("P0", "gpio", "bottom"),
           _pin("P1", "gpio", "bottom"),
           _pin("P2", "gpio", "bottom"),
           _pin("P19", "i2c_scl", "bottom"),
           _pin("P20", "i2c_sda", "bottom"),
       ]),

    # ---------------- Sensors (10) ----------------
    _c("hc-sr04", "HC-SR04 Ultrasonic", "sensor",
       width=140, height=80,
       match_keywords=["hc-sr04", "hcsr04", "sr04", "ultrasonic", "ultrasonic sensor"],
       pins=[
           _pin("VCC", "power", "bottom"),
           _pin("TRIG", "signal", "bottom"),
           _pin("ECHO", "signal", "bottom"),
           _pin("GND", "ground", "bottom"),
       ]),
    _c("dht22", "DHT22 Temp/Humidity", "sensor",
       width=80, height=100,
       match_keywords=["dht22", "am2302", "dht-22"],
       pins=[
           _pin("VCC", "power", "bottom"),
           _pin("DATA", "signal", "bottom"),
           _pin("GND", "ground", "bottom"),
       ]),
    _c("dht11", "DHT11 Temp/Humidity", "sensor",
       width=80, height=100,
       match_keywords=["dht11", "dht-11"],
       pins=[
           _pin("VCC", "power", "bottom"),
           _pin("DATA", "signal", "bottom"),
           _pin("GND", "ground", "bottom"),
       ]),
    _c("bme280", "BME280 Pressure/Temp/Humidity", "sensor",
       width=100, height=80,
       match_keywords=["bme280", "bme-280", "pressure sensor", "barometer"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SCL", "i2c_scl", "right"),
           _pin("SDA", "i2c_sda", "right"),
       ]),
    _c("mpu6050", "MPU-6050 IMU", "sensor",
       width=100, height=80,
       match_keywords=["mpu6050", "mpu-6050", "imu", "gyroscope", "accelerometer"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SCL", "i2c_scl", "right"),
           _pin("SDA", "i2c_sda", "right"),
           _pin("INT", "signal", "right"),
       ]),
    _c("pir-motion", "PIR Motion Sensor", "sensor",
       width=100, height=100,
       match_keywords=["pir", "pir motion", "motion sensor"],
       pins=[
           _pin("VCC", "power", "bottom"),
           _pin("OUT", "signal", "bottom"),
           _pin("GND", "ground", "bottom"),
       ]),
    _c("ldr", "LDR (Photoresistor)", "sensor",
       width=60, height=60,
       match_keywords=["ldr", "photoresistor", "light sensor", "photocell"],
       pins=[
           _pin("A", "analog", "left"),
           _pin("B", "ground", "right"),
       ]),
    _c("tcs34725", "TCS34725 Color Sensor", "sensor",
       width=100, height=80,
       match_keywords=["tcs34725", "color sensor", "tcs-34725"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SCL", "i2c_scl", "right"),
           _pin("SDA", "i2c_sda", "right"),
       ]),
    _c("soil-moisture", "Soil Moisture Sensor", "sensor",
       width=100, height=100,
       match_keywords=["soil moisture", "soil-moisture", "moisture sensor"],
       pins=[
           _pin("VCC", "power", "top"),
           _pin("GND", "ground", "top"),
           _pin("AOUT", "analog", "top"),
           _pin("DOUT", "signal", "top"),
       ]),
    _c("ds18b20", "DS18B20 Temperature", "sensor",
       width=80, height=80,
       match_keywords=["ds18b20", "ds-18b20", "one-wire temp", "1-wire temp"],
       pins=[
           _pin("VCC", "power", "bottom"),
           _pin("DATA", "signal", "bottom"),
           _pin("GND", "ground", "bottom"),
       ]),

    # ---------------- Actuators (7) ----------------
    _c("servo-sg90", "SG90 Micro Servo", "actuator",
       width=120, height=80,
       match_keywords=["sg90", "servo", "micro servo", "servo motor"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SIG", "pwm", "left"),
       ]),
    _c("stepper-28byj-48", "28BYJ-48 Stepper Motor", "actuator",
       width=140, height=100,
       match_keywords=["28byj-48", "28byj48", "stepper motor", "stepper"],
       pins=[
           _pin("IN1", "signal", "left"),
           _pin("IN2", "signal", "left"),
           _pin("IN3", "signal", "left"),
           _pin("IN4", "signal", "left"),
           _pin("VCC", "power", "right"),
           _pin("GND", "ground", "right"),
       ]),
    _c("l298n-driver", "L298N Motor Driver", "actuator",
       width=160, height=120,
       match_keywords=["l298n", "l298", "motor driver", "h-bridge"],
       pins=[
           _pin("12V", "power", "left"),
           _pin("5V", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("IN1", "signal", "right"),
           _pin("IN2", "signal", "right"),
           _pin("IN3", "signal", "right"),
           _pin("IN4", "signal", "right"),
           _pin("ENA", "pwm", "right"),
           _pin("ENB", "pwm", "right"),
       ]),
    _c("l293d", "L293D H-Bridge", "actuator",
       width=140, height=120,
       match_keywords=["l293d", "l293", "h-bridge", "quad half-h"],
       pins=[
           _pin("VCC1", "power", "left"),
           _pin("VCC2", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("IN1", "signal", "right"),
           _pin("IN2", "signal", "right"),
           _pin("EN1", "pwm", "right"),
       ]),
    _c("dc-motor", "DC Motor", "actuator",
       width=100, height=100,
       match_keywords=["dc motor", "dc-motor", "brushed motor"],
       pins=[
           _pin("M+", "signal", "left"),
           _pin("M-", "signal", "right"),
       ]),
    _c("relay-single", "Single Channel Relay", "actuator",
       width=120, height=100,
       match_keywords=["relay single", "1-channel relay", "single relay",
                       "relay module"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("IN", "signal", "left"),
       ]),
    _c("relay-4ch", "4 Channel Relay Module", "actuator",
       width=180, height=120,
       match_keywords=["relay 4ch", "4-channel relay", "4 channel relay",
                       "quad relay"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("IN1", "signal", "left"),
           _pin("IN2", "signal", "left"),
           _pin("IN3", "signal", "left"),
           _pin("IN4", "signal", "left"),
       ]),

    _c("mosfet-nch-logic", "N-MOSFET Low-Side Switch (IRLZ44N/AO3400)", "switch",
       width=120, height=120,
       match_keywords=["mosfet", "n-mosfet", "n-channel mosfet",
                       "logic-level mosfet", "logic level mosfet",
                       "logic-level n-mosfet", "mosfet module", "mosfet driver",
                       "fet driver", "low-side driver", "low side switch",
                       "irlz44n", "ao3400", "irf520", "high-power mosfet"],
       pins=[
           # GATE driven by MCU GPIO (through a gate resistor); DRAIN sinks the
           # load's negative leg; SOURCE returns to the common ground rail.
           _pin("GATE", "gpio", "left"),
           _pin("DRAIN", "drain", "right"),
           _pin("SOURCE", "ground", "bottom"),
       ]),
    _c("led-strip", "LED Light Strip Segment", "actuator",
       width=190, height=64,
       match_keywords=["led strip", "led strips", "led-strip", "led light strip",
                       "light strip", "strip light", "12v led", "5v led strip",
                       "booth lighting", "led lighting"],
       pins=[
           # V+ ties to the switched/raw supply rail; V- goes to a MOSFET DRAIN.
           _pin("V+", "power", "left"),
           _pin("V-", "drain", "right"),
       ]),

    # ---------------- Displays (4) ----------------
    _c("ssd1306-oled-i2c", "SSD1306 OLED 128x64 (I2C)", "display",
       width=160, height=100,
       match_keywords=["ssd1306", "oled", "ssd-1306", "ssd1306 oled",
                       "128x64 oled"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SCL", "i2c_scl", "left"),
           _pin("SDA", "i2c_sda", "left"),
       ]),
    _c("st7735-tft", "ST7735 TFT Display", "display",
       width=160, height=140,
       match_keywords=["st7735", "tft display", "1.8 tft"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SCK", "spi_sck", "left"),
           _pin("SDA", "spi_mosi", "left"),
           _pin("CS", "spi_cs", "left"),
           _pin("DC", "signal", "left"),
           _pin("RST", "signal", "left"),
       ]),
    _c("max7219-matrix", "MAX7219 LED Matrix", "display",
       width=160, height=100,
       match_keywords=["max7219", "led matrix", "max-7219", "8x8 matrix"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("DIN", "spi_mosi", "left"),
           _pin("CS", "spi_cs", "left"),
           _pin("CLK", "spi_sck", "left"),
       ]),
    _c("hd44780-lcd-i2c", "HD44780 16x2 LCD (I2C)", "display",
       width=200, height=80,
       match_keywords=["hd44780", "16x2 lcd", "lcd1602", "lcd 16x2", "i2c lcd"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SDA", "i2c_sda", "left"),
           _pin("SCL", "i2c_scl", "left"),
       ]),

    # ---------------- Communication (4) ----------------
    _c("mfrc522-rfid", "MFRC522 RFID Reader", "communication",
       width=140, height=140,
       match_keywords=["mfrc522", "rfid reader", "rc522", "mfrc-522"],
       pins=[
           _pin("3V3", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SCK", "spi_sck", "left"),
           _pin("MOSI", "spi_mosi", "left"),
           _pin("MISO", "spi_miso", "left"),
           _pin("SDA", "spi_cs", "left"),
           _pin("RST", "signal", "left"),
       ]),
    _c("nrf24l01", "nRF24L01 2.4GHz Radio", "communication",
       width=120, height=120,
       match_keywords=["nrf24l01", "nrf24", "2.4ghz radio"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SCK", "spi_sck", "left"),
           _pin("MOSI", "spi_mosi", "left"),
           _pin("MISO", "spi_miso", "left"),
           _pin("CSN", "spi_cs", "left"),
           _pin("CE", "signal", "left"),
       ]),
    _c("sx1276-lora", "SX1276 LoRa Module", "communication",
       width=140, height=120,
       match_keywords=["sx1276", "lora", "ra-02", "ra02", "lora module"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SCK", "spi_sck", "left"),
           _pin("MOSI", "spi_mosi", "left"),
           _pin("MISO", "spi_miso", "left"),
           _pin("NSS", "spi_cs", "left"),
           _pin("RST", "signal", "left"),
       ]),
    _c("bluetooth-hc-05", "HC-05 Bluetooth Module", "communication",
       width=140, height=100,
       match_keywords=["hc-05", "hc05", "bluetooth", "bluetooth module"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("TXD", "uart_tx", "left"),
           _pin("RXD", "uart_rx", "left"),
       ]),

    # ---------------- Energy harvesting (8) ----------------
    _c("solar-panel", "Solar PV Panel", "harvester",
       width=150, height=90,
       match_keywords=["solar", "solar pv", "pv panel", "solar panel",
                       "photovoltaic", "solar cell", "pv module", "pv cell"],
       pins=[
           _pin("V+", "power", "right"),
           _pin("V-", "ground", "right"),
       ]),
    _c("teg-module", "Thermoelectric Generator (TEG / Seebeck)", "harvester",
       width=150, height=90,
       match_keywords=["teg", "thermoelectric generator", "thermoelectric",
                       "seebeck", "peltier", "tec1-12706", "tec1-12715",
                       "thermo-electric"],
       pins=[
           _pin("TEG+", "power", "right"),
           _pin("TEG-", "ground", "right"),
       ]),
    _c("rf-harvester-p2110", "Powercast P2110 RF Harvester", "harvester",
       width=150, height=110,
       match_keywords=["powercast", "p2110", "rf harvester", "rf energy harvester",
                       "rectenna", "rf energy", "rf-harvest"],
       pins=[
           _pin("RF", "signal", "left"),   # antenna input
           _pin("GND", "ground", "bottom"),
           _pin("VCAP", "power", "right"),
           _pin("VOUT", "power", "right"),
       ]),
    _c("bq25570", "TI BQ25570 Boost Charger + MPPT", "converter",
       width=150, height=130,
       match_keywords=["bq25570", "bq-25570", "nano-power boost", "boost charger",
                       "mppt charger", "energy harvesting ic", "harvest charger",
                       "nano power charger"],
       pins=[
           _pin("VIN", "power", "left"),    # from harvester
           _pin("GND", "ground", "left"),
           _pin("EN", "signal", "left"),
           _pin("VBAT", "power", "right"),  # to storage
           _pin("VSTOR", "power", "right"),
           _pin("VOUT", "power", "right"),  # regulated load rail
       ]),
    _c("ltc3108", "ADI LTC3108 Ultra-Low-Vin Step-Up", "converter",
       width=150, height=120,
       match_keywords=["ltc3108", "ltc-3108", "ultra-low-vin", "ultra low vin",
                       "low-vin step-up", "thermoelectric step-up",
                       "20mv step-up", "harvest step-up"],
       pins=[
           _pin("VIN", "power", "left"),    # from TEG
           _pin("GND", "ground", "left"),
           _pin("VOUT", "power", "right"),
           _pin("VSTORE", "power", "right"),
           _pin("VAUX", "power", "right"),
       ]),
    _c("supercap", "Supercapacitor (EDLC)", "storage",
       width=120, height=90,
       match_keywords=["supercapacitor", "supercap", "super capacitor",
                       "ultracapacitor", "edlc", "farad cap", "gold cap"],
       pins=[
           _pin("+", "power", "left"),
           _pin("-", "ground", "left"),
       ]),
    _c("lifepo4-cell", "LiFePO4 Cell", "storage",
       width=130, height=80,
       match_keywords=["lifepo4", "lifepo", "lfp cell", "lifepo4 cell",
                       "iron phosphate", "lfp battery"],
       pins=[
           _pin("+", "power", "left"),
           _pin("-", "ground", "left"),
       ]),
    _c("ina226", "INA226 Power Monitor (I2C)", "sensor",
       width=120, height=100,
       match_keywords=["ina226", "ina-226", "ina219", "power monitor",
                       "high-side power monitor", "current+voltage", "current sensor",
                       "energy monitor"],
       pins=[
           _pin("VCC", "power", "left"),
           _pin("GND", "ground", "left"),
           _pin("SDA", "i2c_sda", "left"),
           _pin("SCL", "i2c_scl", "left"),
           _pin("IN+", "power", "right"),   # high-side shunt -> rail
           _pin("IN-", "power", "right"),
       ]),
    _c("schottky-diode", "Schottky Diode (low-Vf, OR-ing)", "passive",
       width=90, height=44,
       match_keywords=["schottky", "schottky diode", "bat54", "ss14", "1n5819",
                       "or-ing diode", "low-vf diode"],
       pins=[
           _pin("A", "signal", "left"),
           _pin("K", "signal", "right"),
       ]),

    # ---------------- Power (5) ----------------
    _c("dc-power-supply", "DC Power Supply / Battery Pack", "power",
       width=160, height=90,
       match_keywords=["dc power supply", "power supply", "power source",
                       "battery pack", "battery", "li-ion battery", "li-ion pack",
                       "18650", "12v supply", "5v supply", "wall adapter",
                       "psu", "mains adapter", "barrel jack supply"],
       pins=[
           # V+ is the raw high-current rail (feeds loads + MCU VIN);
           # V- is the common ground reference for the whole device.
           _pin("V+", "power", "right"),
           _pin("V-", "ground", "right"),
       ]),
    _c("tp4056-charger", "TP4056 Li-ion Charger / Protection", "power",
       width=140, height=110,
       match_keywords=["tp4056", "tp-4056", "battery charger", "li-ion charger",
                       "lipo charger", "charging module", "charge controller"],
       pins=[
           _pin("IN+", "power", "left"),
           _pin("IN-", "ground", "left"),
           _pin("BAT+", "power", "right"),
           _pin("BAT-", "ground", "right"),
           _pin("OUT+", "power", "right"),
           _pin("OUT-", "ground", "right"),
       ]),
    _c("lipo-1s", "LiPo 1S Battery", "power",
       width=140, height=80,
       match_keywords=["lipo", "lipo 1s", "lithium battery", "1s lipo",
                       "3.7v battery"],
       pins=[
           _pin("+", "power", "right"),
           _pin("-", "ground", "right"),
       ]),
    _c("buck-converter", "Buck Converter (LM2596)", "power",
       width=140, height=100,
       match_keywords=["buck", "buck converter", "lm2596", "step-down"],
       pins=[
           _pin("VIN+", "power", "left"),
           _pin("VIN-", "ground", "left"),
           _pin("VOUT+", "power", "right"),
           _pin("VOUT-", "ground", "right"),
       ]),
    _c("voltage-regulator-7805", "7805 Voltage Regulator", "power",
       width=100, height=80,
       match_keywords=["7805", "voltage regulator", "lm7805"],
       pins=[
           _pin("VIN", "power", "left"),
           _pin("GND", "ground", "bottom"),
           _pin("VOUT", "power", "right"),
       ]),

    # ---------------- Passives + misc (4) ----------------
    _c("led-3mm", "LED (3mm)", "passive",
       width=60, height=60,
       match_keywords=["led", "led 3mm", "3mm led", "5mm led", "indicator led"],
       pins=[
           _pin("A", "signal", "left"),
           _pin("K", "ground", "right"),
       ]),
    _c("resistor", "Resistor", "passive",
       width=100, height=40,
       match_keywords=["resistor", "1k resistor", "10k resistor", "220 resistor",
                       "330 resistor"],
       pins=[
           _pin("1", "signal", "left"),
           _pin("2", "signal", "right"),
       ]),
    _c("push-button", "Push Button (Momentary)", "passive",
       width=80, height=80,
       match_keywords=["push button", "push-button", "momentary switch",
                       "tactile switch", "button"],
       pins=[
           _pin("A", "signal", "left"),
           _pin("B", "ground", "right"),
       ]),
    _c("hcsr501-pir", "HC-SR501 PIR Module", "passive",
       width=120, height=100,
       match_keywords=["hc-sr501", "sr501", "hcsr501", "pir module"],
       pins=[
           _pin("VCC", "power", "bottom"),
           _pin("OUT", "signal", "bottom"),
           _pin("GND", "ground", "bottom"),
       ]),
]


# Build the dict the spec advertises
COMPONENTS: dict[str, dict] = {c["id"]: c for c in _COMPONENT_LIST}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def list_components() -> list[dict]:
    """Return all components."""
    return list(COMPONENTS.values())


def get_component(component_id: str) -> Optional[dict]:
    """Lookup a component by id."""
    if not component_id:
        return None
    return COMPONENTS.get(component_id)


def match_component(bom_name: str) -> Optional[dict]:
    """Case-insensitive match of bom_name against match_keywords.

    Returns the first component whose match_keywords contains a substring
    of bom_name (case-insensitive), or None.
    Longer keywords are preferred to break ties — "esp32-devkit" should win
    over a generic "esp32" match when the BOM says "ESP32 DevKit".
    """
    if not bom_name:
        return None
    needle = bom_name.lower()

    best = None
    best_len = 0
    for comp in COMPONENTS.values():
        for kw in comp["match_keywords"]:
            if kw and kw in needle and len(kw) > best_len:
                best = comp
                best_len = len(kw)
    return best
