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

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Control de Operaciones", page_icon="✈️", layout="wide")

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

st.sidebar.markdown("### 🔍 Filtros de Visualización")
mostrar_baja = st.sidebar.checkbox("🟢 Probabilidad BAJA", value=True)
mostrar_moderada = st.sidebar.checkbox("🟠 Probabilidad MODERADA", value=True)
mostrar_alta = st.sidebar.checkbox("🔴 Probabilidad ALTA", value=True)

filtros_activos = []
if mostrar_baja: filtros_activos.append("BAJA")
if mostrar_moderada: filtros_activos.append("MODERADA")
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
            "hourly": "wind_speed_10m,wind_direction_10m,precipitation",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
            "timezone": "UTC"
        }
        try:
            datos = requests.get(url, params=parametros).json()
            tiempos = datos["hourly"]["time"]
            vientos = datos["hourly"]["wind_speed_10m"]
            direcciones = datos["hourly"]["wind_direction_10m"]
            precipitaciones = datos["hourly"]["precipitation"]
            
            dicc_global[apt] = {
                "viento": {tiempos[i]: vientos[i] for i in range(len(tiempos))},
                "direccion": {tiempos[i]: direcciones[i] for i in range(len(tiempos))},
                "precipitacion": {tiempos[i]: precipitaciones[i] for i in range(len(tiempos))}
            }
        except:
            dicc_global[apt] = {"viento": {}, "direccion": {}, "precipitacion": {}}
    return dicc_global

@st.cache_data(ttl=300)
def obtener_url_radar_lluvia():
    try:
        data = requests.get("https://api.rainviewer.com/public/weather-maps.json", timeout=5).json()
        latest_time = data['radar']['past'][-1]['time']
        return f"https://tilecache.rainviewer.com/v2/radar/{latest_time}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"
    except:
        return None

def evaluar_probabilidad_cancelacion(hora_dt, dicc_meteo_apt):
    if not dicc_meteo_apt or not dicc_meteo_apt.get("viento"):
        return "?", "Desconocida", "gray", "⚪ Desconocida"
        
    minutos = hora_dt.minute
    if minutos >= 30:
        hora_redondeada = hora_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        hora_redondeada = hora_dt.replace(minute=0, second=0, microsecond=0)
        
    hora_clave = hora_redondeada.strftime("%Y-%m-%dT%H:00")
    viento_kmh = dicc_meteo_apt["viento"].get(hora_clave)
    
    if viento_kmh is None:
        return "?", "Sin Datos", "gray", "⚪ Sin Datos"
        
    viento_kmh = round(viento_kmh, 1)
    
    if viento_kmh < 10:
        return viento_kmh, "BAJA", "green", "🟢 Baja"
    elif 10 <= viento_kmh <= 40:
        return viento_kmh, "MODERADA", "orange", "🟠 Moderada"
    else:
        return viento_kmh, "ALTA", "red", "🔴 Alta"

@st.cache_data(ttl=300)
def obtener_metar_taf(iata):
    # En EE.UU. continental, el ICAO es una 'K' delante del IATA
    icao = f"K{iata}"
    try:
        metar_req = requests.get(f"https://aviationweather.gov/api/data/metar?ids={icao}&format=raw", timeout=5)
        metar_txt = metar_req.text.strip() if metar_req.status_code == 200 and metar_req.text else f"No hay METAR disponible para {icao}."
        
        taf_req = requests.get(f"https://aviationweather.gov/api/data/taf?ids={icao}&format=raw", timeout=5)
        taf_txt = taf_req.text.strip() if taf_req.status_code == 200 and taf_req.text else f"No hay TAF disponible para {icao}."
        
        return metar_txt, taf_txt
    except:
        return "Error de conexión al servidor de la FAA.", "Error de conexión al servidor de la FAA."

