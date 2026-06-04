"""
voltmeter.py — Lectura de voltaje de batería con el ADC de la Pico.

Circuito (GP26):
    BATT+ ──[ 18kΩ ]──┬──[ 10kΩ ]── GND
                      │
                    GP26 (ADC0)

Factor divisor = (18k + 10k) / 10k = 2.8
→ Mide hasta 7.4V (batería LiPo 2S), el pin recibe máx 2.64V (seguro).
"""

from machine import ADC, Pin


class Voltmeter:
    VREF    = 3.3
    ADC_MAX = 65535

    def __init__(self, pin=26, divider=2.8, samples=16):
        self.adc     = ADC(Pin(pin))
        self.divider = divider   # (R1+R2)/R2 = 2.8
        self.samples = samples   # promediado para estabilizar

    def read_raw(self):
        acc = 0
        for _ in range(self.samples):
            acc += self.adc.read_u16()
        return acc // self.samples

    def read_voltage(self):
        """Voltaje real de la batería en voltios (float, 2 decimales)."""
        raw   = self.read_raw()
        v_pin = (raw / self.ADC_MAX) * self.VREF
        return round(v_pin * self.divider, 2)
