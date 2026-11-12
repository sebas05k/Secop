"""
Núcleo del Radar SECOP II (sin Streamlit): lo usan la app (radar_secop.py)
y las alertas diarias (alertas.py).
"""
import hashlib
import json
import os
import re
from datetime import date, timedelta

import pandas as pd
import requests

BASE = "https://www.datos.gov.co"
HEADERS = {}


def configurar_token(token):
    """Token gratuito de Socrata (opcional): más cuota de consultas."""
    HEADERS.clear()
    if token:
        HEADERS["X-App-Token"] = token


configurar_token(os.environ.get("SOCRATA_APP_TOKEN"))

# ---------------------------------------------------------------------------
# Datasets. Cada rol lleva una lista de nombres de columna posibles: la app
# detecta cuál existe de verdad (pestaña "Diagnóstico"), así que si el portal
# renombra una columna no se rompe.
# ---------------------------------------------------------------------------
DATASETS = {
    "📜 Contratos (ya firmados)": {
        "id": "jbjy-vk9h",
        "dias": 365,
        "cols": {
            "entidad": ["nombre_entidad"],
            "departamento": ["departamento"],
            "ciudad": ["ciudad"],
            "descripcion": ["descripcion_del_proceso"],
            "estado": ["estado_contrato"],
            "modalidad": ["modalidad_de_contratacion"],
            "tipo": ["tipo_de_contrato"],
            "valor": ["valor_del_contrato"],
            "fecha": ["fecha_de_firma"],
            "fecha_fin": ["fecha_de_fin_del_contrato"],
            "proveedor": ["proveedor_adjudicado"],
            "nit": ["documento_proveedor"],
            "url": ["urlproceso"],
            "contrato": ["id_contrato", "referencia_del_contrato"],
            "proceso": ["proceso_de_compra"],
            "representante": ["nombre_representante_legal"],
        },
    },
    "🟢 Convocatorias (procesos)": {
        "id": "p6dx-8zbt",  # ← de memoria; si falla, busca "SECOP II Procesos de Contratación"
        "dias": 90,
        "cols": {
            "entidad": ["entidad"],
            "departamento": ["departamento_entidad"],
            "ciudad": ["ciudad_entidad"],
            "titulo": ["nombre_del_procedimiento"],
            "descripcion": ["descripci_n_del_procedimiento"],
            "estado": ["estado_de_apertura_del_proceso", "estado_del_procedimiento"],
            "modalidad": ["modalidad_de_contratacion"],
            "tipo": ["tipo_de_contrato"],
            "valor": ["precio_base"],
            "fecha": ["fecha_de_publicacion_del"],
            "fecha_fin": ["fecha_de_recepcion_de"],
            "proveedor": ["nombre_del_proveedor"],
            "nit": ["nit_del_proveedor_adjudicado"],
            "url": ["urlproceso"],
            "contrato": ["id_del_proceso", "referencia_del_proceso"],
        },
    },
}

# Si ninguna columna candidata existe, se busca por patrón en los nombres reales
PISTAS = {
    "entidad": r"entidad", "departamento": r"departamento", "ciudad": r"ciudad|municipio",
    "descripcion": r"descrip|objeto", "estado": r"^estado", "modalidad": r"modalidad",
    "valor": r"^valor|precio", "fecha": r"fecha.*(firma|publica)", "proveedor": r"proveedor|contratista",
    "nit": r"nit|documento", "url": r"^url",
}
ROLES = ["entidad", "departamento", "ciudad", "titulo", "descripcion", "estado", "modalidad",
         "tipo", "valor", "fecha", "fecha_fin", "proveedor", "nit", "url",
         "contrato", "proceso", "representante"]

# ACTUALIZADO: Se añade "contrato" para el ruteo interno de los filtros
BUSQUEDA = {
    "descripcion": ("titulo", "descripcion"), 
    "entidad": ("entidad",), 
    "proveedor": ("proveedor",),
    "contrato": ("contrato", "proceso")
}

# Modos de la búsqueda directa (con autocompletado)
MODOS = {
    "🏢 Empresa o persona": ("proveedor", "nit", "representante"),
    "🏛️ Entidad": ("entidad",),
    "📄 Contrato / proceso": ("contrato", "proceso"),
}

