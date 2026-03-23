import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import math
from datetime import datetime, timedelta, timezone
from FlightRadar24 import FlightRadar24API

# --- CONFIGURACIÓN DE PÁGINA Y ESTILO PROFESIONAL ---
st.set_page_config(page_title="Aviator's Lens | Ops Control", page_icon="✈️", layout="wide")

# Inyección de CSS para transformar el look de Streamlit a un Dashboard de Aviación
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;700;800&display=swap');

/* Fondo general y fuentes */
.stApp {
    background-color: #0b0e14;
    color: #e2e8f0;
    font-family: 'Inter', sans-serif;
}

/* Sidebar estilizada */
section[data-testid="stSidebar"] {
    background-color: #111827 !important;
    border-right: 1px solid #1f2937;
}

/* Títulos y Headers */
h1, h2, h3 {
    color: #f8fafc !important;
    font-weight: 800 !important;
    letter-spacing: -0.025em;
}

.stMarkdown p {
    color: #94a3b8;
}

/* Tarjetas de Métricas (Glassmorphism) */
div[data-testid="stMetric"] {
    background: rgba(30, 41, 59, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.1);
    padding: 20px !important;
    border-radius: 12px;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

div[data-testid="stMetricValue"] {
    color: #3b82f6 !important;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700;
}

/* Tabs personalizadas */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background-color: transparent;
}

.stTabs [data-baseweb="tab"] {
    background-color: #1e293b;
    border-radius: 8px 8px 0 0;
    padding: 10px 20px;
    color: #94a3b8;
    border: none;
}

.stTabs [aria-selected="true"] {
    background-color: #3b82f6 !important;
    color: white !important;
}

