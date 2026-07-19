# Dreame BLE Mower

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Home Assistant](https://img.shields.io/badge/home--assistant-custom_component-orange) ![License](https://img.shields.io/badge/license-MIT-green)

## 🔌 Local Bluetooth Low Energy integration for the Dreame BLE Mower

A **fully local**, cloud-free Home Assistant integration that controls a Dreame mower directly over Bluetooth LE. Built by reverse-engineering the official Android app protocol and mapping it to the existing `dreame-mower` entity structure.

### ✨ Features
- ✅ **100% Local** — No Dreame vendor cloud, no MQTT broker required.
- 🔌 **Real-time BLE** — Uses Python `bleak` for direct GATT characteristic I/O.
- 🧠 **Protocol Bridge** — Translates internal JSON commands (`{m,a}, {o:207}`) to HA actions (Start Mowing, Park, Dock).
- 🔋 **Live Sensors** — Tracks battery percent, activity status, and mower position directly via BLE pushes.

### ⚙️ How it Works
The official Dreame app sends commands wrapped in a `C0` envelope over specific GATT handles:
- `Handle 0x001d`: Start mowing (`o:207`), Park (`o:202`), and Return to Dock (`o:200`).
- `Handle 0x0029`: Position tracking (`MPOS`) and dock status.
- `Handle 0x0020`: Configuration & battery metrics.

This replacement component mirrors the MQTT payloads from [antondaubert/dreame-mower](https://github.com/antondaubert/dreame-mower) so existing dashboards work out of the box without breaking any entity references. 

---

## 📥 Installation via HACS
1. Add this repository as a **custom repository** in HACS (Settings > Custom Repositories).
2. Search for **Dreame BLE Mower**, install, and restart Home Assistant.
3. Go to **Settings > Devices & Services > Add Integration**, find "Dreame BLE Mower", and select your mower's MAC address from the dropdown list!

---

## 🔧 AI Information

This integration was coded by an Hermes agent running locally with Qwen3.6-27b model. Full local development without any Cloud involved! 

---

## 🛠️ Dependencies
- [bleak >= 0.21.1](https://github.com/hbldh/bleak) (Handled automatically by HA's built-in BLE dependency injection).
- Home Assistant Core >= 2024.x

---

## ⚠️ Known Limitations
- Due to the mower's firmware using `Curve25519` for long-lived pairing sessions, the mower may occasionally drop the plain-text pipe and switch to encrypted traffic. (A Curve25519 handshake bridge is currently being investigated!).
- Requires a Bluetooth adapter (or ESPHome BLE Proxy) within 10 meters of the mower.

---

## Acknowledgments & Development
This integration was developed through community collaboration for the purpose of achieving interoperability with Home Assistant. It builds upon:
- [antondaubert/dreame-mower](https://github.com/antondaubert/dreame-mower) thank you for this great cloud integration!
- [Alistair Automower BLE](https://github.com/alistair23/AutoMower-BLE) the idea of reverse engineering mowers bluetooth connection.

---

## 📜 License
This project is licensed under the MIT License. See [LICENSE](./LICENSE).