# "_" funciona como comodín de un carácter: AGR_COLA encuentra AGRÍCOLA y AGRICOLA
PLANTILLAS = {
    "🌱 Agro": ["AGR_COLA", "AGRONOM", "AGROPECUARI", "FITOSANIT", "SEMILLA", "VIVERO", "UMATA", "CULTIVO", "SEGURIDAD ALIMENTARIA"],
    "📐 Arquitectura y diseño": ["ARQUITECT", "DISE_O", "URBANISMO", "PAISAJISM"],
    "🏗️ Obras civiles": ["OBRA CIVIL", "CONSTRUCCI_N", "ADECUACI_N", "REHABILITACI_N", "MEJORAMIENTO DE VIA"],
    "🔍 Interventoría": ["INTERVENTOR_A", "SUPERVISI_N T_CNICA"],
    "🌳 Ambiental": ["AMBIENTAL", "REFOREST", "SILVICUL", "FLORA", "RESTAURACI_N ECOL_GICA", "BIODIVERSIDAD"],
    "🗺️ Topografía y catastro": ["TOPOGRAF", "CATASTR", "GEORREFERENC", "CARTOGRAF"],
    "🏥 Salud": ["SALUD", "HOSPITAL", "DOTACI_N HOSPITALARIA"],
    "💻 TIC y datos": ["SOFTWARE", "SISTEMA DE INFORMACI_N", "TRANSFORMACI_N DIGITAL", "ANAL_TICA"],
    "🎓 Educación y capacitación": ["CAPACITACI_N", "FORMACI_N", "EDUCACI_N"],
    "📊 Consultoría y estudios": ["CONSULTOR_A", "ESTUDIO", "DIAGN_STICO"],
}


# ---------------------------------------------------------------------------
# API de datos.gov.co
# ---------------------------------------------------------------------------
def api(ds_id, params):
    r = requests.get(f"{BASE}/resource/{ds_id}.json", params=params, headers=HEADERS, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"datos.gov.co respondió {r.status_code}: {r.text[:300]}")
    return r.json()


def columnas_disponibles(ds_id):
    """Nombres reales de columnas (el JSON omite las nulas, por eso se usa la metadata)."""
    try:
        r = requests.get(f"{BASE}/api/views/{ds_id}.json", headers=HEADERS, timeout=30)
        r.raise_for_status()
        return [c["fieldName"] for c in r.json()["columns"]]
    except Exception:
        return sorted({k for fila in api(ds_id, {"$limit": 200}) for k in fila})


def resolver_columnas(ds, disponibles):
    res = {}
    for rol, candidatas in ds["cols"].items():
        col = next((c for c in candidatas if c in disponibles), None)
        if not col and rol in PISTAS:
            col = next((c for c in disponibles if re.search(PISTAS[rol], c)), None)
        if col:
            res[rol] = col
    return res


def valores_distintos(ds_id, col, limite=300):
    """Lanza excepción si falla (así los wrappers con caché no guardan una lista vacía por error)."""
    filas = api(ds_id, {"$select": col, "$group": col, "$order": col, "$limit": limite})
    return [f[col] for f in filas if f.get(col)]


def estados_abiertos(opciones_estado):
    return [o for o in opciones_estado if re.search(r"abiert|public|convoc", o, re.I)]


def esc(s):
    return s.replace("'", "''")


def patron(termino):
    t = re.sub(r"[ÁÉÍÓÚÜÑ]", "_", termino.upper().strip())
    return f"%{esc(t)}%"


def contiene(col, texto):
    return f"upper({col}) like '{patron(texto)}'"


def _flexible(palabra):
    """Palabras largas escritas sin tilde: cada vocal pasa a comodín '_', así 'fundacion' encuentra FUNDACIÓN y
    FUNDACION. En palabras cortas no se hace, para no traer ruido ('campo' no debe encontrar 'COMPETENCIA')."""
    u = palabra.upper()
    if len(u) >= 6 and u.isalpha() and not re.search(r"[ÁÉÍÓÚÜÑ]", u):
        return re.sub(r"[AEIOU]", "_", u)
    return palabra


def contiene_palabras(col, texto):
    """Todas las palabras deben aparecer, en cualquier orden ('fundacion campo' encuentra 'FUNDACIÓN PARA EL CAMPO').
    Se ignoran las palabras de menos de 3 letras (de, la, el...), que no filtran nada."""
    palabras = list(dict.fromkeys(t for t in texto.split() if len(t) >= 3)) or [texto.strip()]
    return "(" + " AND ".join(contiene(col, _flexible(t)) for t in palabras) + ")"


def sugerir(ds_id, cols, texto):
    """Valores más frecuentes que contienen `texto` en cada columna."""
    out = []
    for col in cols:
        try:
            filas = api(ds_id, {"$select": f"{col}, count(*) as n", "$where": contiene_palabras(col, texto),
                                "$group": col, "$order": "n DESC", "$limit": 6})
        except Exception:
            continue
        out += [str(f[col]) for f in filas if f.get(col)]
    return list(dict.fromkeys(out))[:12]


