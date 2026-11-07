"""
Radar SECOP II — buscador de contratos y convocatorias (datos.gov.co / Socrata)

    pip install "streamlit>=1.35" pandas plotly requests
    streamlit run radar_secop.py

Opcional: token gratuito de Socrata para tener más cuota de consultas
    export SOCRATA_APP_TOKEN="tu_token"     (o ponlo en .streamlit/secrets.toml)
"""
import os
import re
from datetime import date, timedelta
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

BASE = "https://www.datos.gov.co"


def _token():
    t = os.environ.get("SOCRATA_APP_TOKEN")
    if not t:
        try:
            t = st.secrets.get("SOCRATA_APP_TOKEN")
        except Exception:
            t = None
    return t


TOKEN = _token()
HEADERS = {"X-App-Token": TOKEN} if TOKEN else {}

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
         "tipo", "valor", "fecha", "fecha_fin", "proveedor", "nit", "url"]
BUSQUEDA = {"descripcion": ("titulo", "descripcion"), "entidad": ("entidad",), "proveedor": ("proveedor",)}
ETIQUETA_BUSQUEDA = {"descripcion": "Objeto / descripción", "entidad": "Nombre de la entidad", "proveedor": "Nombre de la empresa"}

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
# API
# ---------------------------------------------------------------------------
def api(ds_id, params):
    r = requests.get(f"{BASE}/resource/{ds_id}.json", params=params, headers=HEADERS, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"datos.gov.co respondió {r.status_code}: {r.text[:300]}")
    return r.json()


@st.cache_data(ttl=86400, show_spinner=False)
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


@st.cache_data(ttl=86400, show_spinner=False)
def valores_distintos(ds_id, col, limite=300):
    try:
        filas = api(ds_id, {"$select": col, "$group": col, "$order": col, "$limit": limite})
        return [f[col] for f in filas if f.get(col)]
    except Exception:
        return []


def esc(s):
    return s.replace("'", "''")


def patron(termino):
    t = re.sub(r"[ÁÉÍÓÚÜÑ]", "_", termino.upper().strip())
    return f"%{esc(t)}%"


def contiene(col, texto):
    return f"upper({col}) like '{patron(texto)}'"


def construir_where(c, f):
    w, cols_busq = [], []
    for clave in f["buscar_en"]:
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
        cols = [c[r] for r in ("proveedor", "nit") if r in c]
        if cols:
            w.append("(" + " OR ".join(contiene(col, f["proveedor"]) for col in cols) + ")")
    if "fecha" in c:
        w.append(f"{c['fecha']} >= '{f['desde']}T00:00:00'")
        w.append(f"{c['fecha']} <= '{f['hasta']}T23:59:59'")
    return " AND ".join(w)


@st.cache_data(ttl=600, show_spinner=False)
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
    return out.drop_duplicates(), ignorados


def partir(txt):
    return [t.strip() for t in re.split(r"[,;\n]", txt or "") if t.strip()]


def fechas(rango):
    if isinstance(rango, (tuple, list)):
        return (rango[0], rango[-1])
    return (rango, rango)


# ---------------------------------------------------------------------------
# Interfaz
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Radar SECOP II", page_icon="📡", layout="wide")
st.title("📡 Radar SECOP II")
st.caption("Contratos y convocatorias de cualquier sector, directo desde datos.gov.co.")

fuente = st.sidebar.radio("¿Qué quieres buscar?", list(DATASETS))
ds = DATASETS[fuente]
es_proceso = "Convocatorias" in fuente
try:
    disponibles = columnas_disponibles(ds["id"])
except Exception as e:
    st.error(f"No pude leer el dataset `{ds['id']}`: {e}. Revisa el ID en datos.gov.co o tu conexión.")
    st.stop()
c = resolver_columnas(ds, disponibles)


def opciones(rol):
    return valores_distintos(ds["id"], c[rol]) if rol in c else []


hoy = date.today()
opt_estado = opciones("estado")
estado_defecto = [o for o in opt_estado if re.search(r"abiert|public|convoc", o, re.I)] if es_proceso else []

