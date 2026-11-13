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


VISTAS = ["🃏 Tarjetas", "📋 Tabla"]


def tarjetas(df, es_proceso, pref):
    kn = f"{pref}_n"
    n = st.session_state.get(kn, 15)
    claves_seg = {x["clave"] for x in st.session_state["seg"]}
    for r in df.head(n).itertuples():
        tarjeta(r, claves_seg, es_proceso, f"{pref}_g_{r.clave}")
    if len(df) > n:
        st.button(f"Ver más ({len(df) - n} restantes)", key=f"{pref}_mas", width="stretch",
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
        df = df.sort_values("fecha_fin")  # primero los que cierran pronto
    elif modo and not modo.startswith("📄"):
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


PERIODOS = {"Último mes": 30, "Últimos 3 meses": 90, "Último año": 365, "Últimos 3 años": 1095, "Todo el historial": None}


def opciones_union(rol):
    """Valores posibles de un campo en contratos y convocatorias, sin repetir."""
    vals = set()
    for ds in sc.DATASETS.values():
        c, _ = columnas_de(ds)
        if c:
            vals |= set(opciones(rol, ds, c))
    return sorted(vals)


def resultados(b):
    """Corre la búsqueda guardada en `b` en contratos y convocatorias, y muestra cada una en su pestaña.
    Las consultas quedan en caché, así que mostrar de nuevo (⭐, Ver más, cambiar vista) no vuelve a consultar."""
    if st.session_state.get("_ultima") != b["id"]:
        st.session_state.update(_ultima=b["id"], con_n=15, pro_n=15)
    st.caption("Buscando: " + b["resumen"])
    hoy = date.today()
    dias = PERIODOS[b["periodo"]]
    desde = str(hoy - timedelta(days=dias)) if dias else "2000-01-01"
    salidas, notas = {}, {}
    for nombre, ds in sc.DATASETS.items():
        c, err = columnas_de(ds)
        es_proceso = nombre == CONVOCATORIAS
        if err:
            salidas[nombre] = ("error", err)
        elif b["valor"] and not any(r in c for r in sc.MODOS[b["modo"]]):
            salidas[nombre] = ("vacio", None)
        else:
            try:
                validos = [d for d in b["deptos"] if d in opciones("departamento", ds, c)]
                if b["deptos"] and not validos:
                    notas[nombre] = "El departamento elegido no figura en este conjunto de datos, así que se muestra todo el país."
                estados = sc.estados_abiertos(opciones("estado", ds, c)) if es_proceso and b["solo_abiertas"] and "estado" in c else []
                f = sc.filtros_base(
                    rapida=(b["modo"], b["valor"]) if b["valor"] else None, terminos=b["terminos"], excluir=b["excluir"],
                    deptos=validos, ciudad=b["ciudad"],
                    entidad=b["entidad"], proveedor=b["proveedor"], modalidades=b["modalidades"], tipos=b["tipos"],
                    estados=estados, desde=desde, hasta=str(hoy), solo_vigentes=es_proceso and b["solo_abiertas"],
                    valor_min=b["valor_min"], limite=1000)
                with st.spinner(f"Buscando en {NOMBRE_SECCION[nombre]}…"):
                    salidas[nombre] = ("ok", sc.ejecutar(ds, c, f, traer))
            except Exception as e:
                salidas[nombre] = ("error", str(e)[:250])
    etiquetas = [NOMBRE_SECCION[n] + (f" ({d['total']:,})" if e == "ok" else "") for n, (e, d) in salidas.items()]
    for tab, (nombre, (estado, dato)) in zip(st.tabs(etiquetas), salidas.items()):
        with tab:
            if notas.get(nombre):
                st.caption("⚠️ " + notas[nombre])
            if estado == "ok":
                mostrar_resultados(dato, nombre == CONVOCATORIAS, PREFIJO[nombre], b["modo"] if b["valor"] else None)
            elif estado == "error":
                st.warning(f"No pude consultar {NOMBRE_SECCION[nombre]}: {dato}")
                st.button("🔄 Reintentar", key=f"reintentar_{PREFIJO[nombre]}")
            else:
                st.caption("Este conjunto de datos no tiene esa información. Prueba con otro tipo de búsqueda.")


def pagina_buscar():
    ds = sc.DATASETS[CONTRATOS]
    c, err = columnas_de(ds)
    if err:
        st.error(f"No pude leer el dataset de contratos: {err}")
        return

    # 1) Barra con autocompletado (opcional): un nombre concreto
    st.markdown("##### 1 · ¿Buscas a alguien o algo en concreto? (opcional)")
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
        try:
            return sugerir(ds["id"], cols, t) if cols else []
        except Exception:   # si datos.gov.co tarda, no se rompe la página: simplemente no hay sugerencias
            return []

    placeholder = PLACEHOLDER.get(modo, "Escribe 3+ letras…")
    if st_searchbox:
        valor = st_searchbox(buscador, placeholder=placeholder, key=f"sb_{modo}")
    else:  # respaldo sin el paquete: escribe + Enter, luego elige
        txt = st.text_input(placeholder, key=f"txt_{modo}", label_visibility="collapsed", placeholder=placeholder)
        sug = buscador(txt)
        valor = st.selectbox("Sugerencias", sug, index=None, key=f"sel_{modo}_{txt}") if sug else None
        valor = valor or (txt.strip() if len(txt.strip()) >= 3 else None)

    # 2) Filtros (todos opcionales y combinables)
    st.markdown("##### 2 · Filtros (todos opcionales)")
    with st.form("filtros"):
        a, b_ = st.columns(2)
        sectores = a.multiselect("Sector", list(sc.PLANTILLAS))
        extra = a.text_input("Palabras en el objeto", placeholder="ej: café, acuicultura")
        excluir = a.text_input("Excluir palabras", placeholder="ej: vigilancia, aseo")
        deptos = b_.multiselect("Departamento (vacío = todos)", opciones_union("departamento"))
        periodo = b_.selectbox("Periodo", list(PERIODOS), index=2,
                               help="Contratos: fecha de firma. Convocatorias: fecha de publicación. Para ver todo el historial de una empresa elige «Todo el historial».")
        solo_abiertas = b_.checkbox("Convocatorias: solo abiertas", value=True)
        with st.expander("Más filtros"):
            m1, m2 = st.columns(2)
            valor_min = m1.number_input("Valor mínimo (COP)", min_value=0, value=0, step=10_000_000)
            ciudad = m1.text_input("Ciudad o municipio contiene")
            entidad = m2.text_input("Entidad contiene")
            proveedor = m2.text_input("Empresa, persona o NIT contiene")
            modalidades = st.multiselect("Modalidad de contratación", opciones_union("modalidad"))
            tipos = st.multiselect("Tipo de contrato", opciones_union("tipo"))
        enviado = st.form_submit_button("🔎 Buscar", type="primary", width="stretch")

    if enviado:
        terminos = list(dict.fromkeys([k for s_ in sectores for k in sc.PLANTILLAS[s_]] + sc.partir(extra)))
        partes = [f"{modo} «{valor}»" if valor else None, ", ".join(sectores) or None,
                  f"palabras: {extra.strip()}" if extra.strip() else None, ", ".join(deptos) or None, periodo.lower()]
        spec = dict(
            modo=modo, valor=valor, terminos=terminos, excluir=sc.partir(excluir), deptos=deptos, ciudad=ciudad.strip(),
            entidad=entidad.strip(), proveedor=proveedor.strip(), modalidades=modalidades, tipos=tipos, periodo=periodo,
            solo_abiertas=solo_abiertas, valor_min=int(valor_min), resumen=" · ".join(p for p in partes if p))
        st.session_state["busq"] = {**spec, "id": repr(spec)}
    b = st.session_state.get("busq")
    if not b:
        st.caption("Elige lo que quieras (un nombre, un sector, un departamento…) y pulsa «Buscar».")
        return
    st.divider()
    resultados(b)


# ---------------------------------------------------------------------------
# ⭐ Guardados y 🔔 alertas
# ---------------------------------------------------------------------------
TIPOS_ALERTA = {
    "convocatorias": "🟢 Convocatorias abiertas (de cualquier modalidad)",
    "adjudicados": "🏆 Contratos recién adjudicados (licitaciones, concursos…)",
    "directos": "🤝 Contratos directos recientes (incluye prestación de servicios)",
}


def vista_previa(perfil, tipos):
    """Muestra lo que traería la alerta hoy con estos ajustes (no envía nada ni marca nada como visto)."""
    import alertas
    secciones = []
    try:
        with st.spinner("Consultando datos.gov.co… (puede tardar un minuto)"):
            if "convocatorias" in tipos:
                secciones.append(("🟢 Convocatorias abiertas", alertas.convocatorias_nuevas(perfil, set())))
            if "adjudicados" in tipos or "directos" in tipos:
                adj, dire = alertas.contratos_nuevos(perfil, set())
                if "adjudicados" in tipos:
                    secciones.append(("🏆 Contratos adjudicados", adj))
                if "directos" in tipos:
                    secciones.append(("🤝 Contratos directos", dire))
    except Exception as e:
        st.error(f"No pude consultar: {e}")
        return
    secciones = [(t, df) for t, df in secciones if not df.empty]
    total = sum(len(df) for _, df in secciones)
    if not total:
        st.info("Con estos ajustes hoy no llegaría nada. Si es demasiado estricto, agrega palabras al núcleo o combinaciones.")
        return
    if total > 40:
        st.warning(f"Hoy te llegarían {total} avisos: son muchos. Mira qué palabras traen más resultados y ajusta.")
    else:
        st.success(f"Hoy te llegarían {total} avisos.")
    tabla = sc.conteo_por_palabra(pd.concat([df for _, df in secciones]), perfil.get("terminos", []), perfil.get("reglas", []))
    with st.expander("Qué palabras y combinaciones traen más resultados"):
        st.dataframe(tabla.head(25), hide_index=True)
        st.caption("Si una fila trae muchísimos resultados que no son de tu área, bórrala o hazla más específica.")
    for titulo, df in secciones:
        st.markdown(f"**{titulo}: {len(df)}**")
        for r in df.head(8).itertuples():
            quien = f"{r.proveedor} · " if isinstance(r.proveedor, str) and r.proveedor else ""
            valor = f" · ${r.valor:,.0f}" if pd.notna(r.valor) else ""
            st.markdown(f"- {md((r.texto or '')[:120])} — {md(quien)}{md(r.entidad or '')}{valor}")


def ajustes_alertas():
    perfil = st.session_state["perfil"]
    ds = sc.DATASETS[CONTRATOS]
    c, _ = columnas_de(ds)
    opts_depto = opciones("departamento", ds, c) if c else []
    estricto = "nucleo" in perfil   # ya guardaste el perfil restrictivo
    with st.expander("🔔 Alertas diarias (Telegram o correo)", expanded=not estricto):
        st.caption("Cada mañana te avisan lo nuevo que encaja con esto. Los avisos los envía alertas.py (GitHub Actions).")
        if not estricto:
            st.info("🎯 Te dejé cargado el perfil restrictivo de ingeniería agronómica. Revísalo, pulsa «Ver qué traería hoy» y luego «Guardar alertas».")
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
                        persistir_perfil(dict(texto=desc.strip(), sectores=[], nucleo="\n".join(r["terminos"]), combos="",
                                              reglas=[], **r))
                        st.session_state["al_ver"] = v + 1
                        st.rerun()
        tipos = st.multiselect("Qué quieres que te avise", list(TIPOS_ALERTA), format_func=TIPOS_ALERTA.get, key=f"al_tip_{v}",
                               default=[t for t in perfil.get("alertas_tipos", list(TIPOS_ALERTA)) if t in TIPOS_ALERTA])
        vmin = st.number_input("Valor mínimo de los contratos (COP), para evitar contratos pequeños", min_value=0, step=10_000_000,
                               value=int(perfil.get("valor_min_contratos") or 0), key=f"al_vmin_{v}")
        st.markdown("**🎯 Palabras que bastan solas** (una por línea)")
        nucleo = st.text_area("Núcleo", height=190, key=f"al_nuc_{v}", label_visibility="collapsed",
                              value=perfil.get("nucleo", "\n".join(sc.NUCLEO_AGRONOMO)))
        st.markdown("**🧩 Combinaciones** (una por línea)")
        st.caption("Ambas partes deben aparecer; «|» separa alternativas. Ej: «UARIV + PRODUCTIV | RURAL» = UARIV junto con "
                   "PRODUCTIV o RURAL. Sirve para palabras generales (apoyo, supervisor, QGIS…) que solas traerían ruido. "
                   "Usa «_» en lugar de una vocal con tilde.")
        combos = st.text_area("Combinaciones", height=190, key=f"al_cmb_{v}", label_visibility="collapsed",
                              value=perfil.get("combos", sc.COMBOS_AGRONOMO))
        sectores = st.multiselect("Sectores completos (opcional; traen más ruido)", list(sc.PLANTILLAS), key=f"al_sec_{v}",
                                  default=[s_ for s_ in perfil.get("sectores", []) if s_ in sc.PLANTILLAS])
        excluir = st.text_input("Excluir palabras", key=f"al_exc_{v}",
                                value=", ".join(perfil.get("excluir", []) if estricto else sc.EXCLUIR_AGRONOMO))
        deptos = st.multiselect("Departamentos (vacío = todos)", opts_depto, key=f"al_dp_{v}",
                                default=[d for d in perfil.get("deptos", []) if d in opts_depto])
        ruido = st.checkbox("Quitar ruido típico (médicos, abogados, deportes, eventos, aseo…)", key=f"al_ruido_{v}",
                            value=bool(perfil.get("quitar_ruido", True)))
        terminos = list(dict.fromkeys([k for s_ in sectores for k in sc.PLANTILLAS[s_]] + sc.partir(nucleo)))
        reglas = sc.texto_a_reglas(combos)
        malas = sc.palabras_genericas(terminos)
        if malas:
            st.warning("Estas palabras son demasiado generales y traen mucho ruido; mejor pásalas a una combinación: " + ", ".join(malas))
        sueltas = [ln.strip() for ln in combos.splitlines() if ln.strip() and not sc.texto_a_reglas(ln)]
        if sueltas:
            st.warning("Estas líneas de combinaciones no tienen «+» y se ignoran: " + " · ".join(sueltas[:3]))
        if st.button("👀 Ver qué traería hoy", width="stretch", key="al_prev"):
            if terminos or reglas:
                vista_previa(dict(terminos=terminos, reglas=reglas, excluir=sc.partir(excluir), deptos=deptos,
                                  valor_min_contratos=int(vmin), quitar_ruido=ruido), tipos or list(TIPOS_ALERTA))
            else:
                st.warning("Escribe al menos una palabra o una combinación.")
        if st.button("💾 Guardar alertas", type="primary", width="stretch"):
            if not (terminos or reglas):
                st.warning("Escribe al menos una palabra o una combinación.")
            elif not tipos:
                st.warning("Elige al menos un tipo de aviso.")
            else:
                persistir_perfil(dict(terminos=terminos, reglas=reglas, nucleo=nucleo.strip(), combos=combos.strip(),
                                      excluir=sc.partir(excluir), deptos=deptos, sectores=sectores, alertas_tipos=tipos,
                                      valor_min_contratos=int(vmin), quitar_ruido=ruido))
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
