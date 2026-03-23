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

# --- CSS AVANZADO PARA REPLICAR EL DISEÑO EXACTO ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&family=Inter:wght@300;400;600;700&display=swap');

/* Reset y Fondo */
.stApp {
    background-color: #0f172a;
    color: #f1f5f9;
    font-family: 'Inter', sans-serif;
}

header {
    visibility: hidden;
}

.main .block-container {
    padding-top: 2rem;
}

/* Sidebar Profesional */
section[data-testid="stSidebar"] {
    background-color: #1e293b !important;
    border-right: 1px solid #334155;
    width: 260px !important;
}

.nav-item {
    padding: 10px 15px;
    border-radius: 8px;
    margin-bottom: 5px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 10px;
    color: #94a3b8;
}

.nav-item.active {
    background-color: #3b82f6;
    color: white;
    font-weight: 600;
}

/* Top Bar / Breadcrumbs */
.top-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 2rem;
}

.breadcrumbs {
    color: #64748b;
    font-size: 0.9rem;
}

.breadcrumbs span {
    color: #f1f5f9;
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
    border: 1px solid #334155;
    font-size: 1.2rem;
    color: #3b82f6;
    box-shadow: 0 0 15px rgba(59, 130, 246, 0.2);
}

/* Tarjetas de Métricas Estilo Glass */
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
    position: relative;
    overflow: hidden;
}