with st.sidebar.form("filtros"):
    st.markdown("**1. ¿Sobre qué?**")
    plantillas = st.multiselect("Plantillas por sector", list(PLANTILLAS), help="Cada plantilla es una lista de palabras clave. Puedes combinarlas.")
    extra = st.text_area("Palabras propias", placeholder="una por línea o separadas por coma")
    todos = st.checkbox("Exigir TODAS las palabras (si no, basta una)")
    excluir = st.text_input("Excluir palabras", placeholder="ej: vigilancia, aseo")
    buscar_en = st.multiselect("Buscar en", list(ETIQUETA_BUSQUEDA), default=["descripcion"], format_func=ETIQUETA_BUSQUEDA.get)
    st.markdown("**2. ¿Dónde y cuándo?**")
    deptos = st.multiselect("Departamentos", opciones("departamento"))
    ciudad = st.text_input("Ciudad o municipio contiene")
    rango = st.date_input("Rango de fechas", value=(hoy - timedelta(days=ds["dias"]), hoy))
    with st.expander("3. Más filtros"):
        estados = st.multiselect("Estado", opt_estado, default=estado_defecto)
        modalidades = st.multiselect("Modalidad", opciones("modalidad"))
        tipos = st.multiselect("Tipo de contrato", opciones("tipo"))
        entidad = st.text_input("Entidad contiene")
        proveedor = st.text_input("Empresa o NIT contiene")
        solo_vigentes = st.checkbox("Solo vigentes / con cierre futuro", value=es_proceso)
        valor_min = st.number_input("Valor mínimo (COP)", min_value=0, value=0, step=10_000_000)
        tope = st.number_input("Ignorar valores mayores a (COP)", min_value=0, value=10**12, step=10**9,
                               help="Algunos registros tienen valores absurdos por errores de digitación.")
        limite = st.select_slider("Máx. registros a descargar", [1000, 2000, 5000, 10000, 20000], value=2000)
    enviado = st.form_submit_button("🔎 Buscar", type="primary")

desde, hasta = fechas(rango)
filtros = dict(
    fuente=fuente, terminos=list(dict.fromkeys([k for p in plantillas for k in PLANTILLAS[p]] + partir(extra))),
    todos=todos, excluir=partir(excluir), buscar_en=buscar_en or ["descripcion"], deptos=deptos, ciudad=ciudad,
    desde=str(desde), hasta=str(hasta), estados=estados, modalidades=modalidades, tipos=tipos, entidad=entidad,
    proveedor=proveedor, solo_vigentes=solo_vigentes, valor_min=int(valor_min), tope=int(tope), limite=int(limite),
)
if enviado or st.session_state.get("f", {}).get("fuente") != fuente:
    st.session_state["f"] = filtros
f = st.session_state["f"]

where = construir_where(c, f)
select = ",".join(dict.fromkeys(c.values()))
order = f"{c['fecha']} DESC" if "fecha" in c else ""
try:
    with st.spinner("Consultando datos.gov.co..."):
        total, crudo = traer(ds["id"], select, where, order, f["limite"])
except Exception as e:
    st.error(f"La consulta falló: {e}")
    st.code(where, language="sql")
    st.stop()

tabs = st.tabs(["📋 Resultados", "🏢 Empresas y entidades", "📊 Análisis", "🔧 Diagnóstico"])
ignorados = 0
df = pd.DataFrame()
if not crudo.empty:
    df, ignorados = normalizar(crudo, c, f["tope"])
    if f["solo_vigentes"] and "fecha_fin" in c:
        df = df[df["vigente"]]
    if f["valor_min"]:
        df = df[df["valor"].fillna(0) >= f["valor_min"]]

with tabs[0]:
    if df.empty:
        st.info("Sin resultados con estos filtros. Prueba quitar palabras, ampliar fechas o desmarcar 'vigentes'.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Coincidencias en el portal", f"{total:,}")
        m2.metric("Mostrando", f"{len(df):,}")
        m3.metric("Valor total (COP)", f"${df['valor'].sum():,.0f}")
        m4.metric("Valor mediano (COP)", f"${df['valor'].median():,.0f}" if df["valor"].notna().any() else "—")
        if total > len(crudo):
            st.warning(f"Hay {total:,} coincidencias pero solo bajé las {len(crudo):,} más recientes. Afina filtros o sube el límite.")
        if ignorados:
            st.caption(f"{ignorados} valores superaban el tope y no cuentan en los totales.")
        vista = df[["proveedor", "entidad", "departamento", "ciudad", "valor", "estado", "fecha", "fecha_fin", "texto", "url"]].copy()
        vista.insert(0, "⭐", False)
        editado = st.data_editor(
            vista, hide_index=True, height=480, key="tabla",
            disabled=[x for x in vista.columns if x != "⭐"],
            column_config={
                "⭐": st.column_config.CheckboxColumn("⭐", width="small"),
                "proveedor": st.column_config.TextColumn("Empresa"),
                "entidad": st.column_config.TextColumn("Entidad"),
                "valor": st.column_config.NumberColumn("Valor (COP)", format="$%d"),
                "fecha": st.column_config.DateColumn("Fecha", format="YYYY-MM-DD"),
                "fecha_fin": st.column_config.DateColumn("Fin / cierre", format="YYYY-MM-DD"),
                "texto": st.column_config.TextColumn("Descripción", width="large"),
                "url": st.column_config.LinkColumn("SECOP", display_text="Abrir"),
            },
        )
        marcados = df.loc[editado.index[editado["⭐"]]]
        b1, b2 = st.columns(2)
        b1.download_button("⬇️ Descargar todo (CSV)", df.to_csv(index=False).encode("utf-8-sig"), "secop_resultados.csv", "text/csv")
        b2.download_button(f"⬇️ Descargar ⭐ marcados ({len(marcados)})", marcados.to_csv(index=False).encode("utf-8-sig"),
                           "secop_favoritos.csv", "text/csv", disabled=marcados.empty)

