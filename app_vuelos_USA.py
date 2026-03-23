import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import math
from datetime import datetime, timedelta, timezone
from FlightRadar24 import FlightRadar24API

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
    if not dicc_vientos_apt:
        return "?", "Desconocida", "gray", "⚪ Desconocida"
        
    minutos = hora_dt.minute
    if minutos >= 30:
        hora_redondeada = hora_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        hora_redondeada = hora_dt.replace(minute=0, second=0, microsecond=0)
        
    hora_clave = hora_redondeada.strftime("%Y-%m-%dT%H:00")
    viento_kmh = dicc_vientos_apt.get(hora_clave)
    
    if viento_kmh is None:
        return "?", "Sin Datos", "gray", "⚪ Sin Datos"
        
    viento_kmh = round(viento_kmh, 1)
    
    if viento_kmh < 10:
        return viento_kmh, "BAJA", "green", "🟢 Baja"
    elif 10 <= viento_kmh <= 40:
        return viento_kmh, "MODERADA", "orange", "🟠 Moderada"
    else:
        return viento_kmh, "ALTA", "red", "🔴 Alta"

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

with st.spinner('Actualizando posiciones y telemetría...'):
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

# Añadir números de vuelo y aerolíneas de los aviones en el aire al desplegable
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

# --- CUERPO PRINCIPAL ---
st.title(f"✈️ Panel de Operaciones - {nombre_mostrar}")
st.markdown(f"Monitorización predictiva de vuelos para: {', '.join(lista_iatas)}")

hora_actual = datetime.now(timezone.utc)
limite_tiempo = hora_actual + timedelta(hours=horas_prediccion)

# --- KPIs SUPERIORES ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Vuelos Escaneados", len(vuelos_aire_filtrados), "En aire hacia bases")
col2.metric("Llegadas Prog.", len(llegadas), f"Límite API: 100/Aeropuerto")
col3.metric("Salidas Prog.", len(salidas), f"Límite API: 100/Aeropuerto")

if aeropuerto_destino == "TODOS":
    col4.metric("Bases Monitorizadas", len(lista_iatas), "Red Completa")
else:
    viento_actual, prob_actual, _, _ = evaluar_probabilidad_cancelacion(hora_actual, dicc_meteo_global[aeropuerto_destino])
    col4.metric(f"Viento en {aeropuerto_destino}", f"{viento_actual} km/h", prob_actual, delta_color="inverse" if prob_actual == "ALTA" else "normal")

st.divider()

# --- PESTAÑAS ---
tab1, tab2, tab3 = st.tabs(["🗺️ Radar en Vivo", "🛬 Panel de Llegadas", "🛫 Panel de Salidas"])

