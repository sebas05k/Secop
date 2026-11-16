"""
Núcleo del Radar SECOP II (sin Streamlit): lo usan la app (radar_secop.py)
y las alertas diarias (alertas.py).
"""
import hashlib
import json
import os
import re
from datetime import date, timedelta
from urllib.parse import quote_plus

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
BUSQUEDA = {"descripcion": ("titulo", "descripcion"), "entidad": ("entidad",), "proveedor": ("proveedor",)}

# Modos de la búsqueda directa (con autocompletado)
MODOS = {
    "🏢 Empresa o persona": ("proveedor", "nit", "representante"),
    "🏛️ Entidad": ("entidad",),
    "📄 Contrato / proceso": ("contrato", "proceso"),
}

# "_" funciona como comodín de un carácter: AGR_COLA encuentra AGRÍCOLA y AGRICOLA
PLANTILLAS = {
    "🌱 Agro": ["AGR_COLA", "AGR_NOM", "AGROPECUARI", "AGROECOL", "AGROFORESTAL", "FITOSANIT", "FITOPATOL", "SEMILLA", "VIVERO",
               "UMATA", "EXTENSI_N RURAL", "DESARROLLO RURAL", "PROYECTOS PRODUCTIVOS", "SUSTITUCI_N DE CULTIVOS", "REFORMA AGRARIA"],
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
TIMEOUT = 45  # segundos que se espera una respuesta de datos.gov.co


def api(ds_id, params, timeout=TIMEOUT):
    try:
        r = requests.get(f"{BASE}/resource/{ds_id}.json", params=params, headers=HEADERS, timeout=timeout)
    except requests.exceptions.Timeout:
        raise RuntimeError(f"datos.gov.co tardó más de {timeout} s en responder. Reintenta o afina la búsqueda "
                           "(más palabras o una entidad concreta). Con un token de datos.gov.co suele responder más rápido.")
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


# ---------------------------------------------------------------------------
# Perfil restrictivo: "núcleo" (palabras que bastan solas) + "combinaciones" (A + B: deben aparecer ambas)
# Las palabras generales (apoyo, profesional, supervisor, UARIV, QGIS...) NO bastan solas: solo cuentan junto a un tema agro.
# ---------------------------------------------------------------------------
NUCLEO_AGRONOMO = [
    "AGR_NOM", "AGROPECUARI", "AGROECOL", "AGROFORESTAL", "FITOSANIT", "FITOPATOL", "SANIDAD VEGETAL",
    "EXTENSI_N AGROPECUARIA", "EXTENSI_N RURAL", "UMATA", "ASISTENCIA T_CNICA RURAL", "PROYECTOS PRODUCTIVOS",
    "AGRICULTURA CAMPESINA", "AGRICULTURA FAMILIAR", "HUERTA", "HORTALIZA",
]
COMBOS_AGRONOMO = """DESARROLLO RURAL + ASISTENCIA T_CNICA | EXTENSI_N | PRODUCTIV | AGRO | CULTIV
AGR_COLA + PROFESIONAL | T_CNIC | ASISTENCIA | APOYO | SUPERVIS | EXTENSI_N
CULTIV + ASISTENCIA T_CNICA | PROFESIONAL | APOYO | SUPERVIS
APOYO EN CAMPO + AGRO | AGR_COLA | CULTIV | PRODUCTIV | RURAL
SUPERVIS + AGRO | AGR_COLA | PRODUCTIV | RURAL
UARIV | V_CTIMAS + PRODUCTIV | RURAL | AGRO | AGR_COLA | HUERTA
SOBERAN_A ALIMENTARIA | SEGURIDAD ALIMENTARIA + RURAL | CAMPESIN | PRODUCTIV | HUERTA | AGRO
QGIS | GEORREFERENC | CARTOGRAF | INFORMACI_N GEOGR_FICA + AGRO | AGR_COLA | RURAL | CULTIV
AN_LISIS DE DATOS + AGRO | AGR_COLA | RURAL | CULTIV"""
EXCLUIR_AGRONOMO = ["RACIONES", "COMBUSTIBLE"]
PLANTILLAS["🎯 Ingeniería agronómica (estricta)"] = NUCLEO_AGRONOMO


def texto_a_reglas(texto):
    """'A | B + C | D' (una combinación por línea) → [[['A','B'],['C','D']], ...]. Cada lado necesita al menos una palabra."""
    reglas = []
    for linea in (texto or "").splitlines():
        grupos = [[p.strip() for p in g.split("|") if p.strip()] for g in linea.split("+")]
        grupos = [g for g in grupos if g]
        if len(grupos) >= 2:
            reglas.append(grupos)
    return reglas


def reglas_a_texto(reglas):
    return "\n".join(" + ".join(" | ".join(g) for g in r) for r in reglas)


# Contratos que casi nunca interesan a un agrónomo ni a un arquitecto y se cuelan por palabras sueltas
RUIDO_TIPICO = ["M_DICO", "M_DICA", "ODONT_LOG", "ENFERMER", "PSIC_LOG", "ABOGAD", "JUR_DIC", "CONTADOR", "CONTABLE",
                "ART_STIC", "ESC_NIC", "DEPORT", "ALIMENTACI_N ESCOLAR", "VIGILANCIA", "ASEO", "CAFETER_A", "PAPELER_A",
                "ROPA HOSPITALARIA", "DOTACI_N HOSPITALARIA", "FESTIVAL", "FIESTAS"]

# Palabras que, solas, aparecen en casi cualquier contrato: no sirven como palabra clave
_GENERALES = {"PRESTACI_N", "PRESTAR", "SERVICIO", "SERVICIOS", "PROFESIONAL", "PROFESIONALES", "APOYO", "GESTI_N", "ASESOR_A",
              "ASESOR", "ASESORAR", "CONSULTOR_A", "ESTUDIO", "ESTUDIOS", "DIAGN_STICO", "CAPACITACI_N", "FORMACI_N", "EDUCACI_N",
              "DE", "LA", "EL", "LOS", "LAS", "PARA", "Y", "EN", "A", "COMO", "CONTRATO", "TECNICO", "T_CNICO", "ACTIVIDADES"}


def palabras_genericas(terminos):
    """Palabras clave formadas solo por palabras muy generales (ej. 'prestación de servicios profesionales')."""
    def norm(t):
        return re.sub(r"[ÁÉÍÓÚÜÑ]", "_", t.upper()).split()
    return [t for t in terminos if norm(t) and all(w in _GENERALES for w in norm(t))]


def _rx(t):
    """Palabra clave estilo LIKE ('_' = cualquier carácter) → expresión regular, para contar coincidencias en pandas."""
    return re.escape(re.sub(r"[ÁÉÍÓÚÜÑ]", "_", t.upper())).replace("_", ".")


def _coincide_regla(textos, regla):
    ok = pd.Series(True, index=textos.index)
    for grupo in regla:
        ok &= textos.str.contains("|".join(_rx(x) for x in grupo), regex=True)
    return ok


def puntaje(df, terminos, reglas=()):
    """Relevancia de cada fila: 2 puntos por palabra del núcleo y 1 por combinación cumplida (para ordenar)."""
    textos = df["texto"].fillna("").str.upper()
    p = pd.Series(0, index=df.index)
    for t in terminos:
        p += 2 * textos.str.contains(_rx(t), regex=True).astype(int)
    for r in reglas:
        p += _coincide_regla(textos, r).astype(int)
    return p


def conteo_por_palabra(df, terminos, reglas=()):
    """Cuántos resultados trae cada palabra clave o combinación (para detectar las que meten ruido)."""
    textos = df["texto"].fillna("").str.upper()
    filas = [(t, int(textos.str.contains(_rx(t), regex=True).sum())) for t in terminos]
    filas += [(" + ".join(" | ".join(g) for g in r), int(_coincide_regla(textos, r).sum())) for r in reglas]
    return pd.DataFrame(filas, columns=["palabra", "resultados"]).sort_values("resultados", ascending=False)


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


MAX_URL = 5000  # largo máximo (ya codificado) del filtro de una consulta; si se pasa, se parte en varias


def construir_wheres(c, f, max_url=MAX_URL):
    """Lista de filtros SoQL. Normalmente es uno; si hay tantas palabras que la dirección sería demasiado larga,
    se reparten en varias consultas cuyos resultados se unen después."""
    base, cols_busq, alts = [], [], []
    if "fecha" in c:
        base.append(f"{c['fecha']} >= '{f['desde']}T00:00:00'")
        base.append(f"{c['fecha']} <= '{f['hasta']}T23:59:59'")
    if f.get("rapida"):  # nombre elegido en la barra (empresa/persona, entidad o contrato); se suma a los demás filtros
        modo, valor = f["rapida"]
        cols = [c[r] for r in MODOS[modo] if r in c]
        if cols:
            base.append("(" + " OR ".join(contiene_palabras(col, valor) for col in cols) + ")")
    for clave in f["buscar_en"]:
        cols_busq += [c[r] for r in BUSQUEDA[clave] if r in c]
    reglas = f.get("reglas") or []
    if (f["terminos"] or reglas) and cols_busq:
        def en_cols(t):
            return "(" + " OR ".join(contiene(col, t) for col in cols_busq) + ")"
        alts = [en_cols(t) for t in f["terminos"]]
        for regla in reglas:  # cada grupo es un OR de alternativas; los grupos de una regla se unen con AND
            alts.append("(" + " AND ".join("(" + " OR ".join(en_cols(x) for x in g) + ")" for g in regla) + ")")
    for t in f["excluir"]:
        base += [f"upper({col}) not like '{patron(t)}'" for col in cols_busq]
    for rol, clave in (("departamento", "deptos"), ("estado", "estados"), ("modalidad", "modalidades"), ("tipo", "tipos")):
        if f[clave] and rol in c:
            base.append(f"{c[rol]} in (" + ", ".join(f"'{esc(v)}'" for v in f[clave]) + ")")
    for rol, clave in (("ciudad", "ciudad"), ("entidad", "entidad")):
        if f[clave] and rol in c:
            base.append(contiene_palabras(c[rol], f[clave]))
    if f["proveedor"]:
        cols = [c[r] for r in ("proveedor", "nit") if r in c]
        if cols:
            base.append("(" + " OR ".join(contiene_palabras(col, f["proveedor"]) for col in cols) + ")")
    if not alts:
        return [" AND ".join(base)]
    union = " AND " if f["todos"] else " OR "

    def armar(grupo):
        return " AND ".join(base + ["(" + union.join(grupo) + ")"])
    if f["todos"] or max_url is None:
        return [armar(alts)]
    trozos, actual = [], []
    for a in alts:
        if actual and len(quote_plus(armar(actual + [a]))) > max_url:
            trozos.append(actual)
            actual = []
        actual.append(a)
    trozos.append(actual)
    return [armar(t) for t in trozos]


def construir_where(c, f):
    return construir_wheres(c, f, max_url=None)[0]


def traer(ds_id, select, where, order, limite):
    """Devuelve (total de coincidencias en el portal, DataFrame con hasta `limite` filas).
    Primero baja los datos; solo si llegó al límite (puede haber más) gasta una consulta extra en contar."""
    base = {"$where": where} if where else {}
    filas = []
    while len(filas) < limite:
        n = min(5000, limite - len(filas))
        params = {**base, "$select": select, "$limit": n, "$offset": len(filas)}
        if order:
            params["$order"] = order
        lote = api(ds_id, params)
        filas += lote
        if len(lote) < n:
            break
    total = len(filas)
    if total >= limite:
        try:
            total = int(list(api(ds_id, {**base, "$select": "count(*)"}, timeout=20)[0].values())[0])
        except Exception:
            pass  # no se pudo contar: se muestra al menos lo descargado
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
    wheres = construir_wheres(c, f)
    select = ",".join(dict.fromkeys(c.values()))
    order = f"{c['fecha']} DESC" if "fecha" in c else ""
    total, crudos = 0, []
    for w in wheres:
        t, crudo_i = traer_fn(ds["id"], select, w, order, f["limite"])
        total += t
        if not crudo_i.empty:
            crudos.append(crudo_i)
    crudo = pd.concat(crudos, ignore_index=True) if crudos else pd.DataFrame()
    df, ignorados = pd.DataFrame(), 0
    if not crudo.empty:
        df, ignorados = normalizar(crudo, c, f["tope"])
        if len(wheres) > 1:  # una fila puede coincidir en varias consultas
            df = df.drop_duplicates("clave")
            total = len(df)
        if f.get("excluir_local"):  # palabras a descartar DESPUÉS de bajar los datos (no alargan la consulta)
            rx = "|".join(_rx(t) for t in f["excluir_local"])
            df = df[~df["texto"].fillna("").str.upper().str.contains(rx, regex=True)]
        if f["solo_vigentes"] and "fecha_fin" in c:
            df = df[df["vigente"]]
        if f["valor_min"]:
            df = df[df["valor"].fillna(0) >= f["valor_min"]]
    return dict(where=wheres[0] if len(wheres) == 1 else f"-- {len(wheres)} consultas --\n" + "\n".join(wheres),
                select=select, order=order, total=total, n_crudo=len(crudo), df=df, ignorados=ignorados)


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
- "terminos": de 12 a 25 palabras clave o raíces CORTAS en MAYÚSCULAS, tal como suelen aparecer en objetos contractuales (sinónimos, cargos, actividades, entidades afines). Usa raíces para cubrir variantes (AGR_NOM cubre AGRÓNOMO, AGRONOMO y AGRONOMÍA). Reemplaza cada vocal con tilde y cada Ñ por "_" (AGR_COLA, DISE_O, INTERVENTOR_A). Evita palabras genéricas que traerían mucho ruido (APOYO, GESTION, SERVICIOS, PRESTACION).
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
