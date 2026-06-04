# simple_broker.py — Broker PubSub TCP + WebSocket
#
# TCP:  puerto 5051  (Pico W + ESP32-CAM)
# WS:   puerto 8080  (navegador)
#
# El broker:
#   1. Recibe JSON puro de la Pico W (car/vel, arm/joint_state, ultra/distance, etc.)
#   2. Recibe JSON + JPEG binario del ESP32-CAM (camera/frame con jpeg_size)
#   3. Reenvía todo por WebSocket al navegador
#   4. Reenvía comandos del navegador a la Pico por TCP

import asyncio
import websockets
import json
import base64


def _topic_matches(pattern, topic):
    """Wildcards estilo MQTT: # (cualquier cosa), + (un nivel)."""
    p = pattern.split('/')
    t = topic.split('/')
    for i in range(len(p)):
        if p[i] == '#':
            return True
        if p[i] == '+':
            if i >= len(t):
                return False
            continue
        if i >= len(t) or p[i] != t[i]:
            return False
    return len(p) == len(t)


# ─────────────────────────────────────────────────────────────────────
# CLIENTES
# ─────────────────────────────────────────────────────────────────────

class TCPClient:
    def __init__(self, writer):
        self.writer = writer
        addr = writer.get_extra_info('peername')
        self._repr = f"TCP({addr[0]}:{addr[1]})" if addr else "TCP"

    async def send(self, msg: str):
        try:
            self.writer.write((msg + "\n").encode())
            await self.writer.drain()
        except Exception:
            pass

    def __repr__(self):
        return self._repr


class WSClient:
    def __init__(self, websocket):
        self.ws = websocket

    async def send(self, msg: str):
        try:
            await self.ws.send(msg)
        except Exception:
            pass

    def __repr__(self):
        return "WS"


# ─────────────────────────────────────────────────────────────────────
# PUBSUB
# ─────────────────────────────────────────────────────────────────────

class PubSub:
    def __init__(self):
        self._subs: dict[str, set] = {}

    def subscribe(self, client, pattern: str):
        self._subs.setdefault(pattern, set()).add(client)
        print(f"[SUB] {client} → {pattern}")

    def unsubscribe(self, client):
        for clients in self._subs.values():
            clients.discard(client)
        print(f"[DEL] {client} eliminado")

    async def publish(self, topic: str, data, origin=None):
        targets = set()
        for pattern, clients in self._subs.items():
            if _topic_matches(pattern, topic):
                targets |= clients

        # No reenviar al origen (evitar eco)
        targets.discard(origin)

        print(f"[PUB] {topic}  →  {len(targets)} cliente(s)")

        msg = json.dumps({"action": "PUB", "topic": topic, "data": data})
        for c in list(targets):
            await c.send(msg)


# ─────────────────────────────────────────────────────────────────────
# SERVIDOR TCP  (Pico + ESP32-CAM)
# ─────────────────────────────────────────────────────────────────────

class TCPServer:
    def __init__(self, pubsub: PubSub, host="0.0.0.0", port=5051):
        self.pubsub = pubsub
        self.host = host
        self.port = port

    async def handle_client(self, reader, writer):
        client = TCPClient(writer)
        print(f"[TCP] Conectado: {client}")
        buffer = b""

        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buffer += chunk

                # Procesar todas las líneas completas (terminadas en \n)
                while b"\n" in buffer:
                    line_end = buffer.index(b"\n")
                    line = buffer[:line_end].strip()
                    buffer = buffer[line_end + 1:]

                    if not line:
                        continue

                    # Intentar parsear como JSON
                    try:
                        j = json.loads(line.decode("utf-8", errors="ignore"))
                    except json.JSONDecodeError:
                        continue

                    action = j.get("action")

                    if action == "SUB":
                        self.pubsub.subscribe(client, j.get("topic", ""))

                    elif action == "PUB":
                        topic = j.get("topic", "")
                        data  = j.get("data")

                        # ¿Viene acompañado de JPEG binario? (ESP32-CAM)
                        jpeg_size = j.get("jpeg_size", 0)
                        if jpeg_size > 0:
                            # Leer exactamente jpeg_size bytes
                            while len(buffer) < jpeg_size:
                                more = await reader.read(8192)
                                if not more:
                                    break
                                buffer += more

                            if len(buffer) >= jpeg_size:
                                jpeg_bytes = buffer[:jpeg_size]
                                buffer = buffer[jpeg_size:]
                                # Descartar el \n que sigue al binario
                                if buffer.startswith(b"\n"):
                                    buffer = buffer[1:]
                                # Convertir a base64 para el navegador
                                b64 = base64.b64encode(jpeg_bytes).decode("ascii")
                                data = {
                                    "w": j.get("w", 160),
                                    "h": j.get("h", 120),
                                    "jpeg_b64": b64,
                                }

                        await self.pubsub.publish(topic, data, origin=client)

        except Exception as e:
            print(f"[TCP] error {client}: {e}")
        finally:
            self.pubsub.unsubscribe(client)
            writer.close()

    async def start(self):
        server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )
        print(f"[TCP] Escuchando en {self.host}:{self.port}")
        async with server:
            await server.serve_forever()


# ─────────────────────────────────────────────────────────────────────
# SERVIDOR WS  (navegador)
# ─────────────────────────────────────────────────────────────────────

class WSServer:
    def __init__(self, pubsub: PubSub, host="0.0.0.0", port=8080):
        self.pubsub = pubsub
        self.host = host
        self.port = port

    async def handle_client(self, websocket, path=""):
        client = WSClient(websocket)
        print(f"[WS]  Conectado: {client}")
        try:
            async for raw in websocket:
                try:
                    j = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                action = j.get("action")
                if action == "SUB":
                    self.pubsub.subscribe(client, j.get("topic", ""))
                elif action == "PUB":
                    await self.pubsub.publish(
                        j.get("topic", ""), j.get("data"), origin=client
                    )
        except Exception as e:
            print(f"[WS]  error {client}: {e}")
        finally:
            self.pubsub.unsubscribe(client)

    async def start(self):
        async with websockets.serve(self.handle_client, self.host, self.port):
            print(f"[WS]  Escuchando en {self.host}:{self.port}")
            await asyncio.Future()


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

async def main():
    pubsub = PubSub()
    tcp_srv = TCPServer(pubsub, host="0.0.0.0", port=5051)
    ws_srv  = WSServer(pubsub,  host="0.0.0.0", port=8080)

    print("\n========================================")
    print("  Broker corriendo")
    print("  TCP  → puerto 5051  (Pico + ESP32)")
    print("  WS   → puerto 8080  (HTML)")
    print("========================================\n")

    await asyncio.gather(tcp_srv.start(), ws_srv.start())


if __name__ == "__main__":
    asyncio.run(main())
