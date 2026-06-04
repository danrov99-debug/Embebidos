# main.py — Robot Diferencial + MeArm + OLED  (cámara = ESP32-CAM externa)
# Hardware: Raspberry Pi Pico W (RP2040)
#
# La cámara YA NO la maneja el Pico: es un ESP32-CAM independiente que
# publica 'camera/frame' y 'camera/analysis' al broker. Este Pico:
#   - controla motores, servos, OLED y ultrasonido
#   - se suscribe a 'camera/analysis' para:
#       (a) mostrar el color detectado en la OLED
#       (b) MOVER el carro automáticamente según el color (reacción)
#
# ANTES DE EJECUTAR:
#   1. import mip; mip.install("ssd1306")
#   2. Ajusta SSID, PASSWORD e IP del broker abajo.
#   3. Sube todos los .py de esta carpeta al Pico.

import time, gc

import pines
from dc_motor import DCMotor, DifferentialDrive
from servo import ServoSmooth, MultiServoController
from oled import OledI2C
from ultrasonic import Ultrasonic
from voltmeter import Voltmeter
from pubsub import Scheduler, WiFiManager, SocketClient, Node, Task

# =============================================================================
# RED
# =============================================================================
SSID        = "Nokia"
PASSWORD    = "monito123"
BROKER_IP   = "10.53.154.129"
BROKER_PORT = 5051

# =============================================================================
# REACCIÓN AUTOMÁTICA POR COLOR  (ajusta a tu gusto)
# =============================================================================
# Cuando el ESP32-CAM detecta ROJO **Y** VERDE **Y** AZUL presentes AL MISMO
# TIEMPO en la imagen, el carro AVANZA durante REACT_MS. Si falta alguno de
# los tres colores, el carro se detiene.
# El control manual (joystick) tiene prioridad: si llega un 'car/vel' del
# navegador, se desactiva la reacción por MANUAL_LOCK_MS.
REACT_ENABLED   = True
REACT_SPEED     = 50      # % de velocidad al avanzar
REACT_MS        = 600     # cuánto dura cada avance
MANUAL_LOCK_MS  = 1500    # tras un comando manual, ignora la cámara este tiempo

# =============================================================================
# HARDWARE
# =============================================================================
# Carro (L298N: IN1/IN2 dirección, ENA/ENB velocidad por PWM)
_mi = DCMotor(pines.LEFT_IN1,  pines.LEFT_IN2,  pines.LEFT_ENA)
_md = DCMotor(pines.RIGHT_IN1, pines.RIGHT_IN2, pines.RIGHT_ENB)
car = DifferentialDrive(_mi, _md, left_correction=1.0, right_correction=1.0)

# Brazo MeArm (3 servos SG90) — rangos reales del carro físico
_s0 = ServoSmooth(pin=pines.SERVO_BASE,     min_pulse=500, max_pulse=2400, min_angle=0, max_angle=180)
_s1 = ServoSmooth(pin=pines.SERVO_SHOULDER, min_pulse=500, max_pulse=2300, min_angle=0, max_angle=90)
_s2 = ServoSmooth(pin=pines.SERVO_ELBOW,    min_pulse=500, max_pulse=2500, min_angle=0, max_angle=90)
arm = MultiServoController(timer_freq=50)
arm.add_servo(_s0); arm.add_servo(_s1); arm.add_servo(_s2)
arm.start()

# Pose de inicio (HOME) — el brazo SIEMPRE arranca aquí
ARM_HOME = (90, 45, 45)   # (base, hombro, codo)
arm.move_all([ARM_HOME[0], ARM_HOME[1], ARM_HOME[2]], duration=1.2)

# OLED (I2C0)
oled = OledI2C(sda_pin=pines.OLED_SDA, scl_pin=pines.OLED_SCL, i2c_id=0)
oled.screen_boot(ssid="Conectando...")

# Ultrasonido
us = Ultrasonic(pines.US_TRIG, pines.US_ECHO)

# Voltímetro — divisor 18kΩ+10kΩ → factor 2.8 (mide batería 7.4V)
volt = Voltmeter(pin=pines.VOLT_ADC, divider=2.8)

# =============================================================================
# PUBSUB
# =============================================================================
scheduler = Scheduler()
time.sleep(2)
wifi   = WiFiManager(SSID)
sockc  = SocketClient(BROKER_IP, BROKER_PORT, scheduler)
pubsub = Node(sockc, prefix='UDFJC/emb1/robot0/')