with tab1:
    if aeropuerto_destino == "TODOS":
        map_center = [39.5, -98.35]
        zoom = 4
    else:
        map_center = AEROPUERTOS[aeropuerto_destino]["coords"]
        zoom = 5
        
    mapa = folium.Map(location=map_center, zoom_start=zoom)
    
    for apt in lista_iatas:
        folium.Marker(
            location=AEROPUERTOS[apt]["coords"], 
            popup=f"<b>{AEROPUERTOS[apt]['nombre']} ({apt})</b>", 
            icon=folium.Icon(color="black", icon="building", prefix="fa")
        ).add_to(mapa)
    
    vuelos_pintados = 0
    for vuelo in vuelos_aire_filtrados:
        destino = str(getattr(vuelo, 'destination_airport_iata', 'N/A')).upper()
        origen = str(getattr(vuelo, 'origin_airport_iata', 'N/A')).upper()
        callsign = getattr(vuelo, 'callsign', 'N/A')
        aerolinea = getattr(vuelo, 'nombre_aerolinea_mapeado', 'N/A')
        
        # Telemetría Aeronáutica
        altitud = getattr(vuelo, 'altitude', 'N/A')
        velocidad = getattr(vuelo, 'ground_speed', 'N/A')
        rumbo = getattr(vuelo, 'heading', 'N/A')
        matricula = getattr(vuelo, 'registration', 'N/A')
        modelo = getattr(vuelo, 'aircraft_code', 'N/A')
        v_speed = getattr(vuelo, 'vertical_speed', 0)
        
        # Formato de velocidad vertical
        v_speed_str = f"+{v_speed}" if v_speed > 0 else str(v_speed)
        v_speed_color = "green" if v_speed > 0 else "red" if v_speed < 0 else "gray"
        
        coords_destino = AEROPUERTOS[destino]["coords"]
        dist = calcular_distancia_nm(vuelo.latitude, vuelo.longitude, coords_destino[0], coords_destino[1])
        horas_restantes = dist / max(vuelo.ground_speed, 1)
        eta = hora_actual + timedelta(hours=horas_restantes)
        
        viento, prob, color, icono = evaluar_probabilidad_cancelacion(eta, dicc_meteo_global[destino])
        
        if prob in filtros_activos:
            html_popup = f"""
            <div style='font-family: Arial; font-size: 12px; width: 250px;'>
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
            timestamp = vuelo.get('flight', {}).get('time', {}).get('scheduled', {}).get('arrival')
            if timestamp:
                hora_vuelo = datetime.fromtimestamp(timestamp, timezone.utc)
                if hora_actual <= hora_vuelo <= limite_tiempo:
                    
                    target_apt = vuelo.get('target_apt')
                    viento, prob, color, icono = evaluar_probabilidad_cancelacion(hora_vuelo, dicc_meteo_global.get(target_apt, {}))
                    
                    origen = obtener_iata_seguro(vuelo.get('flight', {}).get('airport', {}).get('origin'))
                    aerolinea = obtener_aerolinea(vuelo)
                    num_vuelo = obtener_num_vuelo_seguro(vuelo)
                    
                    # Extraer modelo y matrícula de los datos programados
                    aircraft_data = vuelo.get('flight', {}).get('aircraft') or {}
                    modelo_avion = (aircraft_data.get('model') or {}).get('code', 'N/A')
                    matricula_avion = aircraft_data.get('registration', 'N/A')
                    
                    pasa_filtro_al = (not filtro_aerolineas) or (aerolinea in filtro_aerolineas)
                    pasa_filtro_apt = (not filtro_aeropuertos) or (origen in filtro_aeropuertos or target_apt in filtro_aeropuertos)
                    pasa_filtro_vuelo = (not filtro_vuelos) or (num_vuelo in filtro_vuelos)
                    
                    if prob in filtros_activos and pasa_filtro_al and pasa_filtro_apt and pasa_filtro_vuelo:
                        datos_llegadas.append({
                            "Hora (UTC)": hora_vuelo.strftime('%H:%M'),
                            "Vuelo": num_vuelo,
                            "Aerolínea": aerolinea,
                            "Aeronave": modelo_avion,
                            "Matrícula": matricula_avion,
                            "Origen": origen,
                            "Destino": target_apt,
                            "Viento (km/h)": viento,
                            "Alerta": icono
                        })
        except:
            pass
            
    if datos_llegadas:
        df = pd.DataFrame(datos_llegadas)
        df = df.sort_values(by="Hora (UTC)")
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No hay llegadas programadas que coincidan con los filtros en este rango de horas.")

with tab3:
    datos_salidas = []
    for vuelo in salidas:
        try:
            timestamp = vuelo.get('flight', {}).get('time', {}).get('scheduled', {}).get('departure')
            if timestamp:
                hora_vuelo = datetime.fromtimestamp(timestamp, timezone.utc)
                if hora_actual <= hora_vuelo <= limite_tiempo:
                    
                    target_apt = vuelo.get('target_apt')
                    viento, prob, color, icono = evaluar_probabilidad_cancelacion(hora_vuelo, dicc_meteo_global.get(target_apt, {}))
                    
                    destino = obtener_iata_seguro(vuelo.get('flight', {}).get('airport', {}).get('destination'))
                    aerolinea = obtener_aerolinea(vuelo)
                    num_vuelo = obtener_num_vuelo_seguro(vuelo)
                    
                    # Extraer modelo y matrícula de los datos programados
                    aircraft_data = vuelo.get('flight', {}).get('aircraft') or {}
                    modelo_avion = (aircraft_data.get('model') or {}).get('code', 'N/A')
                    matricula_avion = aircraft_data.get('registration', 'N/A')
                    
                    pasa_filtro_al = (not filtro_aerolineas) or (aerolinea in filtro_aerolineas)
                    pasa_filtro_apt = (not filtro_aeropuertos) or (destino in filtro_aeropuertos or target_apt in filtro_aeropuertos)
                    pasa_filtro_vuelo = (not filtro_vuelos) or (num_vuelo in filtro_vuelos)
                    
                    if prob in filtros_activos and pasa_filtro_al and pasa_filtro_apt and pasa_filtro_vuelo:
                        datos_salidas.append({
                            "Hora (UTC)": hora_vuelo.strftime('%H:%M'),
                            "Vuelo": num_vuelo,
                            "Aerolínea": aerolinea,
                            "Aeronave": modelo_avion,
                            "Matrícula": matricula_avion,
                            "Origen": target_apt,
                            "Destino": destino,
                            "Viento (km/h)": viento,
                            "Alerta": icono
                        })
        except:
            pass
            
    if datos_salidas:
        df = pd.DataFrame(datos_salidas)
        df = df.sort_values(by="Hora (UTC)")
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No hay salidas programadas que coincidan con los filtros en este rango de horas.")
