from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import joblib
import pandas as pd

# Inicializamos la API
app = FastAPI(title="Motor IA - Operaciones de Vuelo")

# Permitimos que cualquier página web se conecte a nuestro cerebro (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cargamos el modelo al arrancar
print("Cargando modelo de IA...")
MODELO_IA = joblib.load('modelo_vuelos_final.joblib')

# Definimos qué datos nos tiene que enviar la página web
class DatosVuelo(BaseModel):
    origen: str
    destino: str
    aerolinea: str
    orig_viento: float
    orig_rafagas: float
    orig_visib: float
    orig_nubes: float
    orig_temp: float
    dest_viento: float
    dest_rafagas: float
    dest_visib: float
    dest_nubes: float
    dest_temp: float

# Creamos la ruta de predicción
@app.post("/predecir")
def predecir_riesgo(vuelo: DatosVuelo):
    # Traducimos los textos a números como aprendió el modelo
    try:
        enc_orig = MODELO_IA['le_orig'].transform([vuelo.origen])[0] if vuelo.origen in MODELO_IA['le_orig'].classes_ else 0
        enc_dest = MODELO_IA['le_dest'].transform([vuelo.destino])[0] if vuelo.destino in MODELO_IA['le_dest'].classes_ else 0
        enc_carr = MODELO_IA['le_carrier'].transform([vuelo.aerolinea])[0] if vuelo.aerolinea in MODELO_IA['le_carrier'].classes_ else 0
    except:
        enc_orig, enc_dest, enc_carr = 0, 0, 0

    # Creamos la tabla para la IA
    input_df = pd.DataFrame([[
        vuelo.orig_viento, vuelo.orig_rafagas, vuelo.orig_visib, vuelo.orig_nubes, vuelo.orig_temp,
        vuelo.dest_viento, vuelo.dest_rafagas, vuelo.dest_visib, vuelo.dest_nubes, vuelo.dest_temp,
        enc_orig, enc_dest, enc_carr
    ]], columns=MODELO_IA['features'])

    # Calculamos
    prob = MODELO_IA['modelo'].predict_proba(input_df)[0][1]

    # Determinamos el semáforo
    if prob < 0.25: nivel = "BAJO"
    elif prob < 0.60: nivel = "MEDIO"
    else: nivel = "ALTO"

    # Devolvemos la respuesta a la web
    return {
        "probabilidad": round(prob * 100, 1),
        "nivel_riesgo": nivel
    }
