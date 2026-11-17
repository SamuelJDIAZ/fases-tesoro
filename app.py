"""
App: Fases del Tesoro (emision neta de T-Bills) vs Nasdaq 100 / Bitcoin.
Ejecutar:  streamlit run app.py
"""
import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from plotly.subplots import make_subplots

import tesoro_api as api

# ---------------------------------------------------------------- Tokens de diseño
BG, CARD, MUTED, BORDER = "#020617", "#0E1223", "#94A3B8", "#334155"
FG, VERDE, ROJO, AZUL = "#F8FAFC", "#22C55E", "#EF4444", "#38BDF8"

st.set_page_config(page_title="Fases del Tesoro", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input {{ font-family: 'Inter', sans-serif; }}
.block-container {{ padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1400px; }}
h1 {{ font-weight: 700; letter-spacing: -0.02em; margin-bottom: 0 !important; }}
.sub {{ color: {MUTED}; font-size: 0.95rem; margin: 2px 0 18px; }}
.card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; padding: 18px 20px; height: 100%; }}
.hero {{ border-left: 6px solid var(--c); }}
.estado {{ display: inline-flex; align-items: center; gap: 10px; font-weight: 700; font-size: 1.35rem; color: var(--c); }}
.punto {{ width: 14px; height: 14px; border-radius: 50%; background: var(--c); box-shadow: 0 0 12px var(--c); }}
.grande {{ font-size: 2.1rem; font-weight: 700; color: {FG}; margin: 6px 0 2px; font-variant-numeric: tabular-nums; }}
.lbl {{ color: {MUTED}; font-size: 0.85rem; text-transform: uppercase; letter-spacing: .06em; }}
.txt {{ color: {FG}; font-size: 1rem; line-height: 1.5; margin-top: 8px; }}
.aviso {{ margin-top: 12px; padding: 10px 12px; border-radius: 10px; background: rgba(56,189,248,.08);
          border: 1px solid rgba(56,189,248,.35); color: {FG}; font-size: .95rem; }}