# --- EXTRACCIÓN DE FOTOS CON COPYRIGHT (PLANESPOTTERS) ---
@st.cache_data(ttl=86400)
def obtener_foto_aeronave(matricula):
    if not matricula or matricula == "N/A":
        return None, None, None
    try:
        headers = {'User-Agent': 'FlightWxPro/1.0'}
        url = f"https://api.planespotters.net/pub/photos/reg/{matricula}"
        r = requests.get(url, headers=headers, timeout=3)
        if r.status_code == 200:
            data = r.json()
            if data.get('photos'):
                foto_url = data['photos'][0]['thumbnail_large']['src']
                link = data['photos'][0]['link']
                fotografo = data['photos'][0]['photographer']
                return foto_url, link, fotografo
    except:
        pass
    return None, None, None

# --- EXTRACCIÓN SEGURA ---
@st.cache_data(ttl=86400)
def obtener_mapa_aerolineas():
    try:
        api = FlightRadar24API()
        aerolineas = api.get_airlines()
        mapa = {}
        for a in aerolineas:
            if 'ICAO' in a and 'Name' in a:
                mapa[a['ICAO']] = a['Name']
            if 'Code' in a and 'Name' in a:
                mapa[a['Code']] = a['Name']
        return mapa
    except:
        return {}

def obtener_iata_seguro(nodo_aeropuerto):
    try:
        if isinstance(nodo_aeropuerto, dict):
            code = nodo_aeropuerto.get('code')
            if isinstance(code, dict):
                return code.get('iata', "N/A")
    except Exception:
        pass
    return "N/A"

def obtener_aerolinea(vuelo):
    try:
        if isinstance(vuelo, dict):
            flight = vuelo.get('flight')
            if isinstance(flight, dict):
                airline = flight.get('airline')
                if isinstance(airline, dict):
                    return airline.get('name', "N/A")
    except Exception:
        pass
    return "N/A"

def obtener_num_vuelo_seguro(vuelo_dict):
    try:
        ident = (vuelo_dict.get('flight') or {}).get('identification') or {}
        num = ident.get('number') or {}
        resultado = num.get('default')
        return resultado if resultado else "N/A"
    except Exception:
        return "N/A"

# --- FUNCIONES DE FLIGHTRADAR ---
@st.cache_data(ttl=60)
def obtener_datos_vuelos(iatas):
    fr_api = FlightRadar24API()
    vuelos_aire_crudo, llegadas, salidas = [], [], []
    
    try:
        todos_vuelos = fr_api.get_flights()
        for v in todos_vuelos:
            if v.ground_speed > 0:
               for apt in iatas:
                   coords = AEROPUERTOS[apt]["coords"]
                   dist = calcular_distancia_nm(v.latitude, v.longitude, coords[0], coords[1])
                   if dist < 500:
                      vuelos_aire_crudo.append(v)
                      break
    except Exception as e:
        pass

    for apt in iatas:
        try:
            detalles_apt = fr_api.get_airport_details(apt)
            arr = detalles_apt['airport']['pluginData']['schedule']['arrivals']['data']
            dep = detalles_apt['airport']['pluginData']['schedule']['departures']['data']
            
            for v in arr: v['target_apt'] = apt
            for v in dep: v['target_apt'] = apt
                
            llegadas.extend(arr)
            salidas.extend(dep)
        except Exception as e:
            pass
            
    return vuelos_aire_crudo, llegadas, salidas

# --- CARGA DE DATOS PRINCIPAL ---
dicc_meteo_global = obtener_predicciones_globales(lista_iatas)
mapa_aerolineas = obtener_mapa_aerolineas()

with st.spinner('Actualizando posiciones y radares...'):
    vuelos_aire_crudo, llegadas, salidas = obtener_datos_vuelos(lista_iatas)

# --- CREACIÓN DE FILTROS AVANZADOS EN LA BARRA LATERAL ---
st.sidebar.divider()
st.sidebar.markdown("### 🔎 Filtros Avanzados")

aerolineas_disponibles = set()
aeropuertos_disponibles = set()
numeros_vuelo_disponibles = set()