wifi.connect()
oled.screen_boot(ssid=SSID, ip=wifi.wlan.ifconfig()[0])
time.sleep(1)

# =============================================================================
# ESTADO COMPARTIDO
# =============================================================================
oled_state = {
    "source": "motors",            # motors | arm | camera | ultra | volt
    "motors": {"v": 0, "w": 0},
    "arm":    {"a0": 90, "a1": 45, "a2": 45},
    "camera": {"color": "---", "pct": 0, "prox": "---"},
    "ultra":  {"cm": None},
    "volt":   {"v": None},
}

# Control de la reacción automática
react = {
    "active": False,
    "stop_at": 0,          # ticks_ms en que termina la reacción actual
    "manual_until": 0,     # ticks_ms hasta el que el manual bloquea la cámara
}


def render_oled():
    s = oled_state["source"]
    if s == "motors":
        m = oled_state["motors"]
        oled.screen_robot_status(linear=m["v"], angular=m["w"], mode="MOTORS")
    elif s == "arm":
        a = oled_state["arm"]
        oled.print_lines(["BRAZO MeArm",
                          "a0: " + str(a["a0"]),
                          "a1: " + str(a["a1"]),
                          "a2: " + str(a["a2"])])
    elif s == "camera":
        c = oled_state["camera"]
        oled.print_lines(["== CAMARA ==",
                          "Color: " + str(c["color"]),
                          "Ocup : " + str(c["pct"]) + "%",
                          "Dist : " + str(c["prox"])])
    elif s == "ultra":
        u = oled_state["ultra"]
        d = "N/A" if u["cm"] is None else (str(u["cm"]) + " cm")
        oled.print_lines(["ULTRASONIDO", "Dist: " + d])
    elif s == "volt":
        vv = oled_state["volt"]["v"]
        txt = "N/A" if vv is None else "{:.2f} V".format(vv)
        oled.print_lines(["== VOLTAJE ==",
                          "GP26 (18k+10k)",
                          "V: " + txt])


# =============================================================================
# TASKS
# =============================================================================
class CarTask(Task):
    """Control manual del carro (joystick web). Bloquea la cámara un rato."""
    def __init__(self, scheduler, pubsub):
        super().__init__(scheduler, period_ms=100, priority=2)
        pubsub.subscribe("car/vel", self._h)
    def _h(self, msg):
        v = msg.get("linear", msg.get("v", 0))
        w = msg.get("angular", msg.get("w", 0))
        car.set_velocity(v, w)
        oled_state["motors"] = {"v": v, "w": w}
        # Comando manual: la reacción por color queda en pausa un momento
        react["manual_until"] = time.ticks_add(time.ticks_ms(), MANUAL_LOCK_MS)
        react["active"] = False
    def update(self): pass


class ArmTask(Task):
    def __init__(self, scheduler, pubsub):
        super().__init__(scheduler, period_ms=100, priority=2)
        # Recordar la última posición de cada servo (arranca en HOME)
        self.last = [ARM_HOME[0], ARM_HOME[1], ARM_HOME[2]]
        pubsub.subscribe("arm/joint_state", self._h)
    def _h(self, msg):
        # Si un ángulo no viene en el mensaje, conservar el último (no forzar 0)
        a0 = msg.get("alfa0_deg", msg.get("alpha0", self.last[0]))
        a1 = msg.get("alfa1_deg", msg.get("alpha1", self.last[1]))
        a2 = msg.get("alfa2_deg", msg.get("alpha2", self.last[2]))
        dur = msg.get("duration_s", 0.3)
        self.last = [a0, a1, a2]
        arm.move_servo(0, a0, dur)
        arm.move_servo(1, a1, dur)
        arm.move_servo(2, a2, dur)
        oled_state["arm"] = {"a0": a0, "a1": a1, "a2": a2}
    def update(self): pass


