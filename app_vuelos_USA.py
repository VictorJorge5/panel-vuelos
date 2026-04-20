import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import boto3
import json
import math
import requests
import concurrent.futures
import time
from datetime import datetime, timedelta, timezone
import altair as alt

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="IA Control de Operaciones USA", page_icon="✈️", layout="wide", initial_sidebar_state="expanded")

# --- ESTILOS CSS PERSONALIZADOS ---
st.markdown("""
    <style>
    header {visibility: hidden;}
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stDeployButton {display: none;}
    header {background-color: transparent !important;}
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

# --- FUNCIÓN DE CARGA DESDE S3 (100% CLOUD, CERO FALLBACKS) ---
@st.cache_data(ttl=60)
def cargar_todo_desde_s3():
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=st.secrets["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=st.secrets["AWS_SECRET_ACCESS_KEY"],
            region_name=st.secrets["AWS_DEFAULT_REGION"]
        )
        response = s3_client.get_object(Bucket=st.secrets["BUCKET_NAME"], Key='predictions/latest_results.json')
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except Exception as e:
        return {"error": str(e)}

with st.spinner('📡 Sincronizando telemetría y predicciones IA desde AWS S3...'):
    data_s3 = cargar_todo_desde_s3()

if data_s3 is None or "error" in data_s3:
    st.error("❌ Error de Conexión Cloud. Verifica tus credenciales de AWS.")
    if data_s3 and "error" in data_s3:
        st.code(data_s3["error"])
    st.stop()

# --- MAPEADO DE DATOS DESDE S3 ---
vuelos_aire = data_s3.get('vuelos_en_aire', [])
llegadas_raw = data_s3.get('llegadas_programadas', [])
salidas_raw = data_s3.get('salidas_programadas', [])
dicc_meteo = data_s3.get('meteo_detallada', {})
metadata = data_s3.get('metadata', {})

hora_actual = datetime.now(timezone.utc)

AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}

# --- FUNCIONES DE APOYO (DISTANCIA Y FOTOS) ---
def calcular_distancia_nm(lat1, lon1, lat2, lon2):
    R = 3440.065
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2) * math.sin(dLat/2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2) * math.sin(dLon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

@st.cache_data(ttl=300)
def obtener_url_radar_lluvia():
    try:
        data = requests.get("https://api.rainviewer.com/public/weather-maps.json", timeout=5).json()
        return f"{data.get('host', 'https://tilecache.rainviewer.com')}{data['radar']['past'][-1]['path']}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"
    except: 
        return None

@st.cache_data(ttl=86400)
def obtener_foto_aeronave_ia(matricula):
    if not matricula or matricula == "N/A": return None, None, None
    try:
        time.sleep(0.3)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = requests.get(f"https://api.planespotters.net/pub/photos/reg/{matricula}", headers=headers, timeout=10)
        if r.status_code == 200 and r.json().get('photos'):
            return r.json()['photos'][0]['thumbnail_large']['src'], r.json()['photos'][0]['link'], r.json()['photos'][0]['photographer']
    except Exception: pass
    return None, None, None

# --- EXTRACCIÓN SEGURA DESDE LOS JSON ANIDADOS DE LLEGADAS/SALIDAS ---
def obtener_iata_seguro(nodo):
    try: return nodo['code'].get('iata', 'N/A') if isinstance(nodo, dict) and isinstance(nodo.get('code'), dict) else 'N/A'
    except: return 'N/A'

def obtener_num_vuelo_seguro(vuelo_dict):
    try: return vuelo_dict['flight']['identification']['number'].get('default', 'N/A') if isinstance(vuelo_dict, dict) and 'flight' in vuelo_dict else 'N/A'
    except: return 'N/A'

def obtener_aerolinea_segura(vuelo_dict):
    try: return vuelo_dict['flight']['airline'].get('name', 'N/A') if isinstance(vuelo_dict, dict) and 'flight' in vuelo_dict else 'N/A'
    except: return 'N/A'

def obtener_timestamp_seguro(vuelo_dict, tipo_vuelo, tipo_tiempo):
    try: return vuelo_dict['flight']['time'][tipo_tiempo].get(tipo_vuelo) if isinstance(vuelo_dict, dict) and 'flight' in vuelo_dict else None
    except: return None

# --- BARRA LATERAL ---
st.sidebar.title("⚙️ Configuración")
aeropuerto_destino = st.sidebar.selectbox("📍 Selecciona el Aeropuerto", ["TODOS", "ATL", "ORD", "LAX", "JFK"], index=0)
horas_prediccion = st.sidebar.slider("⏳ Horas de previsión a mostrar", min_value=1, max_value=24, value=15)
limite_tiempo = hora_actual + timedelta(hours=horas_prediccion)

st.sidebar.markdown("### 🔍 Filtros de Riesgo IA")
m_baja = st.sidebar.checkbox("🟢 Probabilidad BAJA", value=True)
m_media = st.sidebar.checkbox("🟠 Probabilidad MEDIA", value=True)
m_alta = st.sidebar.checkbox("🔴 Probabilidad ALTA", value=True)

filtros_activos = []
if m_baja: filtros_activos.append("BAJA")
if m_media: filtros_activos.append("MEDIA")
if m_alta: filtros_activos.append("ALTA")

if st.sidebar.button("🔄 Refrescar Datos Ahora"):
    st.cache_data.clear()
    st.rerun()

target_iatas = list(AEROPUERTOS.keys()) if aeropuerto_destino == "TODOS" else [aeropuerto_destino]
nombre_mostrar = "Estados Unidos (Global)" if aeropuerto_destino == "TODOS" else AEROPUERTOS[aeropuerto_destino]["nombre"]

# --- FILTROS LATERALES AVANZADOS ---
st.sidebar.divider()
st.sidebar.markdown("### 🔎 Filtros Avanzados")
aerolineas_disponibles, aeropuertos_disponibles, numeros_vuelo_disponibles = set(), set(), set()

for v in llegadas_raw + salidas_raw:
    orig = obtener_iata_seguro(v.get('flight', {}).get('airport', {}).get('origin'))
    dest = obtener_iata_seguro(v.get('flight', {}).get('airport', {}).get('destination'))
    al = obtener_aerolinea_segura(v)
    num = obtener_num_vuelo_seguro(v)
    if al != "N/A": aerolineas_disponibles.add(al)
    if orig != "N/A": aeropuertos_disponibles.add(orig)
    if dest != "N/A": aeropuertos_disponibles.add(dest)
    if num != "N/A": numeros_vuelo_disponibles.add(num)

for v in vuelos_aire:
    dest = v.get('destino', '').strip().upper()
    if not dest: dest = v.get('aeropuerto_referencia', '').strip().upper()
    if dest in target_iatas:
        callsign = v.get('callsign', 'N/A')
        al_name = v.get('aerolinea_icao', 'N/A')
        if callsign != "N/A": numeros_vuelo_disponibles.add(callsign)
        if al_name != "N/A": aerolineas_disponibles.add(al_name)

filtro_aerolineas = st.sidebar.multiselect("✈️ Filtrar por Aerolínea", sorted([str(x) for x in aerolineas_disponibles]))
filtro_aeropuertos = st.sidebar.multiselect("📍 Filtrar por Aeropuerto", sorted([str(x) for x in aeropuertos_disponibles]))
filtro_vuelos = st.sidebar.multiselect("🔢 Filtrar por Nº Vuelo", sorted([str(x) for x in numeros_vuelo_disponibles]), placeholder="Buscar...")

# --- PREPARACIÓN DE VUELOS (FILTRO ESTRICTO DE DESTINO) ---
vuelos_aire_filtrados = []
for v in vuelos_aire:
    destino = v.get('destino', '').strip().upper()
    origen = v.get('origen', '').strip().upper()
    
    # Si no tiene destino explícito, asume el aeropuerto de referencia
    if not destino: 
        destino = v.get('aeropuerto_referencia', '').strip().upper()
        
    callsign = v.get('callsign', 'N/A')
    aerolinea_vuelo = v.get('aerolinea_icao', 'N/A')
    
    # FILTRO RADAR: Solo agregamos si el vuelo va a uno de los aeropuertos en la lista actual
    if destino in target_iatas:
        if (not filtro_aeropuertos or origen in filtro_aeropuertos or destino in filtro_aeropuertos) and \
           (not filtro_vuelos or callsign in filtro_vuelos) and \
           (not filtro_aerolineas or aerolinea_vuelo in filtro_aerolineas):
            v['destino_real'] = destino
            vuelos_aire_filtrados.append(v)

# DESCARGA DE FOTOS ASÍNCRONA
matriculas_mapa = list(set([v.get('matricula', 'N/A') for v in vuelos_aire_filtrados if v.get('matricula', 'N/A') != 'N/A']))
dicc_fotos = {}
if matriculas_mapa:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futuros = {executor.submit(obtener_foto_aeronave_ia, mat): mat for mat in matriculas_mapa}
        for f in concurrent.futures.as_completed(futuros):
            dicc_fotos[futuros[f]] = f.result()

# --- PANEL SUPERIOR ---
st.title(f"✈️ Panel de Operaciones - {nombre_mostrar}")
st.success(f"✅ Sincronizado: {metadata.get('snapshot_id', 'Snapshot AWS S3')}")
st.markdown(f"**Powered by Cloud AI Predictions** | ⏱️ Hora del Sistema (UTC): `{hora_actual.strftime('%Y-%m-%d %H:%M:%S')} ZULU`")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Vuelos en Radar", len(vuelos_aire_filtrados), "Volando hacia destinos")
col2.metric("Llegadas Prog.", len([v for v in llegadas_raw if v.get('target_apt') in target_iatas]), "Límite: 100/Aeropuerto")
col3.metric("Salidas Prog.", len([v for v in salidas_raw if v.get('target_apt') in target_iatas]), "Límite: 100/Aeropuerto")

if aeropuerto_destino == "TODOS":
    col4.metric("Bases Monitorizadas", len(target_iatas), "Red Completa")
else:
    viento_kts, rafagas = 0, 0
    meteo_apt = dicc_meteo.get(aeropuerto_destino, {})
    if meteo_apt:
        hora_act_str = hora_actual.replace(minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:00')
        clima_ahora = meteo_apt.get(hora_act_str)
        # El JSON puede mandar listas o diccionarios/números crudos
        if clima_ahora and isinstance(clima_ahora, list) and len(clima_ahora) >= 2:
            viento_kts, rafagas = clima_ahora[0], clima_ahora[1]
        elif clima_ahora and isinstance(clima_ahora, (int, float)):
            viento_kts = clima_ahora
    col4.metric(f"Viento en {aeropuerto_destino}", f"{round(viento_kts)} kts", f"Ráfagas: {round(rafagas)}", delta_color="off")

st.divider()

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Radar en Vivo", "🛬 Panel de Llegadas", "🛫 Panel de Salidas", "📊 Dashboard Analítico"])

with tab1:
    map_center = [39.5, -98.35] if aeropuerto_destino == "TODOS" else AEROPUERTOS[aeropuerto_destino]["coords"]
    mapa = folium.Map(location=map_center, zoom_start=4 if aeropuerto_destino == "TODOS" else 5, tiles="CartoDB dark_matter")
    
    url_lluvia = obtener_url_radar_lluvia()
    if url_lluvia:
        folium.TileLayer(tiles=url_lluvia, attr='Weather data © RainViewer', name='Radar de Precipitaciones', overlay=True, control=True, opacity=0.55).add_to(mapa)

    hora_str_clave = hora_actual.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")

    for apt in target_iatas:
        folium.Marker(location=AEROPUERTOS[apt]["coords"], popup=f"<b>{AEROPUERTOS[apt]['nombre']}</b>", icon=folium.Icon(color="black", icon="building", prefix="fa")).add_to(mapa)

        clima_apt = dicc_meteo.get(apt, {}).get(hora_str_clave)
        if clima_apt and isinstance(clima_apt, list) and len(clima_apt) >= 3:
            vel_viento, dir_viento = clima_apt[0], clima_apt[2]
            rotacion_flecha = (dir_viento + 180) % 360
            html_vector_viento = f"<div style='font-family: Arial; font-size: 11px; color: #fff; font-weight: bold; background: rgba(15,23,42,0.8); border: 1px solid #3b82f6; padding: 2px 6px; border-radius: 4px; display: inline-flex; align-items: center; white-space: nowrap; transform: translate(15px, -15px);'><i class='fa fa-arrow-up' style='transform: rotate({rotacion_flecha}deg); margin-right: 4px; color: #3b82f6;'></i>{round(vel_viento)} kts</div>"
            folium.Marker(location=AEROPUERTOS[apt]["coords"], icon=folium.DivIcon(html=html_vector_viento)).add_to(mapa)

    vuelos_pintados = 0
    for vuelo in vuelos_aire_filtrados:
        destino = vuelo.get('destino_real', 'N/A')
        origen = vuelo.get('origen', 'N/A').upper()
        callsign = vuelo.get('callsign', 'N/A')
        aerolinea_nom = vuelo.get('aerolinea_icao', 'N/A')
        altitud = vuelo.get('altitud', 'N/A')
        velocidad = vuelo.get('velocidad_nudos', 'N/A')
        rumbo = vuelo.get('rumbo', 'N/A')
        matricula = vuelo.get('matricula', 'N/A')
        modelo = vuelo.get('modelo_avion', 'N/A')
        v_speed = vuelo.get('velocidad_vertical', 0)
        
        v_speed_str = f"+{v_speed}" if v_speed > 0 else str(v_speed)
        v_speed_color = "green" if v_speed > 0 else "red" if v_speed < 0 else "gray"
        
        horas_restantes = calcular_distancia_nm(vuelo.get('latitud', 0), vuelo.get('longitud', 0), AEROPUERTOS[destino]["coords"][0], AEROPUERTOS[destino]["coords"][1]) / max(velocidad if isinstance(velocidad, (int, float)) else 1, 1) if destino in AEROPUERTOS else 0
        eta = hora_actual + timedelta(hours=horas_restantes)
        
        # OBTENEMOS LA IA DIRECTAMENTE DE LOS DATOS DE VUELO EN S3
        prob_str = vuelo.get('probabilidad_retraso', '0%')
        try:
            prob_num = float(prob_str.replace('%', '')) / 100
        except:
            prob_num = 0.0

        if prob_num < 0.10:
            alerta, color, icono = "BAJA", "green", "🟢"
        elif prob_num < 0.20:
            alerta, color, icono = "MEDIA", "orange", "🟡"
        else:
            alerta, color, icono = "ALTA", "red", "🔴"
        
        if alerta in filtros_activos:
            # Obtener datos de viento/lluvia destino para el popup
            hora_eta_str = eta.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")
            meteo_dest = dicc_meteo.get(destino, {}).get(hora_eta_str)
            viento_dest, lluvia_dest = 0, 0
            if meteo_dest and isinstance(meteo_dest, list) and len(meteo_dest) >= 7:
                viento_dest, lluvia_dest = meteo_dest[0], meteo_dest[6]
            elif meteo_dest and isinstance(meteo_dest, (int, float)):
                viento_dest = meteo_dest
            
            foto_url, foto_link, fotografo = dicc_fotos.get(matricula, (None, None, None))
            if foto_url:
                foto_html = f"<div style='margin-bottom: 8px;'><a href='{foto_link}' target='_blank' title='Ver imagen original'><img src='{foto_url}' width='100%' style='border-radius: 4px; border: 1px solid #ccc; max-height: 140px; object-fit: cover;'></a><div style='font-size: 8px; color: #64748b; text-align: right; margin-top: 2px;'>© {fotografo} | Planespotters.net</div></div>"
            else:
                foto_html = f"<div style='margin-bottom: 8px; text-align: center; background: #e2e8f0; padding: 10px; border-radius: 4px; font-size: 11px;'><a href='https://www.jetphotos.com/registration/{matricula}' target='_blank' style='text-decoration: none; color: #3b82f6;'>📷 Buscar archivo de {matricula} en JetPhotos</a></div>"

            html_popup = f"""
            <div style='font-family: Arial; font-size: 12px; width: 250px;'>
                {foto_html}
                <h4 style='margin-bottom: 2px; color: {color};'>✈️ {callsign} | {aerolinea_nom}</h4>
                <div style='font-size: 10px; color: gray; margin-bottom: 8px;'>Matrícula: {matricula} | Equipo: {modelo}</div>
                <b>Ruta:</b> {origen} ➔ <b>{destino}</b><br><hr style='margin: 4px 0;'>
                <div style='display: flex; justify-content: space-between;'><span><b>Alt:</b> {altitud} ft</span><span><b>Vel:</b> {velocidad} kts</span></div>
                <div style='display: flex; justify-content: space-between;'><span><b>Rumbo:</b> {rumbo}°</span><span><b>V/S:</b> <span style='color: {v_speed_color};'>{v_speed_str} fpm</span></span></div>
                <hr style='margin: 4px 0;'>
                <b>Faltan:</b> {round(horas_restantes, 1)} h <b>(ETA:</b> {eta.strftime('%H:%M')}Z)<br>
                <b>Riesgo IA:</b> <span style='color:{color}'><b>{icono} {prob_str}</b></span><br>
                <b>Viento:</b> {round(viento_dest)} kts | <b>Lluvia:</b> {round(lluvia_dest, 1)} mm
            </div>
            """
            
            folium.Marker(
                location=[vuelo.get('latitud', 0), vuelo.get('longitud', 0)],
                popup=folium.Popup(html_popup, max_width=300),
                icon=folium.Icon(color=color, icon="plane", prefix="fa", angle=vuelo.get('rumbo', 0))
            ).add_to(mapa)
            vuelos_pintados += 1

    folium.LayerControl().add_to(mapa)
    st_folium(mapa, width=1200, height=600, returned_objects=[])
    st.success(f"Radar Activo: Mostrando **{vuelos_pintados}** aviones hacia tus aeropuertos.")

with tab2:
    datos_llegadas = []
    for v in llegadas_raw:
        if v.get('target_apt') in target_iatas:
            t_sched = obtener_timestamp_seguro(v, 'arrival', 'scheduled')
            if t_sched:
                h_vuelo = datetime.fromtimestamp(t_sched, timezone.utc)
                if hora_actual <= h_vuelo <= limite_tiempo:
                    target, f_data = v.get('target_apt'), v.get('flight', {})
                    orig = obtener_iata_seguro(f_data.get('airport', {}).get('origin'))
                    al = obtener_aerolinea_segura(v)
                    num = obtener_num_vuelo_seguro(v)
                    cid = str(f_data.get('identification', {}).get('callsign', '')).strip().upper()
                    
                    # El riesgo ahora debe venir mapeado desde el S3 si Eva lo ha incluido, de momento asumimos BAJA como fallback.
                    pred = {"prob_texto": "N/A", "icono": "⚪", "alerta": "BAJA"}
                    
                    if pred['alerta'] in filtros_activos and (not filtro_aerolineas or al in filtro_aerolineas) and \
                       (not filtro_aeropuertos or orig in filtro_aeropuertos or target in filtro_aeropuertos) and \
                       (not filtro_vuelos or num in filtro_vuelos):
                        
                        t_est = obtener_timestamp_seguro(v, 'arrival', 'estimated') or obtener_timestamp_seguro(v, 'arrival', 'real')
                        
                        aeronave = f_data.get('aircraft', {})
                        if isinstance(aeronave, dict):
                            aeronave_code = aeronave.get('model', {}).get('code', 'N/A') if isinstance(aeronave.get('model'), dict) else 'N/A'
                            aeronave_reg = aeronave.get('registration', 'N/A')
                        else:
                            aeronave_code = 'N/A'
                            aeronave_reg = 'N/A'
                            
                        datos_llegadas.append({
                            "Programado (Z)": h_vuelo.strftime('%H:%M'), "Estimado (Z)": datetime.fromtimestamp(t_est, timezone.utc).strftime('%H:%M') if t_est else "N/A",
                            "Vuelo": num, "Aerolínea": al, "Aeronave": aeronave_code,
                            "Matrícula": aeronave_reg, "Origen": orig, "Destino": target, "Probabilidad IA": f"{pred['icono']} {pred.get('prob_texto', 'N/A')}", "Nivel Alerta": pred['icono']
                        })
    if datos_llegadas: st.dataframe(pd.DataFrame(datos_llegadas).sort_values("Programado (Z)"), use_container_width=True, hide_index=True)

with tab3:
    datos_salidas = []
    for v in salidas_raw:
        if v.get('target_apt') in target_iatas:
            t_sched = obtener_timestamp_seguro(v, 'departure', 'scheduled')
            if t_sched:
                h_vuelo = datetime.fromtimestamp(t_sched, timezone.utc)
                if hora_actual <= h_vuelo <= limite_tiempo:
                    target, f_data = v.get('target_apt'), v.get('flight', {})
                    dest = obtener_iata_seguro(f_data.get('airport', {}).get('destination'))
                    al = obtener_aerolinea_segura(v)
                    num = obtener_num_vuelo_seguro(v)
                    cid = str(f_data.get('identification', {}).get('callsign', '')).strip().upper()
                    
                    pred = {"prob_texto": "N/A", "icono": "⚪", "alerta": "BAJA"}
                    
                    if pred['alerta'] in filtros_activos and (not filtro_aerolineas or al in filtro_aerolineas) and \
                       (not filtro_aeropuertos or target in filtro_aeropuertos or dest in filtro_aeropuertos) and \
                       (not filtro_vuelos or num in filtro_vuelos):
                        
                        t_est = obtener_timestamp_seguro(v, 'departure', 'estimated') or obtener_timestamp_seguro(v, 'departure', 'real')
                        
                        aeronave = f_data.get('aircraft', {})
                        if isinstance(aeronave, dict):
                            aeronave_code = aeronave.get('model', {}).get('code', 'N/A') if isinstance(aeronave.get('model'), dict) else 'N/A'
                            aeronave_reg = aeronave.get('registration', 'N/A')
                        else:
                            aeronave_code = 'N/A'
                            aeronave_reg = 'N/A'
                            
                        datos_salidas.append({
                            "Programado (Z)": h_vuelo.strftime('%H:%M'), "Estimado (Z)": datetime.fromtimestamp(t_est, timezone.utc).strftime('%H:%M') if t_est else "N/A",
                            "Vuelo": num, "Aerolínea": al, "Aeronave": aeronave_code,
                            "Matrícula": aeronave_reg, "Origen": target, "Destino": dest, "Probabilidad IA": f"{pred['icono']} {pred.get('prob_texto', 'N/A')}", "Nivel Alerta": pred['icono']
                        })
    if datos_salidas: st.dataframe(pd.DataFrame(datos_salidas).sort_values("Programado (Z)"), use_container_width=True, hide_index=True)

with tab4:
    if aeropuerto_destino == "TODOS":
        st.warning("⚠️ Selecciona un aeropuerto específico en la barra lateral para ver su dashboard detallado y los reportes METAR/TAF.")
    else:
        # --- FILA 1: VIENTO Y PRECIPITACIONES ---
        row1_col1, row1_col2 = st.columns(2)
        with row1_col1:
            with st.container(border=True):
                st.markdown(f"**Evolución del Viento (Próximas 24h) - {aeropuerto_destino}**")
                datos_apt = dicc_meteo.get(aeropuerto_destino, {})
                if datos_apt:
                    horas_continuas = [(hora_actual + timedelta(hours=i)).strftime('%Y-%m-%dT%H:00') for i in range(24)]
                    vientos_limitados = {}
                    for h in horas_continuas:
                        val = datos_apt.get(h)
                        if val:
                            vientos_limitados[h.split('T')[1]] = val[0] if isinstance(val, list) else val
                    
                    if vientos_limitados:
                        df_clima = pd.DataFrame(list(vientos_limitados.values()), index=list(vientos_limitados.keys()), columns=["Viento (kts)"])
                        st.line_chart(df_clima, color="#2563eb")
                else: st.info("Sin datos meteorológicos disponibles en S3.")
                
        with row1_col2:
            with st.container(border=True):
                st.markdown(f"**Precipitaciones Esperadas (Próximas 24h) - {aeropuerto_destino}**")
                if datos_apt:
                    lluvia_limitada = {}
                    for h in horas_continuas:
                        val = datos_apt.get(h)
                        if val and isinstance(val, list) and len(val) >= 7:
                            lluvia_limitada[h.split('T')[1]] = val[6] 
                    
                    if lluvia_limitada:
                        df_precip = pd.DataFrame(list(lluvia_limitada.values()), index=list(lluvia_limitada.keys()), columns=["Lluvia (mm)"])
                        st.bar_chart(df_precip, color="#2563eb")
                else: st.info("Sin pronóstico de lluvia en S3.")

        # --- FILA 2: CARGA OPERATIVA Y AEROLÍNEAS ---
        row2_col1, row2_col2 = st.columns(2)
        with row2_col1:
            with st.container(border=True):
                st.markdown(f"**Carga Operativa: Vuelos Programados por Hora (Próximas {horas_prediccion}h)**")
                conteo = {h: 0 for h in [(hora_actual + timedelta(hours=i)).strftime('%H:00') for i in range(horas_prediccion + 1)]}
                for v in llegadas_raw + salidas_raw:
                    if v.get('target_apt') == aeropuerto_destino:
                        t = obtener_timestamp_seguro(v, 'arrival' if v in llegadas_raw else 'departure', 'scheduled')
                        if t:
                            h_vuelo = datetime.fromtimestamp(t, timezone.utc)
                            if hora_actual <= h_vuelo <= limite_tiempo:
                                h_str = h_vuelo.strftime('%H:00')
                                if h_str in conteo: conteo[h_str] += 1
                df_horas = pd.DataFrame(list(conteo.items()), columns=["Hora", "Vuelos"])
                st.altair_chart(alt.Chart(df_horas).mark_bar(color="#ea580c", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(x=alt.X("Hora", sort=None, axis=alt.Axis(grid=False)), y=alt.Y("Vuelos", axis=alt.Axis(grid=True, gridColor="#e2e8f0")), tooltip=["Hora", "Vuelos"]).properties(height=300).configure_view(strokeWidth=0), use_container_width=True)

        with row2_col2:
            with st.container(border=True):
                st.markdown(f"**Distribución de Aerolíneas (Próximas {horas_prediccion}h)**")
                todas_ops = []
                for v in llegadas_raw + salidas_raw:
                    if v.get('target_apt') == aeropuerto_destino:
                        t = obtener_timestamp_seguro(v, 'arrival' if v in llegadas_raw else 'departure', 'scheduled')
                        if t:
                            h_vuelo = datetime.fromtimestamp(t, timezone.utc)
                            if hora_actual <= h_vuelo <= limite_tiempo:
                                al = obtener_aerolinea_segura(v)
                                if al != "N/A": todas_ops.append(al)
                if todas_ops:
                    conteo_al = pd.DataFrame(todas_ops, columns=["Aerolínea"])["Aerolínea"].value_counts().reset_index()
                    conteo_al.columns = ["Aerolínea", "Vuelos"]
                    st.altair_chart(alt.Chart(conteo_al.head(10)).mark_bar(color="#2563eb", cornerRadiusEnd=4).encode(x=alt.X("Aerolínea", sort="-y", axis=alt.Axis(grid=False, labelAngle=-45)), y=alt.Y("Vuelos", axis=alt.Axis(grid=True, gridColor="#e2e8f0")), tooltip=["Aerolínea", "Vuelos"]).properties(height=300).configure_view(strokeWidth=0), use_container_width=True)
                else: st.info("No hay suficientes datos en esta franja horaria.")
        
        # --- FILA 3: METAR y TAF ---
        st.markdown("---")
        st.markdown(f"### 📋 Reportes Aeronáuticos (METAR / TAF) - {aeropuerto_destino}")
        m_t = metar_taf.get(aeropuerto_destino, {})
        c_metar, c_taf = st.columns(2)
        with c_metar: 
            st.markdown("**METAR:**")
            st.code(m_t.get('metar', 'Sin METAR en S3'), language='text')
        with c_taf: 
            st.markdown("**TAF:**")
            st.code(m_t.get('taf', 'Sin TAF en S3'), language='text')
