"""
Alertas diarias: te avisan por Telegram y/o correo de lo NUEVO que encaja con tu perfil. Lo ejecuta una
GitHub Action (ver alertas.yml). Hay tres tipos de aviso, que eliges en la app (pestaña Guardados → Alertas):

  🟢 convocatorias   procesos abiertos publicados hace poco (de cualquier modalidad)
  🏆 adjudicados     contratos recién firmados por licitación, concurso, selección abreviada, régimen especial...
                     (las empresas ganadoras son quienes pueden necesitar un profesional)
  🤝 directos        contratos recién firmados por contratación directa (incluye prestación de servicios)

Variables de entorno (en GitHub: Settings → Secrets and variables → Actions):
  SHEET_ID, GCP_SERVICE_ACCOUNT (el JSON completo)      ← obligatorias
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID                  ← para Telegram
  SMTP_USER, SMTP_PASS, EMAIL_TO                        ← para correo (Gmail + contraseña de aplicación)
  SOCRATA_APP_TOKEN                                     ← opcional
  ALERTA_DIAS (por defecto 3), ALERTA_DIAS_CONTRATOS (por defecto 7), ALERTA_MAX (por defecto 25, por sección)
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

DIAS = int(os.environ.get("ALERTA_DIAS", "3"))                      # convocatorias publicadas en los últimos N días
DIAS_CONTRATOS = int(os.environ.get("ALERTA_DIAS_CONTRATOS", "7"))  # contratos firmados en los últimos N días
MAXIMO = int(os.environ.get("ALERTA_MAX", "25"))                    # máximo de avisos por sección
TIPOS = ("convocatorias", "adjudicados", "directos")
CONVOCATORIAS = "🟢 Convocatorias (procesos)"
CONTRATOS = "📜 Contratos (ya firmados)"


def formatear(df, contrato=False):
    items = []
    for r in df.itertuples():
        partes = []
        if contrato and isinstance(r.proveedor, str) and r.proveedor:
            partes.append(f"🏢 {r.proveedor}")
        partes += [p for p in (r.entidad, r.departamento) if isinstance(p, str) and p]
        if pd.notna(r.valor):
            partes.append(f"${r.valor:,.0f}")
        if isinstance(r.modalidad, str) and r.modalidad:
            partes.append(r.modalidad)
        if contrato and pd.notna(r.fecha):
            partes.append(f"firmado {r.fecha:%d/%m/%Y}")
        if not contrato and pd.notna(r.fecha_fin):
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


def filtros_perfil(perfil, desde, **extra):
    hoy = date.today()
    excluir = list(perfil.get("excluir", [])) + (sc.RUIDO_TIPICO if perfil.get("quitar_ruido", True) else [])
    return sc.filtros_base(terminos=perfil.get("terminos", []), reglas=perfil.get("reglas", []), excluir_local=excluir,
                           deptos=perfil.get("deptos", []),
                           ciudad=perfil.get("ciudad", ""), desde=str(desde), hasta=str(hoy), limite=500, **extra)


def convocatorias_nuevas(perfil, ya):
    ds = sc.DATASETS[CONVOCATORIAS]
    c = sc.resolver_columnas(ds, sc.columnas_disponibles(ds["id"]))
    estados = sc.estados_abiertos(sc.valores_distintos(ds["id"], c["estado"])) if "estado" in c else []
    f = filtros_perfil(perfil, date.today() - timedelta(days=DIAS), estados=estados, solo_vigentes=True)
    res = sc.ejecutar(ds, c, f)
    df = res["df"]
    print(f"Convocatorias: {res['total']} coincidencias en el portal, {len(df)} vigentes.")
    if df.empty:
        return df
    return df[~df["clave"].isin(ya)].drop_duplicates("clave").sort_values("fecha_fin")


def contratos_nuevos(perfil, ya):
    """Contratos firmados hace poco: (adjudicados por licitación/concurso/etc., directos). Los más grandes primero."""
    ds = sc.DATASETS[CONTRATOS]
    c = sc.resolver_columnas(ds, sc.columnas_disponibles(ds["id"]))
    f = filtros_perfil(perfil, date.today() - timedelta(days=DIAS_CONTRATOS),
                       valor_min=int(perfil.get("valor_min_contratos") or 0))
    res = sc.ejecutar(ds, c, f)
    df = res["df"]
    print(f"Contratos: {res['total']} coincidencias en el portal, {len(df)} tras filtros.")
    if df.empty:
        return df, df
    df = df[~df["clave"].isin(ya)].drop_duplicates("clave")
    # primero lo que más se parece a tu perfil (más palabras del núcleo), y a igual relevancia lo de mayor valor
    df = df.assign(_rel=sc.puntaje(df, perfil.get("terminos", []), perfil.get("reglas", []))).sort_values(
        ["_rel", "valor"], ascending=False)
    directos = df["modalidad"].fillna("").str.contains("directa", case=False)
    return df[~directos], df[directos]


def main():
    sheet_id, cred = os.environ.get("SHEET_ID"), os.environ.get("GCP_SERVICE_ACCOUNT")
    if not (sheet_id and cred):
        sys.exit("Faltan los secretos SHEET_ID y GCP_SERVICE_ACCOUNT.")
    alm = Almacen(json.loads(cred), sheet_id)

    perfil = alm.leer_perfil()
    if not (perfil.get("terminos") or perfil.get("reglas")):
        print("Tu perfil aún no tiene palabras clave. Defínelas en la app (pestaña Guardados → Alertas) y guárdalas.")
        return
    tipos = [t for t in (perfil.get("alertas_tipos") or TIPOS) if t in TIPOS]
    ya = alm.leer_vistos() | {s["clave"] for s in alm.leer_seguimiento()}

    secciones, errores = [], []   # (título, DataFrame, ¿es contrato?)
    if "convocatorias" in tipos:
        try:
            secciones.append(("🟢 CONVOCATORIAS ABIERTAS", convocatorias_nuevas(perfil, ya), False))
        except Exception as e:
            errores.append(f"convocatorias: {e}")
    if "adjudicados" in tipos or "directos" in tipos:
        try:
            adjudicados, directos = contratos_nuevos(perfil, ya)
            if "adjudicados" in tipos:
                secciones.append(("🏆 CONTRATOS RECIÉN ADJUDICADOS (licitaciones, concursos…)", adjudicados, True))
            if "directos" in tipos:
                secciones.append(("🤝 CONTRATOS DIRECTOS RECIENTES", directos, True))
        except Exception as e:
            errores.append(f"contratos: {e}")
    for e in errores:
        print(f"⚠️ No pude consultar {e}")

    secciones = [(t, df, k) for t, df, k in secciones if not df.empty]
    total = sum(len(df) for _, df, _ in secciones)
    if not total:
        if errores and not secciones:
            sys.exit("Fallaron todas las consultas; reintenta más tarde.")
        print("Nada nuevo desde la última alerta.")
        return

    items = []
    for titulo, df, es_contrato in secciones:
        mostrar = df.head(MAXIMO)
        items.append(f"{titulo} ({len(df)})")
        items += formatear(mostrar, es_contrato)
        if len(df) > len(mostrar):
            items.append(f"… y {len(df) - len(mostrar)} más. Míralas en la app.")
    encabezado = f"📡 {total} novedad(es) que encajan con tu perfil"

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
    alm.agregar_vistos(list(pd.concat([df for _, df, _ in secciones])["clave"]))  # se marcan todas, incluso las que no cupieron
    print(f"Alerta enviada: {total} novedades.")


if __name__ == "__main__":
    main()
