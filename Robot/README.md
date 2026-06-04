# Autonomous Robot — Differential Drive + MeArm
**Final Project · Embedded Systems · Universidad Distrital Francisco José de Caldas**

> Autonomous robotic system capable of picking up a cardboard tray and placing it 10 cm to the right using a differential drive robot, a MeArm robotic arm, computer vision (ESP32-CAM), and a web-based digital twin dashboard.

---

## Team

| Name |
|------|
| Nicolás Velasquez Amarillo | 
| Daniel Nicolás Rodríguez Villar |


---

## System Description

The system integrates three physical layers:

- **Robot (Raspberry Pi Pico W):** Runs MicroPython firmware that controls two DC motors (L298N driver), a 3-DOF MeArm robotic arm (SG90 servos), an HC-SR04 ultrasonic distance sensor, an SSD1306 OLED display, and a battery voltage monitor via ADC.
- **Vision (ESP32-CAM):** An independent microcontroller that captures frames and performs color analysis, publishing results to the broker independently of the Pico W.
- **Frontend (Browser):** A single-page dashboard (`dashboardv2.html`) that provides real-time visualization via a virtual joystick, arm sliders, live camera feed, a 3D digital twin (Three.js), and OLED display control.

All three layers communicate through a **PubSub broker** over **TCP/WebSocket**, with a shared topic namespace prefixed as `UDFJC/emb1/robot0/`.

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│               Browser — Dashboard (dashboardv2.html)          │
│  [Joystick] [Arm sliders] [Camera] [Digital Twin] [OLED sel] │
└──────────────────────┬───────────────────────────────────────┘
                       │ WebSocket
┌──────────────────────▼───────────────────────────────────────┐
│            Broker TCP/WebSocket                               │
│      Routes messages by topic: UDFJC/emb1/robot0/...         │
└──────────┬────────────────────────────────┬──────────────────┘
           │ TCP socket                     │ TCP socket
┌──────────▼──────────────────┐   ┌─────────▼──────────────────┐
│   Raspberry Pi Pico W       │   │       ESP32-CAM             │
│   MicroPython — main.py     │   │  Publishes camera/frame     │
│                             │   │  and camera/analysis        │
│  CarTask   → set_velocity() │   └────────────────────────────┘
│  ArmTask   → move_servo()   │
│  OledTask  → render_oled()  │
│  UltrasonicTask → distance  │
│  VoltageTask → ADC read     │
│  TelemetryTask → robot/state│
└──────────┬──────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────┐
│   Hardware                                                   │
│   L298N (DC motors) · SG90 ×3 · HC-SR04 · SSD1306 · ADC    │
└─────────────────────────────────────────────────────────────┘
```

---

## Topic Structure (PubSub)

All topics use the prefix `UDFJC/emb1/robot0/`. The table below uses the suffix only.

| Topic | Direction | Publisher | Subscriber | Payload |
|-------|-----------|-----------|------------|---------|
| `car/vel` | Frontend → Robot | Dashboard (joystick) | `CarTask` | `{ linear: int, angular: int }` |
| `arm/joint_state` | Frontend → Robot | Dashboard (sliders) | `ArmTask` | `{ alpha0, alpha1, alpha2, duration_s }` |
| `oled/select` | Frontend → Robot | Dashboard (buttons) | `OledTask` | `{ source: "motors"\|"arm"\|"camera"\|"ultra"\|"volt" }` |
| `camera/frame` | ESP32 → Frontend | ESP32-CAM | Dashboard | `{ jpeg_b64: string, w: int, h: int }` |
| `camera/analysis` | ESP32 → Robot+Frontend | ESP32-CAM | `CameraReactTask`, Dashboard | `{ present, percent, x_range, cols_count, dominant, alerts }` |
| `ultra/distance` | Robot → Frontend | `UltrasonicTask` | Dashboard | `{ cm: float }` |
| `volt/value` | Robot → Frontend | `VoltageTask` | Dashboard | `{ v: float }` |
| `robot/state` | Robot → Frontend | `TelemetryTask` | Dashboard (3D twin) | `{ v, w, alpha0, alpha1, alpha2 }` |

### Control flow — manual command
```
User drags joystick
  → bus.pub("car/vel", { linear, angular })
    → Broker forwards to Pico W
      → CarTask._h(msg) calls car.set_velocity(v, w)
        → sets manual_lock for 1500 ms (disables camera reaction)
