import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import math
from datetime import datetime, timedelta, timezone
from FlightRadar24 import FlightRadar24API

--- CONFIGURACIÓN DE LA PÁGINA ---

st.set_page_config(page_title="Aviator's Lens | Operations Control", page_icon="✈️", layout="wide")

--- MOTOR DE ESTILOS PROFESIONAL (AVIATOR'S LENS) ---

Esta sección inyecta el diseño "Avionics Dark" directamente en Streamlit

st.markdown("""





""", unsafe_allow_html=True)

--- BASE DE DATOS DE AEROPUERTOS ---

AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}

--- BARRA LATERAL (SIDEBAR) ---

with st.sidebar:
    st.markdown("""
    

AVIATOR'S LENS

Flight Ops System v1.5
    """, unsafe_allow_html=True)

st.markdown("<h3 style='font-size: 0.9rem; color: #c3c6d6; margin-bottom: 1rem;'>⚙️ CONFIGURATION</h3>", unsafe_allow_html=True)
aeropuerto_destino = st.selectbox(
    "Base Station",
    ["TODOS", "ATL", "ORD", "LAX", "JFK"],
    index=0
)
horas_prediccion = st.slider("Forecast Window (Hours)", min_value=1, max_value=24, value=12)

st.divider()
st.markdown("<h3 style='font-size: 0.9rem; color: #c3c6d6; margin-bottom: 1rem;'>🔍 RISK FILTERS</h3>", unsafe_allow_html=True)
mostrar_baja = st.checkbox("🟢 VFR (Low Risk)", value=True)
mostrar_moderada = st.checkbox("🟠 MVFR (Moderate)", value=True)
mostrar_alta = st.checkbox("🔴 IFR (High Risk)", value=True)

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🔄 REFRESH SYSTEM"):
    st.cache_data.clear()
    st.rerun()

filtros_activos = []
if mostrar_baja: filtros_activos.append("BAJA")
if mostrar_moderada: filtros_activos.append("MODERADA")
if mostrar_alta: filtros_activos.append("ALTA")

if aeropuerto_destino == "TODOS":
    lista_iatas = list(AEROPUERTOS.keys())
    nombre_mostrar = "GLOBAL OPS CENTER"
else:
    lista_iatas = [aeropuerto_destino]
    nombre_mostrar = f"STATION: {aeropuerto_destino}"

--- FUNCIONES ---

