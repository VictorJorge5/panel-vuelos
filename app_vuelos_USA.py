import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import math
import concurrent.futures
import time  # Necesario para regular la descarga de fotos
from datetime import datetime, timedelta, timezone
from FlightRadar24 import FlightRadar24API
import altair as alt
import joblib

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="IA Control de Operaciones USA", page_icon="✈️", layout="wide")

# --- ESTILOS CSS PERSONALIZADOS (Diseño Limpio y Profesional - Tema Claro) ---
st.markdown("""
    <style>
    /* Ocultar elementos predeterminados de Streamlit */
    header {visibility: hidden;}
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

    /* Optimizar espacio de la pantalla */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 1rem;
    }

    /* Estilo de tarjetas para las métricas (KPIs) - Versión Clara/Luminosa */
    [data-testid="stMetric"] {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        padding: 15px;
        border-radius: 8px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }
    </style>
""", unsafe_allow_html=True)

# --- CARGA DEL MODELO IA ---
@st.cache_resource
def cargar_modelo_ia():
    try:
        return joblib.load('modelo_vuelos_final.joblib')
    except Exception as e:
        st.error(f"⚠️ No se encontró el archivo 'modelo_vuelos_final.joblib'. {e}")
        return None

MODELO_IA = cargar_modelo_ia()

# --- BASE DE DATOS DE AEROPUERTOS (DOMINIO EXCLUSIVO) ---
AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}
AEROPUERTOS_VALIDOS = list(AEROPUERTOS.keys())

# --- BARRA LATERAL (SIDEBAR) ---
st.sidebar.title("⚙️ Configuración")

aeropuerto_destino = st.sidebar.selectbox(
    "📍 Selecciona el Aeropuerto",
    ["TODOS"] + AEROPUERTOS_VALIDOS,
    index=0
)

horas_prediccion = st.sidebar.slider("⏳ Horas de previsión a mostrar", min_value=1, max_value=24, value=15)

st.sidebar.markdown("### 🔍 Filtros de Riesgo IA")
mostrar_baja = st.sidebar.checkbox("🟢 Probabilidad BAJA", value=True)
mostrar_moderada = st.sidebar.checkbox("🟠 Probabilidad MEDIA", value=True)
mostrar_alta = st.sidebar.checkbox("🔴 Probabilidad ALTA", value=True)

filtros_activos = []
if mostrar_baja: filtros_activos.append("BAJA")
if mostrar_moderada: filtros_activos.append("MEDIA")
if mostrar_alta: filtros_activos.append("ALTA")

if st.sidebar.button("🔄 Refrescar Datos Ahora"):
    st.cache_data.clear()
    st.rerun()

if aeropuerto_destino == "TODOS":
    lista_iatas = AEROPUERTOS_VALIDOS
    nombre_mostrar = "Estados Unidos (Hubs Principales)"
else:
    lista_iatas = [aeropuerto_destino]
    nombre_mostrar = AEROPUERTOS[aeropuerto_destino]["nombre"]

# --- FUNCIONES MATEMÁTICAS Y METEO ---
def calcular_distancia_nm(lat1, lon1, lat2, lon2):
    R = 3440.065
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2) * math.sin(dLat/2) + math.cos(math.radians(lat1)) \
        * math.cos(math.radians(lat2)) * math.sin(dLon/2) * math.sin(dLon/2)
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
            "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,visibility,cloudcover,temperature_2m,precipitation",
            "wind_speed_unit": "kn",
            "precipitation_unit": "mm",
            "timezone": "UTC"
        }
        try:
            datos = requests.get(url, params=parametros).json()
            tiempos = datos["hourly"]["time"]
            
            clima_hora = {}
            for i, t in enumerate(tiempos):
                clima_hora[t] = {
                    'viento_kts': datos["hourly"]["wind_speed_10m"][i] or 0,
                    'rafagas_kts': datos["hourly"]["wind_gusts_10m"][i] or 0,
                    'direccion': datos["hourly"]["wind_direction_10m"][i] or 0,
                    'visib_m': datos["hourly"]["visibility"][i] or 10000,
                    'nubes_pct': datos["hourly"]["cloudcover"][i] or 0,
                    'temp_c': datos["hourly"]["temperature_2m"][i] or 15,
                    'precip': datos["hourly"]["precipitation"][i] or 0
                }
            dicc_global[apt] = clima_hora
        except:
            dicc_global[apt] = {}
    return dicc_global

