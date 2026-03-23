import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import math
import concurrent.futures
from datetime import datetime, timedelta, timezone
from FlightRadar24 import FlightRadar24API
import altair as alt
import joblib

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="IA Control de Operaciones", page_icon="✈️", layout="wide")

# --- CARGA DEL MODELO IA ---
@st.cache_resource
def cargar_modelo_ia():
    try:
        return joblib.load('modelo_vuelos_final.joblib')
    except Exception as e:
        st.error(f"⚠️ No se encontró el archivo 'modelo_vuelos_final.joblib'. {e}")
        return None

MODELO_IA = cargar_modelo_ia()

# --- BASE DE DATOS DE AEROPUERTOS ---
AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}

# --- BARRA LATERAL (SIDEBAR) ---
st.sidebar.title("⚙️ Configuración")

aeropuerto_destino = st.sidebar.selectbox(
    "📍 Selecciona el Aeropuerto",
    ["TODOS", "ATL", "ORD", "LAX", "JFK"],
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
    lista_iatas = list(AEROPUERTOS.keys())
    nombre_mostrar = "Estados Unidos (Global)"
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
            "hourly": "wind_speed_10m,wind_gusts_10m,visibility,cloudcover,temperature_2m,precipitation",
            "wind_speed_unit": "kn",
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
    clima_ideal = {'viento_kts': 0.0, 'rafagas_kts': 0.0, 'visib_m': 10000.0, 'nubes_pct': 0.0, 'temp_c': 15.0}
    if iata not in dicc_meteo: return clima_ideal
    hora_redondeada = hora_dt.replace(minute=0, second=0, microsecond=0)
    hora_str = hora_redondeada.strftime("%Y-%m-%dT%H:00")
    return dicc_meteo[iata].get(hora_str, clima_ideal)

def predecir_riesgo_ia(origen, destino, aerolinea, hora_vuelo_dt, dicc_meteo):
    if not MODELO_IA:
        return "N/A", "Desconocida", "gray", "⚪ Error IA"
        
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
    if prob < 0.25: return texto_prob, "BAJA", "green", "🟢 Baja"
    elif prob < 0.60: return texto_prob, "MEDIA", "orange", "🟡 Media"
    else: return texto_prob, "ALTA", "red", "🔴 Alta"

@st.cache_data(ttl=300)
def obtener_url_radar_lluvia():
    try:
        data = requests.get("https://api.rainviewer.com/public/weather-maps.json", timeout=5).json()
        latest_time = data['radar']['past'][-1]['time']
        return f"https://tilecache.rainviewer.com/v2/radar/{latest_time}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"
    except: return None

@st.cache_data(ttl=300)
def obtener_metar_taf(iata):
    icao = f"K{iata}"
    try:
        metar_req = requests.get(f"https://aviationweather.gov/api/data/metar?ids={icao}&format=raw", timeout=5)
        metar_txt = metar_req.text.strip() if metar_req.status_code == 200 and metar_req.text else f"No hay METAR para {icao}."
        taf_req = requests.get(f"https://aviationweather.gov/api/data/taf?ids={icao}&format=raw", timeout=5)
        taf_txt = taf_req.text.strip() if taf_req.status_code == 200 and taf_req.text else f"No hay TAF para {icao}."
        return metar_txt, taf_txt
    except:
        return "Error de conexión", "Error de conexión"

@st.cache_data(ttl=86400)
def obtener_foto_aeronave(matricula):
    if not matricula or matricula == "N/A": return None, None, None
    try:
        headers = {'User-Agent': 'FlightWxPro/1.0'}
        r = requests.get(f"https://api.planespotters.net/pub/photos/reg/{matricula}", headers=headers, timeout=3)
        if r.status_code == 200:
            data = r.json()
            if data.get('photos'):
                return data['photos'][0]['thumbnail_large']['src'], data['photos'][0]['link'], data['photos'][0]['photographer']
    except: pass
    return None, None, None

@st.cache_data(ttl=86400)
def obtener_mapa_aerolineas():
    try:
        aerolineas = FlightRadar24API().get_airlines()
        return {a.get('ICAO', a.get('Code')): a['Name'] for a in aerolineas if 'Name' in a}
    except: return {}

# --- EXTRACCIÓN SEGURA (BLINDAJE ANTICAÍDAS) ---
def obtener_iata_seguro(nodo):
    try:
        if isinstance(nodo, dict) and isinstance(nodo.get('code'), dict):
            return nodo['code'].get('iata', 'N/A')
    except: pass
    return 'N/A'

def obtener_num_vuelo_seguro(vuelo_dict):
    try:
        if isinstance(vuelo_dict, dict) and isinstance(vuelo_dict.get('flight'), dict):
            ident = vuelo_dict['flight'].get('identification')
            if isinstance(ident, dict) and isinstance(ident.get('number'), dict):
                return ident['number'].get('default', 'N/A')
    except: pass
    return 'N/A'

def obtener_aerolinea_segura(vuelo_dict):
    try:
        if isinstance(vuelo_dict, dict) and isinstance(vuelo_dict.get('flight'), dict):
            airline = vuelo_dict['flight'].get('airline')
            if isinstance(airline, dict):
                return airline.get('name', 'N/A')
    except: pass
    return 'N/A'

def obtener_carrier_iata_seguro(vuelo_dict):
    try:
        if isinstance(vuelo_dict, dict) and isinstance(vuelo_dict.get('flight'), dict):
            airline = vuelo_dict['flight'].get('airline')
            if isinstance(airline, dict) and isinstance(airline.get('code'), dict):
                return airline['code'].get('iata', 'N/A')
    except: pass
    return 'N/A'

def obtener_timestamp_seguro(vuelo_dict, tipo_vuelo, tipo_tiempo):
    """tipo_vuelo: 'arrival' o 'departure', tipo_tiempo: 'scheduled', 'estimated', 'real'"""
    try:
        if isinstance(vuelo_dict, dict) and isinstance(vuelo_dict.get('flight'), dict):
            time_node = vuelo_dict['flight'].get('time')
            if isinstance(time_node, dict) and isinstance(time_node.get(tipo_tiempo), dict):
                return time_node[tipo_tiempo].get(tipo_vuelo)
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

with st.spinner('Procesando telemetría y consultando a la IA...'):
    vuelos_aire_crudo, llegadas, salidas = obtener_datos_vuelos(lista_iatas)

# --- FILTROS LATERALES DINÁMICOS ---
st.sidebar.divider()
st.sidebar.markdown("### 🔎 Filtros Avanzados")
aerolineas_disponibles, aeropuertos_disponibles, numeros_vuelo_disponibles = set(), set(), set()

for v in llegadas + salidas:
    f_data = v.get('flight') or {}
    orig = obtener_iata_seguro(f_data.get('airport', {}).get('origin'))
    dest = obtener_iata_seguro(f_data.get('airport', {}).get('destination'))
    num = obtener_num_vuelo_seguro(v)
    al = obtener_aerolinea_segura(v)
    
    if al != "N/A": aerolineas_disponibles.add(al)
    if orig != "N/A": aeropuertos_disponibles.add(orig)
    if dest != "N/A": aeropuertos_disponibles.add(dest)
    if num != "N/A": numeros_vuelo_disponibles.add(num)

for v in vuelos_aire_crudo:
    dest = str(getattr(v, 'destination_airport_iata', 'N/A')).upper()
    if dest in lista_iatas:
        callsign = getattr(v, 'callsign', 'N/A')
        al_name = mapa_aerolineas.get(getattr(v, 'airline_icao', 'N/A'), "N/A")
        if callsign != "N/A": numeros_vuelo_disponibles.add(callsign)
        if al_name != "N/A": aerolineas_disponibles.add(al_name)

# Convertimos todo a texto (str) antes de ordenar para evitar el TypeError
filtro_aerolineas = st.sidebar.multiselect("✈️ Filtrar por Aerolínea", sorted([str(x) for x in aerolineas_disponibles]))
filtro_aeropuertos = st.sidebar.multiselect("📍 Filtrar por Aeropuerto", sorted([str(x) for x in aeropuertos_disponibles]))
filtro_vuelos = st.sidebar.multiselect("🔢 Filtrar por Nº Vuelo", sorted([str(x) for x in numeros_vuelo_disponibles]), placeholder="Buscar...")

vuelos_aire_filtrados = []
for v in vuelos_aire_crudo:
    destino = str(getattr(v, 'destination_airport_iata', 'N/A')).upper()
    origen = str(getattr(v, 'origin_airport_iata', 'N/A')).upper()
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futuros = {executor.submit(obtener_foto_aeronave, mat): mat for mat in matriculas_mapa}
        for f in concurrent.futures.as_completed(futuros):
            dicc_fotos[futuros[f]] = f.result()

hora_actual = datetime.now(timezone.utc)
limite_tiempo = hora_actual + timedelta(hours=horas_prediccion)

# --- PANEL SUPERIOR ---
st.title(f"✈️ Panel de Operaciones - {nombre_mostrar}")
st.markdown(f"**Powered by AI Predictions** | ⏱️ Hora del Sistema (UTC): `{hora_actual.strftime('%Y-%m-%d %H:%M:%S')} ZULU`")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Vuelos en Radar", len(vuelos_aire_filtrados), "Acercándose a bases")
col2.metric("Llegadas Prog.", len(llegadas), "Límite: 100")
col3.metric("Salidas Prog.", len(salidas), "Límite: 100")

if aeropuerto_destino == "TODOS":
    col4.metric("Estado Modelo IA", "ACTIVO", "Monitorizando Red")
else:
    clima_actual = extraer_clima_hora(aeropuerto_destino, hora_actual, dicc_meteo_global)
    col4.metric(f"Viento en {aeropuerto_destino}", f"{round(clima_actual['viento_kts'])} kts", "Ráfagas: " + str(round(clima_actual['rafagas_kts'])), delta_color="off")

st.divider()

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Radar IA en Vivo", "🛬 Llegadas", "🛫 Salidas", "📊 Dashboard Meteo"])

with tab1:
    map_center = [39.5, -98.35] if aeropuerto_destino == "TODOS" else AEROPUERTOS[aeropuerto_destino]["coords"]
    mapa = folium.Map(location=map_center, zoom_start=4 if aeropuerto_destino == "TODOS" else 5, tiles="CartoDB dark_matter")
    
    url_lluvia = obtener_url_radar_lluvia()
    if url_lluvia:
        folium.TileLayer(tiles=url_lluvia, attr='RainViewer', name='Radar', overlay=True, control=True, opacity=0.55).add_to(mapa)

    for apt in lista_iatas:
        folium.Marker(location=AEROPUERTOS[apt]["coords"], popup=f"<b>{AEROPUERTOS[apt]['nombre']}</b>", icon=folium.Icon(color="black", icon="building", prefix="fa")).add_to(mapa)

    vuelos_pintados = 0
    for vuelo in vuelos_aire_filtrados:
        destino = str(getattr(vuelo, 'destination_airport_iata', 'N/A')).upper()
        origen = str(getattr(vuelo, 'origin_airport_iata', 'N/A')).upper()
        callsign = getattr(vuelo, 'callsign', 'N/A')
        aerolinea_nom = getattr(vuelo, 'nombre_aerolinea_mapeado', 'N/A')
        aerolinea_iata = getattr(vuelo, 'airline_iata', 'N/A')
        
        horas_restantes = calcular_distancia_nm(vuelo.latitude, vuelo.longitude, AEROPUERTOS[destino]["coords"][0], AEROPUERTOS[destino]["coords"][1]) / max(vuelo.ground_speed, 1)
        eta = hora_actual + timedelta(hours=horas_restantes)
        
        # --- LLAMADA AL MODELO IA ---
        score_texto, prob, color, icono = predecir_riesgo_ia(origen, destino, aerolinea_iata, eta, dicc_meteo_global)
        
        if prob in filtros_activos:
            foto_url, foto_link, fotografo = dicc_fotos.get(getattr(vuelo, 'registration', 'N/A'), (None, None, None))
            foto_html = f"<img src='{foto_url}' width='100%' style='border-radius:4px;'>" if foto_url else ""
            
            html_popup = f"""
            <div style='font-family: Arial; font-size: 12px; width: 250px;'>
                {foto_html}
                <h4 style='color: {color}; margin-bottom: 2px;'>✈️ {callsign} | {aerolinea_nom}</h4>
                <b>Ruta:</b> {origen} ➔ <b>{destino}</b><br>
                <b>Alt:</b> {getattr(vuelo, 'altitude', 'N/A')} ft | <b>Vel:</b> {getattr(vuelo, 'ground_speed', 'N/A')} kts<br>
                <hr style='margin: 4px 0;'>
                <b>ETA:</b> {eta.strftime('%H:%M')}Z (Faltan {round(horas_restantes, 1)}h)<br>
                <b>RIESGO IA:</b> <span style='color:{color}; font-size:14px;'><b>{score_texto} ({prob})</b></span>
            </div>
            """
            folium.Marker(
                location=[vuelo.latitude, vuelo.longitude],
                popup=folium.Popup(html_popup, max_width=300),
                icon=folium.Icon(color=color, icon="plane", prefix="fa", angle=vuelo.heading)
            ).add_to(mapa)
            vuelos_pintados += 1

    st_folium(mapa, width=1200, height=600, returned_objects=[])
    st.success(f"Radar Activo: Analizando riesgo de **{vuelos_pintados}** aeronaves mediante el modelo predictivo.")

with tab2:
    datos_llegadas = []
    for vuelo in llegadas:
        timestamp = obtener_timestamp_seguro(vuelo, 'arrival', 'scheduled')
        if timestamp:
            hora_vuelo = datetime.fromtimestamp(timestamp, timezone.utc)
            if hora_actual <= hora_vuelo <= limite_tiempo:
                target = vuelo.get('target_apt')
                
                f_data = vuelo.get('flight') or {}
                origen = obtener_iata_seguro(f_data.get('airport', {}).get('origin'))
                aerolinea = obtener_aerolinea_segura(vuelo)
                carrier_iata = obtener_carrier_iata_seguro(vuelo)
                num_vuelo = obtener_num_vuelo_seguro(vuelo)
                
                # --- LLAMADA AL MODELO IA ---
                score_texto, prob, color, icono = predecir_riesgo_ia(origen, target, carrier_iata, hora_vuelo, dicc_meteo_global)
                
                if prob in filtros_activos and (not filtro_aerolineas or aerolinea in filtro_aerolineas) and \
                   (not filtro_aeropuertos or origen in filtro_aeropuertos or target in filtro_aeropuertos) and \
                   (not filtro_vuelos or num_vuelo in filtro_vuelos):
                    datos_llegadas.append({
                        "Prog (Z)": hora_vuelo.strftime('%H:%M'), "Vuelo": num_vuelo, "Aerolínea": aerolinea,
                        "Origen": origen, "Destino": target, "Probabilidad IA": score_texto, "Nivel Alerta": icono
                    })
    if datos_llegadas: st.dataframe(pd.DataFrame(datos_llegadas).sort_values("Prog (Z)"), use_container_width=True)

with tab3:
    datos_salidas = []
    for vuelo in salidas:
        timestamp = obtener_timestamp_seguro(vuelo, 'departure', 'scheduled')
        if timestamp:
            hora_vuelo = datetime.fromtimestamp(timestamp, timezone.utc)
            if hora_actual <= hora_vuelo <= limite_tiempo:
                target = vuelo.get('target_apt')
                
                f_data = vuelo.get('flight') or {}
                destino = obtener_iata_seguro(f_data.get('airport', {}).get('destination'))
                aerolinea = obtener_aerolinea_segura(vuelo)
                carrier_iata = obtener_carrier_iata_seguro(vuelo)
                num_vuelo = obtener_num_vuelo_seguro(vuelo)
                
                # --- LLAMADA AL MODELO IA ---
                score_texto, prob, color, icono = predecir_riesgo_ia(target, destino, carrier_iata, hora_vuelo, dicc_meteo_global)
                
                if prob in filtros_activos and (not filtro_aerolineas or aerolinea in filtro_aerolineas) and \
                   (not filtro_aeropuertos or target in filtro_aeropuertos or destino in filtro_aeropuertos) and \
                   (not filtro_vuelos or num_vuelo in filtro_vuelos):
                    datos_salidas.append({
                        "Prog (Z)": hora_vuelo.strftime('%H:%M'), "Vuelo": num_vuelo, "Aerolínea": aerolinea,
                        "Origen": target, "Destino": destino, "Probabilidad IA": score_texto, "Nivel Alerta": icono
                    })
    if datos_salidas: st.dataframe(pd.DataFrame(datos_salidas).sort_values("Prog (Z)"), use_container_width=True)

with tab4:
    if aeropuerto_destino == "TODOS":
        st.warning("⚠️ Selecciona un aeropuerto específico para ver gráficos meteorológicos y METAR.")
    else:
        st.markdown(f"**Pronóstico de Viento (Nudos) - {aeropuerto_destino}**")
        datos_v = dicc_meteo_global.get(aeropuerto_destino, {})
        if datos_v:
            vientos = {k: v['viento_kts'] for k, v in datos_v.items() if k >= hora_actual.strftime("%Y-%m-%dT%H:00")}
            st.line_chart(pd.DataFrame(list(vientos.values())[:24], index=[k[-5:] for k in list(vientos.keys())[:24]], columns=["Viento (kts)"]), color="#3b82f6")
        
        st.markdown(f"### 📋 Reportes Aeronáuticos Oficiales")
        metar_text, taf_text = obtener_metar_taf(aeropuerto_destino)
        c1, c2 = st.columns(2)
        c1.markdown("**METAR:**")
        c1.code(metar_text)
        c2.markdown("**TAF:**")
        c2.code(taf_text)
