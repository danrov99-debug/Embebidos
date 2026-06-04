# pubsub.py — Infraestructura PubSub: Task, Scheduler, WiFiManager, SocketClient, Node

import network, time, json, gc
import usocket as socket


# =============================================================================
# TASK & SCHEDULER
# =============================================================================

class Task:
    def __init__(self, scheduler, period_ms, priority=1):
        self.period = period_ms
        self.priority = priority
        self.next_run = time.ticks_ms()
        scheduler.add(self)

    def update(self):
        pass


class Scheduler:
    def __init__(self):
        self.tasks = []

    def add(self, task):
        self.tasks.append(task)
        self.tasks.sort(key=lambda t: t.priority)

    def run(self):
        while True:
            now = time.ticks_ms()
            for task in self.tasks:
                if time.ticks_diff(now, task.next_run) >= 0:
                    task.update()
                    task.next_run = time.ticks_add(now, task.period)
            gc.collect()
            time.sleep_ms(1)


# =============================================================================
# WIFI
# =============================================================================

class WiFiManager:
    def __init__(self, ssid, password="monito123"):
        self.ssid = ssid
        self.password = password
        # Obtener la instancia ACTIVA de WLAN, no crear una nueva
        self.wlan = network.WLAN(network.STA_IF)
        # Si ya está activa, no reactivar (evita status -2)
        if not self.wlan.active():
            self.wlan.active(True)

    def connect(self):
        if not self.wlan.isconnected():
            print(f"Conectando a {self.ssid}...")
            self.wlan.connect(self.ssid, self.password)
            attempts = 0
            while not self.wlan.isconnected() and attempts < 20:
                attempts += 1
                status = self.wlan.status()
                print(f"  Intento {attempts}: status={status}")
                time.sleep(1)
            if not self.wlan.isconnected():
                print("ERROR: No se pudo conectar a WiFi")
                return False
        print(f"✓ WiFi OK. IP: {self.wlan.ifconfig()[0]}")
        return True


# =============================================================================
# SOCKET CLIENT
# =============================================================================

class SocketClient(Task):
    def __init__(self, host, port, scheduler, period_ms=100):
        super().__init__(scheduler, period_ms)
        self.host = host
        self.port = port
        self.sock = None
        self.actions = {}
        self._rx_buffer = b""

    def connect(self):
        addr = socket.getaddrinfo(self.host, self.port)[0][-1]
        self.sock = socket.socket()
        self.sock.connect(addr)
        self.sock.setblocking(False)
        print("Socket conectado a", self.host, self.port)

    def ensure(self):
        if self.sock is None:
            self.connect()

    def send(self, data):
        total = 0
        while total < len(data):
            try:
                sent = self.sock.send(data[total:])
                if sent == 0:
                    return False
                total += sent
            except OSError:
                return False
        return True

    def send_json(self, obj):
        self.send((json.dumps(obj) + "\n").encode())

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except:
            pass
        self.sock = None

    def recv_json_nonblocking(self):
        messages = []
        if self.sock is None:          
            return messages
        try:
            data = self.sock.recv(1024)
            if data == b'':
                self.close()
                return []
            if data:
                self._rx_buffer += data
        except OSError:
            return []

        while b"\n" in self._rx_buffer:
            line, self._rx_buffer = self._rx_buffer.split(b"\n", 1)
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except Exception as e:
                print("JSON error:", e)
        return messages

    def update(self):
        try:
            self.ensure()
        except Exception as e:
            print("Socket reconexion fallida:", e)
            return
        msgs = self.recv_json_nonblocking()
        for msg in msgs:
            action = msg.get("action")
            if action in self.actions:
                self.actions[action](msg)

    def add_action(self, action, callback):
        self.actions[action] = callback


# =============================================================================
# NODE (PubSub)
# =============================================================================

class Node:
    def __init__(self, socket_client, prefix='UDFJC/emb1/robot0/'):
        self.sock = socket_client
        self.sock.add_action("PUB", self.handle_pub)
        self.sock.add_action("SUB", self.handle_sub)
        self.prefix = prefix
        self.subscriptions = {}

    def publish(self, topic, data):
        self.broker_publish(topic, data)
        self.local_publish(topic, data)

    def broker_publish(self, topic, data):
        self.sock.ensure()
        self.sock.send_json({"action": "PUB", "topic": self.prefix + topic, "data": data})

    def local_publish(self, topic, data):
        callbacks = self.subscriptions.get(topic, set())
        for c in list(callbacks):
            try:
                c(data)
            except Exception as e:
                print("PubSub callback error:", e)
                callbacks.discard(c)

    def subscribe(self, topic, callback):
        self.subscriptions.setdefault(topic, set()).add(callback)
        self.sock.ensure()
        self.sock.send_json({"action": "SUB", "topic": self.prefix + topic})
        print("[SUB]", topic)

    def handle_pub(self, msg):
        topic = msg['topic']
        if not topic.startswith(self.prefix):
            return
        self.local_publish(topic[len(self.prefix):], msg['data'])

    def handle_sub(self, msg):
        pass