def extraer_clima_hora(iata, hora_dt, dicc_meteo):
    clima_ideal = {'viento_kts': 0.0, 'rafagas_kts': 0.0, 'direccion': 0.0, 'visib_m': 10000.0, 'nubes_pct': 0.0, 'temp_c': 15.0, 'precip': 0.0}
    if iata not in dicc_meteo: return clima_ideal
    hora_redondeada = hora_dt.replace(minute=0, second=0, microsecond=0)
    hora_str = hora_redondeada.strftime("%Y-%m-%dT%H:00")
    return dicc_meteo[iata].get(hora_str, clima_ideal)

def predecir_riesgo_ia(origen, destino, aerolinea, hora_vuelo_dt, dicc_meteo):
    if not MODELO_IA:
        return "N/A", "Desconocida", "gray", "⚪ Error IA", 0.0, 0.0
        
    c_orig = extraer_clima_hora(origen, hora_vuelo_dt, dicc_meteo)
    c_dest = extraer_clima_hora(destino, hora_vuelo_dt, dicc_meteo)
    
    try:
        enc_orig = MODELO_IA['le_orig'].transform([origen])[0] if origen in MODELO_IA['le_orig'].classes_ else 0
        enc_dest = MODELO_IA['le_dest'].transform([destino])[0] if destino in MODELO_IA['le_dest'].classes_ else 0
        enc_carr = MODELO_IA['le_carrier'].transform([aerolinea])[0] if aerolinea in MODELO_IA['le_carrier'].classes_ else 0
    except:
        enc_orig, enc_dest, enc_carr = 0, 0, 0

    input_df = pd.DataFrame([[
        c_orig['viento_kts'], c_orig['rafagas_kts'], c_orig['visib_m'], c_orig['nubes_pct'], c_orig['temp_c'],
        c_dest['viento_kts'], c_dest['rafagas_kts'], c_dest['visib_m'], c_dest['nubes_pct'], c_dest['temp_c'],
        enc_orig, enc_dest, enc_carr
    ]], columns=MODELO_IA['features'])
    
    prob = MODELO_IA['modelo'].predict_proba(input_df)[0][1]
    
    texto_prob = f"{prob:.1%}"
    if prob < 0.25: return texto_prob, "BAJA", "green", "🟢 Baja", c_dest['viento_kts'], c_dest['precip']
    elif prob < 0.60: return texto_prob, "MEDIA", "orange", "🟡 Media", c_dest['viento_kts'], c_dest['precip']
    else: return texto_prob, "ALTA", "red", "🔴 Alta", c_dest['viento_kts'], c_dest['precip']

# --- RADAR DE LLUVIA DINÁMICO ---
@st.cache_data(ttl=300)
def obtener_url_radar_lluvia():
    try:
        data = requests.get("https://api.rainviewer.com/public/weather-maps.json", timeout=5).json()
        host = data.get('host', 'https://tilecache.rainviewer.com')
        path = data['radar']['past'][-1]['path']
        return f"{host}{path}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"
    except: 
        return None

@st.cache_data(ttl=300)
def obtener_metar_taf(iata):
    icao = f"K{iata}"
    try:
        metar_req = requests.get(f"https://aviationweather.gov/api/data/metar?ids={icao}&format=raw", timeout=5)
        metar_txt = metar_req.text.strip() if metar_req.status_code == 200 and metar_req.text else f"No hay METAR para {icao}."
        taf_req = requests.get(f"https://aviationweather.gov/api/data/taf?ids={icao}&format=raw", timeout=5)
        taf_txt = taf_req.text.strip() if taf_req.status_code == 200 and taf_req.text else f"No hay TAF para {icao}."
        return metar_txt, taf_txt
    except: return "Error de conexión", "Error de conexión"

# --- DESCARGA DE FOTOS BLINDADA (ANTISPAM) ---
@st.cache_data(ttl=86400)
def obtener_foto_aeronave_ia(matricula):
    if not matricula or matricula == "N/A": return None, None, None
    try:
        time.sleep(0.3)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = requests.get(f"https://api.planespotters.net/pub/photos/reg/{matricula}", headers=headers, timeout=10)
        
        if r.status_code == 200:
            data = r.json()
            if data.get('photos'):
                return data['photos'][0]['thumbnail_large']['src'], data['photos'][0]['link'], data['photos'][0]['photographer']
    except Exception: pass
    return None, None, None

@st.cache_data(ttl=86400)
def obtener_mapa_aerolineas():
    try:
        aerolineas = FlightRadar24API().get_airlines()
        return {a.get('ICAO', a.get('Code')): a['Name'] for a in aerolineas if 'Name' in a}
    except: return {}

# --- FUNCIONES DE EXTRACCIÓN SEGURA ---
def obtener_iata_seguro(nodo):
    try:
        if isinstance(nodo, dict) and isinstance(nodo.get('code'), dict): return nodo['code'].get('iata', 'N/A')
    except: pass
    return 'N/A'

