"""
alerta.py - Avisa por Telegram cuando viene o cambia una fase del Tesoro.

Solo manda mensaje si hay algo nuevo:
  - empezo una fase (pay down o emision),
  - aparece una fase nueva en la proyeccion, o se mueve su fecha de inicio,
  - una fase esperada se cancela.

Uso:
  python alerta.py             -> revisa y avisa solo si hay cambio
  python alerta.py --prueba    -> manda el estado actual aunque no haya cambio
  python alerta.py --lectura   -> manda ya la lectura de mercado (normalmente va cada 3 dias)
  python alerta.py --chat-id   -> muestra tu chat_id (escribele algo al bot primero)

Variables de entorno: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, APP_URL (opcional)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import requests

import tesoro_api as api

ESTADO = Path(__file__).with_name("estado.json")
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fecha(d) -> str:
    d = pd.Timestamp(d)
    return f"{d.day} {MESES[d.month - 1]}"


def lunes(inicio) -> pd.Timestamp:
    """Las fases empiezan en la semana que arranca el lunes siguiente a 'inicio' (sabado)."""
    return pd.Timestamp(inicio) + pd.Timedelta(days=2)


def leer_fases(res: dict) -> dict:
    fz = res["fases"]
    hoy = res["hoy"]
    ultima_sem = res["semanal"].index.max()
    actual = fz[(fz["inicio"] <= hoy) & (fz["fin"] >= hoy)]
    futuras = fz[fz["inicio"] > hoy]
    # descartar "fases" de 1 semana en medio de la proyeccion (ruido)
    futuras = futuras[(futuras["semanas"] >= 2) | (futuras["fin"] >= ultima_sem)]
    a = actual.iloc[0] if not actual.empty else None
    f = futuras.iloc[0] if not futuras.empty else None
    s = api.senal(res)
    return {
        "hoy": hoy,
        "actual": None if a is None else {"tipo": a["tipo"], "inicio": str(lunes(a["inicio"]).date()),
                                          "fin": str(a["fin"].date()), "semanas": int(a["semanas"])},
        "siguiente": None if f is None else {"tipo": f["tipo"], "inicio": str(lunes(f["inicio"]).date()),
                                             "fin": str(f["fin"].date()), "semanas": int(f["semanas"]),
                                             "hasta_horizonte": bool(f["fin"] >= ultima_sem)},
        "neto_4s": s["neto_prox"],
    }


def accion(tipo: str) -> str:
    return "AMPLIAR riesgo" if tipo == "PAY DOWN" else "REDUCIR riesgo"


def icono(tipo: str) -> str:
    return "🟢" if tipo == "PAY DOWN" else "🔴"


def nombre(tipo: str) -> str:
    return "PAY DOWN" if tipo == "PAY DOWN" else "EMISIÓN NETA (settlement)"


def duracion(f: dict) -> str:
    dias = f["semanas"] * 7
    if f.get("hasta_horizonte"):
        return f"al menos {dias} días (hasta donde llega la proyección)"
    return f"unos {dias} días (hasta el {fecha(f['fin'])})"


def restante(f: dict, hoy) -> str:
    dias = max((pd.Timestamp(f["fin"]) - pd.Timestamp(hoy)).days, 0)
    return f"unos {dias} días más (hasta el {fecha(f['fin'])})"


def mensajes(antes: dict, ahora: dict, forzar: bool = False) -> list[str]:
    out = []
    a0, a1 = (antes or {}).get("actual"), ahora["actual"]
    s0, s1 = (antes or {}).get("siguiente"), ahora["siguiente"]

    if antes is None:  # primera vez: solo el resumen del estado actual
        forzar, a0, s0 = True, a1, s1

    # 1) Empezo una fase nueva
    if a1 and (not a0 or a0["tipo"] != a1["tipo"]):
        out.append(f"{icono(a1['tipo'])} <b>Empezó {nombre(a1['tipo'])}</b> (semana del {fecha(a1['inicio'])}).\n"
                   f"Se espera que siga {restante(a1, ahora['hoy'])}.\n👉 Considera <b>{accion(a1['tipo'])}</b>.")

    # 2) Viene una fase / cambio de fecha / cancelacion
    if s1 and (not s0 or s0["tipo"] != s1["tipo"] or abs((pd.Timestamp(s0["inicio"]) - pd.Timestamp(s1["inicio"])).days) >= 7):
        cambio = " (cambió la fecha)" if s0 and s0["tipo"] == s1["tipo"] else ""
        out.append(f"{icono(s1['tipo'])} <b>Viene {nombre(s1['tipo'])}</b> desde la semana del "
                   f"<b>{fecha(s1['inicio'])}</b>{cambio}.\nSe espera que dure {duracion(s1)}.\n"
                   f"👉 Cuando empiece: considera <b>{accion(s1['tipo'])}</b>.")
    elif s0 and not s1 and not (a1 and a1["tipo"] == s0["tipo"]):
        out.append(f"⚪ Ya <b>no se espera</b> el {nombre(s0['tipo'])} que venía el {fecha(s0['inicio'])}. "
                   f"El Tesoro cambió sus tamaños de subasta.")

    if forzar and not out:
        t = []
        if a1:
            t.append(f"{icono(a1['tipo'])} Ahora estamos en <b>{nombre(a1['tipo'])}</b> (desde el {fecha(a1['inicio'])}), "
                     f"sigue {restante(a1, ahora['hoy'])}.\n👉 Considera <b>{accion(a1['tipo'])}</b>.")
        if s1:
            t.append(f"Próxima: {icono(s1['tipo'])} <b>{nombre(s1['tipo'])}</b> desde la semana del "
                     f"<b>{fecha(s1['inicio'])}</b>, durando {duracion(s1)} → ahí considera <b>{accion(s1['tipo'])}</b>.")
        else:
            t.append("No se ve cambio de fase en las próximas 8 semanas.")
        out.append("\n".join(t))
    return out


def precio_txt(activo: str, p: float) -> str:
    return f"${p:,.0f}".replace(",", ".") if activo == "Bitcoin" else f"{p:,.0f} pts".replace(",", ".")


def mensaje_lectura(res: dict) -> str:
    fc = api.fases_clave(res)
    a, s = fc["actual"], fc["siguiente"]
    l = [f"📊 <b>Lectura del mercado · {fecha(res['hoy'])}</b>"]
    if a:
        l.append(f"Fase actual: {icono(a['tipo'])} <b>{nombre(a['tipo'])}</b> desde el {fecha(a['inicio'])}, "
                 f"sigue hasta ~{fecha(a['fin'])}.")
    if s:
        l.append(f"Próxima: {icono(s['tipo'])} <b>{nombre(s['tipo'])}</b> desde el {fecha(s['inicio'])} "
                 f"(~{s['semanas'] * 7} días).")
    else:
        l.append("No se ve cambio de fase en las próximas 8 semanas.")
    desde = (res["hoy"] - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
    for activo, simb in [("Bitcoin", "₿"), ("Nasdaq 100", "📈")]:
        try:
            d = api.precios_diarios(activo, desde)
            x = api.lectura(res, d, activo)
            c = x["contexto"]
            l.append(f"\n{simb} <b>{activo}</b>: {precio_txt(activo, c['precio'])} "
                     f"({c['dd']:+.0f} % del máximo de 3 meses, tendencia {'alcista' if c['alcista'] else 'bajista'})\n"
                     f"👉 <b>{x['accion']}</b>. {x['texto']}")
        except Exception as e:  # noqa: BLE001
            l.append(f"\n{simb} {activo}: no pude descargar el precio ({e.__class__.__name__}).")
    l.append("\n<i>Lectura automática según las fases del Tesoro. No es asesoría financiera.</i>")
    return "\n".join(l)


def enviar(texto: str) -> None:
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    url_app = os.environ.get("APP_URL")
    if url_app:
        texto += f"\n\n📊 <a href=\"{url_app}\">Ver gráfica</a>"
    if not token or not chat:
        print("[sin TELEGRAM_TOKEN/TELEGRAM_CHAT_ID] Mensaje:\n" + texto)
        return
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": texto, "parse_mode": "HTML",
                            "disable_web_page_preview": "true"}, timeout=30)
    r.raise_for_status()


def mostrar_chat_id() -> None:
    token = os.environ.get("TELEGRAM_TOKEN") or input("Pega el token del bot: ").strip()
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
    chats = {(u.get("message") or {}).get("chat", {}).get("id") for u in r.get("result", [])} - {None}
    print("Tu chat_id:", ", ".join(map(str, chats)) if chats else "ninguno (escríbele /start a tu bot y repite)")


def main() -> None:
    if "--chat-id" in sys.argv:
        return mostrar_chat_id()
    forzar = "--prueba" in sys.argv
    antes = json.loads(ESTADO.read_text()) if ESTADO.exists() else None
    res = api.calcular_tesoro(horizonte=8)
    ahora = leer_fases(res)
    textos = mensajes(antes, ahora, forzar=forzar)
    if antes is None and textos:
        textos[0] = ("✅ <b>Alertas del Tesoro activadas.</b> Te escribo cuando venga o cambie una fase, "
                     "y cada 3 días una lectura del mercado.\n\n" + textos[0])

    # Lectura de mercado cada 3 dias (o con --lectura / --prueba)
    ultima = (antes or {}).get("ultima_lectura")
    toca = ultima is None or (res["hoy"] - pd.Timestamp(ultima)).days >= 3
    if toca or "--lectura" in sys.argv or forzar:
        textos.append(mensaje_lectura(res))
        ultima = str(res["hoy"].date())

    for t in textos:
        enviar(t)
    estado = {"actual": ahora["actual"], "siguiente": ahora["siguiente"], "ultima_lectura": ultima,
              "revisado": str(pd.Timestamp.now(tz="America/Bogota").floor("min"))}
    ESTADO.write_text(json.dumps(estado, indent=2, ensure_ascii=False))
    print(f"Revisado. Mensajes enviados: {len(textos)}")


if __name__ == "__main__":
    main()