.kpi .v {{ font-size: 1.6rem; font-weight: 700; font-variant-numeric: tabular-nums; }}
.kpi .d {{ color: {MUTED}; font-size: .9rem; margin-top: 2px; }}
.pos {{ color: {VERDE}; }} .neg {{ color: {ROJO}; }}
.chip {{ display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .8rem; font-weight: 600;
         border: 1px solid var(--c); color: var(--c); }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
""", unsafe_allow_html=True)


def mm(x):  # miles de millones USD
    return f"{x / 1e9:+,.1f} mil M"


def pct(x):
    return "—" if x != x else f"{x:+.2f} %"


# ---------------------------------------------------------------- Encabezado y controles
st.markdown("<h1>Fases del Tesoro</h1>", unsafe_allow_html=True)
st.markdown("<div class='sub'>Emisión neta de T-Bills del Tesoro de EE. UU. frente al Nasdaq 100 o Bitcoin. "
            "Rojo = el Tesoro absorbe liquidez · Verde = la devuelve (pay down).</div>", unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns([1.3, 1.1, 1, 0.8])
with c1:
    activo = st.segmented_control("Activo", list(api.ACTIVOS), default="Nasdaq 100") or "Nasdaq 100"
with c2:
    vela = st.segmented_control("Velas", ["Diario", "Semanal"], default="Semanal") or "Semanal"
with c3:
    horizonte = st.selectbox("Semanas a proyectar", [4, 8, 12], index=1)
with c4:
    st.write("")
    if st.button("Actualizar ahora", width="stretch"):
        st.cache_data.clear()

with st.expander("Opciones avanzadas (no hace falta tocarlas)"):
    a1, a2, a3, a4 = st.columns(4)
    desde = a1.date_input("Historial desde", dt.date(2025, 10, 1))
    estacional = a2.toggle("Estimar con el patrón del año pasado", value=True,
                           help="Lo que el Tesoro aún no anuncia se estima con el último tamaño de cada plazo, "
                                "ajustado por lo que hizo en esas mismas semanas del año pasado.")
    ajuste = a3.slider("Ajuste manual extra (%)", -30, 30, 0,
                       help="Solo para probar un escenario propio encima de la estimación automática.")
    min_sem = a4.slider("Mínimo de semanas por fase", 1, 6, 3,
                        help="Une rachas cortas para que las fases no salten cada semana.")


# ---------------------------------------------------------------- Datos (se renuevan solos)
@st.cache_data(ttl=1800, show_spinner="Descargando anuncios del Tesoro…")
def cargar_tesoro(desde, horizonte, ajuste, min_sem, estacional, version=3):
    res = api.calcular_tesoro(str(desde), horizonte, ajuste, min_sem, estacional=estacional)
    res["actualizado"] = dt.datetime.now()
    return res


@st.cache_data(ttl=1800, show_spinner="Descargando precios…")
def cargar_activo(res, activo, desde):
    return api.agregar_activo(res, activo, str(desde))


try:
    base = cargar_tesoro(desde, horizonte, ajuste, min_sem, estacional)
    res = cargar_activo(base, activo, desde)
except Exception as e:  # noqa: BLE001
    st.error(f"No pude descargar los datos: {e}")
    st.stop()

sem, fz, hoy = res["semanal"], res["fases"], res["hoy"]
px = res["diario"] if vela == "Diario" else res["px_semanal"]
s = api.senal(res)
verde = s["color"] == "verde"
col = VERDE if verde else ROJO

# ---------------------------------------------------------------- Semáforo + KPIs
h1, h2 = st.columns([1.35, 1])
with h1:
    fa, fs = s["fase_actual"], s["fase_siguiente"]
    estado = "PAY DOWN EN CAMINO" if verde else "SETTLEMENT · EMISIÓN NETA"
    lectura = ("El Tesoro devuelve liquidez y los precios suelen subir "
               "→ <b>fase para mantener y vender en máximos</b>." if verde else
               "El Tesoro absorbe liquidez y los precios suelen estar débiles "
               "→ <b>fase para acumular por partes</b> antes del próximo pay down.")
    extra = ""
    c = s["corte_para_paydown"]
    if not verde and c == c:
        extra = (f"<div class='aviso'>Pasa a verde si el Tesoro recorta ≈<b>{c:.0f} %</b> más de lo estimado "
                 "en los bills que aún no anuncia. Se recalcula solo con cada anuncio (martes y jueves).</div>")
    fase_txt = ""
    if fa:
        fase_txt += f"Fase actual: <b>{fa['tipo']}</b> desde {pd.Timestamp(fa['inicio']):%d %b %Y}"
    if fs:
        fase_txt += f" · Siguiente estimada: <b>{fs['tipo']}</b> desde {pd.Timestamp(fs['inicio']):%d %b}"
    st.markdown(f"""
    <div class="card hero" style="--c:{col}">
      <div class="estado"><span class="punto"></span>{estado}</div>
      <div class="grande">{mm(s['neto_prox'])} USD</div>
      <div class="lbl">Neto de bills proyectado · próximas {s['semanas']} semanas</div>
      <div class="txt">{lectura}</div>
      <div class="txt" style="color:{MUTED}">{fase_txt}</div>
      {extra}
    </div>""", unsafe_allow_html=True)
    try:
        lx = api.lectura(res, res["diario"], activo)
        cx = lx["contexto"]
        st.markdown(f"""
        <div class="card" style="margin-top:12px">
          <div class="lbl">Lectura para {activo}</div>
          <div style="font-size:1.25rem;font-weight:700;margin:4px 0;color:{AZUL}">{lx['accion']}</div>
          <div class="txt" style="margin-top:2px">{lx['texto']}</div>
          <div class="txt" style="color:{MUTED};font-size:.9rem">Precio {cx['precio']:,.0f} · {cx['dd']:+.1f} % del máximo
          de 3 meses · tendencia {'alcista' if cx['alcista'] else 'bajista'} (vs media de ~10 semanas).
          Regla automática, no es asesoría financiera.</div>
        </div>""", unsafe_allow_html=True)
    except Exception:  # noqa: BLE001
        pass

with h2:
    st_ = res["stats"]
    pdn, emi = st_["PAY DOWN"], st_["EMISION"]
    hist = fz[~fz["proyectada"]].dropna(subset=["activo_pct"])
    g = hist[hist["tipo"] == "PAY DOWN"]
    r = hist[hist["tipo"] == "EMISION"]

    def kpi(titulo, valor, detalle, signo):
        cls = "pos" if signo > 0 else ("neg" if signo < 0 else "")
        return (f"<div class='card kpi' style='margin-bottom:12px'><div class='lbl'>{titulo}</div>"
                f"<div class='v {cls}'>{valor}</div><div class='d'>{detalle}</div></div>")

    st.markdown(
        kpi(f"{activo} · semana media en PAY DOWN", pct(pdn["media"]),
            f"{pdn['pct_positivas']:.0f} % de {pdn['semanas']} semanas al alza", pdn["media"])
        + kpi(f"{activo} · semana media en EMISIÓN", pct(emi["media"]),
              f"{emi['pct_positivas']:.0f} % de {emi['semanas']} semanas al alza", emi["media"])
        + kpi("Fases verdes que terminaron al alza", f"{(g['activo_pct'] > 0).sum()} de {len(g)}",
              f"Fases rojas al alza: {(r['activo_pct'] > 0).sum()} de {len(r)}", 0),
        unsafe_allow_html=True)

st.caption(f"Datos descargados {res['actualizado']:%d %b %H:%M} · se renuevan solos cada 30 min · "
           "Fuentes: TreasuryDirect (subastas de bills) y Yahoo Finance / Stooq (precios).")

# ---------------------------------------------------------------- Pestañas
t1, t2, t3, t4, t5 = st.tabs(["Fases y velas", "Gráfica en vivo (TradingView)", "Próximas semanas",
                              "Historial de fases", "Cómo funciona"])

with t1:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.74, 0.26], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(
        x=px.index, open=px["open"], high=px["high"], low=px["low"], close=px["close"],
        increasing_line_color=VERDE, decreasing_line_color=ROJO, increasing_fillcolor=VERDE,
        decreasing_fillcolor=ROJO, name=activo,
    ), row=1, col=1)
    for _, f in fz.iterrows():
        cc = ROJO if f["tipo"] == "EMISION" else VERDE
        fig.add_vrect(x0=f["inicio"], x1=f["fin"] + pd.Timedelta(days=2), fillcolor=cc,
                      opacity=0.07 if f["proyectada"] else 0.13, line_width=0, row="all", col=1)
        fig.add_annotation(x=f["inicio"] + (f["fin"] - f["inicio"]) / 2, y=1.05, yref="paper",
                           text=f"<b>{f['fase']}</b>", showarrow=False, font=dict(color=cc, size=13),
                           hovertext=f"Fase {f['fase']}: {f['tipo']} · neto {mm(f['neto'])}")
    fut = sem.index > api.fin_de_semana(hoy)
    fig.add_trace(go.Bar(
        x=sem.index, y=sem["neto"] / 1e9, marker_color=[ROJO if v >= 0 else VERDE for v in sem["neto"]],
        marker_opacity=[0.4 if f_ else 0.95 for f_ in fut], customdata=sem["dato"],
        hovertemplate="Semana al %{x|%d %b %Y}<br>Neto %{y:+.1f} mil M USD<br>%{customdata}<extra></extra>",
        name="Neto bills",
    ), row=2, col=1)
    fig.add_vline(x=hoy, line_dash="dot", line_color=MUTED)
    fig.add_annotation(x=hoy, y=0.98, yref="paper", text="hoy → proyección", showarrow=False,
                       xanchor="left", font=dict(color=MUTED, size=11))
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG, height=640, showlegend=False,
        xaxis_rangeslider_visible=False, margin=dict(l=8, r=8, t=36, b=8), hovermode="x unified",
        font=dict(family="Inter, sans-serif", color=FG),
    )
    fig.update_xaxes(gridcolor="#111827")
    fig.update_yaxes(gridcolor="#111827")
    fig.update_yaxes(title_text=f"{activo} ({vela.lower()})", row=1, col=1)
    fig.update_yaxes(title_text="Neto bills (mil M)", row=2, col=1)
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
    st.caption("Franjas rojas = emisión neta · Franjas verdes = pay down · Más claras = proyección. "
               "Barras de abajo: neto semanal de bills (positivo = emisión, negativo = pay down).")

with t2:
    simbolo = api.ACTIVOS[activo]["tradingview"]
    intervalo = "D" if vela == "Diario" else "W"
    components.html(f"""
    <div class="tradingview-widget-container" style="height:620px;width:100%">
      <div id="tv" style="height:620px;width:100%"></div>
      <script src="https://s3.tradingview.com/tv.js"></script>
      <script>
        new TradingView.widget({{container_id: "tv", symbol: "{simbolo}", interval: "{intervalo}",
          theme: "dark", style: "1", locale: "es", autosize: true, timezone: "America/Bogota",
          allow_symbol_change: true, hide_side_toolbar: false, backgroundColor: "{BG}"}});
      </script>
    </div>""", height=630)
    st.caption("Gráfica en vivo de TradingView (puedes cambiar temporalidad, dibujar y poner indicadores). "
               "Las fases del Tesoro se ven en la pestaña 'Fases y velas'.")

with t3:
    p = sem[sem.index >= api.fin_de_semana(hoy)].copy()
    tabla = pd.DataFrame({
        "Semana (viernes)": p.index.strftime("%d %b %Y"),
        "Emisión": p["emision"] / 1e9,
        "Vencimientos": p["vencimientos"] / 1e9,
        "Neto": p["neto"] / 1e9,
        "Lectura": ["Pay down" if v < 0 else "Emisión" for v in p["neto"]],
        "Dato": p["dato"],
    })
    st.dataframe(
        tabla.style.map(lambda v: f"color: {ROJO if v >= 0 else VERDE}", subset=["Neto"]),
        hide_index=True, width="stretch",
        column_config={k: st.column_config.NumberColumn(k, format="%.1f") for k in ["Emisión", "Vencimientos", "Neto"]},
    )
    st.caption("Mil M USD. Real = ya liquidado · Anunciado = el Tesoro ya publicó el tamaño · "
               "Estimado = último tamaño de cada plazo × el cambio que hizo el Tesoro esas semanas del año pasado.")
    sub = res["subastas"]
    an = sub[(sub["liquidacion"] >= hoy) & (sub["estado"] != "Estimado")].copy()
    st.markdown("**Subastas de bills ya anunciadas que aún no liquidan**")
    if an.empty:
        st.write("No hay anuncios pendientes ahora mismo.")
    else:
        st.dataframe(pd.DataFrame({
            "Plazo": an["plazo"], "CMB": an["cmb"].map({True: "Sí", False: "No"}),
            "Liquida": an["liquidacion"].dt.strftime("%d %b %Y"), "Vence": an["vencimiento"].dt.strftime("%d %b %Y"),
            "Monto (mil M)": an["monto"] / 1e9,
        }), hide_index=True, width="stretch",
            column_config={"Monto (mil M)": st.column_config.NumberColumn(format="%.1f")})

with t4:
    tabla = pd.DataFrame({
        "Fase": fz["fase"], "Tipo": fz["tipo"].map({"EMISION": "Emisión", "PAY DOWN": "Pay down"}),
        "Desde": fz["inicio"].dt.strftime("%d %b %Y"), "Hasta": fz["fin"].dt.strftime("%d %b %Y"),
        "Semanas": fz["semanas"], "Neto bills (mil M)": fz["neto"] / 1e9,
        f"{activo} %": fz["activo_pct"],
        "Estado": fz["proyectada"].map({True: "Incluye proyección", False: "Histórica"}),
    })

    def pintar(row):
        c_ = "rgba(239,68,68,.14)" if row["Tipo"] == "Emisión" else "rgba(34,197,94,.14)"
        return [f"background-color: {c_}"] * len(row)

    st.dataframe(tabla.style.apply(pintar, axis=1), hide_index=True, width="stretch",
                 column_config={"Neto bills (mil M)": st.column_config.NumberColumn(format="%+.1f"),
                                f"{activo} %": st.column_config.NumberColumn(format="%+.2f")})
    st.caption(f"Rentabilidad de {activo}: cierre previo al inicio de la fase → último cierre de la fase.")

with t5:
    st.markdown(f"""
**Qué mide.** Cada semana el Tesoro emite bills nuevos y paga los que vencen.
Neto = emisiones − vencimientos. Si es positivo (**emisión**), el Tesoro le quita liquidez al mercado;
si es negativo (**pay down**), se la devuelve.

**Cómo ve el futuro.**
- Los **vencimientos** futuros ya se conocen exactamente (se emitieron antes).
- Lo que el Tesoro **ya anunció** se usa tal cual (anuncios cada martes y jueves).
- Lo que **no ha anunciado** se estima con el último tamaño de cada plazo × el cambio que hizo el Tesoro
  en esas mismas semanas del año pasado (por ejemplo, los recortes de diciembre por impuestos corporativos).

**Fechas a vigilar.** Impuestos corporativos (≈15 dic, 15 abr, 15 jun, 15 sep) suelen traer recortes y pay downs.
El Quarterly Refunding (inicios de feb, may, ago y nov) da la guía del trimestre.

**Ojo.** Es una comparación histórica: no demuestra causalidad ni es recomendación de inversión.
""")