def obtener_num_vuelo_seguro(vuelo_dict):
    try:
        if isinstance(vuelo_dict, dict) and isinstance(vuelo_dict.get('flight'), dict):
            ident = vuelo_dict['flight'].get('identification')
            if isinstance(ident, dict) and isinstance(ident.get('number'), dict): return ident['number'].get('default', 'N/A')
    except: pass
    return 'N/A'

def obtener_aerolinea_segura(vuelo_dict):
    try:
        if isinstance(vuelo_dict, dict) and isinstance(vuelo_dict.get('flight'), dict):
            airline = vuelo_dict['flight'].get('airline')
            if isinstance(airline, dict): return airline.get('name', 'N/A')
    except: pass
    return 'N/A'

def obtener_carrier_iata_seguro(vuelo_dict):
    try:
        if isinstance(vuelo_dict, dict) and isinstance(vuelo_dict.get('flight'), dict):
            airline = vuelo_dict['flight'].get('airline')
            if isinstance(airline, dict) and isinstance(airline.get('code'), dict): return airline['code'].get('iata', 'N/A')
    except: pass
    return 'N/A'

def obtener_timestamp_seguro(vuelo_dict, tipo_vuelo, tipo_tiempo):
    try:
        if isinstance(vuelo_dict, dict) and isinstance(vuelo_dict.get('flight'), dict):
            time_node = vuelo_dict['flight'].get('time')
            if isinstance(time_node, dict) and isinstance(time_node.get(tipo_tiempo), dict): return time_node[tipo_tiempo].get(tipo_vuelo)
    except: pass
    return None

# --- EXTRACCIÓN DE VUELOS ---
@st.cache_data(ttl=60)
def obtener_datos_vuelos(iatas):
    fr_api = FlightRadar24API()
    vuelos_aire, llegadas, salidas = [], [], []
    try:
        for v in fr_api.get_flights():
            if v.ground_speed > 0:
               for apt in iatas:
                   dist = calcular_distancia_nm(v.latitude, v.longitude, AEROPUERTOS[apt]["coords"][0], AEROPUERTOS[apt]["coords"][1])
                   if dist < 500:
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
            llegadas.extend(arr)
            salidas.extend(dep)
        except: pass
    return vuelos_aire, llegadas, salidas

# --- INICIALIZACIÓN ---
dicc_meteo_global = obtener_predicciones_globales(lista_iatas)
mapa_aerolineas = obtener_mapa_aerolineas()

with st.spinner('📡 Sincronizando telemetría estricta de la red (ATL, ORD, LAX, JFK)...'):
    vuelos_aire_crudo, llegadas, salidas = obtener_datos_vuelos(lista_iatas)

# --- FILTROS LATERALES DINÁMICOS Y LIMPIEZA ESTRICTA DOMÉSTICA ---
st.sidebar.divider()
st.sidebar.markdown("### 🔎 Filtros Avanzados")
aerolineas_disponibles, aeropuertos_disponibles, numeros_vuelo_disponibles = set(), set(), set()

# Recopilar filtros solo de vuelos que cumplen la regla estricta (origen y destino en la red de 4)
for v in llegadas + salidas:
    f_data = v.get('flight') or {}
    orig = obtener_iata_seguro(f_data.get('airport', {}).get('origin'))
    dest = obtener_iata_seguro(f_data.get('airport', {}).get('destination'))
    
    # REGLA DE EXCLUSIVIDAD: Solo vuelos entre nuestros 4 aeropuertos
    if orig not in AEROPUERTOS_VALIDOS or dest not in AEROPUERTOS_VALIDOS:
        continue

    num = obtener_num_vuelo_seguro(vuelo_dict=v)
    al = obtener_aerolinea_segura(vuelo_dict=v)
    
    if al != "N/A": aerolineas_disponibles.add(al)
    if orig != "N/A": aeropuertos_disponibles.add(orig)
    if dest != "N/A": aeropuertos_disponibles.add(dest)
    if num != "N/A": numeros_vuelo_disponibles.add(num)

for v in vuelos_aire_crudo:
    dest = str(getattr(v, 'destination_airport_iata', 'N/A')).upper()
    orig = str(getattr(v, 'origin_airport_iata', 'N/A')).upper()
    
    if orig not in AEROPUERTOS_VALIDOS or dest not in AEROPUERTOS_VALIDOS:
        continue
        
    if dest in lista_iatas:
        callsign = getattr(v, 'callsign', 'N/A')
        al_name = mapa_aerolineas.get(getattr(v, 'airline_icao', 'N/A'), "N/A")
        if callsign != "N/A": numeros_vuelo_disponibles.add(callsign)
        if al_name != "N/A": aerolineas_disponibles.add(al_name)

