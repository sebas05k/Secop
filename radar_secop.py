"""
Radar SECOP II — contratos y convocatorias (datos.gov.co) para buscar trabajo.

Secciones:
  🎯 Para mí             perfil → IA (Gemini) → palabras clave → tarjetas aptas para celular
  🔎 Búsqueda avanzada   filtros completos, búsqueda directa con autocompletado, análisis
  ⭐ Seguimiento         oportunidades guardadas con su estado (se guardan en Google Sheets)

Archivos: radar_secop.py, secop_core.py, almacen.py (+ alertas.py y alertas.yml para los avisos).
Secretos (Streamlit Cloud → Settings → Secrets): ver GUIA_CONFIGURACION.md
"""
import os
import re
from datetime import date, timedelta
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import requests
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

# ---- Contraseña opcional (recomendado: la URL de Streamlit Cloud es pública) --
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
# Wrappers con caché (el núcleo no depende de Streamlit)
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
# Google Sheets: perfil y seguimiento (si no está configurado, vive solo en la sesión)
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
    """Fila de seguimiento a partir de un registro (dict o Series) del DataFrame de resultados."""
    fin, val = r.get("fecha_fin"), r.get("valor")
    return {
        "clave": r["clave"], "estado": ESTADOS[0], "notas": "",
        "entidad": _s(r.get("entidad")), "proveedor": _s(r.get("proveedor")), "texto": _s(r.get("texto"))[:300],
        "valor": "" if pd.isna(val) else str(int(val)),
        "fecha_fin": "" if pd.isna(fin) else pd.Timestamp(fin).strftime("%Y-%m-%d"),
        "url": _s(r.get("url")), "guardado": date.today().isoformat(),
    }


def guardar_en_seguimiento(registros):
    """Agrega registros (iterable de dict/Series) sin duplicar. Devuelve cuántos eran nuevos."""
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
# Utilidades de presentación
# ---------------------------------------------------------------------------
def md(texto):
    """Escapa caracteres que Streamlit interpretaría como formato ($ activa fórmulas)."""
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
            a.link_button("Abrir en SECOP", r.url, width="stretch")
        b.button("✅ Guardado" if guardado else "⭐ Guardar", key=clave_widget, disabled=guardado, width="stretch",
                 on_click=guardar_en_seguimiento, args=([r._asdict()],))


# ---------------------------------------------------------------------------
# 🎯 Para mí
# ---------------------------------------------------------------------------
ETIQUETA_FUENTE = {"🟢 Convocatorias (procesos)": "🟢 Convocatorias abiertas", "📜 Contratos (ya firmados)": "📜 Quién contrató (último año)"}


