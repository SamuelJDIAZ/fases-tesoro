"""
tesoro_api.py - "API facil" para seguir la emision neta de T-Bills del Tesoro de EE. UU.
frente al Nasdaq 100.

Fuente principal: API publica del Tesoro (fiscaldata.treasury.gov). Gratis y sin clave.
  - /v1/accounting/od/auctions_query     -> subastas de bills (pasadas y anunciadas)
  - /v1/accounting/od/upcoming_auctions  -> subastas anunciadas que aun no liquidan
Nasdaq 100: Yahoo Finance (yfinance), con Stooq como respaldo.

Como se calcula:
  neto semanal = bills que liquidan (issue/settlement) en la semana
               - bills que vencen en la semana
  (excluye notes/bonds, igual que la grafica de referencia)

Para las semanas futuras:
  - los vencimientos ya se conocen (se emitieron antes);
  - lo ya anunciado por el Tesoro se usa tal cual;
  - lo no anunciado se estima con el ultimo tamano de cada plazo (+/- un ajuste manual).

Uso rapido en consola:  python tesoro_api.py
"""
from __future__ import annotations

import datetime as dt
import os
import re

import numpy as np
import pandas as pd
import requests

BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
SEMANA = "W-FRI"  # semanas que terminan el viernes
MOCK = os.environ.get("TESORO_MOCK") == "1"  # datos simulados para pruebas


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def _get(endpoint: str, params: dict) -> pd.DataFrame:
    """Descarga todas las paginas de un endpoint de fiscaldata."""
    rows, page = [], 1
    while True:
        p = dict(params)
        p["page[size]"] = 10000
        p["page[number]"] = page
        r = requests.get(f"{BASE}{endpoint}", params=p, timeout=60)
        r.raise_for_status()
        j = r.json()
        rows += j.get("data", [])
        total_pages = int(j.get("meta", {}).get("total-pages", 1) or 1)
        if page >= total_pages:
            break
        page += 1
    return pd.DataFrame(rows)


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace({"null": None, "": None}), errors="coerce")


def _fecha(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.replace({"null": None, "": None}), errors="coerce")


def _col(df: pd.DataFrame, *nombres: str) -> pd.Series:
    """Devuelve la primera columna que exista (los nombres de la API varian un poco)."""
    for n in nombres:
        if n in df.columns:
            return df[n]
    return pd.Series([None] * len(df), index=df.index)


def plazo_en_dias(term: str) -> int | None:
    """'13-Week' -> 91, '42-Day' -> 42, '52-Week' -> 364."""
    if not isinstance(term, str):
        return None
    m = re.match(r"\s*(\d+)\s*-?\s*(Week|Day)", term, re.I)
    if not m:
        return None
    n = int(m.group(1))
    return n * 7 if m.group(2).lower() == "week" else n


def fin_de_semana(d) -> pd.Timestamp:
    """Viernes que cierra la semana de la fecha d."""
    d = pd.Timestamp(d).normalize()
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7)


# ----------------------------------------------------------------------------
# 1) Subastas de bills
# ----------------------------------------------------------------------------
def subastas_bills(desde: str) -> pd.DataFrame:
    """Todas las subastas de bills que liquidan desde `desde` (incluye CMBs y anunciadas).

    Columnas: cusip, plazo, cmb, fecha_subasta, liquidacion, vencimiento, monto, estado
    (monto en USD; 'estado' = Subastado | Anunciado)
    """
    if MOCK:
        return _mock_subastas(desde)
    if os.environ.get("TESORO_CSV"):  # datos guardados localmente (pruebas)
        return _desde_csv(os.environ["TESORO_CSV"])
    try:
        out = _treasurydirect(desde)
        if not out.empty:
            return out
    except Exception:
        pass
    return _fiscaldata(desde)


TD = "https://www.treasurydirect.gov/TA_WS/securities"
HEADERS = {"User-Agent": "Mozilla/5.0 (fases-tesoro app)", "Accept": "application/json"}