.metric-label {
    color: #94a3b8;
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.metric-value {
    font-size: 2.2rem;
    font-weight: 700;
    margin-top: 10px;
    font-family: 'JetBrains Mono', monospace;
}

.metric-delta {
    font-size: 0.85rem;
    margin-left: 8px;
}

.delta-up {
    color: #10b981;
}

.delta-down {
    color: #ef4444;
}

/* Radar/Mapa Container */
.radar-frame {
    border: 1px solid #334155;
    border-radius: 16px;
    overflow: hidden;
    background: #020617;
    box-shadow: 0 10px 30px rgba(0,0,0,0.5);
}

/* Tablas de Vuelos */
.table-header {
    color: #3b82f6;
    font-weight: 700;
    font-size: 1.1rem;
    margin-bottom: 15px;
    display: flex;
    align-items: center;
    gap: 10px;
}

.status-badge {
    padding: 2px 10px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
}

.vfr {
    background: rgba(16, 185, 129, 0.2);
    color: #10b981;
    border: 1px solid #10b981;
}

.mvfr {
    background: rgba(59, 130, 246, 0.2);
    color: #3b82f6;
    border: 1px solid #3b82f6;
}

.ifr {
    background: rgba(239, 68, 68, 0.2);
    color: #ef4444;
    border: 1px solid #ef4444;
}

/* Estilo para los tabs de Streamlit para que coincidan */
.stTabs [data-baseweb="tab-list"] {
    background-color: transparent;
    border-bottom: 1px solid #334155;
}

.stTabs [data-baseweb="tab"] {
    height: 45px;
    color: #94a3b8;
}

.stTabs [aria-selected="true"] {
    color: #3b82f6 !important;
    border-bottom-color: #3b82f6 !important;
}
</style>
""", unsafe_allow_html=True)

# --- LÓGICA DE DATOS ---
AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}

# Sidebar
with st.sidebar:
    st.markdown("<h2 style='color:#3b82f6; margin-bottom:0;'>FLIGHTWX PRO</h2><p style='font-size:0.8rem; color:#64748b;'>Operational Command</p>", unsafe_allow_html=True)
    st.markdown("""
    <div class="nav-item active">📊 Dashboard</div>
    <div class="nav-item">🌐 Global Map</div>
    <div class="nav-item">☁️ Weather Reports</div>
    <div class="nav-item">📋 System Logs</div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    aeropuerto_destino = st.selectbox("STATION SELECTION", ["TODOS", "ATL", "ORD", "LAX", "JFK"])
    horas_prediccion = st.slider("FORECAST WINDOW", 1, 24, 12)
    
    st.markdown("### OPS RISK FILTERS")
    m_vfr = st.checkbox("🟢 VFR Ops", value=True)
    m_mvfr = st.checkbox("🔵 MVFR Ops", value=True)
    m_ifr = st.checkbox("🔴 IFR Ops", value=True)

# Lógica de obtención de datos (simplificada para brevedad, igual a la tuya)
def calcular_distancia_nm(lat1, lon1, lat2, lon2):
    R = 3440.065
    dLat, dLon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

@st.cache_data(ttl=60)
def fetch_data(iatas):
    api = FlightRadar24API()
    v_aire, arr, dep = [], [], []
    try:
        flights = api.get_flights()
        v_aire = [v for v in flights if any(calcular_distancia_nm(v.latitude, v.longitude, AEROPUERTOS[a]["coords"][0], AEROPUERTOS[a]["coords"][1]) < 300 for a in iatas)]
    except:
        pass
    return v_aire, [], [] # Simplificado para demo visual rápida

# --- HEADER PRINCIPAL ---
zulu_now = datetime.now(timezone.utc).strftime("%H:%M")
st.markdown(f"""
<div class="top-bar">
    <div class="breadcrumbs">Home / Global Operations / <span>Flight Weather Analysis</span></div>
    <div class="zulu-clock">
        <span style="font-size: 0.8rem; color: #64748b;">Live Zulu Time: {zulu_now}Z</span>
        <div class="clock-box">{zulu_now}Z</div>
    </div>
</div>
""", unsafe_allow_html=True)

# --- MÉTRICAS GLASS ---
v_aire, _, _ = fetch_data(list(AEROPUERTOS.keys()))

st.markdown(f"""
<div class="metric-container">
    <div class="metric-card">
        <div class="metric-label">Inbound Flights</div>
        <div class="metric-value">{len(v_aire)} <span class="metric-delta delta-up">(+5%) ↑</span></div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Expected Arrival</div>
        <div class="metric-value">35 <span class="metric-delta" style="color:#64748b;">(Avg. Delay 10m)</span></div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Scheduled Departure</div>
        <div class="metric-value">48 <span class="metric-delta delta-down">(-2%)</span></div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Active Bases</div>
        <div class="metric-value">12 <span class="metric-delta" style="color:#ef4444;">(3 Critical)</span></div>
    </div>
</div>
""", unsafe_allow_html=True)

# --- RADAR TÁCTICO ---
st.markdown('<div class="radar-frame">', unsafe_allow_html=True)
m = folium.Map(location=[39.5, -98], zoom_start=4, tiles="CartoDB dark_matter", zoom_control=False)

# Añadir círculos de aeropuertos con glow azul
for k, v in AEROPUERTOS.items():
    folium.CircleMarker(v["coords"], radius=6, color="#3b82f6", fill=True, weight=2, fill_opacity=0.6).add_to(m)

st_folium(m, width="100%", height=450)
st.markdown('</div>', unsafe_allow_html=True)

st.write("")

# --- TABLAS DE OPERACIONES ---
col_a, col_b = st.columns(2)

with col_a:
    st.markdown('<div class="table-header">🛬 Arrivals</div>', unsafe_allow_html=True)
    df_arr = pd.DataFrame([
        {"Flight": "AA100", "Origin": "KORD", "ETA (Z)": "21:10Z", "Status": "VFR", "Weather": "CLR 15/0"},
        {"Flight": "UA45", "Origin": "KSFO", "ETA (Z)": "21:25Z", "Status": "MVFR", "Weather": "FEW 025"},
        {"Flight": "DL88", "Origin": "EGLL", "ETA (Z)": "21:50Z", "Status": "IFR", "Weather": "OVC 008"}
    ])
    st.dataframe(df_arr, use_container_width=True, hide_index=True)

with col_b:
    st.markdown('<div class="table-header">🛫 Departures</div>', unsafe_allow_html=True)
    df_dep = pd.DataFrame([
        {"Flight": "IB32", "Dest": "LEMD", "ETD (Z)": "21:15Z", "Status": "VFR", "Weather": "SKC 20/12"},
        {"Flight": "AF11", "Dest": "LFPG", "ETD (Z)": "21:40Z", "Status": "IFR", "Weather": "RA BR 004"},
        {"Flight": "BA05", "Dest": "EGLL", "ETD (Z)": "22:00Z", "Status": "VFR", "Weather": "CLR 18/5"}
    ])
    st.dataframe(df_dep, use_container_width=True, hide_index=True)
