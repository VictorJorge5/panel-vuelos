import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import boto3
import json
from datetime import datetime, timezone

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="IA Control de Operaciones USA", page_icon="✈️", layout="wide")

st.markdown("""
    <style>
    header {visibility: hidden;}
    .stDeployButton {display: none;}
    .block-container { padding-top: 1rem; }
    [data-testid="stMetric"] { background-color: #ffffff; border: 1px solid #e2e8f0; padding: 10px; border-radius: 8px; }
    </style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=30)
def cargar_todo_desde_s3():
    try:
        s3 = boto3.client('s3', 
            aws_access_key_id=st.secrets["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=st.secrets["AWS_SECRET_ACCESS_KEY"],
            region_name=st.secrets["AWS_DEFAULT_REGION"])
        res = s3.get_object(Bucket=st.secrets["BUCKET_NAME"], Key='predictions/latest_results.json')
        return json.loads(res['Body'].read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

with st.spinner('📡 Sincronizando telemetría IA...'):
    data = cargar_todo_desde_s3()

if "error" in data:
    st.error(f"❌ Error: {data['error']}")
    st.stop()

# --- DATOS ---
vuelos_aire = data.get('vuelos_en_aire', [])
llegadas_raw = data.get('llegadas_programadas', [])
salidas_raw = data.get('salidas_programadas', [])
# Normalizamos las llaves de predicción a mayúsculas para que coincidan siempre
predicciones_ia = {str(k).upper(): v for k, v in data.get('predicciones_ia', {}).items()}
metadata = data.get('metadata', {})

AEROPUERTOS = {
    "ATL": [33.6407, -84.4277], "ORD": [41.9742, -87.9073], 
    "LAX": [33.9416, -118.4085], "JFK": [40.6413, -73.7781]
}

# --- SIDEBAR ---
st.sidebar.title("⚙️ Filtros IA")
aeropuerto_ref = st.sidebar.selectbox("📍 Selecciona Hub", ["TODOS"] + list(AEROPUERTOS.keys()))
filtros = []
if st.sidebar.checkbox("🟢 Riesgo BAJA", value=True): filtros.append("BAJA")
if st.sidebar.checkbox("🟠 Riesgo MEDIA", value=True): filtros.append("MEDIA")
if st.sidebar.checkbox("🔴 Riesgo ALTA", value=True): filtros.append("ALTA")

if st.sidebar.button("🔄 Forzar Refresco"):
    st.cache_data.clear()
    st.rerun()

target_iatas = list(AEROPUERTOS.keys()) if aeropuerto_ref == "TODOS" else [aeropuerto_ref]

# --- UI ---
st.title(f"✈️ Panel de Control - {aeropuerto_ref}")
st.caption(f"Última actualización IA: {metadata.get('procesado_ia_utc', 'N/A')}")

tab1, tab2, tab3 = st.tabs(["🗺️ Radar IA", "🛬 Llegadas", "🛫 Salidas"])

with tab1:
    m = folium.Map(location=[39.5, -98.35], zoom_start=4, tiles="CartoDB dark_matter")
    for v in vuelos_aire:
        if v['aeropuerto_referencia'] in target_iatas:
            # NORMALIZACIÓN CRÍTICA: Buscamos el ID en mayúsculas
            cid = str(v.get('callsign', '')).upper()
            pred = predicciones_ia.get(cid, {"prob_texto": "N/A", "alerta": "BAJA", "color": "gray", "icono": "⚪"})
            
            if pred['alerta'] in filtros:
                folium.Marker(
                    location=[v['latitud'], v['longitud']],
                    popup=f"Vuelo: {cid}<br>IA: {pred['icono']} {pred['prob_texto']}",
                    icon=folium.Icon(color=pred['color'], icon="plane", prefix="fa")
                ).add_to(m)
    st_folium(m, width="100%", height=550)

with tab2:
    tabla = []
    for v in llegadas_raw:
        if v.get('target_apt') in target_iatas:
            f = v.get('flight', {})
            cid = str(f.get('identification', {}).get('callsign', '')).upper()
            p = predicciones_ia.get(cid, {"prob_texto": "N/A", "icono": "⚪"})
            ts = f.get('time', {}).get('scheduled', {}).get('arrival')
            hora = datetime.fromtimestamp(ts, timezone.utc).strftime('%H:%M') if ts else "N/A"
            tabla.append({"Hora (Z)": hora, "Vuelo": cid, "Origen": f.get('airport', {}).get('origin', {}).get('code', {}).get('iata'), "IA": p['icono'], "Prob": p['prob_texto']})
    if tabla: st.dataframe(pd.DataFrame(tabla).sort_values("Hora (Z)"), use_container_width=True, hide_index=True)

with tab3:
    tabla_s = []
    for v in salidas_raw:
        if v.get('target_apt') in target_iatas:
            f = v.get('flight', {})
            cid = str(f.get('identification', {}).get('callsign', '')).upper()
            p = predicciones_ia.get(cid, {"prob_texto": "N/A", "icono": "⚪"})
            ts = f.get('time', {}).get('scheduled', {}).get('departure')
            hora = datetime.fromtimestamp(ts, timezone.utc).strftime('%H:%M') if ts else "N/A"
            tabla_s.append({"Hora (Z)": hora, "Vuelo": cid, "Destino": f.get('airport', {}).get('destination', {}).get('code', {}).get('iata'), "IA": p['icono'], "Prob": p['prob_texto']})
    if tabla_s: st.dataframe(pd.DataFrame(tabla_s).sort_values("Hora (Z)"), use_container_width=True, hide_index=True)
