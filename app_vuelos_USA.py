import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import boto3
import json
import math
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
@st.cache_data(ttl=30) # Bajamos a 30 seg para que refresque rápido
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

# --- MAPEADO ---
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

filtros = []
if m_baja: filtros.append("BAJA")
if m_media: filtros.append("MEDIA")
if m_alta: filtros.append("ALTA")

if st.sidebar.button("🔄 Refrescar ahora"):
    st.cache_data.clear()
    st.rerun()

target_iatas = list(AEROPUERTOS.keys()) if aeropuerto_ref == "TODOS" else [aeropuerto_ref]

# --- UI PRINCIPAL ---
st.title(f"✈️ Operaciones USA - {aeropuerto_ref}")
st.caption(f"Último Snapshot: {metadata.get('snapshot_id', 'N/A')} | Actualizado: {metadata.get('procesado_ia_utc', 'N/A')}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("En Radar", len([v for v in vuelos_aire if v['aeropuerto_referencia'] in target_iatas]))
c2.metric("Llegadas", len([v for v in llegadas_raw if v.get('target_apt') in target_iatas]))
c3.metric("Salidas", len([v for v in salidas_raw if v.get('target_apt') in target_iatas]))
c4.metric("Predicciones IA", len(predicciones_ia))

tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Radar IA", "🛬 Llegadas", "🛫 Salidas", "📊 Meteo"])

with tab1:
    map_center = [39.5, -98.35] if aeropuerto_ref == "TODOS" else AEROPUERTOS[aeropuerto_ref]["coords"]
    m = folium.Map(location=map_center, zoom_start=4 if aeropuerto_ref == "TODOS" else 6, tiles="CartoDB dark_matter")
    
    for v in vuelos_aire:
        if v['aeropuerto_referencia'] in target_iatas:
            # Lógica de búsqueda flexible (Callsign o Número de Vuelo)
            cid = v.get('callsign')
            fid = v.get('vuelo_numero')
            
            # Buscamos en el diccionario de la IA
            pred = predicciones_ia.get(cid) or predicciones_ia.get(fid) or {
                "prob_texto": "N/A", "alerta": "BAJA", "color": "gray", "icono": "⚪", "hora_ref_prediccion": "N/A"
            }
            
            if pred['alerta'] in filtros:
                pop = f"<b>Vuelo: {cid or fid}</b><br>Ruta: {v['origen']}->{v['destino']}<br>IA: {pred['icono']} {pred['prob_texto']}"
                folium.Marker(
                    location=[v['latitud'], v['longitud']],
                    popup=folium.Popup(pop, max_width=200),
                    icon=folium.Icon(color=pred['color'], icon="plane", prefix="fa")
                ).add_to(m)
    st_folium(m, width="100%", height=550)

with tab2:
    data_t = []
    for v in llegadas_raw:
        if v.get('target_apt') in target_iatas:
            f = v.get('flight', {})
            num = f.get('identification', {}).get('number', {}).get('default', 'N/A')
            p = predicciones_ia.get(num, {"prob_texto": "N/A", "icono": "⚪"})
            ts = f.get('time', {}).get('scheduled', {}).get('arrival')
            hora = datetime.fromtimestamp(ts, timezone.utc).strftime('%H:%M') if ts else "N/A"
            data_t.append({"Hora": hora, "Vuelo": num, "Origen": f.get('airport', {}).get('origin', {}).get('code', {}).get('iata'), "IA": p['icono'], "Prob": p['prob_texto'], "Estado": f.get('status', {}).get('text')})
    if data_t: st.dataframe(pd.DataFrame(data_t).sort_values("Hora"), use_container_width=True, hide_index=True)

with tab3:
    data_s = []
    for v in salidas_raw:
        if v.get('target_apt') in target_iatas:
            f = v.get('flight', {})
            num = f.get('identification', {}).get('number', {}).get('default', 'N/A')
            p = predicciones_ia.get(num, {"prob_texto": "N/A", "icono": "⚪"})
            ts = f.get('time', {}).get('scheduled', {}).get('departure')
            hora = datetime.fromtimestamp(ts, timezone.utc).strftime('%H:%M') if ts else "N/A"
            data_s.append({"Hora": hora, "Vuelo": num, "Destino": f.get('airport', {}).get('destination', {}).get('code', {}).get('iata'), "IA": p['icono'], "Prob": p['prob_texto']})
    if data_s: st.dataframe(pd.DataFrame(data_s).sort_values("Hora"), use_container_width=True, hide_index=True)

with tab4:
    if aeropuerto_ref == "TODOS": st.warning("Selecciona un hub")
    else:
        m_apt = dicc_meteo.get(aeropuerto_ref, {})
        if m_apt:
            df_m = pd.DataFrame.from_dict(m_apt, orient='index').reset_index().head(24)
            df_m.columns = ['H', 'Viento', 'R', 'D', 'Vis', 'N', 'T', 'P']
            c1, c2 = st.columns(2)
            c1.line_chart(df_m.set_index('H')['Viento'])
            c2.bar_chart(df_m.set_index('H')['P'])