def calcular_distancia_nm(lat1, lon1, lat2, lon2):
    R = 3440.065
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2) * math.sin(dLat/2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2) * math.sin(dLon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

@st.cache_data(ttl=3600) 
def obtener_predicciones_globales(iatas):
    dicc_global = {}
    url = "https://api.open-meteo.com/v1/forecast"
    for apt in iatas:
        parametros = {
            "latitude": AEROPUERTOS[apt]["coords"][0],
            "longitude": AEROPUERTOS[apt]["coords"][1],
            "hourly": "wind_speed_10m",
            "wind_speed_unit": "kmh",
            "timezone": "UTC"
        }
        try:
            datos = requests.get(url, params=parametros).json()
            tiempos = datos["hourly"]["time"]
            vientos = datos["hourly"]["wind_speed_10m"]
            dicc_global[apt] = {tiempos[i]: vientos[i] for i in range(len(tiempos))}
        except:
            dicc_global[apt] = {}
    return dicc_global

def evaluar_probabilidad_cancelacion(hora_dt, dicc_vientos_apt):
    if not dicc_vientos_apt: return "?", "UNKNOWN", "gray", "⚪"
    hora_clave = (hora_dt.replace(minute=0, second=0, microsecond=0) + (timedelta(hours=1) if hora_dt.minute >= 30 else timedelta(0))).strftime("%Y-%m-%dT%H:00")
    viento_kmh = dicc_vientos_apt.get(hora_clave)
    if viento_kmh is None: return "?", "NO DATA", "gray", "⚪"
    viento_kmh = round(viento_kmh, 1)
    if viento_kmh < 15: return viento_kmh, "BAJA", "#10b981", "🟢"
    elif 15 <= viento_kmh <= 35: return viento_kmh, "MODERADA", "#f59e0b", "🟠"
    else: return viento_kmh, "ALTA", "#ef4444", "🔴"

@st.cache_data(ttl=60)
def obtener_datos_vuelos(iatas):
    fr_api = FlightRadar24API()
    vuelos_aire, llegadas, salidas = [], [], []
    try:
        todos = fr_api.get_flights()
        for v in todos:
            if v.ground_speed > 0:
               for apt in iatas:
                   if calcular_distancia_nm(v.latitude, v.longitude, AEROPUERTOS[apt]["coords"][0], AEROPUERTOS[apt]["coords"][1]) < 300:
                      vuelos_aire.append(v)
                      break
    except: pass
    for apt in iatas:
        try:
            detalles = fr_api.get_airport_details(apt)
            arr = detalles['airport']['pluginData']['schedule']['arrivals']['data']
            dep = detalles['airport']['pluginData']['schedule']['departures']['data']
            for v in arr: v['target_apt'] = apt
            for v in dep: v['target_apt'] = apt
            llegadas.extend(arr); salidas.extend(dep)
        except: pass
    return vuelos_aire, llegadas, salidas

--- MAIN INTERFACE RENDER ---

st.markdown(f"""

{nombre_mostrar}

REAL-TIME OPERATIONS FEED • {datetime.now(timezone.utc).strftime('%H:%M')} ZULU

 """, unsafe_allow_html=True)

dicc_meteo_global = obtener_predicciones_globales(lista_iatas)
with st.spinner('ESTABLISHING RADAR UPLINK...'):
    vuelos_aire, llegadas, salidas = obtener_datos_vuelos(lista_iatas)

hora_actual = datetime.now(timezone.utc)
limite_tiempo = hora_actual + timedelta(hours=horas_prediccion)

KPI Section

c1, c2, c3, c4 = st.columns(4)
c1.metric("INBOUND TRAFFIC", len(vuelos_aire), "LIVE RADAR")
c2.metric("EXPECTED ARRIVALS", len(llegadas), f"{horas_prediccion}H WINDOW")
c3.metric("SCHEDULED DEPARTURES", len(salidas), "PLANNED")
if aeropuerto_destino != "TODOS":
    v, p, _, _ = evaluar_probabilidad_cancelacion(hora_actual, dicc_meteo_global[aeropuerto_destino])
    c4.metric(f"WIND @ {aeropuerto_destino}", f"{v} KM/H", p, delta_color="inverse" if p == "ALTA" else "normal")
else:
    c4.metric("ACTIVE BASES", len(lista_iatas), "NETWORK OK")

st.markdown("
", unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs(["[ 🗺️ RADAR FEED ]", "[ 🛬 ARRIVALS ]", "[ 🛫 DEPARTURES ]"])

with tab1:
    map_center = [39.5, -98.35] if aeropuerto_destino == "TODOS" else AEROPUERTOS[aeropuerto_destino]["coords"]
    # Mapa con estilo Dark Matter de CartoDB
    mapa = folium.Map(location=map_center, zoom_start=4 if aeropuerto_destino == "TODOS" else 6, tiles="CartoDB dark_matter")

for apt in lista_iatas:
    folium.CircleMarker(
        location=AEROPUERTOS[apt]["coords"], 
        radius=8, 
        color="#b2c5ff", 
        fill=True, 
        fill_opacity=0.6,
        popup=f"Base Station: {apt}"
    ).add_to(mapa)

v_pintados = 0
for v in vuelos_aire:
    dest = str(v.destination_airport_iata).upper()
    if dest in lista_iatas:
        dist = calcular_distancia_nm(v.latitude, v.longitude, AEROPUERTOS[dest]["coords"][0], AEROPUERTOS[dest]["coords"][1])
        if v.ground_speed > 0:
            eta = hora_actual + timedelta(hours=dist / v.ground_speed)
            viento, prob, color, _ = evaluar_probabilidad_cancelacion(eta, dicc_meteo_global[dest])
            
            if prob in filtros_activos:
                folium.Marker(
                    location=[v.latitude, v.longitude],
                    icon=folium.Icon(color="blue" if color=="#10b981" else "orange" if color=="#f59e0b" else "red", icon="plane", prefix="fa"),
                    tooltip=f"{v.callsign} ➔ {dest} | RISK: {prob}"
                ).add_to(mapa)
                v_pintados += 1

st_folium(mapa, width="100%", height=550, returned_objects=[])

with tab2:
    res_arr = []
    for v in llegadas:
        try:
            t = v['flight']['time']['scheduled']['arrival']
            if t:
                h = datetime.fromtimestamp(t, timezone.utc)
                if hora_actual <= h <= limite_tiempo:
                    viento, prob, _, icono = evaluar_probabilidad_cancelacion(h, dicc_meteo_global[v['target_apt']])
                    if prob in filtros_activos:
                        res_arr.append({
                            "TIME (Z)": h.strftime('%H:%M'),
                            "FLIGHT": v['flight']['identification']['number']['default'],
                            "DEST": v['target_apt'],
                            "RISK": icono + " " + prob,
                            "WIND FORECAST": f"{viento} km/h"
                        })
        except: pass
    if res_arr: 
        df_arr = pd.DataFrame(res_arr).sort_values("TIME (Z)")
        st.dataframe(df_arr, use_container_width=True, hide_index=True)
    else: st.info("NO MATCHING ARRIVALS FOUND.")

with tab3:
    res_dep = []
    for v in salidas:
        try:
            t = v['flight']['time']['scheduled']['departure']
            if t:
                h = datetime.fromtimestamp(t, timezone.utc)
                if hora_actual <= h <= limite_tiempo:
                    viento, prob, _, icono = evaluar_probabilidad_cancelacion(h, dicc_meteo_global[v['target_apt']])
                    if prob in filtros_activos:
                        res_dep.append({
                            "TIME (Z)": h.strftime('%H:%M'),
                            "FLIGHT": v['flight']['identification']['number']['default'],
                            "ORIGIN": v['target_apt'],
                            "RISK": icono + " " + prob,
                            "WIND FORECAST": f"{viento} km/h"
                        })
        except: pass
    if res_dep: 
        df_dep = pd.DataFrame(res_dep).sort_values("TIME (Z)")
        st.dataframe(df_dep, use_container_width=True, hide_index=True)
    else: st.info("NO MATCHING DEPARTURES FOUND.")
