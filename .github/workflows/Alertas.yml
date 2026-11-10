"""
Alertas diarias: busca convocatorias NUEVAS que encajan con tu perfil y te avisa
por Telegram y/o correo. Lo ejecuta una GitHub Action (ver alertas.yml).

Variables de entorno (en GitHub: Settings → Secrets and variables → Actions):
  SHEET_ID, GCP_SERVICE_ACCOUNT (el JSON completo)      ← obligatorias
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID                  ← para Telegram
  SMTP_USER, SMTP_PASS, EMAIL_TO                        ← para correo (Gmail + contraseña de aplicación)
  SOCRATA_APP_TOKEN                                     ← opcional
  ALERTA_DIAS (por defecto 3), ALERTA_MAX (por defecto 25)
"""
import json
import os
import smtplib
import sys
from datetime import date, timedelta
from email.message import EmailMessage

import pandas as pd
import requests

import secop_core as sc
from almacen import Almacen

DIAS = int(os.environ.get("ALERTA_DIAS", "3"))   # mira lo publicado en los últimos N días
MAXIMO = int(os.environ.get("ALERTA_MAX", "25"))


def formatear(df):
    items = []
    for r in df.itertuples():
        partes = [p for p in (r.entidad, r.departamento) if isinstance(p, str) and p]
        if pd.notna(r.valor):
            partes.append(f"${r.valor:,.0f}")
        if pd.notna(r.fecha_fin):
            partes.append(f"cierra {r.fecha_fin:%d/%m/%Y}")
        items.append(f"• {(r.texto or 'Sin descripción')[:160]}\n  {' · '.join(partes)}\n  {r.url or ''}".rstrip())
    return items


def en_bloques(encabezado, items, limite=3800):
    bloques, actual = [], encabezado
    for it in items:
        if len(actual) + len(it) + 2 > limite:
            bloques.append(actual)
            actual = ""
        actual += ("\n\n" if actual else "") + it
    bloques.append(actual)
    return bloques


def telegram(token, chat_id, bloques):
    for b in bloques:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": b, "disable_web_page_preview": True}, timeout=30)
        r.raise_for_status()


def correo(usuario, clave, destino, asunto, cuerpo):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = asunto, usuario, destino
    msg.set_content(cuerpo)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(usuario, clave)
        s.send_message(msg)


def main():
    sheet_id, cred = os.environ.get("SHEET_ID"), os.environ.get("GCP_SERVICE_ACCOUNT")
    if not (sheet_id and cred):
        sys.exit("Faltan los secretos SHEET_ID y GCP_SERVICE_ACCOUNT.")
    alm = Almacen(json.loads(cred), sheet_id)

    perfil = alm.leer_perfil()
    if not perfil.get("terminos"):
        print("Tu perfil aún no tiene palabras clave. Genéralas en la app (pestaña Para mí) y guárdalas.")
        return

    ds = sc.DATASETS["🟢 Convocatorias (procesos)"]
    c = sc.resolver_columnas(ds, sc.columnas_disponibles(ds["id"]))
    estados = []
    if "estado" in c:
        estados = sc.estados_abiertos(sc.valores_distintos(ds["id"], c["estado"]))
    hoy = date.today()
    f = sc.filtros_base(terminos=perfil["terminos"], excluir=perfil.get("excluir", []),
                        deptos=perfil.get("deptos", []), ciudad=perfil.get("ciudad", ""),
                        desde=str(hoy - timedelta(days=DIAS)), hasta=str(hoy),
                        estados=estados, solo_vigentes=True, limite=500)
    res = sc.ejecutar(ds, c, f)
    df = res["df"]
    print(f"Consulta: {res['total']} coincidencias en el portal, {len(df)} vigentes.")
    if df.empty:
        return

    ya = alm.leer_vistos() | {s["clave"] for s in alm.leer_seguimiento()}
    nuevas = df[~df["clave"].isin(ya)].drop_duplicates("clave").sort_values("fecha_fin")
    if nuevas.empty:
        print("Nada nuevo desde la última alerta.")
        return

    mostrar = nuevas.head(MAXIMO)
    resto = len(nuevas) - len(mostrar)
    encabezado = f"📡 {len(nuevas)} convocatoria(s) nueva(s) que encajan con tu perfil"
    items = formatear(mostrar)
    if resto:
        items.append(f"… y {resto} más. Míralas en la app.")

    enviado = False
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        telegram(token, chat, en_bloques(encabezado, items))
        enviado = True
    user, pwd, destino = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS"), os.environ.get("EMAIL_TO")
    if user and pwd and destino:
        correo(user, pwd, destino, encabezado, "\n\n".join(items))
        enviado = True

    if not enviado:
        print("No hay Telegram ni correo configurados; muestro las alertas aquí y NO las marco como vistas:\n")
        print(encabezado + "\n\n" + "\n\n".join(items))
        return
    alm.agregar_vistos(list(nuevas["clave"]))   # se marcan todas, incluso las que no cupieron en el mensaje
    print(f"Alerta enviada: {len(nuevas)} nuevas.")


if __name__ == "__main__":
    main()
