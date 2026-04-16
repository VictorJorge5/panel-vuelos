import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import math
import time
from datetime import datetime, timedelta, timezone
import boto3
import json
import altair as alt

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="IA Control de Operaciones USA", page_icon="✈️", layout="wide", initial_sidebar_state="expanded")

# --- ESTILOS CSS ---
st.markdown("""
    <style>
    header {visibility: hidden;}
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

# --- CONEXIÓN A AWS S3 ---
@st.cache_data(ttl=60)
def cargar_datos_desde_s3():
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=st.secrets["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=st.secrets["AWS_SECRET_ACCESS_KEY"],
            region_name=st.secrets["AWS_DEFAULT_REGION"]
        )
        response = s3_client.get_object(Bucket=st.secrets["BUCKET_NAME"], Key='predictions/latest_results.json')
        return json.loads(response['Body'].read().decode('utf-8'))
    except Exception as e:
        st.error(f"⚠️ Error de conexión Cloud: {e}")
        return None

# Carga inicial de datos
datos_tfm = cargar_datos_desde_s3()

# --- BASE DE DATOS DE AEROPUERTOS ---
AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}

# --- BARRA LATERAL ---
st.sidebar.title("⚙️ Control de Misión")
aeropuerto_destino = st.sidebar.selectbox("📍 Hub de Referencia", ["TODOS", "ATL", "ORD", "LAX", "JFK"])
horas_prediccion = st.sidebar.slider("⏳ Previsión (horas)", 1, 24, 15)

if st.sidebar.button("🔄 Sincronizar S3"):
    st.cache_data.clear()
    st.rerun()

# --- PROCESAMIENTO DE DATOS ---
if not datos_tfm:
    st.warning("📡 Esperando respuesta de AWS S3... Verifica los Secrets y las Lambdas.")
    st.stop()

# Extraemos todo del Súper JSON de Eva
vuelos_aire_crudo = datos_tfm.get('vuelos_en_aire', [])
llegadas_raw = datos_tfm.get('llegadas_programadas', [])
salidas_raw = datos_tfm.get('salidas_programadas', [])
dicc_meteo_global = datos_tfm.get('meteo_detallada', {})
dicc_predicciones = datos_tfm.get('predicciones_ia', {})
dicc_metar_taf = datos_tfm.get('metar_taf', {})

# Filtros lógicos
lista_iatas = list(AEROPUERTOS.keys()) if aeropuerto_destino == "TODOS" else [aeropuerto_destino]
hora_actual = datetime.now(timezone.utc)

# --- FUNCIONES DE APOYO ---
def obtener_datos_riesgo(callsign):
    # Buscamos en el diccionario que generó la Lambda de Inferencia
    res = dicc_predicciones.get(callsign)
    if res:
        return res['prob_texto'], res['alerta'], res['color'], res['icono'], res['viento_dest'], res['lluvia_dest']
    return "N/A", "BAJA", "gray", "⚪ Pendiente", 0, 0

# --- PANEL SUPERIOR ---
st.title(f"✈️ Panel de Operaciones - {aeropuerto_destino}")
st.success("✅ Conectado a AWS S3 | Datos 100% Sincronizados")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Vuelos en Radar", len(vuelos_aire_crudo))
col2.metric("Llegadas Prog.", len(llegadas_raw))
col3.metric("Salidas Prog.", len(salidas_raw))
col4.metric("Estado Sistema", "ACTIVO", delta="IA Cloud")

st.divider()

tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Radar en Vivo", "🛬 Llegadas", "🛫 Salidas", "📊 Dashboard"])

with tab1:
    map_center = [39.5, -98.35] if aeropuerto_destino == "TODOS" else AEROPUERTOS[aeropuerto_destino]["coords"]
    mapa = folium.Map(location=map_center, zoom_start=4, tiles="CartoDB dark_matter")
    
    for v in vuelos_aire_crudo:
        if v['aeropuerto_referencia'] in lista_iatas:
            # Obtener el riesgo de la IA desde el JSON
            prob, nivel, color, icono, v_dest, l_dest = obtener_datos_riesgo(v['callsign'])
            
            html_popup = f"""
            <div style='font-family: Arial; font-size: 12px; width: 200px;'>
                <b>Vuelo:</b> {v['callsign']}<br>
                <b>Ruta:</b> {v['origen']} ➔ {v['destino']}<br>
                <hr>
                <b>Riesgo IA:</b> <span style='color:{color}'>{icono} {prob}</span><br>
                <b>Viento Destino:</b> {v_dest} kts
            </div>
            """
            folium.Marker(
                location=[v['latitud'], v['longitud']],
                popup=folium.Popup(html_popup, max_width=250),
                icon=folium.Icon(color=color, icon="plane", prefix="fa")
            ).add_to(mapa)

    st_folium(mapa, width=1200, height=500)

with tab2:
    st.subheader("Panel de Llegadas (Control IA)")
    df_llegadas = []
    for v in llegadas_raw:
        f = v.get('flight', {})
        callsign = f.get('identification', {}).get('number', {}).get('default', 'N/A')
        prob, nivel, color, icono, _, _ = obtener_datos_riesgo(callsign)
        
        df_llegadas.append({
            "Vuelo": callsign,
            "Origen": f.get('airport', {}).get('origin', {}).get('code', {}).get('iata', 'N/A'),
            "Destino": v.get('target_apt'),
            "Riesgo IA": icono,
            "Probabilidad": prob
        })
    st.dataframe(pd.DataFrame(df_llegadas), use_container_width=True)

with tab4:
    if aeropuerto_destino == "TODOS":
        st.info("Selecciona un aeropuerto para ver el METAR/TAF")
    else:
        metar = dicc_metar_taf.get(aeropuerto_destino, {}).get('metar', 'No disponible')
        taf = dicc_metar_taf.get(aeropuerto_destino, {}).get('taf', 'No disponible')
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**METAR Actual:**")
            st.code(metar)
        with c2:
            st.markdown("**TAF Previsión:**")
            st.code(taf)
