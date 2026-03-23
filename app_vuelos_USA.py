import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import math
from datetime import datetime, timedelta, timezone
from FlightRadar24 import FlightRadar24API

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="FLIGHTWX PRO | Ops Center", page_icon="✈️", layout="wide")

# --- CSS AVANZADO (DISEÑO PROFESIONAL) ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&family=Inter:wght@300;400;600;700&display=swap');

.stApp {
    background-color: #0f172a;
    color: #f1f5f9;
    font-family: 'Inter', sans-serif;
}

header {
    visibility: hidden;
}

section[data-testid="stSidebar"] {
    background-color: #1e293b !important;
    border-right: 1px solid #334155;
    width: 300px !important;
}

.top-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 2rem;
}

.zulu-clock {
    display: flex;
    align-items: center;
    gap: 10px;
    font-family: 'JetBrains Mono', monospace;
}

.clock-box {
    background: #1e293b;
    padding: 5px 12px;
    border-radius: 6px;
    border: 1px solid #3b82f6;
    font-size: 1.2rem;
    color: #3b82f6;
    box-shadow: 0 0 15px rgba(59, 130, 246, 0.2);
}

.metric-container {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 20px;
    margin-bottom: 25px;
}

.metric-card {
    background: linear-gradient(135deg, rgba(30, 41, 59, 0.6) 0%, rgba(15, 23, 42, 0.8) 100%);
    border: 1px solid rgba(255, 255, 255, 0.05);
    padding: 20px;
    border-radius: 16px;
}

.metric-label {
    color: #94a3b8;
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
}

.metric-value {
    font-size: 2rem;
    font-weight: 700;
    margin-top: 5px;
    font-family: 'JetBrains Mono', monospace;
    color: #3b82f6;
}

.radar-frame {
    border: 1px solid #334155;
    border-radius: 16px;
    overflow: hidden;
    background: #020617;
}

.table-header {
    color: #3b82f6;
    font-weight: 700;
    font-size: 1.1rem;
    margin: 20px 0 10px 0;
}
</style>
""", unsafe_allow_html=True)

# --- DATOS Y LÓGICA DE NEGOCIO ---
AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}

def calcular_distancia_nm(lat1, lon1, lat2, lon2):
    R = 3440.065
    dLat, dLon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

@st.cache_data(ttl=3600)
def obtener_predicciones_globales(iatas):
    dicc_global = {}
    for apt in iatas:
        params = {"latitude": AEROPUERTOS[apt]["coords"][0], "longitude": AEROPUERTOS[apt]["coords"][1], "hourly": "wind_speed_10m", "wind_speed_unit": "kmh", "timezone": "UTC"}
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", params=params).json()
            dicc_global[apt] = {r["hourly"]["time"][i]: r["hourly"]["wind_speed_10m"][i] for i in range(len(r["hourly"]["time"]))}
        except:
            dicc_global[apt] = {}
    return dicc_global

def evaluar_riesgo(hora_dt, dicc_vientos_apt):
    if not dicc_vientos_apt: return 0, "UNKNOWN", "gray", "⚪"
    clave = (hora_dt.replace(minute=0, second=0, microsecond=0)).strftime("%Y-%m-%dT%H:00")
    viento = dicc_vientos_apt.get(clave, 0)
    if viento < 15: return round(viento,1), "BAJA", "#10b981", "🟢"
    elif 15 <= viento <= 35: return round(viento,1), "MODERADA", "#f59e0b", "🟠"
    else: return round(viento,1), "ALTA", "#ef4444", "🔴"

@st.cache_data(ttl=60)
def obtener_datos_vuelos_reales(iatas):
    fr_api = FlightRadar24API()
    v_aire, arr, dep = [], [], []
    try:
        todos = fr_api.get_flights()
        for v in todos:
            if v.ground_speed > 0:
                for apt in iatas:
                    if calcular_distancia_nm(v.latitude, v.longitude, AEROPUERTOS[apt]["coords"][0], AEROPUERTOS[apt]["coords"][1]) < 300:
                        v_aire.append(v)
                        break
    except:
        pass
        
    for apt in iatas:
        try:
            det = fr_api.get_airport_details(apt)['airport']['pluginData']['schedule']
            for v in det['arrivals']['data']:
                v['target_apt'] = apt
                arr.append(v)
            for v in det['departures']['data']:
                v['target_apt'] = apt
                dep.append(v)
        except:
            pass
            
    return v_aire, arr, dep

# --- SIDEBAR (CONTROLES) ---
with st.sidebar:
    st.markdown("<h2 style='color:#3b82f6; margin-bottom:0;'>AVIATOR'S LENS</h2>", unsafe_allow_html=True)
    st.divider()
    
    apt_sel = st.selectbox("PRIMARY STATION", ["TODOS", "ATL", "ORD", "LAX", "JFK"])
    h_pred = st.slider("FORECAST WINDOW (H)", 1, 24, 12)
    
    st.markdown("### RISK FILTERS")
    f_baja = st.checkbox("🟢 VFR (Low)", value=True)
    f_mod = st.checkbox("🟠 MVFR (Moderate)", value=True)
    f_alt = st.checkbox("🔴 IFR (Critical)", value=True)
    
    if st.button("🔄 REFRESH SYSTEM", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# --- PROCESAMIENTO ---
filtros = []
if f_baja: filtros.append("BAJA")
if f_mod: filtros.append("MODERADA")
if f_alt: filtros.append("ALTA")

iatas_work = list(AEROPUERTOS.keys()) if apt_sel == "TODOS" else [apt_sel]
dicc_meteo = obtener_predicciones_globales(iatas_work)

with st.spinner('UPLINKING REAL-TIME DATA...'):
    v_aire, llegadas, salidas = obtener_datos_vuelos_reales(iatas_work)

# --- UI PRINCIPAL ---
zulu_now = datetime.now(timezone.utc).strftime("%H:%M")
st.markdown(f"""
<div class="top-bar">
    <div style="color:#64748b;">System / <b>{apt_sel if apt_sel != 'TODOS' else 'Global Ops'}</b></div>
    <div class="zulu-clock"><div class="clock-box">{zulu_now} ZULU</div></div>
