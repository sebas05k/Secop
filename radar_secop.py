"""
Radar SECOP II — buscador simple de contratos y convocatorias (datos.gov.co).

Dos pestañas:
  🔎 Buscar     Panel unificado de filtros y parámetros
  ⭐ Guardados  lo que marcas con ⭐ (en Google Sheets si lo configuraste) y tus alertas diarias
"""
import os
import re
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import secop_core as sc
from almacen import COLS_SEG, ESTADOS, Almacen

try:
    from streamlit_searchbox import st_searchbox
except ImportError:
    st_searchbox = None

st.set_page_config(page_title="Radar SECOP II", page_icon="📡", layout="wide")


def secreto(nombre, defecto=None):
    v = os.environ.get(nombre)
    if v:
        return v
    try:
        return st.secrets.get(nombre, defecto)
    except Exception:
        return defecto


sc.configurar_token(secreto("SOCRATA_APP_TOKEN"))

# ---- Contraseña opcional ----
_clave_app = secreto("APP_PASSWORD")
if _clave_app and not st.session_state.get("acceso"):
    st.title("📡 Radar SECOP II")
    _p = st.text_input("Contraseña", type="password")
    if _p and _p == _clave_app:
        st.session_state["acceso"] = True
        st.rerun()
    elif _p:
        st.error("Contraseña incorrecta.")
    st.stop()


# ---------------------------------------------------------------------------
# Wrappers con caché
# ---------------------------------------------------------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def columnas_disponibles(ds_id):
    return sc.columnas_disponibles(ds_id)

@st.cache_data(ttl=86400, show_spinner=False)
def valores_distintos(ds_id, col):
    return sc.valores_distintos(ds_id, col)

@st.cache_data(ttl=600, show_spinner=False)
def sugerir(ds_id, cols, texto):
    return sc.sugerir(ds_id, cols, texto)

@st.cache_data(ttl=600, show_spinner=False)
def traer(ds_id, select, where, order, limite):
    return sc.traer(ds_id, select, where, order, limite)

def opciones(rol, ds, c):
    if rol not in c:
        return []
    try:
        return valores_distintos(ds["id"], c[rol])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Google Sheets: perfil y seguimiento
# ---------------------------------------------------------------------------
@st.cache_resource(ttl=300, show_spinner=False)
def _conectar():
    sheet_id, cred = secreto("SHEET_ID"), secreto("gcp_service_account")
    if not sheet_id or not cred:
        return None, None
    try:
        return Almacen(dict(cred), sheet_id), None
    except Exception as e:
        return None, str(e)[:300]

ALM, ALM_ERR = _conectar()

def seguro(fn, defecto, mensaje):
    try:
        return fn()
    except Exception as e:
        st.warning(f"{mensaje}: {str(e)[:200]}")
        return defecto

if "perfil" not in st.session_state:
    st.session_state["perfil"] = seguro(ALM.leer_perfil, {}, "No pude leer tu perfil") if ALM else {}
if "seg" not in st.session_state:
    st.session_state["seg"] = seguro(ALM.leer_seguimiento, [], "No pude leer tu seguimiento") if ALM else []

def _s(v):
    return v if isinstance(v, str) else ""

def fila_seg(r):
    fin, val = r.get("fecha_fin"), r.get("valor")
    return {
        "clave": r["clave"], "estado": ESTADOS[0], "notas": "",
        "entidad": _s(r.get("entidad")), "proveedor": _s(r.get("proveedor")), "texto": _s(r.get("texto"))[:300],
        "valor": "" if pd.isna(val) else str(int(val)),
        "fecha_fin": "" if pd.isna(fin) else pd.Timestamp(fin).strftime("%Y-%m-%d"),
        "url": _s(r.get("url")), "guardado": date.today().isoformat(),
    }

def guardar_en_seguimiento(registros):
    seg = st.session_state["seg"]
    existentes = {x["clave"] for x in seg}
    nuevas = []
    for r in registros:
        if r["clave"] not in existentes:
            nuevas.append(fila_seg(r))
            existentes.add(r["clave"])
    if nuevas:
        seg.extend(nuevas)
        if ALM:
            seguro(lambda: ALM.agregar_seguimiento(nuevas), None, "No pude guardar en Google Sheets")
    return len(nuevas)

def persistir_perfil(nuevo):
    perfil = st.session_state["perfil"]
    if all(perfil.get(k) == v for k, v in nuevo.items()):
        return
    perfil.update(nuevo)
    if ALM:
        seguro(lambda: ALM.guardar_perfil(perfil), None, "No pude guardar tu perfil")