def _treasurydirect(desde: str) -> pd.DataFrame:
    """Fuente principal: servicio web de TreasuryDirect. Se actualiza el mismo dia del anuncio
    e incluye bills regulares, CMBs y subastas anunciadas que aun no liquidan."""
    hasta = (pd.Timestamp.today() + pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    r = requests.get(
        f"{TD}/search",
        params={"format": "json", "type": "Bill", "startDate": desde, "endDate": hasta, "dateFieldName": "issueDate"},
        headers=HEADERS, timeout=60,
    )
    r.raise_for_status()
    raw = pd.DataFrame(r.json())
    if raw.empty:
        return raw
    df = pd.DataFrame(index=raw.index)
    df["cusip"] = _col(raw, "cusip")
    df["plazo"] = _col(raw, "securityTerm")
    df["cmb"] = _col(raw, "cashManagementBill", "cashManagementBillCMB").astype(str).str.lower().eq("yes")
    df["fecha_subasta"] = _fecha(_col(raw, "auctionDate")).fillna(_fecha(_col(raw, "competitiveAuctionCloseDate")))
    df["liquidacion"] = _fecha(_col(raw, "issueDate"))
    df["vencimiento"] = _fecha(_col(raw, "maturityDate"))
    # Parte del publico (sin la Fed/SOMA): adjudicado dentro del monto ofrecido
    publico = _num(_col(raw, "totalParAwardedInsideOfOfferingAmount"))
    ofrecido = _num(_col(raw, "offeringAmount"))
    df["monto"] = publico.fillna(ofrecido)
    df["estado"] = np.where(publico.notna(), "Subastado", "Anunciado")
    # Una subasta anunciada cuya liquidacion ya paso sin resultado se trata como subastada
    df.loc[(df["estado"] == "Anunciado") & (df["liquidacion"] < pd.Timestamp.today().normalize()), "estado"] = "Subastado"
    df = df.dropna(subset=["liquidacion", "monto"])
    return df.sort_values("liquidacion").reset_index(drop=True)


def _desde_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["liquidacion", "vencimiento"])
    df["fecha_subasta"] = pd.NaT
    df["cmb"] = df["cmb"].astype(bool)
    return df


def _fiscaldata(desde: str) -> pd.DataFrame:
    """Respaldo: fiscaldata.treasury.gov (puede ir con algunas semanas de atraso)."""
    raw = _get(
        "/v1/accounting/od/auctions_query",
        {"filter": f"security_type:eq:Bill,issue_date:gte:{desde}", "sort": "issue_date"},
    )
    out = _normalizar(raw)

    # Agregar anunciadas que aun no esten en auctions_query
    try:
        hoy = pd.Timestamp.today().normalize()
        up = _get("/v1/accounting/od/upcoming_auctions",
                  {"filter": f"security_type:eq:Bill,issue_date:gte:{hoy:%Y-%m-%d}"})
        up = _normalizar(up)
        up = up[up["liquidacion"] >= hoy]
        if not up.empty:
            clave = set(zip(out["cusip"], out["liquidacion"]))
            up = up[[(c, l) not in clave for c, l in zip(up["cusip"], up["liquidacion"])]]
            out = pd.concat([out, up], ignore_index=True)
    except Exception:
        pass
    return out.sort_values("liquidacion").reset_index(drop=True)