</div>
""", unsafe_allow_html=True)

# Métricas Reales
c1, c2, c3, c4 = st.columns(4)

with c1: st.markdown(f'<div class="metric-card"><div class="metric-label">Inbound Traffic</div><div class="metric-value">{len(v_aire)}</div></div>', unsafe_allow_html=True)
with c2: st.markdown(f'<div class="metric-card"><div class="metric-label">Expected Arrivals</div><div class="metric-value">{len(llegadas)}</div></div>', unsafe_allow_html=True)
with c3: st.markdown(f'<div class="metric-card"><div class="metric-label">Scheduled Departures</div><div class="metric-value">{len(salidas)}</div></div>', unsafe_allow_html=True)
with c4:
    val_wind = "N/A"
    if apt_sel != "TODOS":
        v, _, _, _ = evaluar_riesgo(datetime.now(timezone.utc), dicc_meteo[apt_sel])
        val_wind = f"{v} KM/H"
    st.markdown(f'<div class="metric-card"><div class="metric-label">Station Wind</div><div class="metric-value">{val_wind}</div></div>', unsafe_allow_html=True)

# Mapa Radar Real
st.markdown('<div class="radar-frame">', unsafe_allow_html=True)
m = folium.Map(location=[39.5, -98] if apt_sel=="TODOS" else AEROPUERTOS[apt_sel]["coords"], zoom_start=4 if apt_sel=="TODOS" else 6, tiles="CartoDB dark_matter")

for k in iatas_work:
    folium.CircleMarker(AEROPUERTOS[k]["coords"], radius=8, color="#3b82f6", fill=True).add_to(m)
    
for v in v_aire:
    dest = str(v.destination_airport_iata).upper()
    if dest in iatas_work:
        dist = calcular_distancia_nm(v.latitude, v.longitude, AEROPUERTOS[dest]["coords"][0], AEROPUERTOS[dest]["coords"][1])
        eta = datetime.now(timezone.utc) + timedelta(hours=dist/max(v.ground_speed,1))
        _, prob, color, _ = evaluar_riesgo(eta, dicc_meteo.get(dest))
        
        if prob in filtros:
            folium.Marker(
                [v.latitude, v.longitude], 
                icon=folium.Icon(color="blue" if color=="#10b981" else "orange" if color=="#f59e0b" else "red", icon="plane", prefix="fa"), 
                tooltip=f"FLIGHT: {v.callsign} | DEST: {dest}"
            ).add_to(m)
            
st_folium(m, width="100%", height=500, key="radar_map")
st.markdown('</div>', unsafe_allow_html=True)

# Tablas Reales Filtradas
col_in, col_out = st.columns(2)

with col_in:
    st.markdown('<div class="table-header">🛬 LIVE INBOUND FEED</div>', unsafe_allow_html=True)
    res_in = []
    for v in llegadas:
        try:
            h = datetime.fromtimestamp(v['flight']['time']['scheduled']['arrival'], timezone.utc)
            if datetime.now(timezone.utc) <= h <= datetime.now(timezone.utc) + timedelta(hours=h_pred):
                viento, prob, _, icono = evaluar_riesgo(h, dicc_meteo[v['target_apt']])
                if prob in filtros:
                    res_in.append({
                        "TIME (Z)": h.strftime('%H:%M'), 
                        "FLIGHT": v['flight']['identification']['number']['default'], 
                        "DEST": v['target_apt'], 
                        "RISK": f"{icono} {prob}", 
                        "WIND": viento
                    })
        except:
            pass
    st.dataframe(pd.DataFrame(res_in), use_container_width=True, hide_index=True) if res_in else st.info("No active inbound matches.")

with col_out:
    st.markdown('<div class="table-header">🛫 SCHEDULED OUTBOUND</div>', unsafe_allow_html=True)
    res_out = []
    for v in salidas:
        try:
            h = datetime.fromtimestamp(v['flight']['time']['scheduled']['departure'], timezone.utc)
            if datetime.now(timezone.utc) <= h <= datetime.now(timezone.utc) + timedelta(hours=h_pred):
                viento, prob, _, icono = evaluar_riesgo(h, dicc_meteo[v['target_apt']])
                if prob in filtros:
                    res_out.append({
                        "TIME (Z)": h.strftime('%H:%M'), 
                        "FLIGHT": v['flight']['identification']['number']['default'], 
                        "ORIG": v['target_apt'], 
                        "RISK": f"{icono} {prob}", 
                        "WIND": viento
                    })
        except:
            pass
    st.dataframe(pd.DataFrame(res_out), use_container_width=True, hide_index=True) if res_out else st.info("No active outbound matches.")