def pagina_para_mi():
    aviso_almacenamiento()
    perfil = st.session_state["perfil"]
    fuente = st.radio("¿Qué buscas?", list(ETIQUETA_FUENTE), format_func=ETIQUETA_FUENTE.get, horizontal=True, key="pm_fuente")
    ds = sc.DATASETS[fuente]
    es_proceso = "Convocatorias" in fuente
    try:
        c = sc.resolver_columnas(ds, columnas_disponibles(ds["id"]))
    except Exception as e:
        st.error(f"No pude leer el dataset `{ds['id']}`: {e}")
        return
    opts_depto = opciones("departamento", ds, c)

    st.markdown("#### Tu perfil")
    texto = st.text_area(
        "Cuéntame qué haces", value=perfil.get("texto", ""), height=130, key="pm_texto", label_visibility="collapsed",
        placeholder="Ej: Ingeniero agrónomo con 5 años en café y aguacate, asistencia técnica y extensión rural. "
                    "Busco contratos en Boyacá y Cundinamarca.")
    gem = secreto("GEMINI_API_KEY")
    if st.button("✨ Interpretar con IA", disabled=not gem, width="stretch"):
        if len(texto.strip()) < 15:
            st.warning("Cuéntame un poco más de tu perfil (profesión, experiencia, zona).")
        else:
            try:
                with st.spinner("Pensando…"):
                    r = sc.perfil_a_filtros(texto, gem, opts_depto, secreto("GEMINI_MODEL"))
            except Exception as e:
                st.error(str(e))
            else:
                persistir_perfil(dict(texto=texto.strip(), **r))
                st.session_state["pm_ver"] = st.session_state.get("pm_ver", 0) + 1
                st.rerun()
    if not gem:
        st.caption("Sin GEMINI_API_KEY: escribe las palabras clave a mano (o agrega la clave en Secrets).")
    if perfil.get("explicacion"):
        st.caption("🧠 " + perfil["explicacion"])

    v = f"{st.session_state.get('pm_ver', 0)}_{ds['id']}"
    kw = st.text_area("Palabras clave (una por línea; _ = cualquier letra, cubre las tildes)",
                      value="\n".join(perfil.get("terminos", [])), height=150, key=f"pm_kw_{v}")
    exc = st.text_input("Excluir palabras", value=", ".join(perfil.get("excluir", [])), key=f"pm_ex_{v}")
    deptos = st.multiselect("Departamentos (vacío = todos)", opts_depto,
                            default=[d for d in perfil.get("deptos", []) if d in opts_depto], key=f"pm_dp_{v}")
    ciudad = st.text_input("Ciudad o municipio (opcional)", value=perfil.get("ciudad", ""), key=f"pm_ci_{v}")

    b1, b2 = st.columns(2)
    guardar = b1.button("💾 Guardar perfil", width="stretch")
    buscar = b2.button("🔎 Buscar", type="primary", width="stretch")
    actuales = dict(texto=texto.strip(), terminos=sc.partir(kw), excluir=sc.partir(exc), deptos=deptos, ciudad=ciudad.strip())
    if guardar:
        persistir_perfil(actuales)
        st.success("Perfil guardado ✅" if ALM else "Perfil actualizado (solo en esta sesión).")
    if buscar:
        persistir_perfil(actuales)
        hoy = date.today()
        estados = sc.estados_abiertos(opciones("estado", ds, c)) if es_proceso else []
        st.session_state["pm_f"] = dict(ds=ds["id"], f=sc.filtros_base(
            terminos=actuales["terminos"], excluir=actuales["excluir"], deptos=deptos, ciudad=actuales["ciudad"],
            desde=str(hoy - timedelta(days=ds["dias"])), hasta=str(hoy), estados=estados,
            solo_vigentes=es_proceso, limite=1000))
        st.session_state["pm_n"] = 15

    guardada = st.session_state.get("pm_f")
    if not guardada or guardada["ds"] != ds["id"]:
        st.info("Ajusta tu perfil y pulsa **Buscar**.")
        return
    if not guardada["f"]["terminos"]:
        st.warning("Faltan palabras clave: usa «Interpretar con IA» o escríbelas.")
        return

    try:
        with st.spinner("Buscando en datos.gov.co…"):
            res = sc.ejecutar(ds, c, guardada["f"], traer)
    except Exception as e:
        st.error(f"La consulta falló: {e}")
        return
    df = res["df"].drop_duplicates("clave")
    if df.empty:
        st.info("Sin resultados. Prueba quitar exclusiones, ampliar departamentos o usar menos palabras.")
        return
    if es_proceso:
        df = df.sort_values("fecha_fin")

    if not es_proceso:
        st.markdown("##### 🏛️ Entidades que más contratan perfiles como el tuyo")
        ent = (df.groupby(["entidad", "departamento"], dropna=False).agg(contratos=("clave", "size"), valor=("valor", "sum"))
               .reset_index().sort_values("contratos", ascending=False).head(10))
        st.dataframe(ent, hide_index=True, column_config={"valor": st.column_config.NumberColumn("Valor total", format="$%d")})
        st.caption("Si una entidad firmó varios contratos así el año pasado, probablemente vuelva a hacerlo: buena candidata para enviar tu hoja de vida.")

    n = st.session_state.get("pm_n", 15)
    st.markdown(f"##### Resultados ({len(df):,} · {res['total']:,} en el portal)")
    claves_seg = {x["clave"] for x in st.session_state["seg"]}
    for r in df.head(n).itertuples():
        tarjeta(r, claves_seg, es_proceso, f"pm_g_{r.clave}")
    if len(df) > n:
        st.button(f"Ver más ({len(df) - n} restantes)", width="stretch",
                  on_click=lambda: st.session_state.update(pm_n=n + 15))


# ---------------------------------------------------------------------------
# ⭐ Seguimiento
# ---------------------------------------------------------------------------
def pagina_seguimiento():
    aviso_almacenamiento()
    seg = st.session_state["seg"]
    if not seg:
        st.info("Aún no guardas nada. Pulsa ⭐ en una oportunidad (sección «Para mí» o «Búsqueda avanzada»).")
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
    if a.button("💾 Guardar cambios", type="primary", width="stretch"):
        quedan = editado[~editado["quitar"]]
        nuevo = [{k: ("" if pd.isna(fila[k]) else str(fila[k])) for k in COLS_SEG} for _, fila in quedan.iterrows()]
        st.session_state["seg"] = nuevo
        if ALM:
            seguro(lambda: ALM.guardar_seguimiento(nuevo), None, "No pude guardar en Google Sheets")
        st.rerun()
    b.download_button("⬇️ Descargar (CSV)", pd.DataFrame(seg, columns=COLS_SEG).to_csv(index=False).encode("utf-8-sig"),
                      "seguimiento_secop.csv", "text/csv", width="stretch")