filtro_aerolineas = st.sidebar.multiselect("✈️ Filtrar por Aerolínea", sorted([str(x) for x in aerolineas_disponibles]))
filtro_aeropuertos = st.sidebar.multiselect("📍 Filtrar por Aeropuerto", sorted([str(x) for x in aeropuertos_disponibles]))
filtro_vuelos = st.sidebar.multiselect("🔢 Filtrar por Nº Vuelo", sorted([str(x) for x in numeros_vuelo_disponibles]), placeholder="Buscar...")

vuelos_aire_filtrados = []
for v in vuelos_aire_crudo:
    destino = str(getattr(v, 'destination_airport_iata', 'N/A')).upper()
    origen = str(getattr(v, 'origin_airport_iata', 'N/A')).upper()
    
    # REGLA DE EXCLUSIVIDAD PARA EL RADAR
    if origen not in AEROPUERTOS_VALIDOS or destino not in AEROPUERTOS_VALIDOS:
        continue
        
    callsign = getattr(v, 'callsign', 'N/A')
    aerolinea_vuelo = mapa_aerolineas.get(getattr(v, 'airline_icao', 'N/A'), "N/A")
    
    if destino in lista_iatas:
        if (not filtro_aeropuertos or origen in filtro_aeropuertos or destino in filtro_aeropuertos) and \
           (not filtro_vuelos or callsign in filtro_vuelos) and \
           (not filtro_aerolineas or aerolinea_vuelo in filtro_aerolineas):
            v.nombre_aerolinea_mapeado = aerolinea_vuelo
            vuelos_aire_filtrados.append(v)

matriculas_mapa = list(set([getattr(v, 'registration', 'N/A') for v in vuelos_aire_filtrados if getattr(v, 'registration', 'N/A') != 'N/A']))
dicc_fotos = {}
if matriculas_mapa:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futuros = {executor.submit(obtener_foto_aeronave_ia, mat): mat for mat in matriculas_mapa}
        for f in concurrent.futures.as_completed(futuros):
            dicc_fotos[futuros[f]] = f.result()

hora_actual = datetime.now(timezone.utc)
limite_tiempo = hora_actual + timedelta(hours=horas_prediccion)

# --- PANEL SUPERIOR ---
st.title(f"✈️ Panel de Operaciones - {nombre_mostrar}")
st.markdown(f"**Powered by AI Predictions (Strict Domestic Mode)** | ⏱️ Hora del Sistema (UTC): `{hora_actual.strftime('%Y-%m-%d %H:%M:%S')} ZULU`")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Vuelos en Radar", len(vuelos_aire_filtrados), "Acercándose a bases")
# Recalculamos totales exactos para los KPIs
llegadas_validas = sum(1 for v in llegadas if obtener_iata_seguro((v.get('flight') or {}).get('airport', {}).get('origin')) in AEROPUERTOS_VALIDOS and obtener_iata_seguro((v.get('flight') or {}).get('airport', {}).get('destination')) in AEROPUERTOS_VALIDOS)
salidas_validas = sum(1 for v in salidas if obtener_iata_seguro((v.get('flight') or {}).get('airport', {}).get('origin')) in AEROPUERTOS_VALIDOS and obtener_iata_seguro((v.get('flight') or {}).get('airport', {}).get('destination')) in AEROPUERTOS_VALIDOS)

col2.metric("Llegadas Prog.", llegadas_validas, "Red Estricta 4 Hubs")
col3.metric("Salidas Prog.", salidas_validas, "Red Estricta 4 Hubs")

if aeropuerto_destino == "TODOS":
    col4.metric("Bases Monitorizadas", len(lista_iatas), "Red Completa")
else:
    clima_actual = extraer_clima_hora(aeropuerto_destino, hora_actual, dicc_meteo_global)
    col4.metric(f"Viento en {aeropuerto_destino}", f"{round(clima_actual['viento_kts'])} kts", "Ráfagas: " + str(round(clima_actual['rafagas_kts'])), delta_color="off")

st.divider()

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Radar en Vivo", "🛬 Panel de Llegadas", "🛫 Panel de Salidas", "📊 Dashboard Analítico"])

