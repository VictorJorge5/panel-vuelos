import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import boto3
import json
import math
import altair as alt
from datetime import datetime, timedelta, timezone

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(
    page_title="IA Control de Operaciones USA", 
    page_icon="✈️", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# --- ESTILOS CSS PERSONALIZADOS ---
st.markdown("""
    <style>
    header {visibility: hidden;}
    .stDeployButton {display: none;}
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

# --- FUNCIÓN DE CARGA DESDE S3 ---
@st.cache_data(ttl=60)
def cargar_todo_desde_s3():
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=st.secrets["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=st.secrets["AWS_SECRET_ACCESS_KEY"],
            region_name=st.secrets["AWS_DEFAULT_REGION"]
        )
        # El archivo que genera tu Lambda de Inferencia
        response = s3_client.get_object(
            Bucket=st.secrets["BUCKET_NAME"], 
            Key='predictions/latest_results.json'
        )
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except Exception as e:
        return {"error": str(e)}

# --- BLOQUE DE CARGA CRÍTICA (CON SPINNER) ---
with st.spinner('📡 Sincronizando telemetría y predicciones IA desde AWS Cloud...'):
    data_s3 = cargar_todo_desde_s3()

if data_s3 is None or "error" in data_s3:
    st.error("❌ Error de Conexión Cloud")
    st.info("Asegúrate de que la Lambda de Inferencia ha generado el archivo 'predictions/latest_results.json' en S3.")
    if data_s3 and "error" in data_s3:
        st.code(data_s3["error"])
    st.stop()

# --- MAPEADO DE DATOS DEL CLOUD ---
vuelos_aire = data_s3.get('vuelos_en_aire', [])
llegadas_raw = data_s3.get('llegadas_programadas', [])
salidas_raw = data_s3.get('salidas_programadas', [])
dicc_meteo = data_s3.get('meteo_detallada', {})
predicciones_ia = data_s3.get('predicciones_ia', {})
metar_taf = data_s3.get('metar_taf', {})
metadata = data_s3.get('metadata', {})

# --- BASE DE DATOS DE AEROPUERTOS ---
AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}

# --- BARRA LATERAL (SIDEBAR) ---
st.sidebar.title("⚙️ Configuración")
aeropuerto_referencia = st.sidebar.selectbox(
    "📍 Selecciona el Aeropuerto",
    ["TODOS", "ATL", "ORD", "LAX", "JFK"]
)

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

# --- LÓGICA DE FILTRADO ---
target_iatas = list(AEROPUERTOS.keys()) if aeropuerto_referencia == "TODOS" else [aeropuerto_referencia]
hora_actual = datetime.now(timezone.utc)

# --- PANEL SUPERIOR ---
st.title(f"✈️ Panel de Operaciones - {aeropuerto_referencia}")
st.success(f"✅ Datos sincronizados: {metadata.get('snapshot_id', 'Desconocido')}")
st.caption(f"Última actualización IA: {metadata.get('procesado_ia_utc', 'N/A')}")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Vuelos en Radar", len([v for v in vuelos_aire if v['aeropuerto_referencia'] in target_iatas]))
col2.metric("Llegadas Prog.", len([v for v in llegadas_raw if v['target_apt'] in target_iatas]))
col3.metric("Salidas Prog.", len([v for v in salidas_raw if v['target_apt'] in target_iatas]))
col4.metric("Estado Cloud", "SINCRO OK")

st.divider()

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Radar en Vivo", "🛬 Llegadas", "🛫 Salidas", "📊 Dashboard Analítico"])

with tab1:
    map_center = [39.5, -98.35] if aeropuerto_referencia == "TODOS" else AEROPUERTOS[aeropuerto_referencia]["coords"]
    mapa = folium.Map(location=map_center, zoom_start=4 if aeropuerto_referencia == "TODOS" else 6, tiles="CartoDB dark_matter")
    
    # Marcadores de Aeropuertos y Viento
    for apt in target_iatas:
        folium.Marker(
            location=AEROPUERTOS[apt]["coords"], 
            popup=AEROPUERTOS[apt]["nombre"], 
            icon=folium.Icon(color="black", icon="building", prefix="fa")
        ).add_to(mapa)

    # Dibujar Vuelos
    vuelos_pintados = 0
    for v in vuelos_aire:
        if v['aeropuerto_referencia'] in target_iatas:
            callsign = v['callsign']
            pred = predicciones_ia.get(callsign, {"prob_texto": "N/A", "alerta": "BAJA", "color": "gray", "icono": "⚪"})
            
            if pred['alerta'] in filtros_activos:
                html_popup = f"""
                <div style='font-family: Arial; font-size: 12px; width: 220px;'>
                    <h4 style='margin-bottom: 5px; color: {pred['color']};'>✈️ {callsign}</h4>
                    <b>Ruta:</b> {v['origen']} ➔ {v['destino']}<br>
                    <b>Altitud:</b> {v['altitud']} ft | <b>Vel:</b> {v['velocidad_nudos']} kts<br>
                    <hr>
                    <b>Riesgo IA:</b> <span style='color:{pred['color']}'><b>{pred['icono']} {pred['prob_texto']}</b></span><br>
                    <b>Viento Destino:</b> {round(pred.get('viento_dest', 0))} kts
                </div>
                """
                folium.Marker(
                    location=[v['latitud'], v['longitud']],
                    popup=folium.Popup(html_popup, max_width=250),
                    icon=folium.Icon(color=pred['color'], icon="plane", prefix="fa")
                ).add_to(mapa)
                vuelos_pintados += 1
    
    st_folium(mapa, width=1200, height=600)
    st.info(f"Mostrando {vuelos_pintados} aeronaves detectadas en el área de influencia.")

with tab2:
    st.subheader("🛬 Próximas Llegadas")
    datos_tabla = []
    for v in llegadas_raw:
        if v['target_apt'] in target_iatas:
            f_data = v.get('flight', {})
            callsign = f_data.get('identification', {}).get('number', {}).get('default', 'N/A')
            pred = predicciones_ia.get(callsign, {"prob_texto": "N/A", "icono": "⚪"})
            
            datos_tabla.append({
                "Vuelo": callsign,
                "Origen": f_data.get('airport', {}).get('origin', {}).get('code', {}).get('iata', 'N/A'),
                "Destino": v['target_apt'],
                "Modelo": f_data.get('aircraft', {}).get('model', {}).get('code', 'N/A'),
                "IA Riesgo": pred['icono'],
                "Prob. IA": pred['prob_texto']
            })
    st.dataframe(pd.DataFrame(datos_tabla), use_container_width=True)

with tab3:
    st.subheader("🛫 Próximas Salidas")
    datos_tabla_sal = []
    for v in salidas_raw:
        if v['target_apt'] in target_iatas:
            f_data = v.get('flight', {})
            callsign = f_data.get('identification', {}).get('number', {}).get('default', 'N/A')
            pred = predicciones_ia.get(callsign, {"prob_texto": "N/A", "icono": "⚪"})
            
            datos_tabla_sal.append({
                "Vuelo": callsign,
                "Destino": f_data.get('airport', {}).get('destination', {}).get('code', {}).get('iata', 'N/A'),
                "Origen": v['target_apt'],
                "IA Riesgo": pred['icono'],