def aviso_almacenamiento():
    if ALM_ERR:
        st.warning(f"No pude conectar con Google Sheets ({ALM_ERR}). Por ahora tu perfil y seguimiento solo duran en esta sesión.")
    elif not ALM:
        st.caption("⚠️ Sin Google Sheets configurado: tu perfil y seguimiento se pierden al cerrar. Ver GUIA_CONFIGURACION.md.")


# ---------------------------------------------------------------------------
# Utilidades de presentación y UI
# ---------------------------------------------------------------------------
def md(texto):
    return re.sub(r"([\\$*_`#\[\]<>~|])", r"\\\1", texto or "")

def fmt_fecha(v):
    return "" if pd.isna(v) else v.strftime("%d/%m/%Y")

def tarjeta(r, claves_seg, es_proceso, clave_widget):
    guardado = r.clave in claves_seg
    with st.container(border=True):
        st.markdown(f"**{md((r.texto or r.entidad or 'Sin descripción')[:220])}**")
        lugar = r.ciudad if isinstance(r.ciudad, str) and r.ciudad else r.departamento
        datos = [x for x in (r.entidad, lugar) if isinstance(x, str) and x]
        if pd.notna(r.valor):
            datos.append(f"${r.valor:,.0f}")
        if es_proceso and pd.notna(r.fecha_fin):
            dias = (r.fecha_fin.normalize() - pd.Timestamp.today().normalize()).days
            datos.append(f"cierra {fmt_fecha(r.fecha_fin)}" + (f" (en {dias} d)" if dias >= 0 else ""))
        elif not es_proceso:
            if isinstance(r.proveedor, str) and r.proveedor:
                datos.append(f"a {r.proveedor}")
            if pd.notna(r.fecha):
                datos.append(f"firmado {fmt_fecha(r.fecha)}")
        st.caption(md(" · ".join(datos)))
        a, b = st.columns(2)
        if r.url:
            a.link_button("Abrir en SECOP", r.url, use_container_width=True)
        b.button("✅ Guardado" if guardado else "⭐ Guardar", key=clave_widget, disabled=guardado, use_container_width=True,
                 on_click=guardar_en_seguimiento, args=([r._asdict()],))

CONTRATOS, CONVOCATORIAS = "📜 Contratos (ya firmados)", "🟢 Convocatorias (procesos)"
NOMBRE_SECCION = {CONTRATOS: "📜 Contratos", CONVOCATORIAS: "🟢 Convocatorias"}
PREFIJO = {CONTRATOS: "con", CONVOCATORIAS: "pro"}
PLACEHOLDER = {
    "🏢 Empresa o persona": "Nombre, NIT o cédula…", 
    "🏛️ Entidad": "Ej: unidad para las víctimas",
    "📄 Contrato / proceso": "Número de contrato o proceso…"
}
VISTAS = ["🃏 Tarjetas", "📋 Tabla"]

def columnas_de(ds):
    try:
        return sc.resolver_columnas(ds, columnas_disponibles(ds["id"])), None
    except Exception as e:
        return None, str(e)[:200]

def resumen(df, modo):
    es_entidad = modo.startswith("🏛️")
    otra, plural = ("proveedor", "Empresas") if es_entidad else ("entidad", "Entidades")
    a, b = st.columns(2)
    a.metric("Contratos", f"{len(df):,}")
    b.metric("Valor total (COP)", f"${df['valor'].sum():,.0f}")
    a, b = st.columns(2)
    a.metric(plural, f"{df[otra].nunique():,}")
    b.metric("Vigentes", f"{int(df['vigente'].sum()):,}")
    tabla = (df.groupby(otra).agg(contratos=("clave", "size"), valor_total=("valor", "sum"), ultimo=("fecha", "max"))
             .reset_index().sort_values("valor_total", ascending=False).head(10))
    with st.expander("Entidades que le contratan" if not es_entidad else "Empresas a las que más adjudica"):
        st.dataframe(tabla, hide_index=True, column_config={
            "valor_total": st.column_config.NumberColumn("Valor total", format="$%d"),
            "ultimo": st.column_config.DateColumn("Último", format="YYYY-MM-DD")})