with tab1:
    # Centrado modificado para ver bien los 4 aeropuertos en EE.UU.
    map_center = [39.8283, -98.5795] if aeropuerto_destino == "TODOS" else AEROPUERTOS[aeropuerto_destino]["coords"]
    mapa = folium.Map(location=map_center, zoom_start=4 if aeropuerto_destino == "TODOS" else 5, tiles="CartoDB dark_matter")
    
    url_lluvia = obtener_url_radar_lluvia()
    if url_lluvia:
        folium.TileLayer(
            tiles=url_lluvia,
            attr='Weather data © RainViewer',
            name='Radar de Precipitaciones',
            overlay=True,
            control=True,
            opacity=0.55
        ).add_to(mapa)

    hora_str_clave = hora_actual.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")

    for apt in lista_iatas:
        folium.Marker(location=AEROPUERTOS[apt]["coords"], popup=f"<b>{AEROPUERTOS[apt]['nombre']}</b>", icon=folium.Icon(color="black", icon="building", prefix="fa")).add_to(mapa)

        clima_apt = dicc_meteo_global.get(apt, {}).get(hora_str_clave, {})
        vel_viento = clima_apt.get('viento_kts')
        dir_viento = clima_apt.get('direccion')
        
        if vel_viento is not None and dir_viento is not None:
            rotacion_flecha = (dir_viento + 180) % 360
            html_vector_viento = f"""
            <div style='font-family: Arial; font-size: 11px; color: #fff; font-weight: bold; background: rgba(15,23,42,0.8); border: 1px solid #3b82f6; padding: 2px 6px; border-radius: 4px; display: inline-flex; align-items: center; white-space: nowrap; transform: translate(15px, -15px);'>
                <i class='fa fa-arrow-up' style='transform: rotate({rotacion_flecha}deg); margin-right: 4px; color: #3b82f6;'></i>
                {round(vel_viento)} kts
            </div>
            """
            folium.Marker(location=AEROPUERTOS[apt]["coords"], icon=folium.DivIcon(html=html_vector_viento)).add_to(mapa)

    vuelos_pintados = 0
    for vuelo in vuelos_aire_filtrados:
        destino = str(getattr(vuelo, 'destination_airport_iata', 'N/A')).upper()
        origen = str(getattr(vuelo, 'origin_airport_iata', 'N/A')).upper()
        callsign = getattr(vuelo, 'callsign', 'N/A')
        aerolinea_nom = getattr(vuelo, 'nombre_aerolinea_mapeado', 'N/A')
        aerolinea_iata = getattr(vuelo, 'airline_iata', 'N/A')
        
        altitud = getattr(vuelo, 'altitude', 'N/A')
        velocidad = getattr(vuelo, 'ground_speed', 'N/A')
        rumbo = getattr(vuelo, 'heading', 'N/A')
        matricula = getattr(vuelo, 'registration', 'N/A')
        modelo = getattr(vuelo, 'aircraft_code', 'N/A')
        v_speed = getattr(vuelo, 'vertical_speed', 0)
        
        v_speed_str = f"+{v_speed}" if v_speed > 0 else str(v_speed)
        v_speed_color = "green" if v_speed > 0 else "red" if v_speed < 0 else "gray"
        
        horas_restantes = calcular_distancia_nm(vuelo.latitude, vuelo.longitude, AEROPUERTOS[destino]["coords"][0], AEROPUERTOS[destino]["coords"][1]) / max(vuelo.ground_speed, 1)
        eta = hora_actual + timedelta(hours=horas_restantes)
        
        # --- LLAMADA AL MODELO IA ---
        score_texto, prob, color, icono, viento_dest, lluvia_dest = predecir_riesgo_ia(origen, destino, aerolinea_iata, eta, dicc_meteo_global)
        
        if prob in filtros_activos:
            foto_url, foto_link, fotografo = dicc_fotos.get(matricula, (None, None, None))
            
            if foto_url:
                foto_html = f"""
                <div style="margin-bottom: 8px;">
                    <a href="{foto_link}" target="_blank" title="Ver imagen original">
                        <img src="{foto_url}" width="100%" style="border-radius: 4px; border: 1px solid #ccc; max-height: 140px; object-fit: cover;">
                    </a>
                    <div style="font-size: 8px; color: #64748b; text-align: right; margin-top: 2px;">
                        © {fotografo} | Planespotters.net
                    </div>
                </div>
                """
            else:
                foto_html = f"""
                <div style="margin-bottom: 8px; text-align: center; background: #e2e8f0; padding: 10px; border-radius: 4px; font-size: 11px;">
                    <a href="https://www.jetphotos.com/registration/{matricula}" target="_blank" style="text-decoration: none; color: #3b82f6;">
                        📷 Buscar archivo de {matricula} en JetPhotos
                    </a>
                </div>
                """

            html_popup = f"""
            <div style='font-family: Arial; font-size: 12px; width: 250px;'>
                {foto_html}
                <h4 style='margin-bottom: 2px; color: {color};'>✈️ {callsign} | {aerolinea_nom}</h4>
                <div style='font-size: 10px; color: gray; margin-bottom: 8px;'>Matrícula: {matricula} | Equipo: {modelo}</div>
                
                <b>Ruta:</b> {origen} ➔ <b>{destino}</b><br>
                <hr style='margin: 4px 0;'>
                
                <div style='display: flex; justify-content: space-between;'>
                    <span><b>Alt:</b> {altitud} ft</span>
                    <span><b>Vel:</b> {velocidad} kts</span>
                </div>
                <div style='display: flex; justify-content: space-between;'>
                    <span><b>Rumbo:</b> {rumbo}°</span>
                    <span><b>V/S:</b> <span style='color: {v_speed_color};'>{v_speed_str} fpm</span></span>
                </div>
                
                <hr style='margin: 4px 0;'>
                <b>Faltan:</b> {round(horas_restantes, 1)} h <b>(ETA:</b> {eta.strftime('%H:%M')}Z)<br>
                <b>Riesgo IA:</b> <span style='color:{color}'><b>{score_texto} ({prob})</b></span><br>
                <b>Viento:</b> {round(viento_dest)} kts | <b>Lluvia:</b> {round(lluvia_dest, 1)} mm
            </div>
            """
            
            folium.Marker(
                location=[vuelo.latitude, vuelo.longitude],
                popup=folium.Popup(html_popup, max_width=300),
                icon=folium.Icon(color=color, icon="plane", prefix="fa", angle=vuelo.heading)
            ).add_to(mapa)
            vuelos_pintados += 1

    folium.LayerControl().add_to(mapa)
    st_folium(mapa, width=1200, height=600, returned_objects=[])
    st.success(f"Radar Activo: Mostrando **{vuelos_pintados}** aviones con telemetría en vivo operando en rutas validadas.")