```

### Control flow — autonomous camera reaction
```
ESP32-CAM detects RGB present
  → publishes camera/analysis to Broker
    → CameraReactTask._on_analysis(msg)
      → if all 3 colors present AND no manual_lock:
          car.set_velocity(REACT_SPEED, 0) for REACT_MS (600 ms)
      → else: car.stop()
```

---

## Project Structure

```
autonomous-robot/
│
├── robot/                      # Firmware for Raspberry Pi Pico W
│   ├── main.py                 # Main loop + all Task definitions
│   ├── pines.py                # Pin map (GPIO assignments)
│   ├── voltmeter.py            # ADC battery voltage reader
│   ├── dc_motor.py             # DCMotor + DifferentialDrive classes
│   ├── servo.py                # ServoSmooth + MultiServoController
│   ├── oled.py                 # OledI2C wrapper (SSD1306)
│   ├── ultrasonic.py           # HC-SR04 driver
│   └── pubsub.py               # Scheduler, WiFiManager, SocketClient, Node, Task
│
├── frontend/
│   └── dashboardv2.html        # Single-file dashboard (HTML + CSS + JS + Three.js)
│
└── README.md
```

---

## Hardware Pin Map (Pico W)

| Peripheral | Pin(s) | Notes |
|------------|--------|-------|
| Left motor IN1/IN2 | GP16, GP17 | Direction |
| Left motor ENA (PWM) | GP27 | Speed |
| Right motor IN1/IN2 | GP18, GP19 | Direction |
| Right motor ENB (PWM) | GP28 | Speed |
| Servo base (a0) | GP20 | 0–180° |
| Servo shoulder (a1) | GP21 | 0–90° |
| Servo elbow (a2) | GP22 | 45–90° |
| OLED SDA / SCL | GP0, GP1 | I2C0 |
| Ultrasonic TRIG / ECHO | GP2, GP3 | ECHO needs 5V→3.3V divider |
| Battery ADC | GP26 | 18kΩ + 10kΩ divider, factor 2.8, max 7.4V |

---

## How to Run

### 1. Prerequisites

**Broker**
Any TCP/WebSocket PubSub broker compatible with the `{ action, topic, data }` JSON protocol. Run it on a machine accessible from the Pico W and the browser on the same local network.

**Pico W firmware**
- Install MicroPython on the Pico W.
- Install the SSD1306 library: open the REPL and run:
  ```python
  import mip
  mip.install("ssd1306")
  ```
- Upload all files inside `robot/` to the root of the Pico W filesystem (using Thonny or `mpremote`).

**ESP32-CAM**
- Flash with firmware that publishes `camera/frame` and `camera/analysis` to the same broker.

### 2. Configure network credentials

Edit `robot/main.py`:
```python
SSID        = "YourNetworkName"
PASSWORD    = "YourPassword"
BROKER_IP   = "192.168.x.x"   # IP of the machine running the broker
BROKER_PORT = 5051
```

### 3. Start the broker

```bash
# Example with your custom broker (adjust as needed):
python broker.py --port 5051
```

### 4. Power on the robot

Connect the Pico W to power. It will:
1. Connect to Wi-Fi.
2. Show the IP on the OLED.
3. Connect to the broker and start all tasks.

### 5. Open the dashboard

Open `frontend/dashboardv2.html` in any modern browser (Chrome recommended).

1. Enter the broker IP and port (`8080` for WebSocket if the broker bridges TCP→WS).
2. Enter the topic prefix: `UDFJC/emb1/robot0`
3. Click **Conectar**.
4. The status indicator turns green when connected.

### 6. Manual control

| Control | Topic published | Effect |
|---------|----------------|--------|
| Drag joystick | `car/vel` | Moves differential drive |
| Move arm sliders | `arm/joint_state` | Moves MeArm servos |
| Click OLED buttons | `oled/select` | Changes robot's OLED display |

### 7. Autonomous mode

The robot reacts to the camera automatically when `REACT_ENABLED = True` in `main.py`. Manual commands override autonomous behavior for 1500 ms.

---

## Key Parameters (main.py)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `REACT_ENABLED` | `True` | Enable/disable camera-based autonomous reaction |
| `REACT_SPEED` | `50` | Motor speed (%) during automatic reaction |
| `REACT_MS` | `600` | Duration (ms) of each automatic movement burst |
| `MANUAL_LOCK_MS` | `1500` | Time (ms) manual commands block autonomous mode |
| `ARM_HOME` | `(90, 45, 45)` | Home position for MeArm on startup |

---

## License

MIT — see `LICENSE` file.