def tarjetas(df, es_proceso, pref):
    kn = f"{pref}_n"
    n = st.session_state.get(kn, 15)
    claves_seg = {x["clave"] for x in st.session_state["seg"]}
    for r in df.head(n).itertuples():
        tarjeta(r, claves_seg, es_proceso, f"{pref}_g_{r.clave}")
    if len(df) > n:
        st.button(f"Ver más ({len(df) - n} restantes)", key=f"{pref}_mas", use_container_width=True,
                  on_click=lambda: st.session_state.update({kn: n + 15}))

def tabla_resultados(df, pref):
    cols = [x for x in ["proveedor", "entidad", "contrato", "departamento", "valor", "estado", "fecha", "fecha_fin", "texto", "url"]
            if df[x].notna().any()]
    vista = df[cols].copy()
    vista.insert(0, "⭐", False)
    config = {
        "⭐": st.column_config.CheckboxColumn("⭐", width="small"),
        "proveedor": st.column_config.TextColumn("Empresa"),
        "entidad": st.column_config.TextColumn("Entidad"),
        "contrato": st.column_config.TextColumn("N.º contrato / proceso"),
        "valor": st.column_config.NumberColumn("Valor (COP)", format="$%d"),
        "fecha": st.column_config.DateColumn("Fecha", format="YYYY-MM-DD"),
        "fecha_fin": st.column_config.DateColumn("Fin / cierre", format="YYYY-MM-DD"),
        "texto": st.column_config.TextColumn("Descripción", width="large"),
        "url": st.column_config.LinkColumn("SECOP", display_text="Abrir"),
    }
    editado = st.data_editor(vista, hide_index=True, height=480, key=f"{pref}_tabla",
                             disabled=[x for x in vista.columns if x != "⭐"],
                             column_config={k: v for k, v in config.items() if k in vista.columns})
    marcados = df.loc[editado.index[editado["⭐"]]]
    if st.button(f"📌 Guardar ⭐ en seguimiento ({len(marcados)})", key=f"{pref}_guardar_tabla", disabled=marcados.empty):
        st.success(f"{guardar_en_seguimiento(r for _, r in marcados.iterrows())} guardado(s) en Guardados.")

def mostrar_resultados(res, es_proceso, pref, modo=None):
    df = res["df"].drop_duplicates("clave")
    if df.empty:
        st.info("Sin resultados. Prueba con menos palabras o quita filtros.")
        return
    if es_proceso:
        df = df.sort_values("fecha_fin")  
    elif modo and modo not in ["📄 Contrato / proceso", "🔍 Palabras clave / Objeto"]:
        resumen(df, modo)
    st.caption(f"{len(df):,} resultados · {res['total']:,} en el portal")
    kv = f"{pref}_vista"
    st.radio("Ver como", VISTAS, index=VISTAS.index(st.session_state.get("vista", VISTAS[0])), horizontal=True,
             key=kv, label_visibility="collapsed", on_change=lambda: st.session_state.update(vista=st.session_state[kv]))
    if st.session_state.get("vista") == VISTAS[1]:
        tabla_resultados(df, pref)
    else:
        tarjetas(df, es_proceso, pref)
    st.download_button("⬇️ Descargar (CSV)", df.to_csv(index=False).encode("utf-8-sig"),
                       f"secop_{pref}.csv", "text/csv", key=f"{pref}_csv")