def _normalizar(raw: pd.DataFrame) -> pd.DataFrame:
    cols = ["cusip", "plazo", "cmb", "fecha_subasta", "liquidacion", "vencimiento", "monto", "estado"]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(index=raw.index)
    df["cusip"] = _col(raw, "cusip")
    df["plazo"] = _col(raw, "security_term")
    cmb = _col(raw, "cash_management_bill_cmb", "cmb").astype(str).str.lower()
    df["cmb"] = cmb.isin(["yes", "y", "true"]) | df["plazo"].astype(str).str.contains("CMB", case=False)
    df["fecha_subasta"] = _fecha(_col(raw, "auction_date"))
    df["liquidacion"] = _fecha(_col(raw, "issue_date"))
    df["vencimiento"] = _fecha(_col(raw, "maturity_date"))
    # total_accepted incluye la Fed (SOMA): se resta para quedarse con el publico
    aceptado = _num(_col(raw, "total_accepted")) - _num(_col(raw, "soma_accepted")).fillna(0)
    ofrecido = _num(_col(raw, "offering_amt"))
    df["monto"] = aceptado.fillna(ofrecido)
    df["estado"] = np.where(aceptado.notna(), "Subastado", "Anunciado")
    # Vencimiento faltante (pasa en anunciadas): liquidacion + plazo
    dias = df["plazo"].map(plazo_en_dias)
    falta = df["vencimiento"].isna() & dias.notna()
    df.loc[falta, "vencimiento"] = df.loc[falta, "liquidacion"] + pd.to_timedelta(dias[falta], unit="D")
    df = df.dropna(subset=["liquidacion", "monto"])
    return df[cols]


# ----------------------------------------------------------------------------
# 2) Proyeccion de las semanas que no se han anunciado
# ----------------------------------------------------------------------------
def _factor_estacional(hist: pd.DataFrame, fecha_ult, fecha_obj) -> float:
    """Cuanto cambio el tamano de este plazo el ano pasado entre las mismas dos fechas.
    Ej.: si el ano pasado el 4-Week paso de 110 (mediados nov) a 85 (mediados dic) -> 0.77.
    Asi la app copia sola los recortes/aumentos estacionales del Tesoro (impuestos, fin de ano)."""
    def cerca(f):
        obj = f - pd.Timedelta(days=364)
        d = (hist["liquidacion"] - obj).abs()
        if d.empty or d.min() > pd.Timedelta(days=4):
            return None
        return float(hist.loc[d.idxmin(), "monto"])
    a, b = cerca(pd.Timestamp(fecha_obj)), cerca(pd.Timestamp(fecha_ult))
    if not a or not b:
        return 1.0
    return float(np.clip(a / b, 0.6, 1.4))


