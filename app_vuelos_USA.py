import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import boto3
import json
from datetime import datetime, timezone

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(
    page_title="IA Control de Operaciones USA", 
    page_icon="✈️", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# --- ESTILOS CSS ---
st.markdown("""
    <style>
    header {visibility: hidden;}
    .stDeployButton {display: none;}
    .block-container { padding-top: 2rem; }
    [data-testid="stMetric"] { background-color: #ffffff; border: 1px solid #e2e8f0; padding: 15px; border-radius: 8px; }
    </style>
""", unsafe_allow_html=True)

# --- CARGA DESDE S3 ---
@st.cache_data(ttl=30)
def cargar_todo_desde_s3():
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
        return {"error": str(e)}

with st.spinner('📡 Sincronizando predicciones IA...'):
    data_s3 = cargar_todo_desde_s3()

if data_s3 is None or "error" in data_s3:
    st.error("❌ Error de Conexión Cloud")
    st.stop()

# --- MAPEADO DE DATOS ---
vuelos_aire = data_s3.get('vuelos_en_aire', [])
llegadas_raw = data_s3.get('llegadas_programadas', [])
salidas_raw = data_s3.get('salidas_programadas', [])
predicciones_ia = data_s3.get('predicciones_ia', {})
metadata = data_s3.get('metadata', {})
dicc_meteo = data_s3.get('meteo_detallada', {})

AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York", "coords": [40.6413, -73.7781]}
}

# --- BARRA LATERAL ---
st.sidebar.title("⚙️ Filtros IA")
aeropuerto_ref = st.sidebar.selectbox("📍 Hub", ["TODOS", "ATL", "ORD", "LAX", "JFK"])
m_baja = st.sidebar.checkbox("🟢 Riesgo BAJA", value=True)
m_media = st.sidebar.checkbox("🟠 Riesgo MEDIA", value=True)
m_alta = st.sidebar.checkbox("🔴 Riesgo ALTA", value=True)

filtros_activos = []
if m_baja: filtros_activos.append("BAJA")
if m_media: filtros_activos.append("MEDIA")
if m_alta: filtros_activos.append("ALTA")

if st.sidebar.button("🔄 Refrescar ahora"):
    st.cache_data.clear()
    st.rerun()

target_iatas = list(AEROPUERTOS.keys()) if aeropuerto_ref == "TODOS" else [aeropuerto_ref]

# --- UI PRINCIPAL ---
st.title(f"✈️ Operaciones USA - {aeropuerto_ref}")
st.caption(f"Actualizado: {metadata.get('procesado_ia_utc', 'N/A')}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("En Radar", len([v for v in vuelos_aire if v['aeropuerto_referencia'] in target_iatas]))
c2.metric("Llegadas", len([v for v in llegadas_raw if v.get('target_apt') in target_iatas]))
c3.metric("Salidas", len([v for v in salidas_raw if v.get('target_apt') in target_iatas]))
c4.metric("IA Activa", len(predicciones_ia))

tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Radar IA", "🛬 Llegadas", "🛫 Salidas", "📊 Meteo"])

with tab1:
    map_center = [39.5, -98.35] if aeropuerto_ref == "TODOS" else AEROPUERTOS[aeropuerto_ref]["coords"]
    m = folium.Map(location=map_center, zoom_start=4 if aeropuerto_ref == "TODOS" else 6, tiles="CartoDB dark_matter")
    
    for v in vuelos_aire:
        if v['aeropuerto_referencia'] in target_iatas:
            # NORMALIZACIÓN: Usamos estrictamente el callsign
            cid = v.get('callsign')
            pred = predicciones_ia.get(cid, {"prob_texto": "N/A", "alerta": "BAJA", "color": "gray", "icono": "⚪"})
            
            if pred['alerta'] in filtros_activos:
                pop = f"<b>Vuelo: {cid}</b><br>Ruta: {v['origen']}->{v['destino']}<br>IA: {pred['icono']} {pred.get('prob_texto','N/A')}"
                folium.Marker(
                    location=[v['latitud'], v['longitud']],
                    popup=folium.Popup(pop, max_width=200),
                    icon=folium.Icon(color=pred['color'], icon="plane", prefix="fa")
                ).add_to(m)
    st_folium(m, width="100%", height=550)

with tab2:
    st.subheader("🛬 Llegadas Programadas")
    datos_t = []
    for v in llegadas_raw:
        if v.get('target_apt') in target_iatas:
            f = v.get('flight', {})
            # NORMALIZACIÓN: Extraemos el callsign de la identificación del vuelo
            cid = f.get('identification', {}).get('callsign')
            num = f.get('identification', {}).get('number', {}).get('default', 'N/A')
            
            p = predicciones_ia.get(cid, {"prob_texto": "N/A", "icono": "⚪"})
            
            ts = f.get('time', {}).get('scheduled', {}).get('arrival')
            hora = datetime.fromtimestamp(ts, timezone.utc).strftime('%H:%M') if ts else "N/A"
            
            datos_t.append({
                "Hora (Z)": hora,
                "Vuelo": num,
                "Callsign": cid,
                "Origen": f.get('airport', {}).get('origin', {}).get('code', {}).get('iata'),
                "IA Riesgo": p['icono'],
                "Prob. IA": p.get('prob_texto','N/A'),
                "Estado": f.get('status', {}).get('text')
            })
    if datos_t:
        st.dataframe(pd.DataFrame(datos_t).sort_values("Hora (Z)"), use_container_width=True, hide_index=True)

with tab3:
    st.subheader("🛫 Salidas Programadas")
    datos_s = []
    for v in salidas_raw:
        if v.get('target_apt') in target_iatas:
            f = v.get('flight', {})
            # NORMALIZACIÓN: Extraemos el callsign aquí también
            cid = f.get('identification', {}).get('callsign')
            num = f.get('identification', {}).get('number', {}).get('default', 'N/A')
            
            p = predicciones_ia.get(cid, {"prob_texto": "N/A", "icono": "⚪"})
            
            ts = f.get('time', {}).get('scheduled', {}).get('departure')
            hora = datetime.fromtimestamp(ts, timezone.utc).strftime('%H:%M') if ts else "N/A"
            
            datos_s.append({
                "Hora (Z)": hora,
                "Vuelo": num,
                "Callsign": cid,
                "Destino": f.get('airport', {}).get('destination', {}).get('code', {}).get('iata'),
                "IA Riesgo": p['icono'],
                "Prob. IA": p.get('prob_texto','N/A')
            })
    if datos_s:
        st.dataframe(pd.DataFrame(datos_s).sort_values("Hora (Z)"), use_container_width=True, hide_index=True)

with tab4:
    if aeropuerto_ref == "TODOS":