# ---------------------------------------------------------------------------
# 🔎 Buscar: Interfaz Unificada
# ---------------------------------------------------------------------------
def pagina_buscar():
    st.markdown("### 🔎 Configura tu búsqueda")
    
    # --- SELECCIÓN DE DATASET ---
    tipo_ds = st.radio("¿Qué quieres buscar?", [CONTRATOS, CONVOCATORIAS], horizontal=True, label_visibility="collapsed")
    ds = sc.DATASETS[tipo_ds]
    es_proceso = (tipo_ds == CONVOCATORIAS)
    c, err = columnas_de(ds)
    
    if err:
        st.error(f"No pude leer el dataset: {err}")
        return

    # --- PANEL DE FILTROS ---
    with st.form("form_busqueda"):
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### 1. ¿Sobre qué?")
            modo = st.selectbox("Modo de búsqueda", list(sc.MODOS) + ["🔍 Palabras clave / Objeto"])
            
            if modo == "🔍 Palabras clave / Objeto":
                valor_busqueda = st.text_input("Palabras en el objeto del contrato", placeholder="Ej: café, tecnología, acueducto")
            else:
                valor_busqueda = st.text_input(PLACEHOLDER.get(modo, "Escribe aquí..."), placeholder=PLACEHOLDER.get(modo))
            
            sectores = st.multiselect("O filtra por Sector (Plantillas)", list(sc.PLANTILLAS))
            
        with col2:
            st.markdown("#### 2. ¿Cuándo y Dónde?")
            hoy = date.today()
            dias_defecto = 90 if es_proceso else 365
            rango_fechas = st.date_input("Rango de fechas", [hoy - timedelta(days=dias_defecto), hoy])
            
            opts_depto = opciones("departamento", ds, c)
            deptos = st.multiselect("Departamentos (vacío = Todos)", opts_depto)
        
        with st.expander("3. Más filtros (Exclusiones, montos, estados)"):
            c_exc, c_val = st.columns(2)
            excluir = c_exc.text_input("Excluir palabras", placeholder="Ej: vigilancia, papelería")
            valor_min = c_val.number_input("Valor mínimo (COP)", min_value=0, value=0, step=10_000_000)
            
            estados = []
            if es_proceso:
                opts_estado = opciones("estado", ds, c)
                estados = st.multiselect("Estados del proceso", opts_estado, default=sc.estados_abiertos(opts_estado))

        buscar_btn = st.form_submit_button("🚀 Buscar Oportunidades", type="primary", use_container_width=True)

    # --- EJECUCIÓN DE LA BÚSQUEDA ---
    if buscar_btn:
        if len(rango_fechas) != 2:
            st.warning("Selecciona una fecha de inicio y una de fin.")
            return
            
        desde, hasta = rango_fechas
        
        terminos = list(dict.fromkeys([k for s in sectores for k in sc.PLANTILLAS[s]]))
        if modo == "🔍 Palabras clave / Objeto" and valor_busqueda.strip():
            terminos.extend(sc.partir(valor_busqueda))
            
        filtros = sc.filtros_base(
            terminos=terminos,
            excluir=sc.partir(excluir),
            deptos=deptos,
            estados=estados,
            desde=str(desde),
            hasta=str(hasta),
            solo_vigentes=es_proceso,
            valor_min=int(valor_min),
            limite=1500
        )

        if valor_busqueda.strip() and modo != "🔍 Palabras clave / Objeto":
            if modo == "🏛️ Entidad":
                filtros["entidad"] = valor_busqueda.strip()
            elif modo == "🏢 Empresa o persona":
                filtros["proveedor"] = valor_busqueda.strip()
            elif modo == "📄 Contrato / proceso":
                filtros["buscar_en"] = ["contrato"]
                filtros["terminos"] = [valor_busqueda.strip()]

        try:
            with st.spinner(f"Buscando en {tipo_ds} (fechas: {desde} a {hasta})..."):
                res = sc.ejecutar(ds, c, filtros, traer)
            
            st.divider()
            mostrar_resultados(res, es_proceso, PREFIJO[tipo_ds], modo)
            
        except Exception as e:
            st.error(f"La consulta falló o tardó demasiado: {e}")


# ---------------------------------------------------------------------------
# ⭐ Guardados y 🔔 alertas
# ---------------------------------------------------------------------------
def ajustes_alertas():
    perfil = st.session_state["perfil"]
    ds = sc.DATASETS[CONTRATOS]
    c, _ = columnas_de(ds)
    opts_depto = opciones("departamento", ds, c) if c else []
    with st.expander("🔔 Alertas diarias (Telegram o correo)"):
        st.caption("Cada mañana te avisan las convocatorias nuevas que encajan con esto. Los avisos los envía alertas.py (GitHub Actions).")
        v = st.session_state.get("al_ver", 0)
        gem = secreto("GEMINI_API_KEY")
        if gem:
            desc = st.text_area("Cuéntame qué haces y la IA propone las palabras (opcional)", value=perfil.get("texto", ""),
                                height=90, key=f"al_desc_{v}")
            if st.button("✨ Proponer con IA", use_container_width=True):
                if len(desc.strip()) < 15:
                    st.warning("Cuéntame un poco más (profesión, experiencia, zona).")
                else:
                    try:
                        with st.spinner("Pensando…"):
                            r = sc.perfil_a_filtros(desc, gem, opts_depto, secreto("GEMINI_MODEL"))
                    except Exception as e:
                        st.error(str(e))
                    else:
                        persistir_perfil(dict(texto=desc.strip(), sectores=[], extra="\n".join(r["terminos"]), **r))
                        st.session_state["al_ver"] = v + 1
                        st.rerun()
        sectores = st.multiselect("Sectores", list(sc.PLANTILLAS), key=f"al_sec_{v}",
                                  default=[s for s in perfil.get("sectores", []) if s in sc.PLANTILLAS])
        extra = st.text_area("Palabras propias (una por línea)", height=110, key=f"al_ex_{v}",
                             value=perfil.get("extra", "\n".join(perfil.get("terminos", []))))
        excluir = st.text_input("Excluir palabras", value=", ".join(perfil.get("excluir", [])), key=f"al_exc_{v}")
        deptos = st.multiselect("Departamentos (vacío = todos)", opts_depto, key=f"al_dp_{v}",
                                default=[d for d in perfil.get("deptos", []) if d in opts_depto])
        if st.button("💾 Guardar alertas", type="primary", use_container_width=True):
            terminos = list(dict.fromkeys([k for s in sectores for k in sc.PLANTILLAS[s]] + sc.partir(extra)))
            if not terminos:
                st.warning("Elige un sector o escribe al menos una palabra.")
            else:
                persistir_perfil(dict(terminos=terminos, excluir=sc.partir(excluir), deptos=deptos,
                                      sectores=sectores, extra=extra.strip()))
                st.success("Alertas guardadas ✅" if ALM else "Guardado solo en esta sesión. Configura Google Sheets para que las alertas lo lean.")