for v in llegadas + salidas:
    al = obtener_aerolinea(v)
    if al != "N/A": aerolineas_disponibles.add(al)
    
    orig = obtener_iata_seguro(v.get('flight', {}).get('airport', {}).get('origin'))
    dest = obtener_iata_seguro(v.get('flight', {}).get('airport', {}).get('destination'))
    num = obtener_num_vuelo_seguro(v)
    
    if orig != "N/A": aeropuertos_disponibles.add(orig)
    if dest != "N/A": aeropuertos_disponibles.add(dest)
    if num != "N/A": numeros_vuelo_disponibles.add(num)

for v in vuelos_aire_crudo:
    destino_temp = str(getattr(v, 'destination_airport_iata', 'N/A')).upper()
    if destino_temp in lista_iatas:
        callsign = getattr(v, 'callsign', 'N/A')
        if callsign != "N/A" and callsign.strip() != "":
            numeros_vuelo_disponibles.add(callsign)
            
        airline_icao = getattr(v, 'airline_icao', 'N/A')
        al_name = mapa_aerolineas.get(airline_icao, "N/A")
        if al_name != "N/A":
            aerolineas_disponibles.add(al_name)

filtro_aerolineas = st.sidebar.multiselect("✈️ Filtrar por Aerolínea", sorted(list(aerolineas_disponibles)))
filtro_aeropuertos = st.sidebar.multiselect("📍 Filtrar por Aeropuerto", sorted(list(aeropuertos_disponibles)))
filtro_vuelos = st.sidebar.multiselect("🔢 Filtrar por Nº Vuelo", sorted(list(numeros_vuelo_disponibles)), placeholder="Escribe para buscar...")

# --- FILTRADO ESTRICTO DE VUELOS EN AIRE PARA EL MAPA ---
vuelos_aire_filtrados = []
for v in vuelos_aire_crudo:
    destino = str(getattr(v, 'destination_airport_iata', 'N/A')).upper()
    origen = str(getattr(v, 'origin_airport_iata', 'N/A')).upper()
    callsign = getattr(v, 'callsign', 'N/A')
    
    airline_icao = getattr(v, 'airline_icao', 'N/A')
    aerolinea_vuelo = mapa_aerolineas.get(airline_icao, "N/A")
    
    if destino in lista_iatas:
        pasa_filtro_apt = (not filtro_aeropuertos) or (origen in filtro_aeropuertos) or (destino in filtro_aeropuertos)
        pasa_filtro_vuelo = (not filtro_vuelos) or (callsign in filtro_vuelos)
        pasa_filtro_al = (not filtro_aerolineas) or (aerolinea_vuelo in filtro_aerolineas)
        
        if pasa_filtro_apt and pasa_filtro_vuelo and pasa_filtro_al:
            v.nombre_aerolinea_mapeado = aerolinea_vuelo
            vuelos_aire_filtrados.append(v)

# --- PRE-CARGA MULTIHILO DE FOTOGRAFÍAS ---
matriculas_mapa = list(set([
    getattr(v, 'registration', 'N/A') 
    for v in vuelos_aire_filtrados 
    if getattr(v, 'registration', 'N/A') != 'N/A'
]))

dicc_fotos = {}
if matriculas_mapa:
    with st.spinner(f"Descargando {len(matriculas_mapa)} imágenes en paralelo..."):
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futuros = {executor.submit(obtener_foto_aeronave, mat): mat for mat in matriculas_mapa}
            for futuro in concurrent.futures.as_completed(futuros):
                mat = futuros[futuro]
                try:
                    dicc_fotos[mat] = futuro.result()
                except Exception:
                    dicc_fotos[mat] = (None, None, None)

# --- CÁLCULO DE TIEMPOS ---
hora_actual = datetime.now(timezone.utc)
limite_tiempo = hora_actual + timedelta(hours=horas_prediccion)

# --- CUERPO PRINCIPAL ---
st.title(f"✈️ Panel de Operaciones - {nombre_mostrar}")
st.markdown(f"""
**Monitorización de vuelos para:** {', '.join(lista_iatas)}  
⏱️ **Hora del Sistema (UTC):** `{hora_actual.strftime('%Y-%m-%d %H:%M:%S')} ZULU`
""")