with tab2:
    datos_llegadas = []
    for vuelo in llegadas:
        f_data = vuelo.get('flight') or {}
        origen = obtener_iata_seguro(f_data.get('airport', {}).get('origin'))
        target = vuelo.get('target_apt')
        
        # REGLA DE EXCLUSIVIDAD
        if origen not in AEROPUERTOS_VALIDOS or target not in AEROPUERTOS_VALIDOS:
            continue

        timestamp_sched = obtener_timestamp_seguro(vuelo, 'arrival', 'scheduled')
        timestamp_est = obtener_timestamp_seguro(vuelo, 'arrival', 'estimated') or obtener_timestamp_seguro(vuelo, 'arrival', 'real')
        
        if timestamp_sched:
            hora_vuelo = datetime.fromtimestamp(timestamp_sched, timezone.utc)
            if hora_actual <= hora_vuelo <= limite_tiempo:
                aerolinea = obtener_aerolinea_segura(vuelo)
                carrier_iata = obtener_carrier_iata_seguro(vuelo)
                num_vuelo = obtener_num_vuelo_seguro(vuelo)
                
                aircraft_data = f_data.get('aircraft') or {}
                modelo_avion = (aircraft_data.get('model') or {}).get('code', 'N/A')
                matricula_avion = aircraft_data.get('registration', 'N/A')
                
                score_texto, prob, color, icono, _, _ = predecir_riesgo_ia(origen, target, carrier_iata, hora_vuelo, dicc_meteo_global)
                
                if prob in filtros_activos and (not filtro_aerolineas or aerolinea in filtro_aerolineas) and \
                   (not filtro_aeropuertos or origen in filtro_aeropuertos or target in filtro_aeropuertos) and \
                   (not filtro_vuelos or num_vuelo in filtro_vuelos):
                    
                    hora_prog_str = hora_vuelo.strftime('%H:%M')
                    hora_est_str = datetime.fromtimestamp(timestamp_est, timezone.utc).strftime('%H:%M') if timestamp_est else "N/A"
                    
                    datos_llegadas.append({
                        "Programado (Z)": hora_prog_str, "Estimado (Z)": hora_est_str, "Vuelo": num_vuelo,
                        "Aerolínea": aerolinea, "Aeronave": modelo_avion, "Matrícula": matricula_avion,
                        "Origen": origen, "Destino": target, "Probabilidad IA": score_texto, "Nivel Alerta": icono
                    })
    if datos_llegadas: st.dataframe(pd.DataFrame(datos_llegadas).sort_values("Programado (Z)"), use_container_width=True)
    else: st.info("No hay llegadas programadas en rutas estrictas para esta franja horaria.")

