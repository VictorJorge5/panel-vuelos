import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import math
import concurrent.futures
import time
from datetime import datetime, timedelta, timezone
from FlightRadar24 import FlightRadar24API
import altair as alt
import joblib

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="IA Control de Operaciones USA", page_icon="✈️", layout="wide")

st.markdown("""
    <style>
    header {visibility: hidden;}
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .block-container { padding-top: 2rem; padding-bottom: 1rem; }
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

AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}
AEROPUERTOS_VALIDOS = list(AEROPUERTOS.keys())

# --- BARRA LATERAL ---
st.sidebar.title("⚙️ Configuración")
aeropuerto_destino = st.sidebar.selectbox("📍 Selecciona el Aeropuerto", ["TODOS"] + AEROPUERTOS_VALIDOS, index=0)
horas_prediccion = st.sidebar.slider("⏳ Horas de previsión (Gráficos)", min_value=1, max_value=72, value=48)

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
    nombre_mostrar = "Estados Unidos (Global)"
else:
    lista_iatas = [aeropuerto_destino]
    nombre_mostrar = AEROPUERTOS[aeropuerto_destino]["nombre"]

# --- FUNCIONES MATEMÁTICAS Y METEO ---
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
            "latitude": AEROPUERTOS[apt]["coords"][0], "longitude": AEROPUERTOS[apt]["coords"][1],
            "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,visibility,cloudcover,temperature_2m,precipitation",
            "wind_speed_unit": "kn", "precipitation_unit": "mm", "timezone": "UTC"
        }
        try:
            datos = requests.get(url, params=parametros).json()
            tiempos = datos["hourly"]["time"]
            clima_hora = {}
            for i, t in enumerate(tiempos):
                clima_hora[t] = {
                    'viento_kts': datos["hourly"]["wind_speed_10m"][i] or 0, 'rafagas_kts': datos["hourly"]["wind_gusts_10m"][i] or 0,
                    'direccion': datos["hourly"]["wind_direction_10m"][i] or 0, 'visib_m': datos["hourly"]["visibility"][i] or 10000,
                    'nubes_pct': datos["hourly"]["cloudcover"][i] or 0, 'temp_c': datos["hourly"]["temperature_2m"][i] or 15,
                    'precip': datos["hourly"]["precipitation"][i] or 0
                }
            dicc_global[apt] = clima_hora
        except: dicc_global[apt] = {}
    return dicc_global

def extraer_clima_hora(iata, hora_dt, dicc_meteo):
    clima_ideal = {'viento_kts': 0.0, 'rafagas_kts': 0.0, 'direccion': 0.0, 'visib_m': 10000.0, 'nubes_pct': 0.0, 'temp_c': 15.0, 'precip': 0.0}
    if iata not in dicc_meteo: return clima_ideal
    hora_str = hora_dt.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")
    return dicc_meteo[iata].get(hora_str, clima_ideal)

def predecir_riesgo_ia(origen, destino, aerolinea, hora_vuelo_dt, dicc_meteo):
    if not MODELO_IA: return "N/A", "Desconocida", "gray", "⚪ Error IA", 0.0, 0.0
    c_orig = extraer_clima_hora(origen, hora_vuelo_dt, dicc_meteo)
    c_dest = extraer_clima_hora(destino, hora_vuelo_dt, dicc_meteo)
    
    try:
        enc_orig = MODELO_IA['le_orig'].transform([origen])[0] if origen in MODELO_IA['le_orig'].classes_ else 0
        enc_dest = MODELO_IA['le_dest'].transform([destino])[0] if destino in MODELO_IA['le_dest'].classes_ else 0
        enc_carr = MODELO_IA['le_carrier'].transform([aerolinea])[0] if aerolinea in MODELO_IA['le_carrier'].classes_ else 0
    except: enc_orig, enc_dest, enc_carr = 0, 0, 0

    input_df = pd.DataFrame([[
        c_orig['viento_kts'], c_orig['rafagas_kts'], c_orig['visib_m'], c_orig['nubes_pct'], c_orig['temp_c'],
        c_dest['viento_kts'], c_dest['rafagas_kts'], c_dest['visib_m'], c_dest['nubes_pct'], c_dest['temp_c'],
        enc_orig, enc_dest, enc_carr
    ]], columns=MODELO_IA['features'])
    
    prob = MODELO_IA['modelo'].predict_proba(input_df)[0][1]
    texto_prob = f"{prob:.1%}"
    if prob < 0.10: return texto_prob, "BAJA", "green", "🟢 Baja", c_dest['viento_kts'], c_dest['precip']
    elif prob < 0.20: return texto_prob, "MEDIA", "orange", "🟡 Media", c_dest['viento_kts'], c_dest['precip']
    else: return texto_prob, "ALTA", "red", "🔴 Alta", c_dest['viento_kts'], c_dest['precip']

@st.cache_data(ttl=300)
def obtener_url_radar_lluvia():
    try:
        data = requests.get("https://api.rainviewer.com/public/weather-maps.json", timeout=5).json()
        return f"{data.get('host', 'https://tilecache.rainviewer.com')}{data['radar']['past'][-1]['path']}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"
    except: return None

@st.cache_data(ttl=300)
def obtener_metar_taf(iata):
    icao = f"K{iata}"
    try:
        m = requests.get(f"https://aviationweather.gov/api/data/metar?ids={icao}&format=raw", timeout=5)
        t = requests.get(f"https://aviationweather.gov/api/data/taf?ids={icao}&format=raw", timeout=5)
        return m.text.strip() if m.status_code == 200 else "N/A", t.text.strip() if t.status_code == 200 else "N/A"
    except: return "Error", "Error"

@st.cache_data(ttl=86400)
def obtener_foto_aeronave_ia(matricula):
    if not matricula or matricula == "N/A": return None, None, None
    try:
        time.sleep(0.3)
        r = requests.get(f"https://api.planespotters.net/pub/photos/reg/{matricula}", headers={'User-Agent': 'Mozilla'}, timeout=10)
        if r.status_code == 200 and r.json().get('photos'):
            p = r.json()['photos'][0]
            return p['thumbnail_large']['src'], p['link'], p['photographer']
    except: pass
    return None, None, None

@st.cache_data(ttl=86400)
def obtener_mapa_aerolineas():
    try: return {a.get('ICAO', a.get('Code')): a['Name'] for a in FlightRadar24API().get_airlines() if 'Name' in a}
    except: return {}

# --- EXTRACCIÓN SEGURA ---
def obtener_iata_seguro(nodo):
    try: return nodo.get('code', {}).get('iata', 'N/A') if isinstance(nodo, dict) else 'N/A'
    except: return 'N/A'

def obtener_num_vuelo_seguro(vuelo_dict):
    try: return vuelo_dict.get('flight', {}).get('identification', {}).get('number', {}).get('default', 'N/A')
    except: return 'N/A'

def obtener_aerolinea_segura(vuelo_dict):
    try: return vuelo_dict.get('flight', {}).get('airline', {}).get('name', 'N/A')
    except: return 'N/A'

def obtener_carrier_iata_seguro(vuelo_dict):
    try: return vuelo_dict.get('flight', {}).get('airline', {}).get('code', {}).get('iata', 'N/A')
    except: return 'N/A'

def obtener_timestamp_seguro(vuelo_dict, tipo_vuelo, tipo_tiempo):
    try: return vuelo_dict.get('flight', {}).get('time', {}).get(tipo_tiempo, {}).get(tipo_vuelo)
    except: return None

def obtener_callsign_seguro(vuelo_dict):
    try: return vuelo_dict.get('flight', {}).get('identification', {}).get('callsign', 'N/A')
    except: return 'N/A'

def limpiar_duplicados(lista_vuelos):
    vistos, unicos = set(), []
    for v in lista_vuelos:
        try:
            fid = v['flight']['identification']['id']
            if fid not in vistos:
                vistos.add(fid)
                unicos.append(v)
        except: unicos.append(v)
    return unicos

# --- EXTRACCIÓN CON CRUCE DE DATOS (DATA IMPUTATION) ---
@st.cache_data(ttl=60)
def obtener_datos_vuelos(iatas_seleccionados):
    fr_api = FlightRadar24API()
    vuelos_aire, llegadas_crudas, salidas_crudas = [], [], []
    vuelos_validos_callsigns = set()
    rutas_validas = {}

    # 1. Extraer paneles masivamente (hasta 3 páginas por aeropuerto)
    for apt in iatas_seleccionados:
        for page in range(1, 4):
            try:
                detalles = fr_api.get_airport_details(apt, page=page)
                arr = detalles['airport']['pluginData']['schedule']['arrivals']['data']
                dep = detalles['airport']['pluginData']['schedule']['departures']['data']
                for v in arr: v['target_apt'] = apt; llegadas_crudas.append(v)
                for v in dep: v['target_apt'] = apt; salidas_crudas.append(v)
            except: break

    llegadas = limpiar_duplicados(llegadas_crudas)
    salidas = limpiar_duplicados(salidas_crudas)

    # 2. Construir diccionario para recuperar datos censurados
    for v in llegadas + salidas:
        orig = obtener_iata_seguro(v.get('flight', {}).get('airport', {}).get('origin'))
        dest = obtener_iata_seguro(v.get('flight', {}).get('airport', {}).get('destination'))
        
        if orig in AEROPUERTOS_VALIDOS and dest in AEROPUERTOS_VALIDOS:
            callsign = obtener_callsign_seguro(v)
            if callsign and callsign != 'N/A':
                vuelos_validos_callsigns.add(callsign)
                rutas_validas[callsign] = {'orig': orig, 'dest': dest}

    # 3. Mapear EE.UU. e inyectar rutas ocultas
    bounds_usa = "55.00,20.00,-130.00,-60.00"
    try:
        for v in fr_api.get_flights(bounds=bounds_usa):
            orig_map = str(getattr(v, 'origin_airport_iata', 'N/A')).upper()
            dest_map = str(getattr(v, 'destination_airport_iata', 'N/A')).upper()
            callsign = getattr(v, 'callsign', 'N/A')

            if (orig_map in AEROPUERTOS_VALIDOS and dest_map in AEROPUERTOS_VALIDOS) or (callsign in vuelos_validos_callsigns):
                if orig_map not in AEROPUERTOS_VALIDOS: setattr(v, 'origin_airport_iata', rutas_validas.get(callsign, {}).get('orig', orig_map))
                if dest_map not in AEROPUERTOS_VALIDOS: setattr(v, 'destination_airport_iata', rutas_validas.get(callsign, {}).get('dest', dest_map))
                vuelos_aire.append(v)
    except: pass

    return vuelos_aire, llegadas, salidas

# --- INICIALIZACIÓN ---
dicc_meteo_global = obtener_predicciones_globales(lista_iatas)
mapa_aerolineas = obtener_mapa_aerolineas()

with st.spinner('📡 Realizando cruce de telemetría ADS-B para revelar vuelos Hub-to-Hub...'):
    vuelos_aire_crudo, llegadas, salidas = obtener_datos_vuelos(lista_iatas)

# --- FILTROS ---
st.sidebar.divider()
st.sidebar.markdown("### 🔎 Filtros Avanzados")
aerolineas_disp, aeropuertos_disp, vuelos_disp = set(), set(), set()

for v in llegadas + salidas:
    orig = obtener_iata_seguro(v.get('flight', {}).get('airport', {}).get('origin'))
    dest = obtener_iata_seguro(v.get('flight', {}).get('airport', {}).get('destination'))
    if orig in AEROPUERTOS_VALIDOS and dest in AEROPUERTOS_VALIDOS:
        al = obtener_aerolinea_segura(v)
        num = obtener_num_vuelo_seguro(v)
        if al != "N/A": aerolineas_disp.add(al)
        if orig != "N/A": aeropuertos_disp.add(orig); aeropuertos_disp.add(dest)
        if num != "N/A": vuelos_disp.add(num)

for v in vuelos_aire_crudo:
    callsign = getattr(v, 'callsign', 'N/A')
    al_name = mapa_aerolineas.get(getattr(v, 'airline_icao', 'N/A'), "N/A")
    setattr(v, 'nombre_aerolinea_mapeado', al_name)
    if callsign != "N/A": vuelos_disp.add(callsign)
    if al_name != "N/A": aerolineas_disp.add(al_name)

filtro_aerolineas = st.sidebar.multiselect("✈️ Filtrar Aerolínea", sorted(list(aerolineas_disp)))
filtro_aeropuertos = st.sidebar.multiselect("📍 Filtrar Aeropuerto", sorted(list(aeropuertos_disp)))
filtro_vuelos = st.sidebar.multiselect("🔢 Filtrar Nº Vuelo", sorted(list(vuelos_disp)), placeholder="Buscar...")

vuelos_aire_filtrados = []
for v in vuelos_aire_crudo:
    dest = str(getattr(v, 'destination_airport_iata', 'N/A')).upper()
    orig = str(getattr(v, 'origin_airport_iata', 'N/A')).upper()
    callsign = getattr(v, 'callsign', 'N/A')
    al_vuelo = getattr(v, 'nombre_aerolinea_mapeado', 'N/A')
    
    if orig in lista_iatas or dest in lista_iatas:
        if (not filtro_aeropuertos or orig in filtro_aeropuertos or dest in filtro_aeropuertos) and \
           (not filtro_vuelos or callsign in filtro_vuelos) and \
           (not filtro_aerolineas or al_vuelo in filtro_aerolineas):
            vuelos_aire_filtrados.append(v)

dicc_fotos = {}
matriculas_mapa = list(set([getattr(v, 'registration', 'N/A') for v in vuelos_aire_filtrados if getattr(v, 'registration', 'N/A') != 'N/A']))
if matriculas_mapa:
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futuros = {executor.submit(obtener_foto_aeronave_ia, mat): mat for mat in matriculas_mapa}
        for f in concurrent.futures.as_completed(futuros): dicc_fotos[futuros[f]] = f.result()

hora_actual = datetime.now(timezone.utc)

# --- PANEL SUPERIOR ---
st.title(f"✈️ Panel de Operaciones - {nombre_mostrar}")
st.markdown(f"**Powered by AI Predictions** | ⏱️ Hora (UTC): `{hora_actual.strftime('%Y-%m-%d %H:%M:%S')} Z`")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Vuelos Hub-to-Hub", len(vuelos_aire_filtrados), "Volando ahora")

# SOLUCIÓN DE BUG DE NOMBRES
llegadas_tabla = sum(1 for v in llegadas if obtener_iata_seguro(v.get('flight', {}).get('airport', {}).get('origin')) in AEROPUERTOS_VALIDOS and v.get('target_apt') in AEROPUERTOS_VALIDOS and obtener_timestamp_seguro(v, 'arrival', 'scheduled') and datetime.fromtimestamp(obtener_timestamp_seguro(v, 'arrival', 'scheduled'), timezone.utc) >= hora_actual)
salidas_tabla = sum(1 for v in salidas if obtener_iata_seguro(v.get('flight', {}).get('airport', {}).get('destination')) in AEROPUERTOS_VALIDOS and v.get('target_apt') in AEROPUERTOS_VALIDOS and obtener_timestamp_seguro(v, 'departure', 'scheduled') and datetime.fromtimestamp(obtener_timestamp_seguro(v, 'departure', 'scheduled'), timezone.utc) >= hora_actual)

col2.metric("Llegadas Prog.", llegadas_tabla, "Histórico Ampliado")
col3.metric("Salidas Prog.", salidas_tabla, "Histórico Ampliado")

if aeropuerto_destino == "TODOS": col4.metric("Bases Monitorizadas", len(lista_iatas), "Red Completa")
else:
    clima_act = extraer_clima_hora(aeropuerto_destino, hora_actual, dicc_meteo_global)
    col4.metric(f"Viento {aeropuerto_destino}", f"{round(clima_act['viento_kts'])} kts", f"Ráfagas: {round(clima_act['rafagas_kts'])}", delta_color="off")

st.divider()

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Radar en Vivo (Hubs)", "🛬 Panel de Llegadas", "🛫 Panel de Salidas", "📊 Dashboard Analítico"])

with tab1:
    map_center = [39.5, -98.35] if aeropuerto_destino == "TODOS" else AEROPUERTOS[aeropuerto_destino]["coords"]
    mapa = folium.Map(location=map_center, zoom_start=4 if aeropuerto_destino == "TODOS" else 5, tiles="CartoDB dark_matter")
    if url_lluvia := obtener_url_radar_lluvia(): folium.TileLayer(tiles=url_lluvia, attr='RainViewer', name='Radar', overlay=True, opacity=0.55).add_to(mapa)

    hora_str_clave = hora_actual.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")
    for apt in lista_iatas:
        folium.Marker(location=AEROPUERTOS[apt]["coords"], popup=f"<b>{AEROPUERTOS[apt]['nombre']}</b>", icon=folium.Icon(color="black", icon="building", prefix="fa")).add_to(mapa)
        clima_apt = dicc_meteo_global.get(apt, {}).get(hora_str_clave, {})
        if (v := clima_apt.get('viento_kts')) is not None and (d := clima_apt.get('direccion')) is not None:
            html = f"<div style='font-family: Arial; font-size: 11px; color: #fff; font-weight: bold; background: rgba(15,23,42,0.8); border: 1px solid #3b82f6; padding: 2px 6px; border-radius: 4px; display: inline-flex; white-space: nowrap; transform: translate(15px, -15px);'><i class='fa fa-arrow-up' style='transform: rotate({(d+180)%360}deg); margin-right: 4px; color: #3b82f6;'></i>{round(v)} kts</div>"
            folium.Marker(location=AEROPUERTOS[apt]["coords"], icon=folium.DivIcon(html=html)).add_to(mapa)

    for vuelo in vuelos_aire_filtrados:
        destino = str(getattr(vuelo, 'destination_airport_iata', 'N/A')).upper()
        origen = str(getattr(vuelo, 'origin_airport_iata', 'N/A')).upper()
        mat = getattr(vuelo, 'registration', 'N/A')
        
        h_rest = calcular_distancia_nm(vuelo.latitude, vuelo.longitude, AEROPUERTOS[destino]["coords"][0], AEROPUERTOS[destino]["coords"][1]) / max(vuelo.ground_speed, 1) if destino in AEROPUERTOS else 0
        eta = hora_actual + timedelta(hours=h_rest)
        
        score, prob, color, icono, v_dst, ll_dst = predecir_riesgo_ia(origen, destino, getattr(vuelo, 'airline_iata', 'N/A'), eta, dicc_meteo_global)
        
        if prob in filtros_activos:
            f_url, f_link, f_phot = dicc_fotos.get(mat, (None, None, None))
            foto_html = f"""<div style="margin-bottom: 8px;"><a href="{f_link}" target="_blank"><img src="{f_url}" width="100%" style="border-radius: 4px; border: 1px solid #ccc; max-height: 140px; object-fit: cover;"></a><div style="font-size: 8px; color: #64748b; text-align: right; margin-top: 2px;">© {f_phot} | Planespotters.net</div></div>""" if f_url else f"""<div style="margin-bottom: 8px; text-align: center; background: #e2e8f0; padding: 10px; border-radius: 4px; font-size: 11px;"><a href="https://www.jetphotos.com/registration/{mat}" target="_blank" style="text-decoration: none; color: #3b82f6;">📷 Buscar {mat} en JetPhotos</a></div>"""

            v_spd = getattr(vuelo, 'vertical_speed', 0)
            html_popup = f"""<div style='font-family: Arial; font-size: 12px; width: 250px;'>{foto_html}<h4 style='margin-bottom: 2px; color: {color};'>✈️ {getattr(vuelo, 'callsign', 'N/A')} | {getattr(vuelo, 'nombre_aerolinea_mapeado', 'N/A')}</h4><div style='font-size: 10px; color: gray; margin-bottom: 8px;'>Matrícula: {mat} | Eq: {getattr(vuelo, 'aircraft_code', 'N/A')}</div><b>Ruta:</b> {origen} ➔ <b>{destino}</b><hr style='margin: 4px 0;'><div style='display: flex; justify-content: space-between;'><span><b>Alt:</b> {getattr(vuelo, 'altitude', 'N/A')} ft</span><span><b>Vel:</b> {getattr(vuelo, 'ground_speed', 'N/A')} kts</span></div><div style='display: flex; justify-content: space-between;'><span><b>Rumbo:</b> {getattr(vuelo, 'heading', 'N/A')}°</span><span><b>V/S:</b> <span style='color: {"green" if v_spd>0 else "red" if v_spd<0 else "gray"};'>{(f"+{v_spd}" if v_spd>0 else str(v_spd))} fpm</span></span></div><hr style='margin: 4px 0;'><b>Faltan:</b> {round(h_rest, 1)} h <b>(ETA:</b> {eta.strftime('%H:%M')}Z)<br><b>Riesgo IA:</b> <span style='color:{color}'><b>{score} ({prob})</b></span><br><b>Viento:</b> {round(v_dst)} kts | <b>Lluvia:</b> {round(ll_dst, 1)} mm</div>"""
            folium.Marker(location=[vuelo.latitude, vuelo.longitude], popup=folium.Popup(html_popup, max_width=300), icon=folium.Icon(color=color, icon="plane", prefix="fa", angle=vuelo.heading)).add_to(mapa)

    st_folium(mapa, width=1200, height=600, returned_objects=[])

with tab2:
    datos_llegadas = []
    for v in llegadas:
        f_data = v.get('flight') or {}
        orig = obtener_iata_seguro(f_data.get('airport', {}).get('origin'))
        target = v.get('target_apt')
        if orig not in AEROPUERTOS_VALIDOS or target not in AEROPUERTOS_VALIDOS: continue

        t_sched = obtener_timestamp_seguro(v, 'arrival', 'scheduled')
        if t_sched:
            h_vuelo = datetime.fromtimestamp(t_sched, timezone.utc)
            if h_vuelo >= hora_actual:
                al = obtener_aerolinea_segura(v)
                num = obtener_num_vuelo_seguro(v)
                score, prob, _, icono, _, _ = predecir_riesgo_ia(orig, target, obtener_carrier_iata_seguro(v), h_vuelo, dicc_meteo_global)
                
                if prob in filtros_activos and (not filtro_aerolineas or al in filtro_aerolineas) and (not filtro_aeropuertos or orig in filtro_aeropuertos or target in filtro_aeropuertos) and (not filtro_vuelos or num in filtro_vuelos):
                    t_est = obtener_timestamp_seguro(v, 'arrival', 'estimated') or obtener_timestamp_seguro(v, 'arrival', 'real')
                    datos_llegadas.append({
                        "Prog (Z)": h_vuelo.strftime('%Y-%m-%d %H:%M'), "Est (Z)": datetime.fromtimestamp(t_est, timezone.utc).strftime('%H:%M') if t_est else "N/A",
                        "Vuelo": num, "Aerolínea": al, "Eq": f_data.get('aircraft', {}).get('model', {}).get('code', 'N/A'),
                        "Matrícula": f_data.get('aircraft', {}).get('registration', 'N/A'), "Orig": orig, "Dest": target, "Riesgo IA": score, "Alerta": icono
                    })
    if datos_llegadas: st.dataframe(pd.DataFrame(datos_llegadas).sort_values("Prog (Z)"), use_container_width=True)
    else: st.info("No hay llegadas programadas.")

with tab3:
    datos_salidas = []
    for v in salidas:
        f_data = v.get('flight') or {}
        dest = obtener_iata_seguro(f_data.get('airport', {}).get('destination'))
        target = v.get('target_apt')
        if target not in AEROPUERTOS_VALIDOS or dest not in AEROPUERTOS_VALIDOS: continue

        t_sched = obtener_timestamp_seguro(v, 'departure', 'scheduled')
        if t_sched:
            h_vuelo = datetime.fromtimestamp(t_sched, timezone.utc)
            if h_vuelo >= hora_actual:
                al = obtener_aerolinea_segura(v)
                num = obtener_num_vuelo_seguro(v)
                score, prob, _, icono, _, _ = predecir_riesgo_ia(target, dest, obtener_carrier_iata_seguro(v), h_vuelo, dicc_meteo_global)
                
                if prob in filtros_activos and (not filtro_aerolineas or al in filtro_aerolineas) and (not filtro_aeropuertos or target in filtro_aeropuertos or dest in filtro_aeropuertos) and (not filtro_vuelos or num in filtro_vuelos):
                    t_est = obtener_timestamp_seguro(v, 'departure', 'estimated') or obtener_timestamp_seguro(v, 'departure', 'real')
                    datos_salidas.append({
                        "Prog (Z)": h_vuelo.strftime('%Y-%m-%d %H:%M'), "Est (Z)": datetime.fromtimestamp(t_est, timezone.utc).strftime('%H:%M') if t_est else "N/A",
                        "Vuelo": num, "Aerolínea": al, "Eq": f_data.get('aircraft', {}).get('model', {}).get('code', 'N/A'),
                        "Matrícula": f_data.get('aircraft', {}).get('registration', 'N/A'), "Orig": target, "Dest": dest, "Riesgo IA": score, "Alerta": icono
                    })
    if datos_salidas: st.dataframe(pd.DataFrame(datos_salidas).sort_values("Prog (Z)"), use_container_width=True)
    else: st.info("No hay salidas programadas.")

with tab4:
    if aeropuerto_destino == "TODOS": st.warning("⚠️ Selecciona un aeropuerto específico para ver su dashboard.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            with st.container(border=True):
                st.markdown(f"**Viento (24h) - {aeropuerto_destino}**")
                if d := dicc_meteo_global.get(aeropuerto_destino):
                    st.line_chart(pd.DataFrame(list({k: v['viento_kts'] for k, v in d.items() if k >= hora_actual.strftime("%Y-%m-%dT%H:00")}.values())[:24], index=[datetime.strptime(k, "%Y-%m-%dT%H:%M").strftime("%H:%M") for k in list({k: v['viento_kts'] for k, v in d.items() if k >= hora_actual.strftime("%Y-%m-%dT%H:00")}.keys())[:24]], columns=["Viento (kts)"]), color="#2563eb")
        with c2:
            with st.container(border=True):
                st.markdown(f"**Precipitaciones (24h) - {aeropuerto_destino}**")
                if d := dicc_meteo_global.get(aeropuerto_destino):
                    st.bar_chart(pd.DataFrame(list({k: v['precip'] for k, v in d.items() if k >= hora_actual.strftime("%Y-%m-%dT%H:00")}.values())[:24], index=[datetime.strptime(k, "%Y-%m-%dT%H:%M").strftime("%H:%M") for k in list({k: v['precip'] for k, v in d.items() if k >= hora_actual.strftime("%Y-%m-%dT%H:00")}.keys())[:24]], columns=["Lluvia (mm)"]), color="#2563eb")
