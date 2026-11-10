"""
Radar SECOP II — buscador simple de contratos y convocatorias (datos.gov.co).

Dos pestañas:
  🔎 Buscar     una barra con autocompletado (empresa o persona, entidad, contrato)
                y, debajo, oportunidades por sector
  ⭐ Guardados  lo que marcas con ⭐ (en Google Sheets si lo configuraste) y tus alertas diarias

Archivos: radar_secop.py, secop_core.py, almacen.py (+ alertas.py y alertas.yml para los avisos).
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
# 🔎 Buscar: una barra con autocompletado + oportunidades por sector
# ---------------------------------------------------------------------------
CONTRATOS, CONVOCATORIAS = "📜 Contratos (ya firmados)", "🟢 Convocatorias (procesos)"
NOMBRE_SECCION = {CONTRATOS: "📜 Contratos", CONVOCATORIAS: "🟢 Convocatorias"}
PREFIJO = {CONTRATOS: "con", CONVOCATORIAS: "pro"}
PLACEHOLDER = {"🏢 Empresa o persona": "Nombre, NIT o cédula…", "🏛️ Entidad": "Ej: unidad para las víctimas",
               "📄 Contrato / proceso": "Número de contrato o proceso…"}


def columnas_de(ds):
    try:
        return sc.resolver_columnas(ds, columnas_disponibles(ds["id"])), None
    except Exception as e:
        return None, str(e)[:200]


def resumen(df, modo):
    """Cifras clave de una empresa/persona o de una entidad (solo para contratos firmados)."""
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


def mostrar_resultados(res, es_proceso, pref, modo=None):
    df = res["df"].drop_duplicates("clave")
    if df.empty:
        st.info("Sin resultados. Prueba con menos palabras o quita filtros.")
        return
    if es_proceso:
        df = df.sort_values("fecha_fin")  # primero los que cierran pronto
    elif modo and not modo.startswith("📄"):
        resumen(df, modo)
    kn = f"{pref}_n"
    n = st.session_state.get(kn, 15)
    st.caption(f"{len(df):,} resultados · {res['total']:,} en el portal")
    claves_seg = {x["clave"] for x in st.session_state["seg"]}
    for r in df.head(n).itertuples():
        tarjeta(r, claves_seg, es_proceso, f"{pref}_g_{r.clave}")
    if len(df) > n:
        st.button(f"Ver más ({len(df) - n} restantes)", key=f"{pref}_mas", width="stretch",
                  on_click=lambda: st.session_state.update({kn: n + 15}))
    st.download_button("⬇️ Descargar (CSV)", df.to_csv(index=False).encode("utf-8-sig"),
                       f"secop_{pref}.csv", "text/csv", key=f"{pref}_csv")


def busqueda_directa(modo, valor):
    """Busca el valor elegido en contratos y en convocatorias, y muestra cada uno en su pestaña."""
    if st.session_state.get("_ultima") != (modo, valor):
        st.session_state.update(_ultima=(modo, valor), con_n=15, pro_n=15)
    st.divider()
    st.markdown(f"#### Resultados para **{md(valor)}**")
    salidas = {}
    for nombre, ds in sc.DATASETS.items():
        c, err = columnas_de(ds)
        if err:
            salidas[nombre] = ("error", err)
        elif not any(r in c for r in sc.MODOS[modo]):
            salidas[nombre] = ("vacio", None)
        else:
            try:
                with st.spinner(f"Buscando en {NOMBRE_SECCION[nombre]}…"):
                    salidas[nombre] = ("ok", sc.ejecutar(ds, c, sc.filtros_base(rapida=(modo, valor), limite=1000), traer))
            except Exception as e:
                salidas[nombre] = ("error", str(e)[:200])
    etiquetas = [NOMBRE_SECCION[n] + (f" ({d['total']:,})" if e == "ok" else "") for n, (e, d) in salidas.items()]
    for tab, (nombre, (estado, dato)) in zip(st.tabs(etiquetas), salidas.items()):
        with tab:
            if estado == "ok":
                mostrar_resultados(dato, nombre == CONVOCATORIAS, PREFIJO[nombre], modo)
            elif estado == "error":
                st.warning(f"No pude consultar {NOMBRE_SECCION[nombre]}: {dato}")
            else:
                st.caption("Este conjunto de datos no tiene esa información.")


def explorar():
    perfil = st.session_state["perfil"]
    st.markdown("#### O explora oportunidades por sector")
    opciones_ver = {"🟢 Convocatorias abiertas": CONVOCATORIAS, "📜 Contratos del último año": CONTRATOS}
    ver = st.radio("Quiero ver", list(opciones_ver), horizontal=True, key="ex_ver")
    nombre = opciones_ver[ver]
    ds = sc.DATASETS[nombre]
    es_proceso = nombre == CONVOCATORIAS
    c, err = columnas_de(ds)
    if err:
        st.error(f"No pude leer el dataset `{ds['id']}`: {err}")
        return
    opts_depto = opciones("departamento", ds, c)
    with st.form("explorar"):
        sectores = st.multiselect("Sector", list(sc.PLANTILLAS),
                                  default=[s for s in perfil.get("sectores", []) if s in sc.PLANTILLAS])
        extra = st.text_input("Otras palabras (opcional)", placeholder="ej: café, acuicultura")
        deptos = st.multiselect("Departamento (opcional)", opts_depto)
        with st.expander("Más filtros"):
            excluir = st.text_input("Excluir palabras", placeholder="ej: vigilancia, aseo")
            valor_min = st.number_input("Valor mínimo (COP)", min_value=0, value=0, step=10_000_000)
        ok = st.form_submit_button("Ver oportunidades", type="primary", width="stretch")
    if ok:
        terminos = list(dict.fromkeys([k for s in sectores for k in sc.PLANTILLAS[s]] + sc.partir(extra)))
        if not terminos:
            st.session_state.pop("ex_f", None)
            st.warning("Elige un sector o escribe una palabra.")
            return
        hoy = date.today()
        estados = sc.estados_abiertos(opciones("estado", ds, c)) if es_proceso else []
        st.session_state["ex_f"] = dict(nombre=nombre, f=sc.filtros_base(
            terminos=terminos, excluir=sc.partir(excluir), deptos=deptos, estados=estados,
            desde=str(hoy - timedelta(days=ds["dias"])), hasta=str(hoy), solo_vigentes=es_proceso,
            valor_min=int(valor_min), limite=1000))
        st.session_state["ex_n"] = 15
    guardada = st.session_state.get("ex_f")
    if not guardada or guardada["nombre"] != nombre:
        st.caption("Elige un sector y pulsa «Ver oportunidades».")
        return
    try:
        with st.spinner("Buscando en datos.gov.co…"):
            res = sc.ejecutar(ds, c, guardada["f"], traer)
    except Exception as e:
        st.error(f"La consulta falló: {e}")
        return
    mostrar_resultados(res, es_proceso, "ex")


def pagina_buscar():
    ds = sc.DATASETS[CONTRATOS]
    c, err = columnas_de(ds)
    if err:
        st.error(f"No pude leer el dataset de contratos: {err}")
        return
    modo = st.radio("Buscar por", list(sc.MODOS), horizontal=True, label_visibility="collapsed", key="b_modo")

    def buscador(texto):
        t = (texto or "").strip()
        if len(t) < 3:
            return []
        roles = sc.MODOS[modo]
        if re.fullmatch(r"[\d.\-\s]+", t):          # solo números: NIT, cédula o nº de contrato
            roles = [r for r in roles if r in ("nit", "contrato", "proceso")] or roles
        else:                                      # con letras no tiene sentido mirar el NIT
            roles = [r for r in roles if r != "nit"]
        cols = tuple(c[r] for r in roles if r in c)
        return sugerir(ds["id"], cols, t) if cols else []

    placeholder = PLACEHOLDER.get(modo, "Escribe 3+ letras…")
    if st_searchbox:
        valor = st_searchbox(buscador, placeholder=placeholder, key=f"sb_{modo}")
    else:  # respaldo sin el paquete: escribe + Enter, luego elige
        txt = st.text_input(placeholder, key=f"txt_{modo}", label_visibility="collapsed", placeholder=placeholder)
        sug = buscador(txt)
        valor = st.selectbox("Sugerencias", sug, index=None, key=f"sel_{modo}_{txt}") if sug else None
        valor = valor or (txt.strip() if len(txt.strip()) >= 3 else None)
    if valor:
        busqueda_directa(modo, valor)
    else:
        explorar()


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
            if st.button("✨ Proponer con IA", width="stretch"):
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
        if st.button("💾 Guardar alertas", type="primary", width="stretch"):
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
# Navegación
# ---------------------------------------------------------------------------
st.title("📡 Radar SECOP II")
tab_buscar, tab_guardados = st.tabs(["🔎 Buscar", "⭐ Guardados"])
with tab_buscar:
    pagina_buscar()
with tab_guardados:
    pagina_guardados()