def pagina_guardados():
    pagina_seguimiento()
    ajustes_alertas()


# ---------------------------------------------------------------------------
# ⭐ Seguimiento
# ---------------------------------------------------------------------------
def pagina_seguimiento():
    aviso_almacenamiento()
    seg = st.session_state["seg"]
    if not seg:
        st.info("Aún no guardas nada. Pulsa ⭐ en una oportunidad (pestaña «Buscar»).")
        return
    vista = pd.DataFrame(seg, columns=COLS_SEG)
    vista["quitar"] = False
    vista["valor_n"] = pd.to_numeric(vista["valor"], errors="coerce")
    hoy = pd.Timestamp.today().normalize()
    vista["dias"] = (pd.to_datetime(vista["fecha_fin"], errors="coerce") - hoy).dt.days

    conteo = vista["estado"].value_counts()
    st.caption("  ·  ".join(f"{e}: {int(conteo.get(e, 0))}" for e in ESTADOS))
    editado = st.data_editor(
        vista, hide_index=True, height=480, key="seg_editor",
        column_order=["quitar", "estado", "notas", "dias", "entidad", "texto", "valor_n", "fecha_fin", "url"],
        disabled=["dias", "entidad", "texto", "valor_n", "fecha_fin", "url"],
        column_config={
            "quitar": st.column_config.CheckboxColumn("🗑️", width="small", help="Marca y guarda para quitarlo"),
            "estado": st.column_config.SelectboxColumn("Estado", options=ESTADOS, required=True),
            "notas": st.column_config.TextColumn("Notas", width="medium"),
            "dias": st.column_config.NumberColumn("Cierra en (días)", format="%d"),
            "entidad": st.column_config.TextColumn("Entidad"),
            "texto": st.column_config.TextColumn("Descripción", width="large"),
            "valor_n": st.column_config.NumberColumn("Valor (COP)", format="$%d"),
            "fecha_fin": st.column_config.TextColumn("Cierre / fin"),
            "url": st.column_config.LinkColumn("SECOP", display_text="Abrir"),
        })
    a, b = st.columns(2)
    if a.button("💾 Guardar cambios", type="primary", use_container_width=True):
        quedan = editado[~editado["quitar"]]
        nuevo = [{k: ("" if pd.isna(fila[k]) else str(fila[k])) for k in COLS_SEG} for _, fila in quedan.iterrows()]
        st.session_state["seg"] = nuevo
        if ALM:
            seguro(lambda: ALM.guardar_seguimiento(nuevo), None, "No pude guardar en Google Sheets")
        st.rerun()
    b.download_button("⬇️ Descargar (CSV)", pd.DataFrame(seg, columns=COLS_SEG).to_csv(index=False).encode("utf-8-sig"),
                      "seguimiento_secop.csv", "text/csv", use_container_width=True)


# ---------------------------------------------------------------------------
# Navegación
# ---------------------------------------------------------------------------
st.title("📡 Radar SECOP II")
tab_buscar, tab_guardados = st.tabs(["🔎 Buscar", "⭐ Guardados"])
with tab_buscar:
    pagina_buscar()
with tab_guardados:
    pagina_guardados()