def estimar_futuro(sub: pd.DataFrame, hoy, semanas: int, ajuste_pct: float = 0.0,
                   estacional: bool = True) -> pd.DataFrame:
    """Agrega filas 'Estimado' para cada plazo regular que aun no tenga subasta anunciada
    en las proximas `semanas`. Tamano = ultima subasta de ese plazo x patron del ano pasado
    (estacional=True) x (1 + ajuste manual opcional)."""
    hoy = pd.Timestamp(hoy).normalize()
    reg = sub[(~sub["cmb"]) & sub["plazo"].map(plazo_en_dias).notna()].copy()
    if reg.empty:
        return sub
    reg["semana"] = reg["liquidacion"].map(fin_de_semana)
    ultima_sem = fin_de_semana(hoy)
    ventana = reg[(reg["semana"] > ultima_sem - pd.Timedelta(weeks=6)) & (reg["semana"] <= ultima_sem + pd.Timedelta(weeks=1))]
    conteo = ventana.groupby("plazo")["semana"].nunique()
    semanales = [p for p, n in conteo.items() if n >= 3 and plazo_en_dias(p) < 364]
    tiene_52 = (reg["plazo"].map(plazo_en_dias) == 364).any()

    filas = []
    semanas_fut = [ultima_sem + pd.Timedelta(weeks=i) for i in range(0, semanas + 1)]
    for p in semanas_fut:
        en_sem = set(reg.loc[reg["semana"] == p, "plazo"])
        plazos = list(semanales)
        if tiene_52:
            r52 = reg[reg["plazo"].map(plazo_en_dias) == 364]
            ult52 = r52["semana"].max()
            if ((p - ult52).days // 7) % 4 == 0 and p > ult52:
                plazos.append(r52.sort_values("liquidacion")["plazo"].iloc[-1])
        for plazo in plazos:
            if plazo in en_sem:
                continue
            hist = reg[reg["plazo"] == plazo].sort_values("liquidacion")
            if hist.empty:
                continue
            # dia de liquidacion habitual de este plazo (martes o jueves)
            dow = int(hist["liquidacion"].dt.weekday.mode().iloc[0])
            liq = p - pd.Timedelta(days=4 - dow)
            if liq < hoy:
                continue  # ya paso y no hubo subasta de ese plazo
            ult = hist.iloc[-1]
            factor = _factor_estacional(hist, ult["liquidacion"], liq) if estacional else 1.0
            monto = float(ult["monto"]) * factor * (1 + ajuste_pct / 100)
            filas.append({
                "cusip": None, "plazo": plazo, "cmb": False, "fecha_subasta": pd.NaT,
                "liquidacion": liq, "vencimiento": liq + pd.Timedelta(days=plazo_en_dias(plazo)),
                "monto": monto, "estado": "Estimado",
            })
    if not filas:
        return sub
    return pd.concat([sub, pd.DataFrame(filas)], ignore_index=True).sort_values("liquidacion").reset_index(drop=True)


# ----------------------------------------------------------------------------
# 3) Neto semanal
# ----------------------------------------------------------------------------
def neto_semanal(sub: pd.DataFrame, desde, hasta) -> pd.DataFrame:
    """Tabla semanal: emision, vencimientos, neto (USD) y tipo de dato (Real/Anunciado/Estimado)."""
    desde, hasta = fin_de_semana(desde), fin_de_semana(hasta)
    idx = pd.date_range(desde, hasta, freq=SEMANA)
    s = sub.copy()
    s["sem_liq"] = s["liquidacion"].map(fin_de_semana)
    s["sem_ven"] = s["vencimiento"].map(lambda d: fin_de_semana(d) if pd.notna(d) else pd.NaT)

    emision = s.groupby("sem_liq")["monto"].sum().reindex(idx, fill_value=0.0)
    venc = s.dropna(subset=["sem_ven"]).groupby("sem_ven")["monto"].sum().reindex(idx, fill_value=0.0)

    rank = {"Subastado": 0, "Anunciado": 1, "Estimado": 2}
    peor = s.assign(r=s["estado"].map(rank)).groupby("sem_liq")["r"].max().reindex(idx).fillna(0)
    tipo = peor.map({0: "Real", 1: "Anunciado", 2: "Estimado"})

    df = pd.DataFrame({"emision": emision, "vencimientos": venc})
    df["neto"] = df["emision"] - df["vencimientos"]
    df["dato"] = tipo
    df.index.name = "semana"
    return df


# ----------------------------------------------------------------------------
# 4) Fases (rojo = emision neta, verde = pay down)
# ----------------------------------------------------------------------------
def _rachas(signos):
    rachas, ini = [], 0
    for i in range(1, len(signos) + 1):
        if i == len(signos) or signos[i] != signos[ini]:
            rachas.append([ini, i - 1, signos[ini]])
            ini = i
    return rachas


def suavizar(signos, min_semanas: int):
    """Une rachas mas cortas que min_semanas con sus vecinas (no toca la primera ni la ultima,
    para no borrar una fase que apenas empieza)."""
    s = list(signos)
    if min_semanas <= 1:
        return s
    while True:
        r = _rachas(s)
        cortas = [x for k, x in enumerate(r) if 0 < k < len(r) - 1 and x[1] - x[0] + 1 < min_semanas]
        if not cortas:
            return s
        x = min(cortas, key=lambda x: x[1] - x[0] + 1)
        for i in range(x[0], x[1] + 1):
            s[i] = -x[2]


def fases(semanal: pd.DataFrame, min_semanas: int = 3) -> pd.DataFrame:
    """Agrupa semanas consecutivas del mismo signo. tipo: EMISION (+) o PAY DOWN (-)."""
    if semanal.empty:
        return pd.DataFrame()
    signos = [1 if v >= 0 else -1 for v in semanal["neto"]]
    signos = suavizar(signos, min_semanas)
    filas = []
    for n, (i, j, sg) in enumerate(_rachas(signos), start=1):
        tramo = semanal.iloc[i : j + 1]
        filas.append({
            "fase": n,
            "tipo": "EMISION" if sg > 0 else "PAY DOWN",
            "inicio": tramo.index[0] - pd.Timedelta(days=6),  # lunes de la primera semana
            "fin": tramo.index[-1],
            "semanas": len(tramo),
            "neto": tramo["neto"].sum(),
            "proyectada": bool((tramo["dato"] != "Real").any()),
        })
    return pd.DataFrame(filas)


# ----------------------------------------------------------------------------
# 5) Precios: Nasdaq 100 o Bitcoin, diario o semanal
# ----------------------------------------------------------------------------
ACTIVOS = {
    "Nasdaq 100": {"yahoo": "^NDX", "stooq": "%5Endx", "tradingview": "NASDAQ:NDX"},
    "Bitcoin": {"yahoo": "BTC-USD", "stooq": "btcusd", "tradingview": "BITSTAMP:BTCUSD"},
}


def precios_diarios(activo: str, desde: str) -> pd.DataFrame:
    """Velas diarias (open, high, low, close) del activo. Yahoo Finance; respaldo Stooq."""
    cfg = ACTIVOS[activo]
    if MOCK:
        return _mock_precios(desde, 24000 if activo == "Nasdaq 100" else 90000)
    diario = None
    try:
        import yfinance as yf

        d = yf.download(cfg["yahoo"], start=desde, interval="1d", progress=False, auto_adjust=False)
        if d is not None and not d.empty:
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            diario = d.rename(columns=str.lower)[["open", "high", "low", "close"]]
    except Exception:
        diario = None
    if diario is None or diario.empty:  # respaldo: Stooq
        d = pd.read_csv(f"https://stooq.com/q/d/l/?s={cfg['stooq']}&i=d", parse_dates=["Date"], index_col="Date")
        diario = d.rename(columns=str.lower)[["open", "high", "low", "close"]]
        diario = diario[diario.index >= pd.Timestamp(desde)]
    diario.index = pd.to_datetime(diario.index).tz_localize(None)
    return diario.dropna()


def a_semanal(diario: pd.DataFrame) -> pd.DataFrame:
    """Convierte velas diarias en semanales con cierre el viernes (igual que las semanas del Tesoro)."""
    sem = diario.resample(SEMANA).agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    return sem.dropna()


def nasdaq_semanal(desde: str) -> pd.DataFrame:  # compatibilidad
    return a_semanal(precios_diarios("Nasdaq 100", desde))


def rentabilidad_fases(fz: pd.DataFrame, px: pd.DataFrame) -> pd.DataFrame:
    """Rentabilidad del activo en cada fase: cierre previo al inicio -> ultimo cierre de la fase."""
    fz = fz.copy()
    rets = []
    for _, f in fz.iterrows():
        antes = px[px.index < f["inicio"]]["close"]
        dentro = px[(px.index >= f["inicio"]) & (px.index <= f["fin"] + pd.Timedelta(days=2))]["close"]
        if dentro.empty:
            rets.append(np.nan)
            continue
        base = antes.iloc[-1] if not antes.empty else px.loc[dentro.index[0], "open"]
        rets.append((dentro.iloc[-1] / base - 1) * 100)
    fz["activo_pct"] = rets
    fz["nasdaq_pct"] = rets  # nombre anterior
    return fz


def estadisticas(semanal: pd.DataFrame, fz: pd.DataFrame, px_sem: pd.DataFrame) -> dict:
    """Rentabilidad semanal media del activo en semanas de pay down vs emision (solo historico)."""
    r = px_sem["close"].pct_change() * 100
    tipo_sem = pd.Series(index=semanal.index, dtype=object)
    for _, f in fz.iterrows():
        tipo_sem[(semanal.index >= f["inicio"]) & (semanal.index <= f["fin"])] = f["tipo"]
    df = pd.DataFrame({"r": r, "tipo": tipo_sem}).dropna()
    out = {}
    for t in ["PAY DOWN", "EMISION"]:
        x = df.loc[df["tipo"] == t, "r"]
        out[t] = {"semanas": int(len(x)), "media": float(x.mean()) if len(x) else np.nan,
                  "pct_positivas": float((x > 0).mean() * 100) if len(x) else np.nan}
    return out


# ----------------------------------------------------------------------------
# 6) Todo junto
# ----------------------------------------------------------------------------
def calcular_tesoro(desde: str = "2025-10-01", horizonte: int = 8, ajuste_pct: float = 0.0,
                    min_semanas: int = 3, hoy=None, estacional: bool = True) -> dict:
    """Solo la parte del Tesoro (no necesita precios). La usa tambien la alerta de Telegram."""
    hoy = pd.Timestamp(hoy or dt.date.today()).normalize()
    # Un ano extra: para saber que vence (52-week) y para el patron estacional del ano pasado
    desde_sub = min(pd.Timestamp(desde) - pd.Timedelta(days=380), hoy - pd.Timedelta(days=400))
    sub = subastas_bills(desde_sub.strftime("%Y-%m-%d"))
    sub = estimar_futuro(sub, hoy, horizonte, ajuste_pct, estacional)
    semanal = neto_semanal(sub, desde, hoy + pd.Timedelta(weeks=horizonte))
    fz = fases(semanal, min_semanas)
    hist = semanal[semanal["dato"] == "Real"]
    return {"hoy": hoy, "subastas": sub, "semanal": semanal, "fases": fz,
            "fases_hist": fases(hist, min_semanas), "min_semanas": min_semanas}


def agregar_activo(res: dict, activo: str, desde: str) -> dict:
    """Agrega precios del activo, rentabilidad por fase y estadisticas al resultado del Tesoro."""
    diario = precios_diarios(activo, desde)
    semanal_px = a_semanal(diario)
    hist = res["semanal"][res["semanal"]["dato"] == "Real"]
    out = dict(res)
    out.update({
        "activo": activo,
        "diario": diario,
        "ndx": semanal_px,  # nombre anterior
        "px_semanal": semanal_px,
        "fases": rentabilidad_fases(res["fases"], semanal_px),
        "stats": estadisticas(hist, res["fases_hist"], semanal_px),
    })
    return out


def calcular(desde: str = "2025-10-01", horizonte: int = 8, ajuste_pct: float = 0.0,
             min_semanas: int = 3, hoy=None, estacional: bool = True, activo: str = "Nasdaq 100") -> dict:
    res = calcular_tesoro(desde, horizonte, ajuste_pct, min_semanas, hoy, estacional)
    return agregar_activo(res, activo, desde)


def senal(res: dict, semanas: int = 4) -> dict:
    """Semaforo simple: neto proyectado de las proximas `semanas` semanas."""
    sem = res["semanal"]
    hoy_sem = fin_de_semana(res["hoy"])
    prox = sem[sem.index >= hoy_sem].head(semanas)
    neto = float(prox["neto"].sum())
    # Recorte (%) a lo NO anunciado que haria falta para que el neto llegue a cero
    sub = res["subastas"]
    fin = prox.index.max() if len(prox) else hoy_sem
    est = sub[(sub["estado"] == "Estimado") & (sub["liquidacion"].map(fin_de_semana) >= hoy_sem)
              & (sub["liquidacion"].map(fin_de_semana) <= fin)]["monto"].sum()
    corte = 0.0 if neto <= 0 else (neto / est * 100 if est > 0 else float("nan"))
    fz = res["fases"]
    actual = fz[(fz["inicio"] <= res["hoy"]) & (fz["fin"] >= res["hoy"])]
    siguiente = fz[fz["inicio"] > res["hoy"]]
    return {
        "neto_prox": neto,
        "semanas": len(prox),
        "color": "verde" if neto < 0 else "rojo",
        "corte_para_paydown": corte,
        "fase_actual": actual.iloc[0].to_dict() if not actual.empty else None,
        "fase_siguiente": siguiente.iloc[0].to_dict() if not siguiente.empty else None,
    }


# ----------------------------------------------------------------------------
# 7) Lectura de mercado (acumular / hold / tomar ganancias / esperar)
# ----------------------------------------------------------------------------
def fases_clave(res: dict) -> dict:
    """Fase actual y siguiente (ignora 'fases' de 1 semana en medio de la proyeccion)."""
    fz, hoy = res["fases"], res["hoy"]
    ultima = res["semanal"].index.max()
    actual = fz[(fz["inicio"] <= hoy) & (fz["fin"] >= hoy)]
    fut = fz[fz["inicio"] > hoy]
    fut = fut[(fut["semanas"] >= 2) | (fut["fin"] >= ultima)]

    def d(f):
        if f is None:
            return None
        return {"tipo": f["tipo"], "inicio": (f["inicio"] + pd.Timedelta(days=2)).normalize(),
                "fin": f["fin"].normalize(), "semanas": int(f["semanas"]), "hasta_horizonte": bool(f["fin"] >= ultima)}
    return {"actual": d(actual.iloc[0] if not actual.empty else None),
            "siguiente": d(fut.iloc[0] if not fut.empty else None)}


def contexto_precio(diario: pd.DataFrame) -> dict:
    """Donde esta el precio: distancia al maximo de 90 dias y tendencia vs media de ~10 semanas."""
    c = diario["close"].dropna()
    ult = float(c.iloc[-1])
    maximo = float(c[c.index >= c.index[-1] - pd.Timedelta(days=90)].max())
    media = float(c[c.index >= c.index[-1] - pd.Timedelta(days=70)].mean())
    return {"precio": ult, "max90": maximo, "dd": (ult / maximo - 1) * 100,
            "media70": media, "alcista": ult > media}


def lectura(res: dict, diario: pd.DataFrame, activo: str) -> dict:
    """Regla simple que combina la fase del Tesoro con donde esta el precio.
    Devuelve accion (texto corto) y explicacion."""
    fc = fases_clave(res)
    a, s = fc["actual"], fc["siguiente"]
    cp = contexto_precio(diario)
    hoy = res["hoy"]
    dias_sig = (s["inicio"] - hoy).days if s else None
    f = lambda x: f"{x.day} {['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'][x.month-1]}"
    dd, cerca_max = cp["dd"], cp["dd"] > -5
    tipo = a["tipo"] if a else ("EMISION" if s is None or s["tipo"] == "PAY DOWN" else "PAY DOWN")

    # Estrategia: ACUMULAR durante el settlement (emision neta, precio debil)
    #             VENDER ALTO durante el pay down (liquidez, precio fuerte)
    if tipo == "EMISION":
        if s and s["tipo"] == "PAY DOWN" and dias_sig is not None and dias_sig <= 10:
            acc = "ACUMULAR (ÚLTIMAS COMPRAS)"
            txt = (f"Quedan ~{dias_sig} días de settlement: el pay down empieza el {f(s['inicio'])}. "
                   "Son los últimos días para comprar antes de que entre la liquidez.")
        elif cerca_max:
            acc = "ESPERAR RETROCESO"
            txt = (f"Estamos en settlement pero el precio sigue cerca del máximo ({dd:+.0f} %). "
                   "No comprar arriba: esperar a que corrija para acumular.")
        elif dd <= -10:
            acc = "ACUMULAR"
            txt = (f"Settlement con el precio {abs(dd):.0f} % bajo su máximo de 3 meses: zona de acumulación. "
                   "Comprar por partes (DCA)"
                   + (f" antes del pay down del {f(s['inicio'])}." if s and s["tipo"] == "PAY DOWN" else "."))
        else:
            acc = "ACUMULAR DE A POCO"
            txt = ("Settlement con caída moderada. Ir comprando por partes y guardar liquidez por si cae más"
                   + (f"; el pay down llega el {f(s['inicio'])}." if s and s["tipo"] == "PAY DOWN" else "."))
    else:  # PAY DOWN
        if s and s["tipo"] == "EMISION" and dias_sig is not None and dias_sig <= 7:
            acc = "VENDER / TOMAR GANANCIAS"
            txt = (f"El pay down termina pronto: el settlement vuelve el {f(s['inicio'])}. "
                   "Vender en fuerza y cerrar largos antes de que se retire la liquidez.")
        elif cerca_max:
            acc = "VENDER ALTO (PARCIAL)"
            txt = (f"Pay down con el precio cerca del máximo ({dd:+.0f} %). Buen momento para vender una parte "
                   "y asegurar ganancias" + (f"; la fase dura hasta el {f(a['fin'])}." if a else "."))
        else:
            acc = "HOLD"
            txt = ("Pay down en curso (liquidez a favor). Mantener y dejar correr; vender en máximos"
                   + (f" antes del {f(a['fin'])}." if a else "."))
    return {"activo": activo, "accion": acc, "texto": txt, "fase": tipo, "contexto": cp, "fases": fc}


# ----------------------------------------------------------------------------
# Datos simulados (solo para probar sin internet: TESORO_MOCK=1)
# ----------------------------------------------------------------------------
def _mock_subastas(desde: str) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    hoy = pd.Timestamp(dt.date.today())
    plazos = {"4-Week": (1, 85e9), "6-Week": (3, 80e9), "8-Week": (1, 80e9), "13-Week": (0, 85e9),
              "17-Week": (3, 65e9), "26-Week": (0, 75e9)}
    filas = []
    lunes = pd.Timestamp(desde) - pd.Timedelta(days=pd.Timestamp(desde).weekday())
    k = 0
    while lunes <= hoy + pd.Timedelta(days=8):
        estacional = 1 + 0.12 * np.sin(2 * np.pi * (lunes.dayofyear / 91.0)) + rng.normal(0, 0.03)
        for plazo, (dow, base) in plazos.items():
            liq = lunes + pd.Timedelta(days=dow)
            anun = liq > hoy
            monto = round(base * estacional / 1e9) * 1e9
            filas.append({"cusip": f"MOCK{k:05d}", "plazo": plazo, "cmb": False,
                          "fecha_subasta": liq - pd.Timedelta(days=2), "liquidacion": liq,
                          "vencimiento": liq + pd.Timedelta(days=plazo_en_dias(plazo)),
                          "monto": monto, "estado": "Anunciado" if anun else "Subastado"})
            k += 1
        if (k // 6) % 4 == 0:
            liq = lunes + pd.Timedelta(days=1)
            filas.append({"cusip": f"MOCK{k:05d}", "plazo": "52-Week", "cmb": False,
                          "fecha_subasta": liq - pd.Timedelta(days=2), "liquidacion": liq,
                          "vencimiento": liq + pd.Timedelta(days=364), "monto": 48e9,
                          "estado": "Anunciado" if liq > hoy else "Subastado"})
        lunes += pd.Timedelta(weeks=1)
    return pd.DataFrame(filas)


def _mock_precios(desde: str, base: float = 24000) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    idx = pd.bdate_range(pd.Timestamp(desde), pd.Timestamp(dt.date.today()))
    c = base * np.exp(np.cumsum(rng.normal(0.0006, 0.012, len(idx))))
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * (1 + rng.uniform(0, 0.015, len(idx)))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.015, len(idx)))
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c}, index=idx)


if __name__ == "__main__":
    res = calcular()
    s = senal(res)
    pd.set_option("display.width", 160)
    print("\nFASES (neto en miles de millones USD)")
    f = res["fases"].copy()
    f["neto"] = (f["neto"] / 1e9).round(1)
    print(f.to_string(index=False))
    print("\nPROXIMAS SEMANAS")
    p = res["semanal"][res["semanal"].index >= fin_de_semana(res["hoy"])].copy()
    print((p[["emision", "vencimientos", "neto"]] / 1e9).round(1).join(p["dato"]).to_string())
    print(f"\nSemaforo: {s['color'].upper()} | neto proximas {s['semanas']} semanas: {s['neto_prox']/1e9:+.1f} mil M USD")