/* Dataframes con estilo dark */
.stDataFrame {
    border: 1px solid #1f2937;
    border-radius: 8px;
}
</style>
""", unsafe_allow_html=True)

# --- DATOS Y LÓGICA ---
AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}

with st.sidebar:
    st.markdown("<h1 style='color: #3b82f6 !important; font-size: 1.5rem;'>AVIATOR'S LENS</h1>", unsafe_allow_html=True)
    st.markdown("<p style='font-size: 0.7rem; margin-top: -15px;'>FLIGHT OPS SYSTEM v2.0</p>", unsafe_allow_html=True)
    st.divider()
    
    st.markdown("### ⚙️ SYSTEM CONFIG")
    aeropuerto_destino = st.selectbox("Primary Station ID", ["TODOS", "ATL", "ORD", "LAX", "JFK"], index=0)
    horas_prediccion = st.slider("Forecast Window (Hours)", 1, 24, 12)
    
    st.markdown("### 🔍 RISK FILTERS")
    mostrar_baja = st.checkbox("🟢 VFR (Low Risk)", value=True)
    mostrar_moderada = st.checkbox("🟠 MVFR (Moderate)", value=True)
    mostrar_alta = st.checkbox("🔴 IFR (Critical)", value=True)
    
    if st.button("🔄 REFRESH DATA", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# --- FUNCIONES DE APOYO ---
def calcular_distancia_nm(lat1, lon1, lat2, lon2):
    R = 3440.065
    dLat, dLon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

@st.cache_data(ttl=3600)
def obtener_predicciones_globales(iatas):
    dicc_global = {}
    for apt in iatas:
        params = {
            "latitude": AEROPUERTOS[apt]["coords"][0],
            "longitude": AEROPUERTOS[apt]["coords"][1],
            "hourly": "wind_speed_10m",
            "wind_speed_unit": "kmh",
            "timezone": "UTC"
        }
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", params=params).json()
            dicc_global[apt] = {r["hourly"]["time"][i]: r["hourly"]["wind_speed_10m"][i] for i in range(len(r["hourly"]["time"]))}
        except:
            dicc_global[apt] = {}
    return dicc_global

def evaluar_probabilidad_cancelacion(hora_dt, dicc_vientos_apt):
    if not dicc_vientos_apt:
        return "?", "UNKNOWN", "gray", "⚪"
    
    clave = (hora_dt.replace(minute=0, second=0, microsecond=0)).strftime("%Y-%m-%dT%H:00")
    viento = dicc_vientos_apt.get(clave, 0)
    
    if viento < 15:
        return round(viento,1), "BAJA", "#10b981", "🟢"
    elif 15 <= viento <= 35:
        return round(viento,1), "MODERADA", "#f59e0b", "🟠"
    else:
        return round(viento,1), "ALTA", "#ef4444", "🔴"

@st.cache_data(ttl=60)
def obtener_datos_vuelos(iatas):
    fr_api = FlightRadar24API()
    v_aire, arr, dep = [], [], []
    
    try:
        for v in fr_api.get_flights():
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

# --- UI PRINCIPAL ---
filtros = []
if mostrar_baja: filtros.append("BAJA")
if mostrar_moderada: filtros.append("MODERADA")
if mostrar_alta: filtros.append("ALTA")

lista_iatas = list(AEROPUERTOS.keys()) if aeropuerto_destino == "TODOS" else [aeropuerto_destino]
nombre_header = "GLOBAL OPERATIONS" if aeropuerto_destino == "TODOS" else f"STATION: {aeropuerto_destino}"

st.markdown(f"## {nombre_header}")
st.markdown(f"**LIVE TELEMETRY** • {datetime.now(timezone.utc).strftime('%H:%M')} ZULU")

dicc_meteo_global = obtener_predicciones_globales(lista_iatas)

with st.spinner('UPLINKING...'):
    v_aire, llegadas, salidas = obtener_datos_vuelos(lista_iatas)

# Métricas en el nuevo formato de tarjetas
c1, c2, c3, c4 = st.columns(4)
c1.metric("INBOUND TRAFFIC", len(v_aire), "LIVE")
c2.metric("ARRIVALS", len(llegadas), f"{horas_prediccion}H")
c3.metric("DEPARTURES", len(salidas), "PLANNED")

if aeropuerto_destino != "TODOS":
    v, p, _, _ = evaluar_probabilidad_cancelacion(datetime.now(timezone.utc), dicc_meteo_global[aeropuerto_destino])
    c4.metric(f"WIND @ {aeropuerto_destino}", f"{v} KM/H", p, delta_color="inverse" if p == "ALTA" else "normal")
else:
    c4.metric("ACTIVE BASES", len(lista_iatas), "STABLE")

st.write("")

tab1, tab2, tab3 = st.tabs(["🗺️ TACTICAL RADAR", "🛬 INBOUND", "🛫 OUTBOUND"])

with tab1:
    center = [39.5, -98.35] if aeropuerto_destino == "TODOS" else AEROPUERTOS[aeropuerto_destino]["coords"]
    mapa = folium.Map(location=center, zoom_start=4 if aeropuerto_destino == "TODOS" else 6, tiles="CartoDB dark_matter")
    
    for apt in lista_iatas:
        folium.CircleMarker(AEROPUERTOS[apt]["coords"], radius=8, color="#3b82f6", fill=True, popup=apt).add_to(mapa)
        
    for v in v_aire:
        dest = str(v.destination_airport_iata).upper()
        if dest in lista_iatas:
            dist = calcular_distancia_nm(v.latitude, v.longitude, AEROPUERTOS[dest]["coords"][0], AEROPUERTOS[dest]["coords"][1])
            eta = datetime.now(timezone.utc) + timedelta(hours=dist/max(v.ground_speed,1))
            _, prob, color, _ = evaluar_probabilidad_cancelacion(eta, dicc_meteo_global.get(dest))
            
            if prob in filtros:
                folium.Marker(
                    [v.latitude, v.longitude], 
                    icon=folium.Icon(color="blue" if color=="#10b981" else "orange" if color=="#f59e0b" else "red", icon="plane", prefix="fa"), 
                    tooltip=f"{v.callsign} -> {dest}"
                ).add_to(mapa)
                
    st_folium(mapa, width="100%", height=550)

with tab2:
    res = []
    for v in llegadas:
        try:
            h = datetime.fromtimestamp(v['flight']['time']['scheduled']['arrival'], timezone.utc)
            if datetime.now(timezone.utc) <= h <= datetime.now(timezone.utc) + timedelta(hours=horas_prediccion):
                viento, prob, _, icono = evaluar_probabilidad_cancelacion(h, dicc_meteo_global[v['target_apt']])
                if prob in filtros:
                    res.append({
                        "TIME (Z)": h.strftime('%H:%M'), 
                        "FLIGHT": v['flight']['identification']['number']['default'], 
                        "DEST": v['target_apt'], 
                        "RISK": f"{icono} {prob}", 
                        "WIND": viento
                    })
        except:
            pass
    st.dataframe(pd.DataFrame(res), use_container_width=True, hide_index=True) if res else st.info("NO DATA")

with tab3:
    res_d = []
    for v in salidas:
        try:
            h = datetime.fromtimestamp(v['flight']['time']['scheduled']['departure'], timezone.utc)
            if datetime.now(timezone.utc) <= h <= datetime.now(timezone.utc) + timedelta(hours=horas_prediccion):
                viento, prob, _, icono = evaluar_probabilidad_cancelacion(h, dicc_meteo_global[v['target_apt']])
                if prob in filtros:
                    res_d.append({
                        "TIME (Z)": h.strftime('%H:%M'), 
                        "FLIGHT": v['flight']['identification']['number']['default'], 
                        "ORIG": v['target_apt'], 
                        "RISK": f"{icono} {prob}", 
                        "WIND": viento
                    })
        except:
            pass
    st.dataframe(pd.DataFrame(res_d), use_container_width=True, hide_index=True) if res_d else st.info("NO DATA")