with tab3:
    datos_salidas = []
    for vuelo in salidas:
        f_data = vuelo.get('flight') or {}
        destino = obtener_iata_seguro(f_data.get('airport', {}).get('destination'))
        target = vuelo.get('target_apt')
        
        # REGLA DE EXCLUSIVIDAD
        if target not in AEROPUERTOS_VALIDOS or destino not in AEROPUERTOS_VALIDOS:
            continue

        timestamp_sched = obtener_timestamp_seguro(vuelo, 'departure', 'scheduled')
        timestamp_est = obtener_timestamp_seguro(vuelo, 'departure', 'estimated') or obtener_timestamp_seguro(vuelo, 'departure', 'real')
        
        if timestamp_sched:
            hora_vuelo = datetime.fromtimestamp(timestamp_sched, timezone.utc)
            if hora_actual <= hora_vuelo <= limite_tiempo:
                aerolinea = obtener_aerolinea_segura(vuelo)
                carrier_iata = obtener_carrier_iata_seguro(vuelo)
                num_vuelo = obtener_num_vuelo_seguro(vuelo)
                
                aircraft_data = f_data.get('aircraft') or {}
                modelo_avion = (aircraft_data.get('model') or {}).get('code', 'N/A')
                matricula_avion = aircraft_data.get('registration', 'N/A')
                
                score_texto, prob, color, icono, _, _ = predecir_riesgo_ia(target, destino, carrier_iata, hora_vuelo, dicc_meteo_global)
                
                if prob in filtros_activos and (not filtro_aerolineas or aerolinea in filtro_aerolineas) and \
                   (not filtro_aeropuertos or target in filtro_aeropuertos or destino in filtro_aeropuertos) and \
                   (not filtro_vuelos or num_vuelo in filtro_vuelos):
                    
                    hora_prog_str = hora_vuelo.strftime('%H:%M')
                    hora_est_str = datetime.fromtimestamp(timestamp_est, timezone.utc).strftime('%H:%M') if timestamp_est else "N/A"
                    
                    datos_salidas.append({
                        "Programado (Z)": hora_prog_str, "Estimado (Z)": hora_est_str, "Vuelo": num_vuelo,
                        "Aerolínea": aerolinea, "Aeronave": modelo_avion, "Matrícula": matricula_avion,
                        "Origen": target, "Destino": destino, "Probabilidad IA": score_texto, "Nivel Alerta": icono
                    })
    if datos_salidas: st.dataframe(pd.DataFrame(datos_salidas).sort_values("Programado (Z)"), use_container_width=True)
    else: st.info("No hay salidas programadas en rutas estrictas para esta franja horaria.")

