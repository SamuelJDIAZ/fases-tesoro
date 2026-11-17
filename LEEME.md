# Fases del Tesoro · T-Bills vs Nasdaq 100 / Bitcoin

App que muestra las fases de **emisión neta** (rojo) y **pay down** (verde) de T-Bills del Tesoro
de EE. UU. sobre las velas del Nasdaq 100 o Bitcoin (diario o semanal). También proyecta las
próximas semanas y da un semáforo de riesgo. Un bot de Telegram avisa cuando viene o cambia una fase.

## Archivos
| Archivo | Qué es |
|---|---|
| `app.py` | La app web (Streamlit) |
| `tesoro_api.py` | La "API fácil": descarga y calcula todo |
| `alerta.py` | El bot de Telegram (solo avisa si hay cambio) |
| `.github/workflows/alerta.yml` | Corre la alerta sola en la nube, lunes a viernes 12:30 pm (Bogotá) |
| `iniciar.bat` | Abre la app en tu PC con doble clic |

## Datos (se actualizan solos)
- **TreasuryDirect** (gratis, sin clave): subastas de bills, anunciadas el mismo día.
- **Yahoo Finance** (respaldo Stooq): precios de ^NDX y BTC-USD.
- **TradingView**: gráfica en vivo dentro de la app.

## Cómo ve el futuro
- Vencimientos: se conocen exactos.
- Lo anunciado por el Tesoro: se usa tal cual.
- Lo no anunciado: último tamaño de cada plazo × el cambio que hizo el Tesoro esas mismas semanas
  del año pasado (copia sola los recortes estacionales, como los de diciembre).

## Bot de Telegram (una sola vez)
1. En Telegram, habla con **@BotFather** → `/newbot` → copia el **token**.
2. Escríbele `/start` a tu bot nuevo.
3. Saca tu chat_id: `python alerta.py --chat-id` (te pide el token).
4. En GitHub → tu repo → Settings → Secrets and variables → Actions:
   - Secret `TELEGRAM_TOKEN` = el token
   - Secret `TELEGRAM_CHAT_ID` = tu chat_id
   - Variable `APP_URL` = el link de la app en Streamlit (opcional)
5. Pestaña Actions → "Alerta Tesoro" → Run workflow (marca "prueba") para recibir el primer mensaje.

Es una comparación histórica, no prueba causalidad ni es recomendación de inversión.