# ---------------------------------------------------------------------------
# 🔎 Búsqueda avanzada (la app original, con la búsqueda directa y el botón de seguimiento)
# ---------------------------------------------------------------------------
ETIQUETA_BUSQUEDA = {"descripcion": "Objeto / descripción", "entidad": "Nombre de la entidad", "proveedor": "Nombre de la empresa"}


def pagina_avanzada():
    fuente = st.sidebar.radio("¿Qué quieres buscar?", list(sc.DATASETS), key="av_fuente")
    ds = sc.DATASETS[fuente]
    es_proceso = "Convocatorias" in fuente
    try:
        disponibles = columnas_disponibles(ds["id"])
    except Exception as e:
        st.error(f"No pude leer el dataset `{ds['id']}`: {e}. Revisa el ID en datos.gov.co o tu conexión.")
        st.stop()
    c = sc.resolver_columnas(ds, disponibles)

    # ---- Búsqueda directa con autocompletado ----
    st.markdown("### 🔎 Búsqueda directa")
    modo = st.radio("Buscar por", list(sc.MODOS), horizontal=True, label_visibility="collapsed")
    cols_sug = tuple(c[r] for r in sc.MODOS[modo] if r in c)
    rapida = None
    if not cols_sug:
        st.caption("Este dataset no tiene columnas para esta búsqueda (revisa la pestaña Diagnóstico).")
    else:
        def buscador(texto):
            return sugerir(ds["id"], cols_sug, texto) if len((texto or "").strip()) >= 3 else []

        if st_searchbox:
            valor = st_searchbox(buscador, placeholder="Escribe 3+ letras: nombre, NIT, número de contrato…",
                                 key=f"sb_{modo}_{fuente}")
        else:  # respaldo sin el paquete: escribe + Enter, luego elige
            txt = st.text_input("Escribe 3+ letras y presiona Enter", key=f"txt_{modo}_{fuente}")
            sug = buscador(txt)
            valor = st.selectbox("Sugerencias", sug, index=None, key=f"sel_{modo}_{fuente}") if sug else None
            valor = valor or (txt.strip() if len(txt.strip()) >= 3 else None)
        if valor:
            rapida = (modo, valor)
            st.info(f"Búsqueda directa: **{valor}**. Se ignoran fechas y filtros laterales; borra la búsqueda para volver a ellos.")

    hoy = date.today()
    opt_estado = opciones("estado", ds, c)
    estado_defecto = sc.estados_abiertos(opt_estado) if es_proceso else []

    with st.sidebar.form("filtros"):
        st.markdown("**1. ¿Sobre qué?**")
        plantillas = st.multiselect("Plantillas por sector", list(sc.PLANTILLAS), help="Cada plantilla es una lista de palabras clave. Puedes combinarlas.")
        extra = st.text_area("Palabras propias", placeholder="una por línea o separadas por coma")
        todos = st.checkbox("Exigir TODAS las palabras (si no, basta una)")
        excluir = st.text_input("Excluir palabras", placeholder="ej: vigilancia, aseo")
        buscar_en = st.multiselect("Buscar en", list(ETIQUETA_BUSQUEDA), default=["descripcion"], format_func=ETIQUETA_BUSQUEDA.get)
        st.markdown("**2. ¿Dónde y cuándo?**")
        deptos = st.multiselect("Departamentos", opciones("departamento", ds, c))
        ciudad = st.text_input("Ciudad o municipio contiene")
        rango = st.date_input("Rango de fechas", value=(hoy - timedelta(days=ds["dias"]), hoy))
        with st.expander("3. Más filtros"):
            estados = st.multiselect("Estado", opt_estado, default=estado_defecto)
            modalidades = st.multiselect("Modalidad", opciones("modalidad", ds, c))
            tipos = st.multiselect("Tipo de contrato", opciones("tipo", ds, c))
            entidad = st.text_input("Entidad contiene")
            proveedor = st.text_input("Empresa o NIT contiene")
            solo_vigentes = st.checkbox("Solo vigentes / con cierre futuro", value=es_proceso)
            valor_min = st.number_input("Valor mínimo (COP)", min_value=0, value=0, step=10_000_000)
            tope = st.number_input("Ignorar valores mayores a (COP)", min_value=0, value=10**12, step=10**9,
                                   help="Algunos registros tienen valores absurdos por errores de digitación.")
            limite = st.select_slider("Máx. registros a descargar", [1000, 2000, 5000, 10000, 20000], value=2000)
        enviado = st.form_submit_button("🔎 Buscar", type="primary")

    desde, hasta = sc.fechas(rango)
    filtros = sc.filtros_base(
        rapida=rapida, terminos=list(dict.fromkeys([k for p in plantillas for k in sc.PLANTILLAS[p]] + sc.partir(extra))),
        todos=todos, excluir=sc.partir(excluir), buscar_en=buscar_en or ["descripcion"], deptos=deptos, ciudad=ciudad,
        desde=str(desde), hasta=str(hasta), estados=estados, modalidades=modalidades, tipos=tipos, entidad=entidad,
        proveedor=proveedor, solo_vigentes=solo_vigentes, valor_min=int(valor_min), tope=int(tope), limite=int(limite))
    filtros["fuente"] = fuente
    previo = st.session_state.get("av_f", {})
    if enviado or previo.get("fuente") != fuente or previo.get("rapida") != rapida:
        st.session_state["av_f"] = filtros
    f = st.session_state["av_f"]

    where = sc.construir_where(c, f)
    try:
        with st.spinner("Consultando datos.gov.co..."):
            res = sc.ejecutar(ds, c, f, traer)
    except Exception as e:
        st.error(f"La consulta falló: {e}")
        st.code(where, language="sql")
        st.stop()
    df, total, ignorados = res["df"], res["total"], res["ignorados"]
    select, order = res["select"], res["order"]

    tabs = st.tabs(["📋 Resultados", "🏢 Empresas y entidades", "📊 Análisis", "🔧 Diagnóstico"])

    with tabs[0]:
        if df.empty:
            st.info("Sin resultados con estos filtros. Prueba quitar palabras, ampliar fechas o desmarcar 'vigentes'.")
        else:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Coincidencias en el portal", f"{total:,}")
            m2.metric("Mostrando", f"{len(df):,}")
            m3.metric("Valor total (COP)", f"${df['valor'].sum():,.0f}")
            m4.metric("Valor mediano (COP)", f"${df['valor'].median():,.0f}" if df["valor"].notna().any() else "—")
            if total > res["n_crudo"]:
                st.warning(f"Hay {total:,} coincidencias pero solo bajé las {res['n_crudo']:,} más recientes. Afina filtros o sube el límite.")
            if ignorados:
                st.caption(f"{ignorados} valores superaban el tope y no cuentan en los totales.")
            vista = df[["proveedor", "entidad", "contrato", "departamento", "ciudad", "valor", "estado", "fecha", "fecha_fin", "texto", "url"]].copy()
            vista.insert(0, "⭐", False)
            editado = st.data_editor(
                vista, hide_index=True, height=480, key="tabla",
                disabled=[x for x in vista.columns if x != "⭐"],
                column_config={
                    "⭐": st.column_config.CheckboxColumn("⭐", width="small"),
                    "proveedor": st.column_config.TextColumn("Empresa"),
                    "entidad": st.column_config.TextColumn("Entidad"),
                    "contrato": st.column_config.TextColumn("N.º contrato / proceso"),
                    "valor": st.column_config.NumberColumn("Valor (COP)", format="$%d"),
                    "fecha": st.column_config.DateColumn("Fecha", format="YYYY-MM-DD"),
                    "fecha_fin": st.column_config.DateColumn("Fin / cierre", format="YYYY-MM-DD"),
                    "texto": st.column_config.TextColumn("Descripción", width="large"),
                    "url": st.column_config.LinkColumn("SECOP", display_text="Abrir"),
                },
            )
            marcados = df.loc[editado.index[editado["⭐"]]]
            b1, b2, b3 = st.columns(3)
            b1.download_button("⬇️ Descargar todo (CSV)", df.to_csv(index=False).encode("utf-8-sig"), "secop_resultados.csv", "text/csv")
            b2.download_button(f"⬇️ Descargar ⭐ ({len(marcados)})", marcados.to_csv(index=False).encode("utf-8-sig"),
                               "secop_favoritos.csv", "text/csv", disabled=marcados.empty)
            if b3.button(f"📌 Guardar ⭐ en seguimiento ({len(marcados)})", disabled=marcados.empty):
                nuevos = guardar_en_seguimiento(r for _, r in marcados.iterrows())
                st.success(f"{nuevos} guardado(s) en Seguimiento.")

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
        st.write(f"Dataset `{ds['id']}` · {len(disponibles)} columnas · token Socrata: {'sí' if sc.HEADERS else 'no (funciona, con menos cuota)'}")
        st.write(f"Google Sheets: {'conectado' if ALM else 'no configurado'} · Gemini: {'clave encontrada' if secreto('GEMINI_API_KEY') else 'sin clave'}")
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
        st.code(requests.Request("GET", f"{sc.BASE}/resource/{ds['id']}.json", params=params).prepare().url)


# ---------------------------------------------------------------------------
# Navegación
# ---------------------------------------------------------------------------
st.title("📡 Radar SECOP II")
seccion = st.radio("Sección", ["🎯 Para mí", "🔎 Búsqueda avanzada", "⭐ Seguimiento"], horizontal=True, label_visibility="collapsed")
if seccion.startswith("🎯"):
    pagina_para_mi()
elif seccion.startswith("🔎"):
    pagina_avanzada()
else:
    pagina_seguimiento()