def filtros_base(**kw):
    """Filtros con todos los campos en su valor neutro; se sobreescribe lo que haga falta."""
    hoy = date.today()
    f = dict(rapida=None, terminos=[], todos=False, excluir=[], buscar_en=["descripcion"], deptos=[],
             ciudad="", desde=str(hoy - timedelta(days=90)), hasta=str(hoy), estados=[], modalidades=[],
             tipos=[], entidad="", proveedor="", solo_vigentes=False, valor_min=0, tope=10**12, limite=2000)
    f.update(kw)
    return f


def construir_where(c, f):
    # ACTUALIZADO: El bloque 'rapida' fue removido para obligar el uso de los filtros
    # completos de fecha sin generar Timeouts en búsquedas por empresas/entidades.
    
    w, cols_busq = [], []
    for clave in f["buscar_en"]:
        if clave in BUSQUEDA:
            cols_busq += [c[r] for r in BUSQUEDA[clave] if r in c]
            
    if f["terminos"] and cols_busq:
        bloques = ["(" + " OR ".join(contiene(col, t) for col in cols_busq) + ")" for t in f["terminos"]]
        w.append("(" + (" AND " if f["todos"] else " OR ").join(bloques) + ")")
        
    for t in f["excluir"]:
        w += [f"upper({col}) not like '{patron(t)}'" for col in cols_busq]
        
    for rol, clave in (("departamento", "deptos"), ("estado", "estados"), ("modalidad", "modalidades"), ("tipo", "tipos")):
        if f[clave] and rol in c:
            w.append(f"{c[rol]} in (" + ", ".join(f"'{esc(v)}'" for v in f[clave]) + ")")
            
    for rol, clave in (("ciudad", "ciudad"), ("entidad", "entidad")):
        if f[clave] and rol in c:
            w.append(contiene(c[rol], f[clave]))
            
    if f["proveedor"]:
        # ACTUALIZADO: Agregada la columna representante para ser más exhaustivos
        cols = [c[r] for r in ("proveedor", "nit", "representante") if r in c] 
        if cols:
            w.append("(" + " OR ".join(contiene(col, f["proveedor"]) for col in cols) + ")")
            
    if "fecha" in c:
        w.append(f"{c['fecha']} >= '{f['desde']}T00:00:00'")
        w.append(f"{c['fecha']} <= '{f['hasta']}T23:59:59'")
        
    return " AND ".join(w)


def traer(ds_id, select, where, order, limite):
    """Devuelve (total de coincidencias en el portal, DataFrame con hasta `limite` filas)."""
    base = {"$where": where} if where else {}
    total = int(list(api(ds_id, {**base, "$select": "count(*)"})[0].values())[0])
    filas = []
    while len(filas) < min(limite, total):
        params = {**base, "$select": select, "$limit": min(5000, limite - len(filas)), "$offset": len(filas)}
        if order:
            params["$order"] = order
        lote = api(ds_id, params)
        if not lote:
            break
        filas += lote
    return total, pd.DataFrame(filas)


def _txt(v):
    return v if isinstance(v, str) else ""


def clave_registro(url, contrato, entidad, texto):
    """Identificador estable de un registro (para seguimiento y para no repetir alertas)."""
    base = _txt(url) or _txt(contrato) or f"{_txt(entidad)}|{_txt(texto)}"
    return hashlib.md5(base.encode("utf-8")).hexdigest()[:12]


def normalizar(df, c, tope):
    out = pd.DataFrame({r: (df[c[r]] if r in c and c[r] in df.columns else None) for r in ROLES}, index=df.index)
    out["url"] = out["url"].map(lambda v: v.get("url", "") if isinstance(v, dict) else (v if isinstance(v, str) else ""))
    out["valor"] = pd.to_numeric(out["valor"], errors="coerce")
    ignorados = int((out["valor"] > tope).sum())
    out["valor"] = out["valor"].where(out["valor"] <= tope)
    for col in ("fecha", "fecha_fin"):
        out[col] = pd.to_datetime(out[col], errors="coerce")
    out["vigente"] = out["fecha_fin"] >= pd.Timestamp.today().normalize()
    out["texto"] = (out["titulo"].fillna("") + " " + out["descripcion"].fillna("")).str.strip()
    out["clave"] = [clave_registro(u, k, e, t) for u, k, e, t in zip(out["url"], out["contrato"], out["entidad"], out["texto"])]
    return out.drop_duplicates(), ignorados