class CameraReactTask(Task):
    """
    Recibe 'camera/analysis' (lo publica el ESP32-CAM) y:
      - actualiza el estado de cámara para la OLED
      - mueve el carro según el color dominante presente
    """
    def __init__(self, scheduler, pubsub):
        super().__init__(scheduler, period_ms=50, priority=2)
        pubsub.subscribe("camera/analysis", self._on_analysis)

    def _on_analysis(self, msg):
        present = msg.get("present", {})
        percent = msg.get("percent", {})
        prox    = msg.get("proximity", {})
        dominant = msg.get("dominant", "---")

        # Para la OLED: color dominante + su ocupación + proximidad
        dom_key = {"ROJO": "red", "VERDE": "green", "AZUL": "blue"}.get(dominant)
        pct = percent.get(dom_key, 0) if dom_key else 0
        pr  = prox.get(dom_key, "---") if dom_key else "---"
        oled_state["camera"] = {"color": dominant, "pct": pct, "prox": pr}

        if not REACT_ENABLED:
            return
        # ¿El manual bloquea la cámara ahora mismo?
        if time.ticks_diff(time.ticks_ms(), react["manual_until"]) < 0:
            return

        # Reacción: AVANZAR solo si los TRES colores están presentes a la vez
        if present.get("red") and present.get("green") and present.get("blue"):
            car.set_velocity(REACT_SPEED, 0)             # RGB juntos -> avanzar
            oled_state["motors"] = {"v": REACT_SPEED, "w": 0}
            react["active"] = True
            react["stop_at"] = time.ticks_add(time.ticks_ms(), REACT_MS)
        else:
            # Falta algún color -> detener
            car.stop()
            oled_state["motors"] = {"v": 0, "w": 0}
            react["active"] = False

    def update(self):
        # Apaga el carro cuando expira la reacción (no bloqueante)
        if react["active"] and time.ticks_diff(time.ticks_ms(), react["stop_at"]) >= 0:
            car.stop()
            oled_state["motors"] = {"v": 0, "w": 0}
            react["active"] = False


class UltrasonicTask(Task):
    def __init__(self, scheduler, pubsub, period_ms=200):
        super().__init__(scheduler, period_ms=period_ms, priority=3)
        self.pubsub = pubsub
    def update(self):
        d = us.distance_cm()
        oled_state["ultra"] = {"cm": d}
        self.pubsub.publish("ultra/distance", {"cm": d})


class VoltageTask(Task):
    """Lee el voltaje de la batería por ADC y lo publica como tópico."""
    def __init__(self, scheduler, pubsub, period_ms=500):
        super().__init__(scheduler, period_ms=period_ms, priority=3)
        self.pubsub = pubsub
    def update(self):
        v = volt.read_voltage()
        oled_state["volt"] = {"v": v}
        self.pubsub.publish("volt/value", {"v": v})


class OledTask(Task):
    def __init__(self, scheduler, pubsub, period_ms=300):
        super().__init__(scheduler, period_ms=period_ms, priority=3)
        pubsub.subscribe("oled/select", self._h)
    def _h(self, msg):
        src = msg.get("source")
        if src in ("motors", "arm", "camera", "ultra", "volt"):
            oled_state["source"] = src
    def update(self):
        render_oled()


class TelemetryTask(Task):
    """Publica el estado real del carro/brazo para sincronizar el gemelo 3D."""
    def __init__(self, scheduler, pubsub, period_ms=150):
        super().__init__(scheduler, period_ms=period_ms, priority=2)
        self.pubsub = pubsub
    def update(self):
        v, w = car.get_velocities()
        self.pubsub.publish("robot/state", {
            "v": v, "w": w,
            "alpha0": _s0.get_angle(),
            "alpha1": _s1.get_angle(),
            "alpha2": _s2.get_angle(),
        })


_car  = CarTask(scheduler, pubsub)
_arm  = ArmTask(scheduler, pubsub)
_camr = CameraReactTask(scheduler, pubsub)
_us   = UltrasonicTask(scheduler, pubsub)
_volt = VoltageTask(scheduler, pubsub)
_oled = OledTask(scheduler, pubsub)
_tele = TelemetryTask(scheduler, pubsub)

# =============================================================================
# LOOP
# =============================================================================
print("=== Sistema listo (camara = ESP32-CAM externa) ===")
oled.print_lines(["Sistema listo", "Esperando cmd..."])

while True:
    now = time.ticks_ms()
    for task in scheduler.tasks:
        if time.ticks_diff(now, task.next_run) >= 0:
            task.update()
            task.next_run = time.ticks_add(now, task.period)
    gc.collect()
    time.sleep_ms(10)