with tab4:
    if aeropuerto_destino == "TODOS":
        st.warning("⚠️ Selecciona un aeropuerto específico en la barra lateral para ver su dashboard detallado y los reportes METAR/TAF.")
    else:
        col_dash1, col_dash2 = st.columns(2)
        
        with col_dash1:
            with st.container(border=True):
                st.markdown(f"**Evolución del Viento (Próximas 24h) - {aeropuerto_destino}**")
                datos_apt = dicc_meteo_global.get(aeropuerto_destino, {})
                if datos_apt:
                    vientos_futuros = {k: v['viento_kts'] for k, v in datos_apt.items() if k >= hora_actual.strftime("%Y-%m-%dT%H:00")}
                    vientos_limitados = dict(list(vientos_futuros.items())[:24])
                    
                    df_clima = pd.DataFrame(
                        list(vientos_limitados.values()), 
                        index=[datetime.strptime(k, "%Y-%m-%dT%H:%M").strftime("%H:%M") for k in vientos_limitados.keys()],
                        columns=["Viento (kts)"]
                    )
                    st.line_chart(df_clima, color="#2563eb")
                else:
                    st.info("Sin datos meteorológicos disponibles.")
                
            with st.container(border=True):
                st.markdown(f"**Precipitaciones Esperadas (Próximas 24h) - {aeropuerto_destino}**")
                if datos_apt:
                    precip_futuras = {k: v['precip'] for k, v in datos_apt.items() if k >= hora_actual.strftime("%Y-%m-%dT%H:00")}
                    precip_limitadas = dict(list(precip_futuras.items())[:24])
                    
                    df_precip = pd.DataFrame(
                        list(precip_limitadas.values()), 
                        index=[datetime.strptime(k, "%Y-%m-%dT%H:%M").strftime("%H:%M") for k in precip_limitadas.keys()],
                        columns=["Lluvia (mm)"]
                    )
                    st.bar_chart(df_precip, color="#2563eb")
                else:
                    st.info("Sin pronóstico de lluvia.")

        with col_dash2:
            with st.container(border=True):
                st.markdown(f"**Distribución de Aerolíneas (Próximas {horas_prediccion}h)**")
                todas_operaciones = []
                
                for v in llegadas + salidas:
                    f_data = v.get('flight') or {}
                    orig = obtener_iata_seguro(f_data.get('airport', {}).get('origin'))
                    dest = obtener_iata_seguro(f_data.get('airport', {}).get('destination'))
                    
                    if orig not in AEROPUERTOS_VALIDOS or dest not in AEROPUERTOS_VALIDOS:
                        continue

                    if v.get('target_apt') == aeropuerto_destino:
                        tipo = "Llegada" if v in llegadas else "Salida"
                        timestamp = obtener_timestamp_seguro(v, 'arrival' if tipo == "Llegada" else 'departure', 'scheduled')
                        if timestamp:
                            hora_vuelo = datetime.fromtimestamp(timestamp, timezone.utc)
                            if hora_actual <= hora_vuelo <= limite_tiempo:
                                al = obtener_aerolinea_segura(v)
                                if al != "N/A":
                                    todas_operaciones.append(al)
                
                if todas_operaciones:
                    df_aerolineas = pd.DataFrame(todas_operaciones, columns=["Aerolínea"])
                    conteo_al = df_aerolineas["Aerolínea"].value_counts().reset_index()
                    conteo_al.columns = ["Aerolínea", "Vuelos"]
                    
                    grafico_al = alt.Chart(conteo_al.head(10)).mark_bar(
                        color="#2563eb", cornerRadiusEnd=4
                    ).encode(
                        x=alt.X("Aerolínea", sort="-y", title=None, axis=alt.Axis(grid=False, labelAngle=-45)),
                        y=alt.Y("Vuelos", title="Cantidad de Vuelos", axis=alt.Axis(grid=True, gridColor="#e2e8f0")),
                        tooltip=["Aerolínea", "Vuelos"]
                    ).properties(height=300).configure_view(strokeWidth=0)
                    
                    st.altair_chart(grafico_al, use_container_width=True)
                else:
                    st.info("No hay suficientes datos de aerolíneas en esta franja horaria.")
        
        with st.container(border=True):
            st.markdown(f"**Carga Operativa: Vuelos Programados por Hora (Próximas {horas_prediccion}h)**")
            
            horas_continuas = [(hora_actual + timedelta(hours=i)).strftime('%H:00') for i in range(horas_prediccion + 1)]
            conteo_horas_dict = {h: 0 for h in horas_continuas}
            
            for v in llegadas + salidas:
                f_data = v.get('flight') or {}
                orig = obtener_iata_seguro(f_data.get('airport', {}).get('origin'))
                dest = obtener_iata_seguro(f_data.get('airport', {}).get('destination'))
                
                if orig not in AEROPUERTOS_VALIDOS or dest not in AEROPUERTOS_VALIDOS:
                    continue

                if v.get('target_apt') == aeropuerto_destino:
                    tipo = "Llegada" if v in llegadas else "Salida"
                    timestamp = obtener_timestamp_seguro(v, 'arrival' if tipo == "Llegada" else 'departure', 'scheduled')
                    if timestamp:
                        hora_vuelo = datetime.fromtimestamp(timestamp, timezone.utc)
                        if hora_actual <= hora_vuelo <= limite_tiempo:
                            hora_str = hora_vuelo.strftime('%H:00')
                            if hora_str in conteo_horas_dict:
                                conteo_horas_dict[hora_str] += 1
                                
            df_horas = pd.DataFrame(list(conteo_horas_dict.items()), columns=["Hora", "Vuelos"])
            grafico_horas = alt.Chart(df_horas).mark_bar(
                color="#ea580c", cornerRadiusTopLeft=4, cornerRadiusTopRight=4
            ).encode(
                x=alt.X("Hora", sort=None, title=None, axis=alt.Axis(grid=False)),
                y=alt.Y("Vuelos", title="Vuelos Programados", axis=alt.Axis(grid=True, gridColor="#e2e8f0")),
                tooltip=["Hora", "Vuelos"]
            ).properties(height=300).configure_view(strokeWidth=0)
            
            st.altair_chart(grafico_horas, use_container_width=True)
        
        st.markdown("---")
        st.markdown(f"### 📋 Reportes Aeronáuticos (METAR / TAF) - {aeropuerto_destino}")
        metar_text, taf_text = obtener_metar_taf(aeropuerto_destino)
        
        c_metar, c_taf = st.columns(2)
        with c_metar:
            st.markdown("**METAR (Condiciones Actuales):**")
            st.code(metar_text, language="text")
        with c_taf:
            st.markdown("**TAF (Pronóstico a 24/30h):**")
            st.code(taf_text, language="text")
