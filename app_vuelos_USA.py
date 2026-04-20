import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import boto3
import json
from datetime import datetime, timezone

# --- CONFIGURACIÓN DE LA PÁGINA (Original de Víctor) ---
st.set_page_config(
    page_title="IA Control de Operaciones USA", 
    page_icon="✈️", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# --- ESTILOS CSS (Original de Víctor) ---
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
        response = s3_client.get_object(Bucket=st.secrets["BUCKET_NAME"], Key='predictions/latest_results.json')
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
# Normalización para asegurar el enganche:
predicciones_ia = {str(k).strip().upper(): v for k, v in data_s3.get('predicciones_ia', {}).items()}
metar_taf = data_s3.get('metar_taf', {})
metadata = data_s3.get('metadata', {})

AEROPUERTOS = {
    "ATL": {"nombre": "Atlanta Hartsfield-Jackson", "coords": [33.6407, -84.4277]},
    "ORD": {"nombre": "Chicago O'Hare", "coords": [41.9742, -87.9073]},
    "LAX": {"nombre": "Los Angeles International", "coords": [33.9416, -118.4085]},
    "JFK": {"nombre": "New York JFK", "coords": [40.6413, -73.7781]}
}

# --- BARRA LATERAL (Original de Víctor) ---
st.sidebar.title("⚙️ Configuración")
aeropuerto_referencia = st.sidebar.selectbox("📍 Selecciona el Aeropuerto", ["TODOS", "ATL", "ORD", "LAX", "JFK"])

st.sidebar.markdown("### 🔍 Filtros de Riesgo IA")
m_baja = st.sidebar.checkbox("🟢 Probabilidad BAJA", value=True)
m_media = st.sidebar.checkbox("🟠 Probabilidad MEDIA", value=True)
m_alta = st.sidebar.checkbox("🔴 Probabilidad ALTA", value=True)

filtros_activos = []
if m_baja: filtros_activos.append("BAJA")
if m_media: filtros_activos.append("MEDIA")
if m_alta: filtros_activos.append("ALTA")

if st.sidebar.button("🔄 Refrescar datos"):
    st.cache_data.clear()
    st.rerun()

# --- LÓGICA DE FILTRADO ---
target_iatas = list(AEROPUERTOS.keys()) if aeropuerto_referencia == "TODOS" else [aeropuerto_referencia]

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
    
    for v in vuelos_aire:
        if v['aeropuerto_referencia'] in target_iatas:
            # FIX: Normalizamos el ID para buscarlo en el diccionario
            cid = str(v.get('callsign', '')).strip().upper()
            pred = predicciones_ia.get(cid, {"prob_texto": "N/A", "alerta": "BAJA", "color": "gray", "icono": "⚪"})
            
            if pred['alerta'] in filtros_activos:
                folium.Marker(
                    location=[v['latitud'], v['longitud']],
                    popup=f"Vuelo: {cid}<br>Riesgo: {pred['icono']} {pred.get('prob_texto','N/A')}",
                    icon=folium.Icon(color=pred['color'], icon="plane", prefix="fa")
                ).add_to(mapa)
    st_folium(mapa, width=1200, height=550)

with tab2:
    st.subheader("🛬 Próximas Llegadas")
    datos_tabla = []
    for v in llegadas_raw:
        if v.get('target_apt') in target_iatas:
            f_data = v.get('flight', {})
            # FIX: Buscamos por callsign para que coincida con el radar
            cid = str(f_data.get('identification', {}).get('callsign', '')).strip().upper()
            num = f_data.get('identification', {}).get('number', {}).get('default', 'N/A')
            pred = predicciones_ia.get(cid, {"prob_texto": "N/A", "icono": "⚪"})
            ts = f_data.get('time', {}).get('scheduled', {}).get('arrival')
            hora = datetime.fromtimestamp(ts, timezone.utc).strftime('%H:%M') if ts else "N/A"
            datos_tabla.append({"Hora (Z)": hora, "Vuelo": num, "Callsign": cid, "Origen": f_data.get('airport', {}).get('origin', {}).get('code', {}).get('iata'), "IA Riesgo": pred['icono'], "Prob. IA": pred.get('prob_texto'), "Estado": f_data.get('status', {}).get('text', 'N/A')})
    if datos_tabla: st.dataframe(pd.DataFrame(datos_tabla).sort_values("Hora (Z)"), use_container_width=True, hide_index=True)

with tab3:
    st.subheader("🛫 Próximas Salidas")
    datos_tabla_sal = []
    for v in salidas_raw:
        if v.get('target_apt') in target_iatas:
            f_data = v.get('flight', {})
            cid = str(f_data.get('identification', {}).get('callsign', '')).strip().upper()
            num = f_data.get('identification', {}).get('number', {}).get('default', 'N/A')
            pred = predicciones_ia.get(cid, {"prob_texto": "N/A", "icono": "⚪"})
            ts = f_data.get('time', {}).get('scheduled', {}).get('departure')
            hora = datetime.fromtimestamp(ts, timezone.utc).strftime('%H:%M') if ts else "N/A"
            datos_tabla_sal.append({"Hora (Z)": hora, "Vuelo": num, "Callsign": cid, "Destino": f_data.get('airport', {}).get('destination', {}).get('code', {}).get('iata', 'N/A'), "IA Riesgo": pred['icono'], "Prob. IA": pred.get('prob_texto')})
    if datos_tabla_sal: st.dataframe(pd.DataFrame(datos_tabla_sal).sort_values("Hora (Z)"), use_container_width=True, hide_index=True)

with tab4:
    if aeropuerto_referencia == "TODOS":
        st.warning("⚠️ Selecciona un aeropuerto específico para ver el análisis.")
    else:
        st.subheader(f"📊 Dashboard Analítico - {aeropuerto_referencia}")
        # 1. Gráficos de Víctor (Aerolíneas y Carga)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Vuelos por Aerolínea**")
            lineas = [v.get('aerolinea_iata', 'N/A') for v in vuelos_aire if v['aeropuerto_referencia'] == aeropuerto_referencia]
            if lineas: st.bar_chart(pd.Series(lineas).value_counts().head(10))
        with c2:
            st.markdown("**Carga Operativa (Prog. vs Real)**")
            # Simulación de carga basada en datos reales de las listas
            horas = [datetime.fromtimestamp(v['flight']['time']['scheduled'].get('arrival', 0), timezone.utc).hour for v in llegadas_raw if v.get('target_apt') == aeropuerto_referencia]
            if horas: st.line_chart(pd.Series(horas).value_counts().sort_index())

        st.divider()
        # 2. Gráficos Meteorológicos (Víctor)
        meteo_apt = dicc_meteo.get(aeropuerto_referencia, {})
        if meteo_apt:
            df_meteo = pd.DataFrame.from_dict(meteo_apt, orient='index').reset_index().head(24)
            df_meteo.columns = ['Hora', 'Viento', 'Rafagas', 'Dir', 'Vis', 'Nubes', 'Temp', 'Precip']
            c3, c4 = st.columns(2)
            c3.line_chart(df_meteo.set_index('Hora')['Viento'])
            c4.bar_chart(df_meteo.set_index('Hora')['Precip'])
        
        st.divider()
        # 3. METAR/TAF (Víctor)
        m_t = metar_taf.get(aeropuerto_referencia, {})
        col_m1, col_m2 = st.columns(2)
        col_m1.code(m_t.get('metar', 'Sin METAR'), language='text')
        col_m2.code(m_t.get('taf', 'Sin TAF'), language='text')