def ejecutar(ds, c, f, traer_fn=traer):
    """Consulta completa: construye el filtro, descarga, normaliza y aplica los filtros posteriores."""
    where = construir_where(c, f)
    select = ",".join(dict.fromkeys(c.values()))
    order = f"{c['fecha']} DESC" if "fecha" in c else ""
    total, crudo = traer_fn(ds["id"], select, where, order, f["limite"])
    df, ignorados = pd.DataFrame(), 0
    if not crudo.empty:
        df, ignorados = normalizar(crudo, c, f["tope"])
        if f["solo_vigentes"] and "fecha_fin" in c and not f.get("rapida"):
            df = df[df["vigente"]]
        if f["valor_min"] and not f.get("rapida"):
            df = df[df["valor"].fillna(0) >= f["valor_min"]]
    return dict(where=where, select=select, order=order, total=total, n_crudo=len(crudo), df=df, ignorados=ignorados)


def partir(txt):
    return [t.strip() for t in re.split(r"[,;\n]", txt or "") if t.strip()]


def fechas(rango):
    if isinstance(rango, (tuple, list)):
        return (rango[0], rango[-1])
    return (rango, rango)


# ---------------------------------------------------------------------------
# IA (Gemini, capa gratuita de Google AI Studio) — se llama por REST, sin librería extra
# ---------------------------------------------------------------------------
# Los nombres de modelo cambian con el tiempo. Se prueba GEMINI_MODEL (si lo defines)
# y luego estos alias; solo se pasa al siguiente si el modelo no existe (404).
MODELOS_GEMINI = ["gemini-flash-latest", "gemini-2.5-flash"]

PROMPT_PERFIL = """Eres experto en contratación pública colombiana (SECOP II). Una persona busca contratos o convocatorias donde pueda ofrecer sus servicios. Con su perfil, genera los filtros de búsqueda.

PERFIL:
<<PERFIL>>

Las búsquedas se hacen con LIKE '%palabra%' sobre el objeto del contrato (texto en mayúsculas), así que:
- "terminos": de 12 a 25 palabras clave o raíces CORTAS en MAYÚSCULAS, tal como suelen aparecer en objetos contractuales (sinónimos, cargos, actividades, entidades afines). Usa raíces para cubrir variantes (AGRONOM cubre AGRÓNOMO y AGRONOMÍA). Reemplaza cada vocal con tilde y cada Ñ por "_" (AGR_COLA, DISE_O, INTERVENTOR_A). Evita palabras genéricas que traerían mucho ruido (APOYO, GESTION, SERVICIOS, PRESTACION).
- "excluir": de 0 a 8 raíces de áreas claramente ajenas al perfil que suelan colarse en los resultados.
- "departamentos": solo nombres EXACTOS de esta lista, y solo si el perfil menciona una ubicación; si no, lista vacía. Lista: <<DEPTOS>>
- "ciudad": un municipio si el perfil menciona uno solo; si no, cadena vacía.
- "explicacion": una frase en español que resuma qué se va a buscar.

Responde SOLO un objeto JSON con esas cinco claves."""


def gemini_json(prompt, api_key, modelo=None):
    candidatos = list(dict.fromkeys(([modelo] if modelo else []) + MODELOS_GEMINI))
    ultimo = ""
    for m in candidatos:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}},
            timeout=60,
        )
        if r.status_code == 200:
            partes = r.json()["candidates"][0]["content"]["parts"]
            texto = "".join(p.get("text", "") for p in partes if not p.get("thought")).strip()
            texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
            return json.loads(texto)
        ultimo = f"{m}: {r.status_code} {r.text[:200]}"
        if r.status_code == 429:
            raise RuntimeError("Se agotó la cuota gratuita de Gemini por ahora. Espera un minuto o inténtalo mañana.")
        if r.status_code != 404:
            raise RuntimeError(f"Gemini respondió {ultimo}")
    raise RuntimeError(f"Ningún modelo de Gemini respondió ({ultimo}). Define GEMINI_MODEL con un nombre vigente de AI Studio.")


def perfil_a_filtros(texto, api_key, departamentos, modelo=None):
    """Convierte el perfil en palabras clave, exclusiones y departamentos para los filtros."""
    prompt = (PROMPT_PERFIL.replace("<<PERFIL>>", texto.strip()[:2000])
              .replace("<<DEPTOS>>", "; ".join(departamentos) or "(sin lista)"))
    d = gemini_json(prompt, api_key, modelo)

    def lista(clave, tope):
        v = d.get(clave) or []
        v = v if isinstance(v, list) else [v]
        return list(dict.fromkeys(str(x).strip() for x in v if str(x).strip()))[:tope]

    canon = {x.lower(): x for x in departamentos}
    return {
        "terminos": [t.upper() for t in lista("terminos", 30)],
        "excluir": [t.upper() for t in lista("excluir", 10)],
        "deptos": [canon[x.lower()] for x in lista("departamentos", 40) if x.lower() in canon],
        "ciudad": str(d.get("ciudad") or "").strip()[:60],
        "explicacion": str(d.get("explicacion") or "").strip()[:300],
    }
