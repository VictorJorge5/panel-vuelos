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
        response = s3_client.get_object(
            Bucket=st.secrets["BUCKET_NAME"], 
            Key='predictions/latest_results.json'
        )
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except Exception as e:
        return {"error": str(e)}

# --- BLOQUE DE CARGA CRÍTICA ---
with st.spinner('📡 Sincronizando telemetría y predicciones IA con precisión temporal...'):
    data_s3 = cargar_todo_desde_s3()

if data_s3 is None or "error" in data_s3:
    st.error("❌ Error de Conexión Cloud")
    if data_s3 and "error" in data_s3:
        st.code(data_s3["error"])
    st.stop()

# --- MAPEADO DE DATOS ---
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

# --- BARRA LATERAL ---
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
st.success(f"✅ Sincronizado: {metadata.get('snapshot_id', 'Snapshot Actual')}")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Vuelos en Radar", len([v for v in vuelos_aire if v['aeropuerto_referencia'] in target_iatas]))
col2.metric("Llegadas Prog.", len([v for v in llegadas_raw if v.get('target_apt') in target_iatas]))
col3.metric("Salidas Prog.", len([v for v in salidas_raw if v.get('target_apt') in target_iatas]))
col4.metric("Estado Cloud", "SINCRO OK")

st.divider()

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Radar en Vivo", "🛬 Llegadas", "🛫 Salidas", "📊 Dashboard Analítico"])

with tab1:
    map_center = [39.5, -98.35] if aeropuerto_referencia == "TODOS" else AEROPUERTOS[aeropuerto_referencia]["coords"]
    mapa = folium.Map(location=map_center, zoom_start=4 if aeropuerto_referencia == "TODOS" else 6, tiles="CartoDB dark_matter")
    
    for apt in target_iatas:
        folium.Marker(
            location=AEROPUERTOS[apt]["coords"], 
            popup=AEROPUERTOS[apt]["nombre"], 
            icon=folium.Icon(color="black", icon="building", prefix="fa")
        ).add_to(mapa)

    vuelos_pintados = 0
    for v in vuelos_aire:
        if v['aeropuerto_referencia'] in target_iatas:
            callsign = v['callsign']
            pred = predicciones_ia.get(callsign, {"prob_texto": "N/A", "alerta": "BAJA", "color": "gray", "icono": "⚪", "hora_ref_prediccion": "N/A"})
            
            if pred['alerta'] in filtros_activos:
                html_popup = f"""
                <div style='font-family: Arial; font-size: 12px; width: 220px;'>
                    <h4 style='margin-bottom: 5px; color: {pred['color']};'>✈️ {callsign}</h4>
                    <b>Ruta:</b> {v['origen']} ➔ {v['destino']}<br>
                    <b>Alt:</b> {v['altitud']} ft | <b>Vel:</b> {v['velocidad_nudos']} kts<br>
                    <hr>
                    <b>Riesgo IA:</b> <span style='color:{pred['color']}'><b>{pred['icono']} {pred['prob_texto']}</b></span><br>
                    <small>Previsión para: {pred['hora_ref_prediccion'][-8:-3]}Z</small>
                </div>
                """
                folium.Marker(
                    location=[v['latitud'], v['longitud']],
                    popup=folium.Popup(html_popup, max_width=250),
                    icon=folium.Icon(color=pred['color'], icon="plane", prefix="fa")
                ).add_to(mapa)
                vuelos_pintados += 1
    
    st_folium(mapa, width=1200, height=550)

with tab2:
    st.subheader("🛬 Próximas Llegadas (Ordenadas por Hora)")
    datos_tabla = []
    for v in llegadas_raw:
        if v.get('target_apt') in target_iatas:
            f_data = v.get('flight', {})
            ident = f_data.get('identification', {})
            callsign = ident.get('number', {}).get('default', 'N/A')
            
            # EXTRACCIÓN DE TIEMPOS
            t_node = f_data.get('time', {})
            ts_sch = t_node.get('scheduled', {}).get('arrival')
            hora_prog = datetime.fromtimestamp(ts_sch, timezone.utc).strftime('%H:%M') if ts_sch else "N/A"
            
            pred = predicciones_ia.get(callsign, {"prob_texto": "N/A", "icono": "⚪"})
            
            datos_tabla.append({
                "Hora (Z)": hora_prog,
                "Vuelo": callsign,
                "Origen": f_data.get('airport', {}).get('origin', {}).get('code', {}).get('iata', 'N/A'),
                "Destino": v.get('target_apt'),
                "IA Riesgo": pred.get('icono'),
                "Prob. IA": pred.get('prob_texto'),
                "Estado": f_data.get('status', {}).get('text', 'N/A')
            })
    if datos_tabla:
        df = pd.DataFrame(datos_tabla).sort_values("Hora (Z)")
        st.dataframe(df, use_container_width=True, hide_index=True)

with tab3:
    st.subheader("🛫 Próximas Salidas (Ordenadas por Hora)")
    datos_tabla_sal = []
    for v in salidas_raw:
        if v.get('target_apt') in target_iatas:
            f_data = v.get('flight', {})
            ident = f_data.get('identification', {})
            callsign = ident.get('number', {}).get('default', 'N/A')
            
            # EXTRACCIÓN DE TIEMPOS
            t_node = f_data.get('time', {})
            ts_sch = t_node.get('scheduled', {}).get('departure')
            hora_prog = datetime.fromtimestamp(ts_sch, timezone.utc).strftime('%H:%M') if ts_sch else "N/A"
            
            pred = predicciones_ia.get(callsign, {"prob_texto": "N/A", "icono": "⚪"})
            
            datos_tabla_sal.append({
                "Hora (Z)": hora_prog,
                "Vuelo": callsign,
                "Destino": f_data.get('airport', {}).get('destination', {}).get('code', {}).get('iata', 'N/A'),
                "Origen": v.get('target_apt'),
                "IA Riesgo": pred.get('icono'),
                "Prob. IA": pred.get('prob_texto')
            })
    if datos_tabla_sal:
        df_sal = pd.DataFrame(datos_tabla_sal).sort_values("Hora (Z)")
        st.dataframe(df_sal, use_container_width=True, hide_index=True)

with tab4:
    if aeropuerto_referencia == "TODOS":
        st.warning("⚠️ Selecciona un aeropuerto específico para ver el análisis meteorológico.")
    else:
        st.subheader(f"📊 Dashboard Meteorológico - {aeropuerto_referencia}")
        meteo_apt = dicc_meteo.get(aeropuerto_referencia, {})
        if meteo_apt:
            df_meteo = pd.DataFrame.from_dict(meteo_apt, orient='index').reset_index()
            df_meteo.columns = ['Hora', 'Viento', 'Rafagas', 'Dir', 'Vis', 'Nubes', 'Temp', 'Precip']
            df_meteo = df_meteo.head(24)
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Evolución del Viento (kts)**")
                st.line_chart(df_meteo.set_index('Hora')['Viento'])
            with c2:
                st.markdown("**Precipitaciones (mm)**")
                st.bar_chart(df_meteo.set_index('Hora')['Precip'])
        
        st.divider()
        m_t = metar_taf.get(aeropuerto_referencia, {})
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("🌐 **METAR (Tiempo Real)**")
            st.code(m_t.get('metar', 'Sin datos'), language='text')
        with c2:
            st.markdown("📅 **TAF (Pronóstico)**")
            st.code(m_t.get('taf', 'Sin datos'), language='text')