with tabs[1]:
    if df.empty:
        st.info("Haz una búsqueda con resultados para ver este resumen.")
    else:
        st.subheader("🏢 Empresas con contratos")
        d = df[df["proveedor"].fillna("") != ""]
        if d.empty:
            st.info("Las convocatorias abiertas aún no tienen adjudicatario. Mira las entidades más abajo, o busca en 'Contratos'.")
        else:
            orden = st.radio("Ordenar por", ["valor_total", "contratos", "vigentes"], horizontal=True,
                             format_func=lambda x: {"valor_total": "Valor total", "contratos": "N.º de contratos", "vigentes": "Contratos vigentes"}[x])
            emp = (d.groupby(["proveedor", "nit"], dropna=False)
                   .agg(contratos=("proveedor", "size"), valor_total=("valor", "sum"), entidades=("entidad", "nunique"),
                        departamentos=("departamento", "nunique"), vigentes=("vigente", "sum"), ultimo=("fecha", "max"))
                   .reset_index().sort_values(orden, ascending=False))
            emp["buscar"] = emp["proveedor"].map(lambda p: "https://www.google.com/search?q=" + quote(f'"{p}" Colombia contacto'))
            st.dataframe(emp, hide_index=True, height=400, column_config={
                "valor_total": st.column_config.NumberColumn("Valor total", format="$%d"),
                "ultimo": st.column_config.DateColumn("Último", format="YYYY-MM-DD"),
                "buscar": st.column_config.LinkColumn("Contacto", display_text="Buscar"),
            })
            st.caption("Contratos vigentes = con fecha de fin futura. Una empresa con varios vigentes en varias entidades suele tener equipo técnico estable.")
        st.subheader("🏛️ Entidades que más contratan")
        ent = (df.groupby(["entidad", "departamento"], dropna=False)
               .agg(registros=("entidad", "size"), valor_total=("valor", "sum"), empresas=("proveedor", "nunique"))
               .reset_index().sort_values("registros", ascending=False))
        st.dataframe(ent, hide_index=True, height=400,
                     column_config={"valor_total": st.column_config.NumberColumn("Valor total", format="$%d")})

with tabs[2]:
    if df.empty:
        st.info("Haz una búsqueda con resultados para ver los gráficos.")
    else:
        por_valor = st.radio("Medir por", ["N.º de registros", "Valor (COP)"], horizontal=True) == "Valor (COP)"

        def agrupar(serie_col):
            g = df.groupby(serie_col)
            return (g["valor"].sum() if por_valor else g.size()).rename("medida")

        g1, g2 = st.columns(2)
        for caja, col, titulo in ((g1, "departamento", "Por departamento"), (g2, "entidad", "Top entidades")):
            datos = agrupar(col).nlargest(15).sort_values().reset_index()
            if not datos.empty:
                caja.subheader(titulo)
                caja.plotly_chart(px.bar(datos, x="medida", y=col, orientation="h"))
        mensual = agrupar(df["fecha"].dt.to_period("M").dt.to_timestamp()).reset_index()
        if not mensual.empty:
            st.subheader("Evolución mensual")
            st.plotly_chart(px.line(mensual, x="fecha", y="medida", markers=True))
        if df["modalidad"].notna().any():
            st.subheader("Por modalidad de contratación")
            st.plotly_chart(px.bar(agrupar("modalidad").sort_values().reset_index(), x="medida", y="modalidad", orientation="h"))

with tabs[3]:
    st.write(f"Dataset `{ds['id']}` · {len(disponibles)} columnas · token: {'sí' if TOKEN else 'no (funciona, con menos cuota)'}")
    st.write("**Columnas detectadas** (revisa que cada rol apunte a lo correcto):")
    st.dataframe(pd.DataFrame({"rol": list(c), "columna": list(c.values())}), hide_index=True)
    st.write("**Filtro SoQL generado:**")
    st.code(where or "(sin filtros)", language="sql")
    params = {"$select": select, "$limit": 100}
    if where:
        params["$where"] = where
    if order:
        params["$order"] = order
    st.write("**La misma consulta como URL** (pégala en el navegador):")
    st.code(requests.Request("GET", f"{BASE}/resource/{ds['id']}.json", params=params).prepare().url)
