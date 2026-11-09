"""
Almacenamiento en Google Sheets (gratis y persistente), para la app y las alertas.

Un solo Google Sheet con tres pestañas que se crean solas:
  perfil       → tu perfil y las palabras clave que generó la IA
  seguimiento  → oportunidades guardadas con su estado (por enviar, enviado...)
  vistos       → convocatorias ya notificadas (para no repetir alertas)
"""
import json
from datetime import date

import gspread

ESTADOS = ["por enviar", "enviado", "respondió", "sin respuesta", "descartado"]
COLS_SEG = ["clave", "estado", "notas", "entidad", "proveedor", "texto", "valor", "fecha_fin", "url", "guardado"]
RAW = "RAW"  # guarda los textos tal cual, sin que Sheets los reinterprete (fechas, números, fórmulas)


def _t(v):
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN
        return ""
    return str(v)


class Almacen:
    def __init__(self, credenciales, sheet_id):
        self.sh = gspread.service_account_from_dict(credenciales).open_by_key(sheet_id)
        self._hojas = {}

    def _hoja(self, nombre, cabecera):
        if nombre not in self._hojas:
            try:
                ws = self.sh.worksheet(nombre)
            except gspread.WorksheetNotFound:
                ws = self.sh.add_worksheet(title=nombre, rows=500, cols=max(len(cabecera), 2))
                ws.update(values=[cabecera], range_name="A1", value_input_option=RAW)
            self._hojas[nombre] = ws
        return self._hojas[nombre]

    # --- perfil ------------------------------------------------------------
    def leer_perfil(self):
        filas = self._hoja("perfil", ["clave", "valor"]).get_all_values()
        for fila in filas[1:]:
            if len(fila) >= 2 and fila[0] == "perfil" and fila[1]:
                try:
                    return json.loads(fila[1])
                except ValueError:
                    return {}
        return {}

    def guardar_perfil(self, perfil):
        ws = self._hoja("perfil", ["clave", "valor"])
        ws.clear()
        ws.update(values=[["clave", "valor"], ["perfil", json.dumps(perfil, ensure_ascii=False)]],
                  range_name="A1", value_input_option=RAW)

    # --- seguimiento -------------------------------------------------------
    def leer_seguimiento(self):
        filas = self._hoja("seguimiento", COLS_SEG).get_all_records(numericise_ignore=["all"])
        return [{c: _t(f.get(c, "")) for c in COLS_SEG} for f in filas if f.get("clave")]

    def agregar_seguimiento(self, filas):
        if filas:
            self._hoja("seguimiento", COLS_SEG).append_rows(
                [[_t(f.get(c, "")) for c in COLS_SEG] for f in filas], value_input_option=RAW)

    def guardar_seguimiento(self, filas):
        """Reescribe la pestaña completa (para ediciones de estado, notas y borrados)."""
        ws = self._hoja("seguimiento", COLS_SEG)
        ws.clear()
        ws.update(values=[COLS_SEG] + [[_t(f.get(c, "")) for c in COLS_SEG] for f in filas],
                  range_name="A1", value_input_option=RAW)

    # --- alertas -----------------------------------------------------------
    def leer_vistos(self):
        return set(self._hoja("vistos", ["clave", "fecha"]).col_values(1)[1:])

    def agregar_vistos(self, claves):
        if claves:
            self._hoja("vistos", ["clave", "fecha"]).append_rows(
                [[k, date.today().isoformat()] for k in claves], value_input_option=RAW)