# --- KPIs SUPERIORES ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Vuelos Escaneados", len(vuelos_aire_filtrados), "En aire hacia bases")
col2.metric("Llegadas Prog.", len(llegadas), f"Límite API: 100/Aeropuerto")
col3.metric("Salidas Prog.", len(salidas), f"Límite API: 100/Aeropuerto")

if aeropuerto_destino == "TODOS":
    col4.metric("Bases Monitorizadas", len(lista_iatas), "Red Completa")
else:
    viento_actual, prob_actual, _, _ = evaluar_probabilidad_cancelacion(hora_actual, dicc_meteo_global.get(aeropuerto_destino, {}))
    col4.metric(f"Viento en {aeropuerto_destino}", f"{viento_actual} km/h", prob_actual, delta_color="inverse" if prob_actual == "ALTA" else "normal")

st.divider()

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Radar en Vivo", "🛬 Panel de Llegadas", "🛫 Panel de Salidas", "📊 Dashboard Analítico"])

with tab1:
    if aeropuerto_destino == "TODOS":
        map_center = [39.5, -98.35]
        zoom = 4
    else:
        map_center = AEROPUERTOS[aeropuerto_destino]["coords"]
        zoom = 5
        
    mapa = folium.Map(location=map_center, zoom_start=zoom, tiles="CartoDB dark_matter")
    
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
    
    hora_redondeada = hora_actual.replace(minute=0, second=0, microsecond=0)
    hora_clave = hora_redondeada.strftime("%Y-%m-%dT%H:00")

    for apt in lista_iatas:
        folium.Marker(
            location=AEROPUERTOS[apt]["coords"], 
            popup=f"<b>{AEROPUERTOS[apt]['nombre']} ({apt})</b>", 
            icon=folium.Icon(color="black", icon="building", prefix="fa")
        ).add_to(mapa)

        dir_viento = dicc_meteo_global.get(apt, {}).get("direccion", {}).get(hora_clave)
        vel_viento = dicc_meteo_global.get(apt, {}).get("viento", {}).get(hora_clave)
        
        if vel_viento is not None and dir_viento is not None:
            rotacion_flecha = (dir_viento + 180) % 360
            html_vector_viento = f"""
            <div style='font-family: Arial; font-size: 11px; color: #fff; font-weight: bold; background: rgba(15,23,42,0.8); border: 1px solid #3b82f6; padding: 2px 6px; border-radius: 4px; display: inline-flex; align-items: center; white-space: nowrap; transform: translate(15px, -15px);'>
                <i class='fa fa-arrow-up' style='transform: rotate({rotacion_flecha}deg); margin-right: 4px; color: #3b82f6;'></i>
                {vel_viento} km/h
            </div>
            """
            folium.Marker(
                location=AEROPUERTOS[apt]["coords"],
                icon=folium.DivIcon(html=html_vector_viento)
            ).add_to(mapa)
    
    vuelos_pintados = 0
    for vuelo in vuelos_aire_filtrados:
        destino = str(getattr(vuelo, 'destination_airport_iata', 'N/A')).upper()
        origen = str(getattr(vuelo, 'origin_airport_iata', 'N/A')).upper()
        callsign = getattr(vuelo, 'callsign', 'N/A')
        aerolinea = getattr(vuelo, 'nombre_aerolinea_mapeado', 'N/A')
        
        altitud = getattr(vuelo, 'altitude', 'N/A')
        velocidad = getattr(vuelo, 'ground_speed', 'N/A')
        rumbo = getattr(vuelo, 'heading', 'N/A')
        matricula = getattr(vuelo, 'registration', 'N/A')
        modelo = getattr(vuelo, 'aircraft_code', 'N/A')
        v_speed = getattr(vuelo, 'vertical_speed', 0)
        
        v_speed_str = f"+{v_speed}" if v_speed > 0 else str(v_speed)
        v_speed_color = "green" if v_speed > 0 else "red" if v_speed < 0 else "gray"
        
        coords_destino = AEROPUERTOS[destino]["coords"]
        dist = calcular_distancia_nm(vuelo.latitude, vuelo.longitude, coords_destino[0], coords_destino[1])
        horas_restantes = dist / max(vuelo.ground_speed, 1)
        eta = hora_actual + timedelta(hours=horas_restantes)
        
        viento, prob, color, icono = evaluar_probabilidad_cancelacion(eta, dicc_meteo_global[destino])
        
        if prob in filtros_activos:
            foto_url, foto_link, fotografo = dicc_fotos.get(matricula, (None, None, None))
            
            foto_html = ""
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
                <h4 style='margin-bottom: 2px; color: {color};'>✈️ {callsign} | {aerolinea}</h4>
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
                <b>Riesgo:</b> <span style='color:{color}'><b>{prob}</b></span> | <b>Viento:</b> {viento} km/h
            </div>
            """
            
            folium.Marker(
                location=[vuelo.latitude, vuelo.longitude],
                popup=folium.Popup(html_popup, max_width=300),
                icon=folium.Icon(color=color, icon="plane", prefix="fa", angle=vuelo.heading)
            ).add_to(mapa)
            vuelos_pintados += 1

    st_folium(mapa, width=1200, height=600, returned_objects=[])
    st.success(f"Radar Activo: Mostrando **{vuelos_pintados}** aviones con telemetría en vivo.")

with tab2:
    datos_llegadas = []
    for vuelo in llegadas:
        try:
            timestamp_sched = vuelo.get('flight', {}).get('time', {}).get('scheduled', {}).get('arrival')
            timestamp_est = vuelo.get('flight', {}).get('time', {}).get('estimated', {}).get('arrival') or vuelo.get('flight', {}).get('time', {}).get('real', {}).get('arrival')
            
            if timestamp_sched:
                hora_vuelo = datetime.fromtimestamp(timestamp_sched, timezone.utc)
                if hora_actual <= hora_vuelo <= limite_tiempo:
                    
                    target_apt = vuelo.get('target_apt')
                    viento, prob, color, icono = evaluar_probabilidad_cancelacion(hora_vuelo, dicc_meteo_global.get(target_apt, {}))
                    
                    origen = obtener_iata_seguro(vuelo.get('flight', {}).get('airport', {}).get('origin'))
                    aerolinea = obtener_aerolinea(vuelo)
                    num_vuelo = obtener_num_vuelo_seguro(vuelo)
                    
                    aircraft_data = vuelo.get('flight', {}).get('aircraft') or {}
                    modelo_avion = (aircraft_data.get('model') or {}).get('code', 'N/A')
                    matricula_avion = aircraft_data.get('registration', 'N/A')
                    
                    pasa_filtro_al = (not filtro_aerolineas) or (aerolinea in filtro_aerolineas)
                    pasa_filtro_apt = (not filtro_aeropuertos) or (origen in filtro_aeropuertos or target_apt in filtro_aeropuertos)
                    pasa_filtro_vuelo = (not filtro_vuelos) or (num_vuelo in filtro_vuelos)
                    
                    if prob in filtros_activos and pasa_filtro_al and pasa_filtro_apt and pasa_filtro_vuelo:
                        
                        hora_prog_str = hora_vuelo.strftime('%H:%M')
                        hora_est_str = datetime.fromtimestamp(timestamp_est, timezone.utc).strftime('%H:%M') if timestamp_est else "N/A"
                        
                        datos_llegadas.append({
                            "Programado (Z)": hora_prog_str,
                            "Estimado (Z)": hora_est_str,
                            "Vuelo": num_vuelo,
                            "Aerolínea": aerolinea,
                            "Aeronave": modelo_avion,
                            "Matrícula": matricula_avion,
                            "Origen": origen,
                            "Destino": target_apt,
                            "Riesgo": icono
                        })
        except:
            pass
            
    if datos_llegadas:
        df_arr = pd.DataFrame(datos_llegadas).sort_values(by="Programado (Z)")
        st.dataframe(df_arr, use_container_width=True)
    else:
        st.info("No hay llegadas programadas que coincidan con los filtros en este rango de horas.")

with tab3:
    datos_salidas = []
    for vuelo in salidas:
        try:
            timestamp_sched = vuelo.get('flight', {}).get('time', {}).get('scheduled', {}).get('departure')
            timestamp_est = vuelo.get('flight', {}).get('time', {}).get('estimated', {}).get('departure') or vuelo.get('flight', {}).get('time', {}).get('real', {}).get('departure')
            
            if timestamp_sched:
                hora_vuelo = datetime.fromtimestamp(timestamp_sched, timezone.utc)
                if hora_actual <= hora_vuelo <= limite_tiempo:
                    
                    target_apt = vuelo.get('target_apt')
                    viento, prob, color, icono = evaluar_probabilidad_cancelacion(hora_vuelo, dicc_meteo_global.get(target_apt, {}))
                    
                    destino = obtener_iata_seguro(vuelo.get('flight', {}).get('airport', {}).get('destination'))
                    aerolinea = obtener_aerolinea(vuelo)
                    num_vuelo = obtener_num_vuelo_seguro(vuelo)
                    
                    aircraft_data = vuelo.get('flight', {}).get('aircraft') or {}
                    modelo_avion = (aircraft_data.get('model') or {}).get('code', 'N/A')
                    matricula_avion = aircraft_data.get('registration', 'N/A')
                    
                    pasa_filtro_al = (not filtro_aerolineas) or (aerolinea in filtro_aerolineas)
                    pasa_filtro_apt = (not filtro_aeropuertos) or (destino in filtro_aeropuertos or target_apt in filtro_aeropuertos)
                    pasa_filtro_vuelo = (not filtro_vuelos) or (num_vuelo in filtro_vuelos)
                    
                    if prob in filtros_activos and pasa_filtro_al and pasa_filtro_apt and pasa_filtro_vuelo:
                        
                        hora_prog_str = hora_vuelo.strftime('%H:%M')
                        hora_est_str = datetime.fromtimestamp(timestamp_est, timezone.utc).strftime('%H:%M') if timestamp_est else "N/A"
                        
                        datos_salidas.append({
                            "Programado (Z)": hora_prog_str,
                            "Estimado (Z)": hora_est_str,
                            "Vuelo": num_vuelo,
                            "Aerolínea": aerolinea,
                            "Aeronave": modelo_avion,
                            "Matrícula": matricula_avion,
                            "Origen": target_apt,
                            "Destino": destino,
                            "Riesgo": icono
                        })
        except:
            pass
            
    if datos_salidas:
        df_dep = pd.DataFrame(datos_salidas).sort_values(by="Programado (Z)")
        st.dataframe(df_dep, use_container_width=True)
    else:
        st.info("No hay salidas programadas que coincidan con los filtros en este rango de horas.")

with tab4:
    if aeropuerto_destino == "TODOS":
        st.warning("⚠️ Selecciona un aeropuerto específico en la barra lateral para ver su dashboard detallado y los reportes METAR/TAF.")
    else:
        st.markdown(f"### 📋 Reportes Aeronáuticos (METAR / TAF) - {aeropuerto_destino}")
        metar_text, taf_text = obtener_metar_taf(aeropuerto_destino)
        
        c_metar, c_taf = st.columns(2)
        with c_metar:
            st.markdown("**METAR (Condiciones Actuales):**")
            st.code(metar_text, language="text")
        with c_taf:
            st.markdown("**TAF (Pronóstico a 24/30h):**")
            st.code(taf_text, language="text")
            
        st.markdown("---")
        
        col_dash1, col_dash2 = st.columns(2)
        
        with col_dash1:
            st.markdown(f"**Evolución del Viento (Próximas 24h) - {aeropuerto_destino}**")
            datos_viento = dicc_meteo_global.get(aeropuerto_destino, {}).get("viento", {})
            if datos_viento:
                vientos_futuros = {k: v for k, v in datos_viento.items() if k >= hora_actual.strftime("%Y-%m-%dT%H:00")}
                vientos_limitados = dict(list(vientos_futuros.items())[:24])
                
                df_clima = pd.DataFrame(
                    list(vientos_limitados.values()), 
                    index=[datetime.strptime(k, "%Y-%m-%dT%H:%M").strftime("%H:%M") for k in vientos_limitados.keys()],
                    columns=["Viento (km/h)"]
                )
                st.line_chart(df_clima, color="#3b82f6")
            else:
                st.info("Sin datos meteorológicos disponibles.")
                
            st.markdown(f"**Precipitaciones Esperadas (Próximas 24h) - {aeropuerto_destino}**")
            datos_precip = dicc_meteo_global.get(aeropuerto_destino, {}).get("precipitacion", {})
            if datos_precip:
                precip_futuras = {k: v for k, v in datos_precip.items() if k >= hora_actual.strftime("%Y-%m-%dT%H:00")}
                precip_limitadas = dict(list(precip_futuras.items())[:24])
                
                df_precip = pd.DataFrame(
                    list(precip_limitadas.values()), 
                    index=[datetime.strptime(k, "%Y-%m-%dT%H:%M").strftime("%H:%M") for k in precip_limitadas.keys()],
                    columns=["Lluvia (mm)"]
                )
                st.bar_chart(df_precip, color="#3b82f6")
            else:
                st.info("Sin pronóstico de lluvia.")

        with col_dash2:
            st.markdown(f"**Distribución de Aerolíneas (Próximas {horas_prediccion}h)**")
            todas_operaciones = []
            
            for v in llegadas + salidas:
                if v.get('target_apt') == aeropuerto_destino:
                    tipo = "Llegada" if v in llegadas else "Salida"
                    timestamp = v.get('flight', {}).get('time', {}).get('scheduled', {}).get('arrival' if tipo == "Llegada" else 'departure')
                    if timestamp:
                        hora_vuelo = datetime.fromtimestamp(timestamp, timezone.utc)
                        if hora_actual <= hora_vuelo <= limite_tiempo:
                            al = obtener_aerolinea(v)
                            if al != "N/A":
                                todas_operaciones.append(al)
            
            if todas_operaciones:
                df_aerolineas = pd.DataFrame(todas_operaciones, columns=["Aerolínea"])
                conteo_al = df_aerolineas["Aerolínea"].value_counts().reset_index()
                conteo_al.columns = ["Aerolínea", "Vuelos"]
                
                grafico_al = alt.Chart(conteo_al.head(10)).mark_bar(color="#10b981").encode(
                    x=alt.X("Aerolínea", sort="-y", title=None),
                    y=alt.Y("Vuelos", title="Cantidad de Vuelos"),
                    tooltip=["Aerolínea", "Vuelos"]
                ).properties(height=300)
                
                st.altair_chart(grafico_al, use_container_width=True)
            else:
                st.info("No hay suficientes datos de aerolíneas en esta franja horaria.")
        
        st.markdown("---")
        st.markdown(f"**Carga Operativa: Vuelos Programados por Hora (Próximas {horas_prediccion}h)**")
        
        horas_continuas = [(hora_actual + timedelta(hours=i)).strftime('%H:00') for i in range(horas_prediccion + 1)]
        conteo_horas_dict = {h: 0 for h in horas_continuas}
        
        for v in llegadas + salidas:
            if v.get('target_apt') == aeropuerto_destino:
                tipo = "Llegada" if v in llegadas else "Salida"
                timestamp = v.get('flight', {}).get('time', {}).get('scheduled', {}).get('arrival' if tipo == "Llegada" else 'departure')
                if timestamp:
                    hora_vuelo = datetime.fromtimestamp(timestamp, timezone.utc)
                    if hora_actual <= hora_vuelo <= limite_tiempo:
                        hora_str = hora_vuelo.strftime('%H:00')
                        if hora_str in conteo_horas_dict:
                            conteo_horas_dict[hora_str] += 1
                            
        df_horas = pd.DataFrame(list(conteo_horas_dict.items()), columns=["Hora", "Vuelos"])
        grafico_horas = alt.Chart(df_horas).mark_bar(color="#f59e0b").encode(
            x=alt.X("Hora", sort=None, title=None),
            y=alt.Y("Vuelos", title="Vuelos Programados"),
            tooltip=["Hora", "Vuelos"]
        ).properties(height=300)
        
        st.altair_chart(grafico_horas, use_container_width=True)
