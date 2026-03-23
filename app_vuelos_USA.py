import math
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple, Any, Optional

import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
from FlightRadar24 import FlightRadar24API

# --- 1. CONSTANTES Y CONFIGURACIÓN ---
st.set_page_config(page_title="FLIGHTWX PRO | Ops Center", page_icon="✈️", layout="wide")

AEROPUERTOS: Dict[str, Dict[str, Any]] = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}

API_METEO_URL = "https://api.open-meteo.com/v1/forecast"
RADIO_TIERRA_NM = 3440.065

# --- 2. ESTILOS AVANZADOS (CSS PROFESIONAL) ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&family=Inter:wght@300;400;600;700&display=swap');

.stApp { background-color: #0f172a; color: #f1f5f9; font-family: 'Inter', sans-serif; }
header { visibility: hidden; }
section[data-testid="stSidebar"] { background-color: #1e293b !important; border-right: 1px solid #334155; width: 340px !important; }
.top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; }
.zulu-clock { display: flex; align-items: center; gap: 10px; font-family: 'JetBrains Mono', monospace; }
.clock-box { background: #1e293b; padding: 5px 12px; border-radius: 6px; border: 1px solid #3b82f6; font-size: 1.2rem; color: #3b82f6; box-shadow: 0 0 15px rgba(59, 130, 246, 0.2); }
.metric-card { background: linear-gradient(135deg, rgba(30, 41, 59, 0.6) 0%, rgba(15, 23, 42, 0.8) 100%); border: 1px solid rgba(255, 255, 255, 0.05); padding: 20px; border-radius: 16px; margin-bottom: 1rem; }
.metric-label { color: #94a3b8; font-size: 0.8rem; font-weight: 600; text-transform: uppercase; }
.metric-value { font-size: 2rem; font-weight: 700; margin-top: 5px; font-family: 'JetBrains Mono', monospace; color: #3b82f6; }
.radar-frame { border: 1px solid #334155; border-radius: 16px; overflow: hidden; background: #020617; }
.table-header { color: #3b82f6; font-weight: 700; font-size: 1.1rem; margin: 20px 0 10px 0; border-bottom: 1px solid #334155; padding-bottom: 10px;}
</style>
""", unsafe_allow_html=True)

# --- 3. LÓGICA DE NEGOCIO SEGURA ---
def calcular_distancia_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dLat, dLon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2)**2
    return RADIO_TIERRA_NM * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

@st.cache_data(ttl=3600) 
def obtener_predicciones_globales(iatas: List[str]) -> Dict[str, Dict[str, float]]:
    dicc_global = {}
    for apt in iatas:
        parametros = {
            "latitude": AEROPUERTOS[apt]["coords"][0], "longitude": AEROPUERTOS[apt]["coords"][1],
            "hourly": "wind_speed_10m", "wind_speed_unit": "kmh", "timezone": "UTC"
        }
        try:
            datos = requests.get(API_METEO_URL, params=parametros, timeout=10).json()
            dicc_global[apt] = {datos["hourly"]["time"][i]: datos["hourly"]["wind_speed_10m"][i] for i in range(len(datos["hourly"]["time"]))}
        except Exception:
            dicc_global[apt] = {}
    return dicc_global

def evaluar_probabilidad_cancelacion(hora_dt: datetime, dicc_vientos_apt: Dict[str, float]) -> Tuple[Any, str, str, str]:
    if not dicc_vientos_apt: return "?", "Desconocida", "gray", "⚪ Desconocida"
    hora_redondeada = hora_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1 if hora_dt.minute >= 30 else 0)
    viento_kmh = dicc_vientos_apt.get(hora_redondeada.strftime("%Y-%m-%dT%H:00"))
    
    if viento_kmh is None: return "?", "Sin Datos", "gray", "⚪ Sin Datos"
    viento_kmh = round(viento_kmh, 1)
    
    if viento_kmh < 15: return viento_kmh, "BAJA", "#10b981", "🟢 VFR"
    elif 15 <= viento_kmh <= 35: return viento_kmh, "MODERADA", "#f59e0b", "🟠 MVFR"
    else: return viento_kmh, "ALTA", "#ef4444", "🔴 IFR"

@st.cache_data(ttl=60)
def obtener_datos_vuelos(iatas: List[str]) -> Tuple[List[Any], List[Dict], List[Dict]]:
    fr_api = FlightRadar24API()
    vuelos_aire, llegadas, salidas = [], [], []
    
    try:
        for v in fr_api.get_flights():
            if v.ground_speed > 0 and any(calcular_distancia_nm(v.latitude, v.longitude, AEROPUERTOS[apt]["coords"][0], AEROPUERTOS[apt]["coords"][1]) < 500 for apt in iatas):
                vuelos_aire.append(v)
    except Exception: pass

    for apt in iatas:
        try:
            detalles = fr_api.get_airport_details(apt)
            # Extracción segura de horarios
            schedule = ((detalles.get('airport') or {}).get('pluginData') or {}).get('schedule') or {}
            arr = (schedule.get('arrivals') or {}).get('data') or []
            dep = (schedule.get('departures') or {}).get('data') or []
            
            for v in arr: v['target_apt'] = apt; llegadas.append(v)
            for v in dep: v['target_apt'] = apt; salidas.append(v)
        except Exception: pass
            
    return vuelos_aire, llegadas, salidas

# --- 4. PANEL DE CONTROL (SIDEBAR) ---
st.sidebar.markdown("<h2 style='color:#3b82f6; margin-bottom:0;'>AVIATOR'S LENS</h2>", unsafe_allow_html=True)
st.sidebar.markdown("<p style='color:#64748b; font-size:0.8rem;'>Sistema de Control Operacional</p>", unsafe_allow_html=True)
st.sidebar.divider()

aeropuerto_destino = st.sidebar.selectbox("📍 ESTACIÓN PRINCIPAL", ["TODOS", "ATL", "ORD", "LAX", "JFK"], index=0)
horas_prediccion = st.sidebar.slider("⏳ VENTANA PREVISIÓN (H)", min_value=1, max_value=24, value=15)

st.sidebar.markdown("### ⚠️ RIESGO METEOROLÓGICO")
mostrar_baja = st.sidebar.checkbox("🟢 VFR (Riesgo Bajo)", value=True)
mostrar_moderada = st.sidebar.checkbox("🟠 MVFR (Riesgo Moderado)", value=True)
mostrar_alta = st.sidebar.checkbox("🔴 IFR (Riesgo Crítico)", value=True)

filtros_activos = []
if mostrar_baja: filtros_activos.append("BAJA")
if mostrar_moderada: filtros_activos.append("MODERADA")
if mostrar_alta: filtros_activos.append("ALTA")

if st.sidebar.button("🔄 INICIALIZAR TELEMETRÍA", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

# --- CARGA DE DATOS ---
lista_iatas = list(AEROPUERTOS.keys()) if aeropuerto_destino == "TODOS" else [aeropuerto_destino]
nombre_mostrar = "OPERACIONES GLOBALES (US)" if aeropuerto_destino == "TODOS" else f"ESTACIÓN: {AEROPUERTOS[aeropuerto_destino]['nombre']}"

dicc_meteo_global = obtener_predicciones_globales(lista_iatas)

with st.spinner('ESTABLECIENDO ENLACE DE DATOS EN VIVO...'):
    vuelos_aire, llegadas, salidas = obtener_datos_vuelos(lista_iatas)

# --- 5. FILTROS AVANZADOS DINÁMICOS ---
aerolineas_unicas, aeropuertos_unicos = set(), set()
for v in llegadas + salidas:
    flight_data = v.get('flight') or {}
    
    # Aerolínea segura
    al_data = flight_data.get('airline') or {}
    nombre_al = al_data.get('name')
    if nombre_al: aerolineas_unicas.add(nombre_al)
    
    # Aeropuertos seguros
    apt_data = flight_data.get('airport') or {}
    orig_iata = ((apt_data.get('origin') or {}).get('code') or {}).get('iata')
    dest_iata = ((apt_data.get('destination') or {}).get('code') or {}).get('iata')
    
    if orig_iata: aeropuertos_unicos.add(orig_iata)
    if dest_iata: aeropuertos_unicos.add(dest_iata)

st.sidebar.divider()
st.sidebar.markdown("### 🔎 FILTROS AVANZADOS")
aerolineas_sel = st.sidebar.multiselect("✈️ Aerolínea", sorted(list(aerolineas_unicas)))
aeropuertos_sel = st.sidebar.multiselect("📍 Aeropuerto Secundario", sorted(list(aeropuertos_unicos)))

# --- 6. PROCESAMIENTO DE TABLAS ---
hora_actual = datetime.now(timezone.utc)
limite_tiempo = hora_actual + timedelta(hours=horas_prediccion)

datos_llegadas_raw, datos_salidas_raw = [], []

for vuelo in llegadas:
    flight_data = vuelo.get('flight') or {}
    timestamp = ((flight_data.get('time') or {}).get('scheduled') or {}).get('arrival')
    
    if timestamp:
        hora_vuelo = datetime.fromtimestamp(timestamp, timezone.utc)
        if hora_actual <= hora_vuelo <= limite_tiempo:
            target_apt = vuelo.get('target_apt', 'N/A')
            viento, prob, _, icono = evaluar_probabilidad_cancelacion(hora_vuelo, dicc_meteo_global.get(target_apt, {}))
            
            aerolinea = (flight_data.get('airline') or {}).get('name') or "N/A"
            origen = ((flight_data.get('airport') or {}).get('origin') or {}).get('code', {}).get('iata', 'N/A')
            
            # Aplicar filtros
            if prob in filtros_activos:
                if (not aerolineas_sel or aerolinea in aerolineas_sel) and (not aeropuertos_sel or origen in aeropuertos_sel or target_apt in aeropuertos_sel):
                    datos_llegadas_raw.append({
                        "Hora (UTC)": hora_vuelo.strftime('%H:%M'), 
                        "Vuelo": (flight_data.get('identification') or {}).get('number', {}).get('default', 'N/A'),
                        "Origen": origen, "Destino": target_apt, "Aerolínea": aerolinea, "Viento": viento, "Estado": icono
                    })

for vuelo in salidas:
    flight_data = vuelo.get('flight') or {}
    timestamp = ((flight_data.get('time') or {}).get('scheduled') or {}).get('departure')
    
    if timestamp:
        hora_vuelo = datetime.fromtimestamp(timestamp, timezone.utc)
        if hora_actual <= hora_vuelo <= limite_tiempo:
            target_apt = vuelo.get('target_apt', 'N/A')
            viento, prob, _, icono = evaluar_probabilidad_cancelacion(hora_vuelo, dicc_meteo_global.get(target_apt, {}))
            
            aerolinea = (flight_data.get('airline') or {}).get('name') or "N/A"
            destino = ((flight_data.get('airport') or {}).get('destination') or {}).get('code', {}).get('iata', 'N/A')
            
            # Aplicar filtros
            if prob in filtros_activos:
                if (not aerolineas_sel or aerolinea in aerolineas_sel) and (not aeropuertos_sel or destino in aeropuertos_sel or target_apt in aeropuertos_sel):
                    datos_salidas_raw.append({
                        "Hora (UTC)": hora_vuelo.strftime('%H:%M'), 
                        "Vuelo": (flight_data.get('identification') or {}).get('number', {}).get('default', 'N/A'),
                        "Origen": target_apt, "Destino": destino, "Aerolínea": aerolinea, "Viento": viento, "Estado": icono
                    })

df_arr = pd.DataFrame(datos_llegadas_raw).sort_values(by="Hora (UTC)") if datos_llegadas_raw else pd.DataFrame()
df_dep = pd.DataFrame(datos_salidas_raw).sort_values(by="Hora (UTC)") if datos_salidas_raw else pd.DataFrame()

# Filtrar mapa por aeropuerto secundario (los aviones en aire no siempre tienen el nombre de la aerolínea exacto)
vuelos_aire_filtrados = [
    v for v in vuelos_aire 
    if not aeropuertos_sel or (getattr(v, 'origin_airport_iata', 'N/A') in aeropuertos_sel or getattr(v, 'destination_airport_iata', 'N/A') in aeropuertos_sel)
]

# --- 7. INTERFAZ PRINCIPAL ---
st.markdown(f"""
<div class="top-bar">
    <div style="color:#64748b;">Sistema / <b>{nombre_mostrar}</b></div>
    <div class="zulu-clock"><div class="clock-box">{hora_actual.strftime("%H:%M")} ZULU</div></div>
</div>
""", unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)
with c1: st.markdown(f'<div class="metric-card"><div class="metric-label">En Aire (Sector)</div><div class="metric-value">{len(vuelos_aire_filtrados)}</div></div>', unsafe_allow_html=True)
with c2: st.markdown(f'<div class="metric-card"><div class="metric-label">Llegadas Filtradas</div><div class="metric-value">{len(df_arr)}</div></div>', unsafe_allow_html=True)
with c3: st.markdown(f'<div class="metric-card"><div class="metric-label">Salidas Filtradas</div><div class="metric-value">{len(df_dep)}</div></div>', unsafe_allow_html=True)
with c4:
    if aeropuerto_destino == "TODOS":
        st.markdown(f'<div class="metric-card"><div class="metric-label">Bases Activas</div><div class="metric-value">{len(lista_iatas)}</div></div>', unsafe_allow_html=True)
    else:
        viento_actual, _, _, _ = evaluar_probabilidad_cancelacion(hora_actual, dicc_meteo_global.get(aeropuerto_destino, {}))
        st.markdown(f'<div class="metric-card"><div class="metric-label">Viento ({aeropuerto_destino})</div><div class="metric-value">{viento_actual} <span style="font-size:1rem; color:#94a3b8;">km/h</span></div></div>', unsafe_allow_html=True)

st.write("")
tab1, tab2, tab3 = st.tabs(["🗺️ RADAR TÁCTICO", "🛬 FEED DE LLEGADAS", "🛫 FEED DE SALIDAS"])

with tab1:
    st.markdown('<div class="radar-frame">', unsafe_allow_html=True)
    mapa = folium.Map(location=[39.5, -98.35] if aeropuerto_destino == "TODOS" else AEROPUERTOS[aeropuerto_destino]["coords"], zoom_start=4 if aeropuerto_destino == "TODOS" else 6, tiles="CartoDB dark_matter")
    for apt in lista_iatas: folium.CircleMarker(AEROPUERTOS[apt]["coords"], radius=8, color="#3b82f6", fill=True).add_to(mapa)
    
    for vuelo in vuelos_aire_filtrados:
        destino = str(getattr(vuelo, 'destination_airport_iata', 'N/A')).upper()
        if destino in lista_iatas:
            dist = calcular_distancia_nm(vuelo.latitude, vuelo.longitude, AEROPUERTOS[destino]["coords"][0], AEROPUERTOS[destino]["coords"][1])
            eta = hora_actual + timedelta(hours=dist / max(vuelo.ground_speed, 1))
            viento, prob, color, _ = evaluar_probabilidad_cancelacion(eta, dicc_meteo_global.get(destino, {}))
            if prob in filtros_activos:
                folium.Marker(
                    [vuelo.latitude, vuelo.longitude],
                    icon=folium.Icon(color="blue" if color=="#10b981" else "orange" if color=="#f59e0b" else "red", icon="plane", prefix="fa", angle=vuelo.heading),
                    tooltip=f"{vuelo.callsign} ➔ {destino} | ETA: {eta.strftime('%H:%M')}Z"
                ).add_to(mapa)

    st_folium(mapa, width="100%", height=550)
    st.markdown('</div>', unsafe_allow_html=True)

with tab2:
    st.markdown('<div class="table-header">🛬 TELEMETRÍA DE LLEGADAS</div>', unsafe_allow_html=True)
    if not df_arr.empty: st.dataframe(df_arr, use_container_width=True, hide_index=True)
    else: st.info("Sin registros de entrada para los parámetros y filtros actuales.")

with tab3:
    st.markdown('<div class="table-header">🛫 TELEMETRÍA DE SALIDAS</div>', unsafe_allow_html=True)
    if not df_dep.empty: st.dataframe(df_dep, use_container_width=True, hide_index=True)
    else: st.info("Sin registros de salida para los parámetros y filtros actuales.")
