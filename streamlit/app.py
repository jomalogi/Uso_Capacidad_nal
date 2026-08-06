import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import mysql.connector
import warnings
import io
import re
from datetime import datetime, timezone, timedelta
warnings.filterwarnings('ignore')

TIMESLOT_RE = re.compile(r"^(\d{1,2}[:\-]\d{1,2}|ALL\s*DAY)", re.IGNORECASE)

def transformar_excel(content):
    # ── LEER EXCEL ─────────────────────────────────────────────────────────────
    df_raw = pd.read_excel(io.BytesIO(content), sheet_name="Quota Data", header=None)

    # ── Detectar todos los bloques de fecha ──────────────────────────────────
    date_blocks_raw = [(i, v) for i, v in enumerate(df_raw.iloc[0, :])
                       if pd.notna(v) and v != "Time slot\nCapacity categories"]
    if not date_blocks_raw:
        raise ValueError("No se encontraron bloques de fecha en el archivo")

    # ── DEDUPLICAR: conservar solo el PRIMER bloque por cada fecha ───────────
    seen_dates = {}
    date_blocks = []
    for col_idx, date_val in date_blocks_raw:
        try:
            date_key = pd.to_datetime(date_val).date()
        except Exception:
            date_key = str(date_val).strip()
        if date_key not in seen_dates:
            seen_dates[date_key] = True
            date_blocks.append((col_idx, date_val))

    diag_msg = None
    if len(date_blocks_raw) != len(date_blocks):
        fechas_dup = len(date_blocks_raw) - len(date_blocks)
        diag_msg = (f"⚠️ Se detectaron {fechas_dup} bloque(s) duplicado(s) en el Excel. "
                    f"Se usó solo el primer bloque por fecha (datos planificados).")

    # ── OPTIMIZACIÓN VELOCIDAD: Pre-procesar las filas 1 sola vez ─────────────
    row_meta = []
    zone = timeslot = None
    
    # Recorremos la estructura vertical (columnas 0 y 1) solo 1 vez
    for idx in range(2, len(df_raw)):
        v0 = df_raw.iat[idx, 0]
        v1 = df_raw.iat[idx, 1]
        
        if pd.notna(v0) and v0 != "Time slot\nCapacity categories":
            zone = str(v0).strip()
            timeslot = str(v1).strip() if pd.notna(v1) else None
            continue
            
        if pd.notna(v1):
            s = str(v1).strip()
            if TIMESLOT_RE.match(s):
                timeslot = s
            else:
                row_meta.append((zone, timeslot, s, idx))

    # Extracción ultrarrápida usando matriz NumPy en lugar de iterrows
    val_matrix = df_raw.values
    rows = []
    
    for block_start, date_val in date_blocks:
        for z, ts, cat, r_idx in row_meta:
            r_vals = val_matrix[r_idx, block_start:block_start + 14]
            rows.append({
                "fecha": date_val, "zona": z,
                "franja_horaria": ts, "categoria": cat,
                "quota_pct":           r_vals[0],
                "min_quota":           r_vals[1],
                "used_quota_pct":      r_vals[2],
                "weight":              r_vals[3],
                "estimated_quota_pct": r_vals[4],
                "stop_booking_pct":    r_vals[5],
                "status":              r_vals[6],
                "close_time":          r_vals[7],
                "max_available":       r_vals[8],
                "other_activities":    r_vals[9],
                "quota_mins":          r_vals[10],
                "plan":                r_vals[11],
                "booked_activities":   r_vals[12],
                "used":                r_vals[13],
            })

    df = pd.DataFrame(rows)
    df["close_time"] = pd.to_datetime(df["close_time"], errors="coerce")
    if not df.empty:
        df = df.drop_duplicates(subset=["fecha", "zona", "franja_horaria", "categoria"], keep="first")
    fechas = sorted([str(f) for f in df["fecha"].dropna().unique()])
    return df, fechas, diag_msg


def render_carga_datos():
    st.markdown("### Carga de Cuotas OFSC")
    st.write("Sube el archivo Excel exportado desde OFSC.")

    uploaded_file = st.file_uploader("Selecciona el archivo .xlsx", type=["xlsx", "xls"], key="uploader_backup_jun20")

    if uploaded_file is not None:
        if st.button("Procesar y guardar en BD", type="primary", key="btn_backup_jun20"):
            with st.spinner("Procesando archivo..."):
                try:
                    file_content = uploaded_file.read()
                    df_new, fechas_nuevas, diag_msg = transformar_excel(file_content)
                    
                    if diag_msg:
                        st.warning(diag_msg)
                        
                    st.info(f"Archivo leído correctamente. Fechas encontradas: {', '.join(fechas_nuevas)}")

                    zonas_nuevas = df_new["zona"].dropna().unique().tolist()

                    conn = mysql.connector.connect(**DB)
                    cur = conn.cursor()

                    if fechas_nuevas and zonas_nuevas:
                        ph_f = ','.join(['%s'] * len(fechas_nuevas))
                        ph_z = ','.join(['%s'] * len(zonas_nuevas))
                        query_del = f"DELETE FROM uso_cupos WHERE fecha IN ({ph_f}) AND zona IN ({ph_z})"
                        params_del = tuple(fechas_nuevas) + tuple(zonas_nuevas)
                        cur.execute(query_del, params_del)
                        num_borrados = cur.rowcount
                        if num_borrados > 0:
                            st.warning(f"Se reemplazaron {num_borrados} registros existentes para las fechas y zonas cargadas.")

                    SQL_INSERT = """INSERT INTO uso_cupos (
                        fecha, zona, franja_horaria, categoria,
                        quota_pct, min_quota, used_quota_pct, weight,
                        estimated_quota_pct, stop_booking_pct,
                        status, close_time, max_available, other_activities,
                        quota_mins, plan, booked_activities, used
                    ) VALUES (
                        %(fecha)s, %(zona)s, %(franja_horaria)s, %(categoria)s,
                        %(quota_pct)s, %(min_quota)s, %(used_quota_pct)s, %(weight)s,
                        %(estimated_quota_pct)s, %(stop_booking_pct)s,
                        %(status)s, %(close_time)s, %(max_available)s, %(other_activities)s,
                        %(quota_mins)s, %(plan)s, %(booked_activities)s, %(used)s
                    ) ON DUPLICATE KEY UPDATE
                        quota_pct=VALUES(quota_pct), min_quota=VALUES(min_quota),
                        used_quota_pct=VALUES(used_quota_pct), weight=VALUES(weight),
                        estimated_quota_pct=VALUES(estimated_quota_pct), stop_booking_pct=VALUES(stop_booking_pct),
                        status=VALUES(status), close_time=VALUES(close_time), max_available=VALUES(max_available),
                        other_activities=VALUES(other_activities), quota_mins=VALUES(quota_mins),
                        plan=VALUES(plan), booked_activities=VALUES(booked_activities), used=VALUES(used)
                    """

                    ins = err = 0
                    errores_detalle = []
                    
                    # Deduplicar en Python por seguridad
                    if not df_new.empty:
                        df_new = df_new.drop_duplicates(subset=["fecha", "zona", "franja_horaria", "categoria"], keep="first")
                    
                    records = df_new.where(pd.notna(df_new), other=None).to_dict("records")

                    def _limpiar(v):
                        if v is None:
                            return None
                        if isinstance(v, pd.Timestamp):
                            return None if pd.isna(v) else v.to_pydatetime()
                        try:
                            if pd.isna(v):
                                return None
                        except (TypeError, ValueError):
                            pass
                        if hasattr(v, "item"):
                            return v.item()
                        return v

                    clean_records = [{k: _limpiar(v) for k, v in rec.items()} for rec in records]

                    # Inserción masiva por lotes
                    chunk_size = 5000
                    for i in range(0, len(clean_records), chunk_size):
                        chunk = clean_records[i:i + chunk_size]
                        try:
                            cur.executemany(SQL_INSERT, chunk)
                            ins += len(chunk)
                        except Exception as e:
                            # Fallback registro por registro si executemany reporta error
                            for rec in chunk:
                                try:
                                    cur.execute(SQL_INSERT, rec)
                                    ins += 1
                                except Exception as ex_single:
                                    err += 1
                                    if len(errores_detalle) < 5:
                                        errores_detalle.append(str(ex_single))

                    conn.commit()
                    cur.close()
                    conn.close()

                    if err > 0:
                        st.error(f"Se guardaron {ins} registros con {err} errores.")
                        st.warning("Muestra de los primeros 5 errores:")
                        for ed in errores_detalle:
                            st.code(ed)
                    else:
                        st.success(f"¡Éxito! Se guardaron {ins} nuevos registros.")
                    st.cache_data.clear()
                except Exception as e:
                    import traceback
                    st.error(f"Error procesando el archivo: {str(e)}")
                    st.code(traceback.format_exc())

# ── CONFIG ────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Capacidades — Todas las Regionales",
    page_icon="🔴",
    layout="wide",
    initial_sidebar_state="expanded"
)

import os

DB_HOST = os.environ.get("DB_HOST", "ofsc_cupos_db")
DB_PORT = int(os.environ.get("DB_PORT", 3306))

DB = dict(host=DB_HOST, port=DB_PORT, database="ofsc_cupos",
          user="ofsc_user", password=os.environ.get("DB_PASSWORD", ""), connection_timeout=30)

# ── TABLA DE TRABAJOS ─────────────────────────────────────────────────────────
TRABAJOS = pd.DataFrame([
    ("Brownfield",                            180, "FTTH", "Bronwfield"),
    ("HFC  Arreglos Pymes",                   90,  "HFC",  "Arreglos"),
    ("HFC  Instalacion Pymes",                180, "HFC",  "Instalaciones"),
    ("Instalacion FTTH",                      150, "FTTH", "Instalaciones"),
    ("Mantenimiento FTTH",                    75,  "FTTH", "Arreglos"),
    ("Postventa  FTTH",                       75,  "FTTH", "Posventas"),
    ("Traslado FTTH",                         150, "FTTH", "Traslados"),
    ("HFC  Traslados pymes",                  180, "HFC",  "Traslados"),
    ("Brownfield Flash",                      180, "FTTH", "Bronwfield"),
    ("Instalaciones Básicas Estándar",        75,  "HFC",  "Instalaciones Básica"),
    ("Instalaciones Empaquetadas Estándar",   150, "HFC",  "Instalaciones"),
    ("Maintenance",                           75,  "HFC",  "Arreglos"),
    ("Postventa Estandar",                    75,  "HFC",  "Posventas"),
    ("Transfers",                             150, "HFC",  "Traslados"),
    ("Disconnect",                            25,  "HFC",  "Desconexiones"),
    ("HFC Postventa Pymes",                   90,  "HFC",  "Posventas"),
    ("Instalacion",                           150, "HFC",  "Instalaciones"),
    ("Instalación FWA",                       75,  "FWA",  "Instalaciones"),
    ("Mantenimiento FWA",                     75,  "FWA",  "Instalaciones"),
    ("Andamios",                              150, "HFC",  "Andamios"),
    ("Technical Appt.",                       75,  "HFC",  "Arreglos"),
    ("Ventas Tecnico",                        75,  "HFC",  "Ventas Técnico"),
    ("Verifications",                         75,  "HFC",  "Verificaciones"),
    ("Blindaje",                              75,  "HFC",  "Posventas"),
    ("Instalacion Claro Box",                 75,  "HFC",  "Instalaciones"),
    ("Instalaciones Alto Valor  Claro Box",   150, "HFC",  "Instalaciones"),
    ("Instalaciones FTTH Claro Box",          150, "FTTH", "Instalaciones"),
    ("Mantenimientos Moviles Especiales",     75,  "HFC",  "Arreglos"),
    ("Ordenes Moviles Especiales",            150, "FTTH", "Instalaciones"),
    ("Desconexion por Cartera",               25,  "HFC",  "Desconexiones"),
    ("Instalaciones FTTH Alto Valor",         150, "FTTH", "Instalaciones"),
    ("Mantenimientos FTTH Alto Valor",        75,  "FTTH", "Arreglos"),
    ("Reconnect",                             75,  "HFC",  "Reconexiones"),
    ("Traslados FTTH Alto Valor",             150, "FTTH", "Traslados"),
    ("Instalaciones DTH Red BI",              150, "HFC",  "Instalaciones"),
    ("Postventa FTTH Alto Valor",             75,  "FTTH", "Posventas"),
    ("Brownfield PYMES",                      180, "FTTH", "Bronwfield"),
    ("CAPACIDAD UNIFICADA ACOMETIDA INTERNA", 180, "HFC",  "Instalaciones"),
    ("Demostraciones",                        75,  "HFC",  "Instalaciones"),
], columns=["Trabajo", "Minutos", "Red", "Actividad"])

# ── TABLA MAESTRA DE CIUDADES ─────────────────────────────────────────────────
TABLA_FTTH = pd.DataFrame([
    # R4
    ("Bojaca",                                    "R4", "Dico",         "BOGOTÁ OCCIDENTE"),
    ("Choachi",                                   "R4", "Tabasco",      "BOGOTÁ ORIENTE"),
    ("Desconexiones-Reconexiones Dico Sabana",    "R4", "Dico",         "BOGOTÁ OCCIDENTE"),
    ("Desconexiones-Reconexiones Sicte Sabana",   "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("Guatavita",                                 "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("Moviles Backup Dico Sabana",                "R4", "Dico",         "BOGOTÁ OCCIDENTE"),
    ("Moviles Backup Sicte Sabana",               "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("MOVILES ESPECIALES DICO SABANA",            "R4", "Dico",         "BOGOTÁ OCCIDENTE"),
    ("MOVILES ESPECIALES SICTE SABANA",           "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("Nemocon",                                   "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("R4 -SICTE CALERA",                          "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("R4 -SICTE CHIA- CAJICA",                    "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("R4 -SICTE COTA",                            "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("R4 -SICTE SOPO-GUASCA",                     "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("R4 -SICTE TENJO- TABIO",                    "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("R4 -SICTE TOCANCIPA-GACHANCIPA-SUESCA",     "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("R4 -SICTE ZIPAQUIRA- COGUA",                "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("R4-Conectar BOGOTA",                        "R4", "Conectar TV",  "BOGOTÁ"),
    ("R4-DICO BOGOTA",                            "R4", "Dico",         "BOGOTÁ OCCIDENTE"),
    ("R4-DICO FACA",                              "R4", "Dico",         "BOGOTÁ OCCIDENTE"),
    ("R4-DICO FUNZA-MOSQUERA",                    "R4", "Dico",         "BOGOTÁ OCCIDENTE"),
    ("R4-DICO MADRID",                            "R4", "Dico",         "BOGOTÁ OCCIDENTE"),
    ("R4-DICO ROSAL-SUBACHOQUE",                  "R4", "Dico",         "BOGOTÁ OCCIDENTE"),
    ("R4-Dominion BOGOTA",                        "R4", "Dominion",     "BOGOTÁ"),
    ("R4-Sicte BOGOTA",                           "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("R4-Tabasco BOGOTA",                         "R4", "TABASCO",      "BOGOTÁ ORIENTE"),
    ("R4-Telcos BOGOTA",                          "R4", "TELCOS",       "BOGOTÁ SUR"),
    ("Bogota_Conectar",                           "R4", "Conectar TV",  "BOGOTÁ"),
    ("Bogota_Dominion",                           "R4", "Dominion",     "BOGOTÁ"),
    ("Bogota_Ezentis",                            "R4", "Ezentis",      "BOGOTÁ"),
    ("Bogota_Itdnt",                              "R4", "ITDNT",        "BOGOTÁ"),
    ("Bogota_Liteyca",                            "R4", "Liteyca",      "BOGOTÁ"),
    ("Bogota_Opegin",                             "R4", "Opegin",       "BOGOTÁ"),
    ("La Calera",                                 "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("Pacho",                                     "R4", "Claro",        "BOGOTÁ NORTE"),
    ("Quebradanegra",                             "R4", "Claro",        "BOGOTÁ OCCIDENTE"),
    ("Sabana Norte 2",                            "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("Sabana Norte 3",                            "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("Sabana Norte 4",                            "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("Sabana Norte 5",                            "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("Sabana Norte 6",                            "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("Sesquile",                                  "R4", "SICTE",        "BOGOTÁ NORTE"),
    ("Ubate",                                     "R4", "Claro",        "BOGOTÁ NORTE"),
    ("Villeta",                                   "R4", "Claro",        "BOGOTÁ OCCIDENTE"),
    ("Zipacon",                                   "R4", "Dico",         "BOGOTÁ OCCIDENTE"),
    ("Arreglos Home Networking",                  "R4", "Claro",        "BOGOTÁ"),
    ("BACKLOG",                                   "R4", "Claro",        "BOGOTÁ"),
    ("Zona 5 - R&R",                              "R4", "Claro",        "BOGOTÁ"),
    # R1
    ("Aguachica DOMINION",                        "R1", "DOMINION",     "COSTA NORTE"),
    ("Aguachica CICSA",                           "R1", "Tabasco",      "COSTA NORTE"),
    ("Barranquilla",                              "R1", "DISTRIBUIDORES","COSTA NORTE"),
    ("Barranquilla DOMINION",                     "R1", "DOMINION",     "COSTA NORTE"),
    ("Barranquilla INMEL",                        "R1", "INMEL",        "COSTA NORTE"),
    ("Cartagena INMEL",                           "R1", "INMEL",        "COSTA SUR"),
    ("Cerete INMEL",                              "R1", "INMEL",        "COSTA SUR"),
    ("Cerete CINCO",                              "R1", "CINCO",        "COSTA SUR"),
    ("Cienaga Directos",                          "R1", "CLARO",        "COSTA NORTE"),
    ("Cienaga DOMINION",                          "R1", "DOMINION",     "COSTA NORTE"),
    ("Corozal INMEL",                             "R1", "INMEL",        "COSTA NORTE"),
    ("Corozal CINCO",                             "R1", "CINCO",        "COSTA NORTE"),
    ("Magangué",                                  "R1", "INMEL",        "COSTA NORTE"),
    ("Monteria INMEL",                            "R1", "INMEL",        "COSTA NORTE"),
    ("Monteria CINCO",                            "R1", "CINCO",        "COSTA NORTE"),
    ("Puerto Colombia",                           "R1", "DISTRIBUIDORES","COSTA NORTE"),
    ("Puerto Colombia INMEL",                     "R1", "INMEL",        "COSTA NORTE"),
    ("Riohacha DOMINION",                         "R1", "DOMINION",     "COSTA NORTE"),
    ("Sabanalarga",                               "R1", "DOMINION",     "COSTA NORTE"),
    ("Sahagun",                                   "R1", "DISTRIBUIDORES","COSTA NORTE"),
    ("Sahagún INMEL",                             "R1", "INMEL",        "COSTA NORTE"),
    ("Salgar INMEL",                              "R1", "INMEL",        "COSTA NORTE"),
    ("SAN ANDRES DOMINION",                       "R1", "DOMINION",     "COSTA NORTE"),
    ("San Andres Islas  CICSA",                   "R1", "Tabasco",      "COSTA NORTE"),
    ("Santa Marta DOMINION",                      "R1", "DOMINION",     "COSTA NORTE"),
    ("Since",                                     "R1", "DISTRIBUIDORES","COSTA NORTE"),
    ("Sincelejo INMEL",                           "R1", "INMEL",        "COSTA NORTE"),
    ("Sincelejo CINCO",                           "R1", "CINCO",        "COSTA NORTE"),
    ("Soledad DOMINION",                          "R1", "DOMINION",     "COSTA NORTE"),
    ("Turbaco INMEL",                             "R1", "INMEL",        "COSTA NORTE"),
    ("Valledupar",                                "R1", "DISTRIBUIDORES","COSTA NORTE"),
    ("Valledupar DOMINION",                       "R1", "DOMINION",     "COSTA NORTE"),
    # R2
    ("Chinchina SICTE",                           "R2", "SICTE",        "EJE CAFETERO"),
    ("La Dorada SICTE",                           "R2", "SICTE",        "EJE CAFETERO"),
    ("Manizales/Villamaria SICTE",                "R2", "SICTE",        "EJE CAFETERO"),
    ("Armenia SICTE",                             "R2", "SICTE",        "EJE CAFETERO"),
    ("Calarca/Circasia SICTE",                    "R2", "SICTE",        "EJE CAFETERO"),
    ("La Tebaida SICTE",                          "R2", "SICTE",        "EJE CAFETERO"),
    ("Montenegro/Quimbaya SICTE",                 "R2", "SICTE",        "EJE CAFETERO"),
    ("Anserma SICTE",                             "R2", "SICTE",        "EJE CAFETERO"),
    ("Belen de Umbria SICTE",                     "R2", "SICTE",        "EJE CAFETERO"),
    ("Belen de Umbria  SICTE",                    "R2", "SICTE",        "EJE CAFETERO"),
    ("La Virginia SICTE",                         "R2", "SICTE",        "EJE CAFETERO"),
    ("Pereira/Dosquebradas SICTE",                "R2", "SICTE",        "EJE CAFETERO"),
    ("Santa Rosa de Cabal SICTE",                 "R2", "SICTE",        "EJE CAFETERO"),
    ("Viterbo SICTE",                             "R2", "SICTE",        "EJE CAFETERO"),
    ("Filandia AYA TELECOMUNICACIONES",           "R2", "AYA",          "EJE CAFETERO"),
    ("Manzanares NET&COM",                        "R2", "NET&COM",      "EJE CAFETERO"),
    ("Riosucio NET&COM",                          "R2", "NET&COM",      "EJE CAFETERO"),
    ("Salamina NET&COM",                          "R2", "NET&COM",      "EJE CAFETERO"),
    ("Supia NET&COM",                             "R2", "NET&COM",      "EJE CAFETERO"),
    ("Apartado INMEL",                            "R2", "INMEL",        "ANTCHO"),
    ("Caldas INMEL",                              "R2", "INMEL",        "MEDELLIN"),
    ("Carepa INMEL",                              "R2", "INMEL",        "ANTCHO"),
    ("Chigorodo INMEL",                           "R2", "INMEL",        "ANTCHO"),
    ("Envigado INMEL",                            "R2", "INMEL",        "MEDELLIN"),
    ("Itagui INMEL",                              "R2", "INMEL",        "MEDELLIN"),
    ("La Estrella INMEL",                         "R2", "INMEL",        "MEDELLIN"),
    ("Medellin INMEL",                            "R2", "INMEL",        "MEDELLIN"),
    ("Sabaneta INMEL",                            "R2", "INMEL",        "MEDELLIN"),
    ("San Antonio de Prado INMEL",                "R2", "INMEL",        "MEDELLIN"),
    ("Turbo INMEL",                               "R2", "INMEL",        "ANTCHO"),
    ("Carmen de Viboral CINCO",                   "R2", "CINCO",        "ANTCHO"),
    ("La CeJa CINCO",                             "R2", "CINCO",        "ANTCHO"),
    ("Marinilla CINCO",                           "R2", "CINCO",        "ANTCHO"),
    ("Medellin CINCO",                            "R2", "CINCO",        "MEDELLIN"),
    ("Rionegro CINCO",                            "R2", "CINCO",        "ANTCHO"),
    ("Barbosa DOMINION",                          "R2", "DOMINION",     "MEDELLIN"),
    ("Bello DOMINION",                            "R2", "DOMINION",     "MEDELLIN"),
    ("Caucasia DOMINION",                         "R2", "DOMINION",     "ANTCHO"),
    ("Copacabana DOMINION",                       "R2", "DOMINION",     "MEDELLIN"),
    ("Girardota DOMINION",                        "R2", "DOMINION",     "MEDELLIN"),
    ("Medellin DOMINION",                         "R2", "DOMINION",     "MEDELLIN"),
    ("Yarumal DOMINION",                          "R2", "DOMINION",     "MEDELLIN"),
    # R5
    ("R5-Acacias TELCOS",                         "R5", "Telcos",       "Cunmenal"),
    ("R5-Aguazul TELCOS",                         "R5", "Telcos",       "Cunmenal"),
    ("Aguazul",                                   "R5", "Telcos",       "Cunmenal"),
    ("R5-Anapoima TABASCO",                       "R5", "Tabasco",      "Cunmenal"),
    ("R5-Barrancabermeja TABASCO",                "R5", "Tabasco",      "Sanboy"),
    ("R5-Bucaramanga TABASCO",                    "R5", "Tabasco",      "Sanboy"),
    ("R5-Bucaramanga TELCOS",                     "R5", "Telcos",       "Sanboy"),
    ("R5-Caqueza TABASCO",                        "R5", "Tabasco",      "Cunmenal"),
    ("R5-Chiquinquira TABASCO",                   "R5", "Tabasco",      "Sanboy"),
    ("Chiquinquira",                              "R5", "Tabasco",      "Sanboy"),
    ("R5-Cucuta TELCOS",                          "R5", "Telcos",       "Sanboy"),
    ("Cucuta (Cicsa)",                            "R5", "Tabasco",      "Sanboy"),
    ("R5-Cumaral TELCOS",                         "R5", "Telcos",       "Cunmenal"),
    ("Cumaral",                                   "R5", "Telcos",       "Cunmenal"),
    ("R5-Duitama TABASCO",                        "R5", "Tabasco",      "Sanboy"),
    ("R5-Floridablanca TELCOS",                   "R5", "Telcos",       "Sanboy"),
    ("R5-Fusagasuga TABASCO",                     "R5", "Tabasco",      "Cunmenal"),
    ("R5-Girardot TABASCO",                       "R5", "Tabasco",      "Cunmenal"),
    ("R5-Giron TABASCO",                          "R5", "Tabasco",      "Sanboy"),
    ("R5-Guaduas TABASCO",                        "R5", "Tabasco",      "Cunmenal"),
    ("R5-La Mesa TABASCO",                        "R5", "Tabasco",      "Cunmenal"),
    ("R5-Los Patios TELCOS",                      "R5", "Telcos",       "Sanboy"),
    ("R5-Ocaña TELCOS",                           "R5", "Telcos",       "Sanboy"),
    ("Ocaña",                                     "R5", "Telcos",       "Sanboy"),
    ("R5-Pacho TABASCO",                          "R5", "Tabasco",      "Cunmenal"),
    ("Pacho",                                     "R5", "Tabasco",      "Cunmenal"),
    ("R5-Paipa TABASCO",                          "R5", "Tabasco",      "Sanboy"),
    ("Paipa",                                     "R5", "Tabasco",      "Sanboy"),
    ("R5-Pamplona TELCOS",                        "R5", "Telcos",       "Sanboy"),
    ("Pamplona",                                  "R5", "Telcos",       "Sanboy"),
    ("R5-Piedecuesta TABASCO",                    "R5", "Tabasco",      "Sanboy"),
    ("R5-Quebradanegra TABASCO",                  "R5", "Tabasco",      "Cunmenal"),
    ("R5-Restrepo TELCOS",                        "R5", "Telcos",       "Cunmenal"),
    ("Restrepo",                                  "R5", "Telcos",       "Cunmenal"),
    ("R5-Ricaurte TABASCO",                       "R5", "Tabasco",      "Cunmenal"),
    ("R5-San Gil TABASCO",                        "R5", "Tabasco",      "Sanboy"),
    ("San Gil (Telcos)",                          "R5", "Telcos",       "Sanboy"),
    ("R5-San Martin TELCOS",                      "R5", "Telcos",       "Cunmenal"),
    ("San Martin",                                "R5", "Telcos",       "Cunmenal"),
    ("R5-Silvania TABASCO",                       "R5", "Tabasco",      "Cunmenal"),
    ("R5-Socorro TABASCO",                        "R5", "Tabasco",      "Sanboy"),
    ("R5-Sogamoso TABASCO",                       "R5", "Tabasco",      "Sanboy"),
    ("R5-Tunja TABASCO",                          "R5", "Tabasco",      "Sanboy"),
    ("R5-Ubate TABASCO",                          "R5", "Tabasco",      "Cunmenal"),
    ("R5-Villa de Leyva TABASCO",                 "R5", "Tabasco",      "Sanboy"),
    ("Villa de Leyva",                            "R5", "Tabasco",      "Sanboy"),
    ("R5-Villa del Rosario TELCOS",               "R5", "Telcos",       "Sanboy"),
    ("R5-Villavicencio TELCOS",                   "R5", "Telcos",       "Cunmenal"),
    ("R5-Villeta TABASCO",                        "R5", "Tabasco",      "Cunmenal"),
    ("Villeta",                                   "R5", "Tabasco",      "Cunmenal"),
    ("R5-Yopal TELCOS",                           "R5", "Telcos",       "Cunmenal"),
    ("VILLANUEVA",                                "R5", "Telcos",       "Cunmenal"),
    # R3 Cali
    ("CALI NORTE CONECTAR",                       "R3", "Conectar TV",  "CALI"),
    ("CALI SUR CICSA",                            "R3", "Tabasco",      "CALI"),
    ("Jamundi CICSA",                             "R3", "Tabasco",      "CALI"),
    ("Yumbo CONECTAR",                            "R3", "Conectar TV",  "CALI"),
    # R3 Tolhuca
    ("Chicoral CONECTAR",                         "R3", "Conectar TV",  "TOLHUCA"),
    ("Espinal CONECTAR",                          "R3", "Conectar TV",  "TOLHUCA"),
    ("Flandes CONECTAR",                          "R3", "Conectar TV",  "TOLHUCA"),
    ("Florencia CONECTAR",                        "R3", "Conectar TV",  "TOLHUCA"),
    ("Garzon CICSA",                              "R3", "Tabasco",      "TOLHUCA"),
    ("Guamo CONECTAR",                            "R3", "Conectar TV",  "TOLHUCA"),
    ("Ibague CONECTAR",                           "R3", "Conectar TV",  "TOLHUCA"),
    ("Melgar CONECTAR",                           "R3", "Conectar TV",  "TOLHUCA"),
    ("Neiva CICSA",                               "R3", "Tabasco",      "TOLHUCA"),
    ("Pitalito CICSA",                            "R3", "Tabasco",      "TOLHUCA"),
    # R3 VACANA
    ("Andalucia CONECTAR",                        "R3", "Conectar TV",  "VACANA"),
    ("Buga CONECTAR",                             "R3", "Conectar TV",  "VACANA"),
    ("Caicedonia CONECTAR",                       "R3", "Conectar TV",  "VACANA"),
    ("Candelaria CONECTAR",                       "R3", "Conectar TV",  "VACANA"),
    ("Cartago CONECTAR",                          "R3", "Conectar TV",  "VACANA"),
    ("Cerrito CONECTAR",                          "R3", "Conectar TV",  "VACANA"),
    ("Ciudad del Campo CONECTAR",                 "R3", "Conectar TV",  "VACANA"),
    ("Florida CONECTAR",                          "R3", "Conectar TV",  "VACANA"),
    ("Ipiales CICSA",                             "R3", "Tabasco",      "VACANA"),
    ("La Union CONECTAR",                         "R3", "Conectar TV",  "VACANA"),
    ("Palmira CONECTAR",                          "R3", "Conectar TV",  "VACANA"),
    ("Pasto CICSA",                               "R3", "Tabasco",      "VACANA"),
    ("Popayan CICSA",                             "R3", "Tabasco",      "VACANA"),
    ("Pradera CONECTAR",                          "R3", "Conectar TV",  "VACANA"),
    ("Puerto Tejada CICSA",                       "R3", "Tabasco",      "VACANA"),
    ("Roldanillo CONECTAR",                       "R3", "Conectar TV",  "VACANA"),
    ("Santander de Quilichao CICSA",              "R3", "Tabasco",      "VACANA"),
    ("Sevilla CONECTAR",                          "R3", "Conectar TV",  "VACANA"),
    ("Tulua CONECTAR",                            "R3", "Conectar TV",  "VACANA"),
    ("Zarzal CONECTAR",                           "R3", "Conectar TV",  "VACANA"),
    ("La Victoria NET&COM",                       "R3", "NET&COM",      "VACANA"),
    ("Leticia",                                   "R3", "Claro",        "TOLHUCA"),
], columns=["Categoria", "Regional", "Aliado", "Territorio"])

# ── CIUDADES QUE SOLO TIENEN FTTH (excluir HFC de éstas) ───────────────────────
ZONAS_SOLO_FTTH = [
    "Andalucia CONECTAR", "Caicedonia CONECTAR", "Cartago CONECTAR",
    "Guamo CONECTAR", "Ipiales CICSA", "La Union CONECTAR", "Pitalito CICSA",
    "Roldanillo CONECTAR", "Sevilla CONECTAR", "Zarzal CONECTAR", "Garzon CICSA"
]

# ── CIUDADES CON FTTH ──────────────────────────────────────────────────────────
CIUDADES_CON_FTTH = [
    # R3
    "CALI NORTE CONECTAR", "CALI SUR CICSA", "Jamundi CICSA", "Yumbo CONECTAR",
    "Florencia CONECTAR", "Ibague CONECTAR", "Neiva CICSA",
    "Andalucia CONECTAR", "Buga CONECTAR", "Caicedonia CONECTAR", "Candelaria CONECTAR",
    "Cartago CONECTAR", "Garzon CICSA", "Guamo CONECTAR", "Ipiales CICSA",
    "La Union CONECTAR", "Palmira CONECTAR", "Pasto CICSA", "Pitalito CICSA",
    "Popayan CICSA", "Puerto Tejada CICSA", "Roldanillo CONECTAR", "Sevilla CONECTAR",
    "Tulua CONECTAR", "Zarzal CONECTAR",
    # R4
    "R4 -SICTE CALERA", "R4 -SICTE CHIA- CAJICA", "R4 -SICTE COTA",
    "R4 -SICTE SOPO-GUASCA", "R4 -SICTE TOCANCIPA-GACHANCIPA-SUESCA",
    "R4 -SICTE ZIPAQUIRA- COGUA", "R4-Conectar BOGOTA", "R4-DICO BOGOTA",
    "R4-DICO FACA", "R4-DICO FUNZA-MOSQUERA", "R4-DICO MADRID",
    "R4-Dominion BOGOTA", "R4-Sicte BOGOTA", "R4-Tabasco BOGOTA", "R4-Telcos BOGOTA",
    # R1
    "Barranquilla", "Barranquilla DOMINION", "Barranquilla INMEL",
    "Cartagena INMEL", "Monteria INMEL", "Puerto Colombia", "Puerto Colombia INMEL",
    "Riohacha DOMINION", "Santa Marta DOMINION", "Sincelejo INMEL",
    "Soledad DOMINION", "Valledupar", "Valledupar DOMINION",
    # R2
    "Chinchina SICTE", "La Dorada SICTE", "Manizales/Villamaria SICTE",
    "Armenia SICTE", "Pereira/Dosquebradas SICTE", "Santa Rosa de Cabal SICTE",
    "Caldas INMEL", "Envigado INMEL", "Itagui INMEL", "La Estrella INMEL",
    "Medellin INMEL", "Sabaneta INMEL", "San Antonio de Prado INMEL",
    "Medellin CINCO", "Rionegro CINCO", "Bello DOMINION", "Copacabana DOMINION",
    "Medellin DOMINION",
    # R5
    "R5-Acacias TELCOS", "R5-Barrancabermeja TABASCO", "R5-Bucaramanga TABASCO",
    "R5-Bucaramanga TELCOS", "R5-Cucuta TELCOS", "R5-Duitama TABASCO",
    "R5-Floridablanca TELCOS", "R5-Fusagasuga TABASCO", "R5-Girardot TABASCO",
    "R5-Giron TABASCO", "R5-Los Patios TELCOS", "R5-Piedecuesta TABASCO",
    "R5-Sogamoso TABASCO", "R5-Tunja TABASCO", "R5-Villa del Rosario TELCOS",
    "R5-Villavicencio TELCOS", "R5-Yopal TELCOS",
    # R3
    "CALI NORTE CONECTAR", "CALI SUR CICSA", "Jamundi CICSA", "Yumbo CONECTAR",
    "Florencia CONECTAR", "Ibague CONECTAR", "Neiva CICSA", "Buga CONECTAR",
    "Candelaria CONECTAR", "Ipiales CICSA", "Palmira CONECTAR", "Pasto CICSA",
    "Popayan CICSA", "Puerto Tejada CICSA", "Santander de Quilichao CICSA",
    "Tulua CONECTAR"
]

# ── ZONAS BROWNFIELD ──────────────────────────────────────────────────────────
ZONAS_BROWNFIELD = [
    # R4
    "R4 -SICTE CALERA", "R4 -SICTE CHIA- CAJICA", "R4 -SICTE COTA",
    "R4 -SICTE SOPO-GUASCA", "R4 -SICTE TOCANCIPA-GACHANCIPA-SUESCA",
    "R4 -SICTE ZIPAQUIRA- COGUA", "R4-Conectar BOGOTA", "R4-DICO BOGOTA",
    "R4-DICO FACA", "R4-DICO FUNZA-MOSQUERA", "R4-DICO MADRID",
    "R4-Dominion BOGOTA", "R4-Sicte BOGOTA", "R4-Tabasco BOGOTA", "R4-Telcos BOGOTA",
    # R1
    "Barranquilla", "Barranquilla DOMINION", "Barranquilla INMEL",
    "Cartagena INMEL", "Monteria INMEL", "Puerto Colombia", "Puerto Colombia INMEL",
    "Riohacha DOMINION", "Santa Marta DOMINION", "Sincelejo INMEL",
    "Soledad DOMINION", "Valledupar", "Valledupar DOMINION",
    # R2
    "Chinchina SICTE", "La Dorada SICTE", "Manizales/Villamaria SICTE",
    "Armenia SICTE", "Pereira/Dosquebradas SICTE", "Santa Rosa de Cabal SICTE",
    "Caldas INMEL", "Envigado INMEL", "Itagui INMEL", "La Estrella INMEL",
    "Medellin INMEL", "Sabaneta INMEL", "San Antonio de Prado INMEL",
    "Medellin CINCO", "Rionegro CINCO", "Bello DOMINION", "Copacabana DOMINION",
    "Medellin DOMINION",
    # R5
    "R5-Acacias TELCOS", "R5-Barrancabermeja TABASCO", "R5-Bucaramanga TABASCO",
    "R5-Bucaramanga TELCOS", "R5-Cucuta TELCOS", "R5-Duitama TABASCO",
    "R5-Floridablanca TELCOS", "R5-Fusagasuga TABASCO", "R5-Girardot TABASCO",
    "R5-Giron TABASCO", "R5-Los Patios TELCOS", "R5-Piedecuesta TABASCO",
    "R5-Sogamoso TABASCO", "R5-Tunja TABASCO", "R5-Villa del Rosario TELCOS",
    "R5-Villavicencio TELCOS", "R5-Yopal TELCOS",
    # R3
    "CALI NORTE CONECTAR", "CALI SUR CICSA", "Jamundi CICSA", "Yumbo CONECTAR",
    "Florencia CONECTAR", "Ibague CONECTAR", "Neiva CICSA", "Buga CONECTAR",
    "Candelaria CONECTAR", "Ipiales CICSA", "Palmira CONECTAR", "Pasto CICSA",
    "Popayan CICSA", "Puerto Tejada CICSA", "Santander de Quilichao CICSA",
    "Tulua CONECTAR"
]

FRANJAS_MAP = {
    "07-13":"AM","13-18":"PM","07-18":"ALL DAY","14-20":"PM","18:00-20:30":"PM",
    "07-10":"AM","01-06":"AM","06-10":"AM","07-09":"AM","08-10":"AM",
    "09-11":"AM","10-13":"AM","11-13":"AM","14-16":"PM","14-17":"PM",
    "16-18":"PM","17-19":"PM","18-22":"PM",
}

# ── CARGA ─────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def obtener_ultima_carga():
    try:
        conn = mysql.connector.connect(**DB)
        cur = conn.cursor()
        cur.execute("SELECT MAX(fecha_carga) FROM uso_cupos")
        r = cur.fetchone()[0]
        cur.close()
        conn.close()
        return r - timedelta(hours=5) if r else None
    except Exception:
        return None

@st.cache_data(ttl=300)
def cargar_datos():
    ...
@st.cache_data(ttl=300)
def cargar_datos():
    try:
        conn = mysql.connector.connect(**DB)
        df = pd.read_sql("""
            SELECT fecha, zona, franja_horaria, categoria,
                   quota_pct, used_quota_pct, status, close_time,
                   max_available, quota_mins, booked_activities, used
            FROM uso_cupos
            ORDER BY fecha DESC, zona, franja_horaria, categoria
        """, conn)
        cur = conn.cursor()
        cur.execute("SELECT MAX(fecha_carga) FROM uso_cupos")
        row = cur.fetchone()
        ultima_actualizacion = row[0] if row and row[0] else None
        cur.close()
        conn.close()
        return df, None, ultima_actualizacion
    except Exception as e:
        return pd.DataFrame(), str(e), None

def enriquecer(df):
    if df.empty:
        return df

    df = df.copy()
    df["zona"]           = df["zona"].str.strip()
    df["categoria"]      = df["categoria"].str.strip()
    df["franja_horaria"] = df["franja_horaria"].str.strip()

    min_d  = dict(zip(TRABAJOS["Trabajo"].str.strip(), TRABAJOS["Minutos"]))
    red_d  = dict(zip(TRABAJOS["Trabajo"].str.strip(), TRABAJOS["Red"]))
    act_d  = dict(zip(TRABAJOS["Trabajo"].str.strip(), TRABAJOS["Actividad"]))
    
    # Mapeo Nacional
    reg_d  = dict(zip(TABLA_FTTH["Categoria"].str.strip(), TABLA_FTTH["Regional"]))
    ali_d  = dict(zip(TABLA_FTTH["Categoria"].str.strip(), TABLA_FTTH["Aliado"]))
    ter_d  = dict(zip(TABLA_FTTH["Categoria"].str.strip(), TABLA_FTTH["Territorio"]))

    df["Min_Trabajo"]        = df["categoria"].map(min_d).fillna(0).astype(float)
    df["Red"]                = df["categoria"].map(red_d).fillna("HFC")
    df["Tipo_Orden"]         = df["categoria"].map(act_d).fillna("")
    
    df["Regional"]           = df["zona"].map(reg_d).fillna("")
    df["Aliado_Final"]       = df["zona"].map(ali_d).fillna("")
    df["Gerencia"]           = df["zona"].map(ter_d).fillna("")
    
    df["Meta_Modernizacion"] = np.where(df["zona"].isin(ZONAS_BROWNFIELD), "Si", "No")
    df["Ciudad_tiene_FTTH"]  = np.where(df["zona"].isin(CIUDADES_CON_FTTH), "Si", "No")
    df["Franja_H"]           = df["franja_horaria"].map(FRANJAS_MAP).fillna("")

    mask_hfc_invalido = (df["Red"] == "HFC") & (df["zona"].isin(ZONAS_SOLO_FTTH))
    df = df[~mask_hfc_invalido].copy()

    qm = pd.to_numeric(df["quota_mins"], errors="coerce")
    used_mins = pd.to_numeric(df["used"], errors="coerce").fillna(0)
    mt = df["Min_Trabajo"]
    df["Cupos_Abiertos"] = np.where((mt > 0) & qm.notna(), qm / mt, 0)
    df["Cupos_Usados"]   = pd.to_numeric(df["booked_activities"], errors="coerce").fillna(0)
    
    libres_calc = np.where((mt > 0) & qm.notna(), (qm - used_mins) / mt, 0)
    df["Cupos_Libres"]   = np.maximum(libres_calc, 0)
    
    uso_cap_calc = np.where(qm > 0, used_mins / qm, 0)
    df["Uso_Cap"] = np.maximum(np.round(uso_cap_calc, 2), 0)
    
    df["used_mins"] = used_mins
    df["quota_mins_num"] = qm
    df["fecha"]          = pd.to_datetime(df["fecha"])
    df = df[df["Regional"].astype(str).str.strip() != ""].copy()
    return df

# ── ESTILOS GLOBALES ──────────────────────────────────────────────────────────
st.markdown("""
<style>
html,body,[class*="css"]{font-family:'Segoe UI',Arial,sans-serif!important}
.stApp{background:#f4f6f9 !important;color:#1a1816 !important}
[data-testid="stAppViewContainer"]{background:#f4f6f9 !important}
[data-testid="block-container"]{background:#f4f6f9 !important;padding-top:1rem!important}
section[data-testid="stSidebar"]{background:#ffffff !important;border-right:1px solid #dde3ec !important}
section[data-testid="stSidebar"] *{color:#1a1816 !important;font-family:'Segoe UI',Arial,sans-serif!important}
.stTabs [data-baseweb="tab-list"]{background:#ffffff !important;border-bottom:2px solid #dde3ec !important}
.stTabs [data-baseweb="tab"]{color:#555 !important;font-family:'Segoe UI',Arial,sans-serif!important}
.stTabs [aria-selected="true"]{color:#c00 !important;border-bottom-color:#c00 !important;font-weight:600!important}
.page-header{margin-bottom:.5rem}
.page-sub{font-size:.78rem;color:#7a7670;margin-top:.1rem}
</style>
""", unsafe_allow_html=True)

# ── INICIALIZAR ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def obtener_ultima_carga():
    try:
        conn = mysql.connector.connect(**DB)
        cur = conn.cursor()
        cur.execute("SELECT MAX(fecha_carga) FROM uso_cupos")
        r = cur.fetchone()[0]
        cur.close(); conn.close()
        return r - timedelta(hours=5) if r else None
    except Exception:
        return None
@st.cache_data(ttl=300)
def cargar_y_enriquecer_datos():
    raw_d, err, ult_act = cargar_datos()
    if err:
        return pd.DataFrame(), raw_d, err, ult_act
    df_d = enriquecer(raw_d)
    return df_d, raw_d, None, ult_act

df, raw, error, ultima_actualizacion = cargar_y_enriquecer_datos()
if error:
    st.error(f"❌ Error BD: {error}")
    st.stop()
if df.empty:
    st.warning("⚠️ Sin datos en la BD. Dirígete a la pestaña 'Carga de Cuotas (ETL)' para subir tu primer archivo Excel.")

fechas_disp = sorted(df["fecha"].dt.date.unique(), reverse=True) if not df.empty else []

MESES_NUM = {1:"Enero",2:"Febrero",3:"Marzo",4:"Abril",5:"Mayo",6:"Junio",
             7:"Julio",8:"Agosto",9:"Septiembre",10:"Octubre",11:"Noviembre",12:"Diciembre"}

meses_anios_disp = sorted(
    df["fecha"].dt.to_period("M").unique(), reverse=True
) if not df.empty else []
meses_anios_str  = [str(m) for m in meses_anios_disp]

def fmt_mes(s):
    y,m = s.split("-")
    return f"{MESES_NUM.get(int(m),m)} {y}"

# ── SIDEBAR ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔴 CAPACIDADES")
    st.markdown("**Todas las Regionales · Claro**")
    st.markdown("---")

    st.markdown("**📆 Mes / Año**")
    mes_sel = st.multiselect("",
        options=meses_anios_str,
        default=[meses_anios_str[0]] if meses_anios_str else [],
        format_func=fmt_mes,
        placeholder="Todos los meses",
        label_visibility="collapsed")
    if not mes_sel and meses_anios_str:
        mes_sel = [meses_anios_str[0]]

    fechas_en_mes = sorted([
        f for f in fechas_disp
        if f.strftime("%Y-%m") in mes_sel
    ], reverse=True)

    hoy_cot = datetime.now(timezone(timedelta(hours=-5))).date()
    fechas_futuras = [f for f in fechas_en_mes if f >= hoy_cot]
    default_fechas = fechas_futuras if fechas_futuras else (fechas_en_mes[:1] if fechas_en_mes else [])

    st.markdown("**📅 Días**")
    fechas_sel = st.multiselect("",
        options=fechas_en_mes,
        default=default_fechas,
        format_func=lambda d: d.strftime("%d/%m/%Y (%a)"),
        placeholder="Todos los días del mes",
        label_visibility="collapsed")
    if not fechas_sel:
        fechas_sel = fechas_en_mes if fechas_en_mes else fechas_disp[:1]

    st.markdown("---")

    st.markdown("**🌐 Regional**")
    regional_sel = st.multiselect("Regional",
        options=sorted(df["Regional"].dropna().unique()) if not df.empty and "Regional" in df.columns else [],
        default=[], placeholder="Todas", label_visibility="collapsed")

    st.markdown("**📡 Red**")
    red_sel = st.multiselect("Red",
        options=["FTTH","HFC","FWA"],
        default=[],
        placeholder="Todas",
        label_visibility="collapsed")

    st.markdown("**🏙️ Ciudad**")
    ciudad_sel = st.multiselect("Ciudad",
        options=sorted(df["zona"].dropna().unique()) if not df.empty and "zona" in df.columns else [],
        default=[], placeholder="Todas", label_visibility="collapsed")

    st.markdown("**⏰ Franja**")
    franja_sel = st.multiselect("Franja",
        options=["AM","PM","ALL DAY"],
        default=[], placeholder="Todas", label_visibility="collapsed")

    st.markdown("**🤝 Aliado**")
    aliado_sel = st.multiselect("Aliado",
        options=sorted(df["Aliado_Final"].dropna().unique()) if not df.empty and "Aliado_Final" in df.columns else [],
        default=[], placeholder="Todos", label_visibility="collapsed")

    st.markdown("**🗺️ Territorio / Gerencia**")
    territorio_sel = st.multiselect("Territorio",
        options=sorted(df["Gerencia"].dropna().unique()) if not df.empty and "Gerencia" in df.columns else [],
        default=[], placeholder="Todos", label_visibility="collapsed")

    st.markdown("**⚠️ Uso Capacidad**")
    uso_menor_50 = st.checkbox("Mostrar solo ciudades < 50%", value=False)

    st.markdown("---")

    with st.expander("🔧 Diagnóstico BD", expanded=False):
        cols_ok = [c for c in raw.columns if raw[c].notna().sum()>0]
        st.caption(f"Registros: {len(raw):,}")
        st.caption(f"Columnas con datos: {', '.join(cols_ok)}")
        st.dataframe(raw[cols_ok].head(5), use_container_width=True)

# ── FILTRO GLOBAL ─────────────────────────────────────────────────────────────
if not df.empty:
    mask = df["fecha"].dt.date.isin(fechas_sel)
    if regional_sel:   mask &= df["Regional"].isin(regional_sel)
    if red_sel:        mask &= df["Red"].isin(red_sel)
    if ciudad_sel:     mask &= df["zona"].isin(ciudad_sel)
    if franja_sel:     mask &= df["Franja_H"].isin(franja_sel)
    if aliado_sel:     mask &= df["Aliado_Final"].isin(aliado_sel)
    if territorio_sel: mask &= df["Gerencia"].isin(territorio_sel)
    dff = df[mask].copy()
else:
    dff = df.copy()

# ── FILTROS POR PÁGINA (del PBIX) ────────────────────────────────────────────
FILTROS_PAGINA = {
    "Meta Modernización": {
        "Tipo_Orden":         ["Bronwfield"],
        "Meta_Modernizacion": ["Si"],
    },
    "Instalaciones FTTH": {
        "Tipo_Orden":        ["Instalaciones"],
        "Ciudad_tiene_FTTH": ["Si"],
        "Red":               ["FTTH"],
    },
    "Instalaciones HFC": {
        "Tipo_Orden":    ["Instalaciones", "Instalaciones Básica"],
        "Red":           ["HFC"],
        "_excluir_zona": ZONAS_SOLO_FTTH,
    },
    "Arreglos":       {"Tipo_Orden": ["Arreglos"]},
    "Posventas":      {"Tipo_Orden": ["Posventas"]},
    "General FTTH": {
        "Red":               ["FTTH"],
        "Ciudad_tiene_FTTH": ["Si"],
    },
    "Total Trabajos": {},
    "Pymes": {
        "categoria": ["Brownfield PYMES", "HFC  Arreglos Pymes",
                      "HFC  Instalacion Pymes", "HFC Postventa Pymes",
                      "HFC  Traslados pymes"],
    },
}

DIAS_ES  = {"Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
            "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado",
            "Sunday": "Domingo"}
MESES_ES = {"January": "enero", "February": "febrero", "March": "marzo",
            "April": "abril", "May": "mayo", "June": "junio", "July": "julio",
            "August": "agosto", "September": "septiembre", "October": "octubre",
            "November": "noviembre", "December": "diciembre"}

def fmt_fecha(d):
    return (f"{DIAS_ES.get(d.strftime('%A'), '')} {d.day:02d} de "
            f"{MESES_ES.get(d.strftime('%B'), '')} de {d.year}")

def uso_bg(u):
    if u >= 0.9:   return "#c6efce", "#276221"
    elif u >= 0.5: return "#ffeb9c", "#9c6500"
    else:          return "#ffc7ce", "#9c0006"

def uso_icon(u, txt_color=None):
    if u >= 0.9:   return '<span style="font-size:1.2rem;line-height:1;">✅</span>'
    elif u >= 0.5: return '<span style="font-size:1.2rem;line-height:1;">⚠️</span>'
    else:          return '<span style="font-size:1.2rem;line-height:1;">❌</span>'

def barra_uso(u, txt_color=None):
    pct = min(u * 100, 100)
    if u >= 0.9:   bar_color = "#70ad47"
    elif u >= 0.5: bar_color = "#ffc000"
    else:          bar_color = "#ff0000"
    bg, fg = uso_bg(u)
    if txt_color == "#ffffff":
        fg = "#ffffff"
    return (f'<div style="display:flex;align-items:center;gap:5px;justify-content:flex-end">'
            f'<div style="width:50px;height:10px;background:#e0e0e0;border-radius:3px;overflow:hidden;flex-shrink:0">'
            f'<div style="width:{pct:.0f}%;height:100%;background:{bar_color};border-radius:3px"></div></div>'
            f'<span style="font-weight:700;color:{fg};min-width:38px;text-align:right">{u:.0%}</span>'
            f'</div>')

# ── RENDER PÁGINA ────────────────────────────────────────────────────────────
def render_pagina(df_full, titulo, filtro_extra=None, aplicar_filtro_uso=False, aplicar_filtro_uso_95=False):
    d = df_full.copy()

    if filtro_extra:
        for col, val in filtro_extra.items():
            if col == "_excluir_zona":
                d = d[~d["zona"].isin(val)]
            elif isinstance(val, list):
                d = d[d[col].isin(val)]
            else:
                d = d[d[col] == val]

    if aplicar_filtro_uso or aplicar_filtro_uso_95:
        uso_ciudad = d.groupby("zona")[["used_mins", "quota_mins_num"]].sum()
        uso_ciudad["Uso"] = np.where(uso_ciudad["quota_mins_num"] > 0,
                                     uso_ciudad["used_mins"] / uso_ciudad["quota_mins_num"], 0)
        if aplicar_filtro_uso and aplicar_filtro_uso_95:
            ciudades_f = uso_ciudad[(uso_ciudad["Uso"] < 0.5) | (uso_ciudad["Uso"] >= 0.95)].index
        elif aplicar_filtro_uso:
            ciudades_f = uso_ciudad[uso_ciudad["Uso"] < 0.5].index
        else:
            ciudades_f = uso_ciudad[uso_ciudad["Uso"] >= 0.95].index
        d = d[d["zona"].isin(ciudades_f)]

    fechas = sorted(d["fecha"].dt.date.unique())

    if d.empty:
        st.info("Sin datos con los filtros aplicados.")
        return

    # AQUI ESTA LA DIFERENCIA PRINCIPAL DE NACIONAL: Agrupa por Regional -> Gerencia -> zona
    agg = d.groupby(["Regional", "Gerencia", "zona", "fecha"], as_index=False).agg(
        Ab=("Cupos_Abiertos", "sum"),
        Us=("Cupos_Usados",   "sum"),
        Li=("Cupos_Libres",   "sum"),
        UM=("used_mins",      "sum"),
        QM=("quota_mins_num", "sum"),
    )
    agg["fd"] = agg["fecha"].dt.date

    reg_agg = agg.groupby(["Regional", "fd"], as_index=False).agg(
        Ab=("Ab", "sum"), Us=("Us", "sum"), Li=("Li", "sum"),
        UM=("UM", "sum"), QM=("QM", "sum"))
    ger_agg = agg.groupby(["Regional", "Gerencia", "fd"], as_index=False).agg(
        Ab=("Ab", "sum"), Us=("Us", "sum"), Li=("Li", "sum"),
        UM=("UM", "sum"), QM=("QM", "sum"))
    ciu_agg = agg.groupby(["Regional", "Gerencia", "zona", "fd"], as_index=False).agg(
        Ab=("Ab", "sum"), Us=("Us", "sum"), Li=("Li", "sum"),
        UM=("UM", "sum"), QM=("QM", "sum"))
    tot_agg = agg.groupby("fd", as_index=False).agg(
        Ab=("Ab", "sum"), Us=("Us", "sum"), Li=("Li", "sum"),
        UM=("UM", "sum"), QM=("QM", "sum"))

    def get_met(src, keys):
        s = src
        for k, v in keys.items():
            s = s[s[k] == v]
        s_dict = s.set_index("fd").to_dict(orient="index") if not s.empty else {}
        
        data = []; ta = tu = tl = tum = tqm = 0
        for f in fechas:
            row = s_dict.get(f)
            if not row:
                data.append((0, 0, 0, 0))
            else:
                a = row["Ab"]; u = row["Us"]; l = row["Li"]
                um = row["UM"]; qm = row["QM"]
                uso = max(round(um / qm, 2) if qm > 0 else 0, 0)
                data.append((a, u, l, uso))
                ta += a; tu += u; tl += l; tum += um; tqm += qm
        uso_tot = max(round(tum / tqm, 2) if tqm > 0 else 0, 0)
        data.append((ta, tu, tl, uso_tot))
        return data

    def libres_icon(l, txt_color=None):
        li = int(round(l))
        if txt_color == "#ffffff":
            c_er, c_wa, c_ok = "#ff6b6b", "#ffd147", "#82e05a"
        else:
            c_er, c_wa, c_ok = "#cc0000", "#b8860b", "#276221"
            
        style = "font-size:1.3rem;line-height:0;vertical-align:middle;margin-right:2px;"
        
        if li <= 0:  return f'<span style="color:{c_er};{style}">&#9679;</span>'
        elif li < 4: return f'<span style="color:{c_wa};{style}">&#9679;</span>'
        else:        return f'<span style="color:{c_ok};{style}">&#9679;</span>'

    STICKY = "position:sticky;left:0;z-index:2;"

    def cells_html(mets, bold=False, is_total=False, txt="#1a1816", bg="transparent"):
        h = ""
        total_uso = mets[-1][3] if mets else 0
        for i,(a,u,l,uso) in enumerate(mets):
            last = (i == len(mets)-1)
            bl = "border-left:2px solid #a0c4e8;" if not last else "border-left:3px solid #6aab8e;"
            fw = "font-weight:700;" if bold or is_total else ""
            col = f"color:{txt};"
            bgc = f"background:{bg};" if bg != "transparent" else ""
            icon = libres_icon(l, txt)
            h += (
                f'<td style="{bl}{fw}{col}{bgc}text-align:right;padding:5px 10px;">{a:,.0f}</td>'
                f'<td style="{fw}{col}{bgc}text-align:right;padding:5px 10px;">{u:,.0f}</td>'
                f'<td style="{fw}{col}{bgc}text-align:right;padding:5px 10px;white-space:nowrap">{icon}&nbsp;{l:,.0f}</td>'
                f'<td style="{bgc}padding:5px 8px;min-width:130px">{barra_uso(uso, txt)}</td>'
            )
        h += f'<td style="{bgc}text-align:center;padding:5px 8px;border-left:1px solid #dde3ec">{uso_icon(total_uso, txt)}</td>'
        return h

    nf = len(fechas)

    # ── ENCABEZADOS ────────────────────────────────────────────────────────────
    h_titulo = (f'<th colspan="{1 + (nf+1)*4 + 1}" '
                f'style="background:#000000;color:#ffffff;text-align:center;'
                f'padding:10px;font-size:.95rem;font-weight:700;'
                f'letter-spacing:.05em;border:1px solid #333">'
                f'{titulo.upper()}</th>')

    h_sub = ('<th rowspan="2" style="background:#E0F7FF;color:#1a3a6f;text-align:center;'
             'vertical-align:bottom;min-width:240px;padding:7px 10px;'
             'border:1px solid #b8d4e8;font-size:.72rem;font-weight:700;'
             f'{STICKY}z-index:4;background:#E0F7FF;">'
             'Regional / Territorio<br>Ciudad</th>')
    for f in fechas:
        h_sub += (f'<th colspan="4" style="text-align:center;background:#E0F7FF;color:#1a3a6f;'
                  f'border-left:2px solid #a0c4e8;border-bottom:1px solid #b8d4e8;'
                  f'padding:7px 4px;font-size:.72rem;font-weight:700;white-space:nowrap">'
                  f'{fmt_fecha(f)}</th>')
    h_sub += ('<th colspan="4" style="text-align:center;background:#d4edda;color:#1a5030;'
              'border-left:3px solid #6aab8e;border-bottom:1px solid #a8d5be;'
              'padding:7px 4px;font-size:.72rem;font-weight:700">Total</th>'
              '<th rowspan="2" style="background:#E0F7FF;border:1px solid #b8d4e8;padding:7px 4px;'
              'font-size:.72rem;font-weight:700;color:#1a3a6f;text-align:center;vertical-align:bottom">Estado</th>')

    h_met = ""
    for _ in range(nf + 1):
        for m, w in [("Abiertos by Cuotas","110px"),("Usados Liberados","110px"),
                     ("Libres Reales","90px"),("Uso Capacidad","130px")]:
            bl = "border-left:2px solid #a0c4e8;" if _ < nf else "border-left:3px solid #6aab8e;"
            h_met += (f'<th style="min-width:{w};text-align:right;background:#E0F7FF;color:#1a3a6f;'
                      f'{bl}padding:5px 8px;font-size:.65rem;font-weight:700;white-space:nowrap;'
                      f'border-bottom:1px solid #b8d4e8">{m}</th>')

    # ── BODY: 3 niveles Regional → Territorio → Ciudad ────────────────────────
    # Regional: azul medio con texto blanco | Territorio: lila claro con texto oscuro
    REG_BG   = ["#155b9e", "#1867b3", "#1c73c8", "#1f7fdd", "#238cf5"]
    REG_TXT  = "#ffffff"
    GER_BG   = ["#eef1f8", "#e4e8f5", "#dae0f2", "#d0d8ef"]
    GER_TXT  = "#1a2a5e"

    regionales = sorted([r for r in d["Regional"].dropna().unique() if str(r).strip() != ""])
    body = ""

    for ri, reg in enumerate(regionales):
        rid = f"r{ri}"
        rc  = REG_BG[ri % len(REG_BG)]
        rm  = cells_html(get_met(reg_agg, {"Regional": reg}), bold=True, txt=REG_TXT, bg=rc)
        body += (
            f'<tr style="background:{rc};cursor:pointer;border-top:2px solid #071d33" '
            f'onclick="togR(\'{rid}\')">'
            f'<td style="{STICKY}background:{rc};font-weight:800;color:#ffffff;'
            f'padding:9px 12px;white-space:nowrap;font-size:.83rem;letter-spacing:.02em">'
            f'<span id="ic_{rid}" style="display:inline-block;width:18px;'
            f'font-size:.8rem;color:#7ec8f7;font-weight:900">+</span>'
            f'&nbsp;{reg}</td>{rm}</tr>'
        )

        gerencias = sorted(d[d["Regional"]==reg]["Gerencia"].dropna().unique())
        for gi, ger in enumerate(gerencias):
            gid = f"g{ri}_{gi}"
            gc  = GER_BG[gi % len(GER_BG)]
            gm  = cells_html(get_met(ger_agg, {"Regional": reg, "Gerencia": ger}), bold=True, txt=GER_TXT, bg=gc)
            body += (
                f'<tr class="sub_{rid}" style="display:none;background:{gc};'
                f'cursor:pointer;border-top:1px solid #c4cce8" '
                f'onclick="togG(\'{rid}\',\'{gid}\')">'  
                f'<td style="{STICKY}background:{gc};font-weight:700;color:{GER_TXT};'
                f'padding:7px 12px 7px 28px;white-space:nowrap;font-size:.78rem">'
                f'<span id="ic_{gid}" style="display:inline-block;width:16px;'
                f'font-size:.65rem;color:#4a5aaa;font-weight:900">+</span>'
                f'&nbsp;{ger}</td>{gm}</tr>'
            )

            ciudades = sorted(d[(d["Regional"]==reg) & (d["Gerencia"]==ger)]["zona"].dropna().unique())
            for ci, ciu in enumerate(ciudades):
                cm     = cells_html(get_met(ciu_agg, {"Regional": reg, "Gerencia": ger, "zona": ciu}),
                                    txt="#333344")
                bg_ciu = "#ffffff" if ci % 2 == 0 else "#f5f7fc"
                body += (
                    f'<tr class="sub_{rid} c_{gid}" '
                    f'style="display:none;background:{bg_ciu};border-top:1px solid #e8ecf5">'
                    f'<td style="{STICKY}background:{bg_ciu};padding:5px 12px 5px 48px;'
                    f'color:#555566;font-size:.74rem;white-space:nowrap">'
                    f'<span style="color:#aab0cc;margin-right:5px">&#8212;</span>{ciu}</td>{cm}</tr>'
                )

    # ── FILA TOTAL ─────────────────────────────────────────────────────────────
    tm=[]; ta=tu=tl=0; tum=tqm=0
    for f in fechas:
        fr = tot_agg[tot_agg["fd"]==f]
        if fr.empty: tm.append((0,0,0,0))
        else:
            a=fr["Ab"].sum(); u=fr["Us"].sum(); l=fr["Li"].sum()
            um=fr["UM"].sum(); qm=fr["QM"].sum()
            uso = round(u / a, 2) if a > 0 else 0
            if uso < 0: uso = 0
            tm.append((a,u,l,uso))
            ta+=a; tu+=u; tl+=l; tum+=um; tqm+=qm

    uso_tot = round(tu / ta, 2) if ta > 0 else 0
    if uso_tot < 0: uso_tot = 0
    tm.append((ta,tu,tl,uso_tot))

    body += (f'<tr style="background:#ddeeff;border-top:3px solid #5599cc">'
             f'<td style="{STICKY}background:#ddeeff;font-weight:800;'
             f'color:#002255;padding:8px 12px;font-size:.82rem;letter-spacing:.02em">TOTAL</td>'
             f'{cells_html(tm, bold=True, is_total=True, txt="#002255", bg="#ddeeff")}</tr>')

    n_tot  = len(gerencias) + sum(
        len(d[d["Gerencia"] == g]["zona"].dropna().unique()) for g in gerencias) + 3
    altura = max(180, min(n_tot * 34 + 120, 900))

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
*{{box-sizing:border-box}}
html, body{{height:100%; margin:0; padding:0; overflow:hidden; background:#fff; color:#1a1816; font-family:'Segoe UI',Arial,sans-serif; font-size:.78rem}}
.wrap{{height:100%; overflow:auto; position:relative}}
table{{border-collapse:collapse;width:100%;border:1px solid #ccd6e0}}
thead {{position: sticky; top: 0; z-index: 10;}}
thead th{{border:1px solid #b8d4e8}}
tbody td{{border-bottom:1px solid #e8e4f0;border-right:1px solid #f0eef8}}
tbody tr:hover td{{filter:brightness(.96)}}
</style></head><body>
<div class="wrap"><table>
<thead>
  <tr>{h_titulo}</tr>
  <tr>{h_sub}</tr>
  <tr>{h_met}</tr>
</thead>
<tbody>{body}</tbody>
</table></div>
<script>
function togR(rid) {{
  var ic   = document.getElementById('ic_' + rid);
  var open = ic.textContent.trim() === '+';
  var subs = document.querySelectorAll('.sub_' + rid);
  if (open) {{
    subs.forEach(function(r) {{
      var isCity = Array.from(r.classList).some(function(c){{ return c.startsWith('c_'); }});
      if (!isCity) r.style.display = 'table-row';
    }});
    ic.textContent = '-';
  }} else {{
    subs.forEach(function(r) {{ r.style.display = 'none'; }});
    var ri_num = rid.slice(1);
    document.querySelectorAll('[id^="ic_g' + ri_num + '_"]').forEach(function(e) {{
      e.textContent = '+';
    }});
    ic.textContent = '+';
  }}
}}
function togG(rid, gid) {{
  var ic   = document.getElementById('ic_' + gid);
  var open = ic.textContent.trim() === '+';
  document.querySelectorAll('.c_' + gid).forEach(function(r) {{
    r.style.display = open ? 'table-row' : 'none';
  }});
  ic.textContent = open ? '-' : '+';
}}
</script>
</body></html>"""

    components.html(html, height=altura, scrolling=True)


# ── REPORTING ─────────────────────────────────────────────────────────────────
def render_reporting(df_full):
    try:
        import plotly.graph_objects as go
    except ImportError:
        st.error("Plotly no está instalado. Ejecuta: pip install plotly")
        return

    st.markdown("""
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:1rem">
        <span style="font-size:1.6rem">📊</span>
        <div>
            <h2 style="margin:0;font-size:1.25rem;font-weight:800;color:#1a1816">
                Reporte de Tendencias — Día a Día
            </h2>
            <p style="margin:0;font-size:.8rem;color:#6c757d">
                Evolución de cupos abiertos, usados liberados y porcentaje de uso por fecha
            </p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if df_full.empty:
        st.info("Sin datos con los filtros aplicados.")
        return

    # ── Columna Segmento: Pymes o Residencial ─────────────────────────────────
    df_full = df_full.copy()
    df_full["Segmento"] = df_full["categoria"].apply(
        lambda c: "Pymes" if "pymes" in str(c).lower() else "Residencial"
    )

    # ── Agrupación Tipo de Orden ──────────────────────────────────────────────
    def agrupar_tipo_orden(val):
        if not isinstance(val, str): return val
        v_lower = val.lower()
        if "instalaciones" in v_lower: return "Instalaciones"
        if "traslado" in v_lower or "transfer" in v_lower: return "Traslados"
        return val
    
    df_full["Tipo_Orden"] = df_full["Tipo_Orden"].apply(agrupar_tipo_orden)

    # ── Filtro Segmento — siempre visible ────────────────────────────────────
    st.markdown("""
    <style>
    div[data-testid="stRadio"] > label {font-weight:700;font-size:.85rem;color:#333}
    div[data-testid="stRadio"] > div {gap:6px}
    div[data-testid="stRadio"] div[role="radio"] label {
        padding:5px 16px;border-radius:20px;border:1.5px solid #ccc;
        font-size:.82rem;cursor:pointer;background:#f8f9fa;transition:all .15s
    }
    div[data-testid="stRadio"] div[role="radio"][aria-checked="true"] label {
        background:#1a1816;color:#fff;border-color:#1a1816;font-weight:700
    }
    </style>
    """, unsafe_allow_html=True)

    seg_col, _ = st.columns([3, 7])
    with seg_col:
        rep_seg = st.radio(
            "Segmento:",
            options=["Todos", "Residencial", "Pymes"],
            index=0,
            horizontal=True,
            key="rep_seg",
        )

    with st.expander("🔧 Filtros adicionales del reporte", expanded=False):
        st.markdown("<hr style='margin:4px 0 10px;border:none;border-top:1px solid #e0e0e0'>",
                    unsafe_allow_html=True)

        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            reg_opts = sorted(df_full["Regional"].dropna().unique())
            rep_reg = st.multiselect("Regional", options=reg_opts, default=[],
                                     placeholder="Todas", key="rep_reg")
        with fc2:
            ger_opts = sorted(df_full["Gerencia"].dropna().unique())
            rep_ger = st.multiselect("Territorio", options=ger_opts, default=[],
                                     placeholder="Todos", key="rep_ger")
        with fc3:
            ciu_opts = sorted(df_full["zona"].dropna().unique())
            rep_ciu = st.multiselect("Ciudad", options=ciu_opts, default=[],
                                     placeholder="Todas", key="rep_ciu")
        fc4, fc5, fc6 = st.columns(3)
        with fc4:
            red_opts = sorted(df_full["Red"].dropna().unique())
            rep_red = st.multiselect("Red", options=red_opts, default=[],
                                     placeholder="Todas", key="rep_red")
        with fc5:
            tipo_opts = sorted(df_full["Tipo_Orden"].dropna().replace("", float("nan")).dropna().unique())
            rep_tipo = st.multiselect("Tipo de Orden", options=tipo_opts, default=[],
                                      placeholder="Todos", key="rep_tipo")
        with fc6:
            trab_opts = sorted(df_full["categoria"].dropna().unique())
            rep_trab = st.multiselect("Tipo de Trabajo", options=trab_opts, default=[],
                                      placeholder="Todos", key="rep_trab")



    d = df_full.copy()
    if rep_seg != "Todos": d = d[d["Segmento"] == rep_seg]
    if rep_reg:  d = d[d["Regional"].isin(rep_reg)]
    if rep_ger:  d = d[d["Gerencia"].isin(rep_ger)]
    if rep_ciu:  d = d[d["zona"].isin(rep_ciu)]
    if rep_red:  d = d[d["Red"].isin(rep_red)]
    if rep_tipo: d = d[d["Tipo_Orden"].isin(rep_tipo)]
    if rep_trab: d = d[d["categoria"].isin(rep_trab)]

    # ── Badge de segmento activo ───────────────────────────────────────────────
    seg_colors = {"Pymes": ("#1a3a7a", "#dce8ff"), "Residencial": ("#276221", "#dff5e0")}
    if rep_seg != "Todos":
        sc, sb = seg_colors.get(rep_seg, ("#333", "#f0f0f0"))
        icon_seg = "🏢" if rep_seg == "Pymes" else "🏠"
        st.markdown(
            f'<div style="display:inline-block;background:{sb};color:{sc};'
            f'border:1px solid {sc}44;border-radius:20px;padding:4px 14px;'
            f'font-size:.8rem;font-weight:700;margin-bottom:10px">'
            f'{icon_seg} Segmento: {rep_seg}</div>',
            unsafe_allow_html=True
        )

    if d.empty:
        st.info("Sin datos con los filtros del reporte.")
        return

    # ── Agregación diaria ─────────────────────────────────────────────────────
    d["fecha_d"] = d["fecha"].dt.date
    daily = d.groupby("fecha_d", as_index=False).agg(
        Abiertos=("Cupos_Abiertos", "sum"),
        Usados=("Cupos_Usados", "sum"),
        Libres=("Cupos_Libres", "sum"),
        used_mins=("used_mins", "sum"),
        quota_mins=("quota_mins_num", "sum"),
    )
    daily["Uso_Pct"] = daily.apply(
        lambda r: round(r["Usados"] / r["Abiertos"] * 100, 1) if r["Abiertos"] > 0 else 0, axis=1
    )
    daily = daily.sort_values("fecha_d")
    # ── Solo fecha, sin horas ──────────────────────────────────────────────────
    daily["fecha_str"] = [d.strftime("%d/%m/%Y") for d in daily["fecha_d"]]

    # ── KPIs resumen ──────────────────────────────────────────────────────────
    uso_prom = daily["Uso_Pct"].mean()
    uso_max  = daily["Uso_Pct"].max()
    uso_min  = daily["Uso_Pct"].min()

    def kpi_color(v):
        if v >= 90: return "#276221", "#c6efce"
        if v >= 50: return "#9c6500", "#ffeb9c"
        return "#9c0006", "#ffc7ce"

    fg, bg = kpi_color(uso_prom)
    k1, k2, k3, k4 = st.columns(4)
    kpi_style = ("border-radius:10px;padding:16px 20px;text-align:center;"
                 "box-shadow:0 2px 8px rgba(0,0,0,.08);margin-bottom:8px;")
    with k1:
        st.markdown(f"""
        <div style="{kpi_style}background:#eef4ff;border:1px solid #c0d4f5">
            <div style="font-size:.72rem;color:#5570a0;font-weight:600;text-transform:uppercase;letter-spacing:.06em">Días con datos</div>
            <div style="font-size:2rem;font-weight:800;color:#1a3a7a">{len(daily)}</div>
        </div>""", unsafe_allow_html=True)
    with k2:
        st.markdown(f"""
        <div style="{kpi_style}background:{bg};border:1px solid {fg}33">
            <div style="font-size:.72rem;color:{fg};font-weight:600;text-transform:uppercase;letter-spacing:.06em">Uso Prom. del período</div>
            <div style="font-size:2rem;font-weight:800;color:{fg}">{uso_prom:.1f}%</div>
        </div>""", unsafe_allow_html=True)
    fg2, bg2 = kpi_color(uso_max)
    with k3:
        st.markdown(f"""
        <div style="{kpi_style}background:{bg2};border:1px solid {fg2}33">
            <div style="font-size:.72rem;color:{fg2};font-weight:600;text-transform:uppercase;letter-spacing:.06em">Uso Máximo</div>
            <div style="font-size:2rem;font-weight:800;color:{fg2}">{uso_max:.1f}%</div>
        </div>""", unsafe_allow_html=True)
    fg3, bg3 = kpi_color(uso_min)
    with k4:
        st.markdown(f"""
        <div style="{kpi_style}background:{bg3};border:1px solid {fg3}33">
            <div style="font-size:.72rem;color:{fg3};font-weight:600;text-transform:uppercase;letter-spacing:.06em">Uso Mínimo</div>
            <div style="font-size:2rem;font-weight:800;color:{fg3}">{uso_min:.1f}%</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)

    # ── Gráfico 1: Cupos Abiertos, Usados Liberados y Libres — con etiquetas ──
    fig1 = go.Figure()

    fig1.add_trace(go.Scatter(
        x=daily["fecha_str"], y=daily["Abiertos"],
        name="Cupos Abiertos",
        mode="lines+markers+text",
        cliponaxis=False,
        line=dict(color="#1a6abf", width=2.5),
        marker=dict(size=8, color="#1a6abf", symbol="circle"),
        text=[f"{v:,.0f}" for v in daily["Abiertos"]],
        textposition="top center",
        textfont=dict(size=10, color="#1a6abf", family="Segoe UI"),
        fill="tozeroy",
        fillcolor="rgba(26,106,191,0.07)",
        hovertemplate="<b>%{x}</b><br>Abiertos: <b>%{y:,.0f}</b><extra></extra>",
    ))
    fig1.add_trace(go.Scatter(
        x=daily["fecha_str"], y=daily["Usados"],
        name="Usados Liberados",
        mode="lines+markers+text",
        cliponaxis=False,
        line=dict(color="#e07b00", width=2.5),
        marker=dict(size=8, color="#e07b00", symbol="diamond"),
        text=[f"{v:,.0f}" for v in daily["Usados"]],
        textposition="bottom center",
        textfont=dict(size=10, color="#e07b00", family="Segoe UI"),
        fill="tozeroy",
        fillcolor="rgba(224,123,0,0.07)",
        hovertemplate="<b>%{x}</b><br>Usados: <b>%{y:,.0f}</b><extra></extra>",
    ))
    fig1.add_trace(go.Scatter(
        x=daily["fecha_str"], y=daily["Libres"],
        name="Libres Reales",
        mode="lines+markers+text",
        cliponaxis=False,
        line=dict(color="#27a23b", width=2, dash="dot"),
        marker=dict(size=7, color="#27a23b", symbol="square"),
        text=[f"{v:,.0f}" for v in daily["Libres"]],
        textposition="top center",
        textfont=dict(size=10, color="#27a23b", family="Segoe UI"),
        hovertemplate="<b>%{x}</b><br>Libres: <b>%{y:,.0f}</b><extra></extra>",
    ))
    fig1.update_layout(
        title=dict(text="<b>Cupos Abiertos · Usados Liberados · Libres Reales</b>",
                   font=dict(size=14, color="#1a1816"), x=0.01),
        xaxis=dict(title="Fecha", tickangle=-30, showgrid=True,
                   gridcolor="#ebebeb", zeroline=False,
                   tickfont=dict(size=11)),
        yaxis=dict(title="Cupos", showgrid=True, gridcolor="#ebebeb",
                   zeroline=False, tickformat=","),
        legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center",
                    font=dict(size=12)),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        margin=dict(l=60, r=20, t=65, b=90),
        height=420,
        hovermode="x unified",
    )
    st.plotly_chart(fig1, use_container_width=True)

    # ── Gráfico 2: % de Uso — con etiquetas en puntos y zonas de color ────────
    fig2 = go.Figure()

    fig2.add_hrect(y0=0,   y1=50,  fillcolor="rgba(255,120,120,0.10)", line_width=0)
    fig2.add_hrect(y0=50,  y1=90,  fillcolor="rgba(255,235,0,0.10)",   line_width=0)
    fig2.add_hrect(y0=90,  y1=110, fillcolor="rgba(90,200,90,0.12)",   line_width=0)

    fig2.add_hline(y=50, line_dash="dash", line_color="#e09000",
                   annotation_text="50% umbral", annotation_position="right",
                   annotation_font=dict(size=10, color="#9c6500"))
    fig2.add_hline(y=90, line_dash="dash", line_color="#276221",
                   annotation_text="90% objetivo", annotation_position="right",
                   annotation_font=dict(size=10, color="#276221"))

    colors = []
    for v in daily["Uso_Pct"]:
        if v >= 90:   colors.append("#27a23b")
        elif v >= 50: colors.append("#e07b00")
        else:         colors.append("#cc1111")

    fig2.add_trace(go.Scatter(
        x=daily["fecha_str"], y=daily["Uso_Pct"],
        name="% Uso Capacidad",
        mode="lines+markers+text",
        cliponaxis=False,
        line=dict(color="#1a1816", width=2.5),
        marker=dict(size=11, color=colors, line=dict(color="#ffffff", width=1.5)),
        text=[f"{v:.1f}%" for v in daily["Uso_Pct"]],
        textposition="top center",
        textfont=dict(size=11, color="#1a1816", family="Segoe UI"),
        hovertemplate="<b>%{x}</b><br>Uso: <b>%{y:.1f}%</b><extra></extra>",
        fill="tozeroy",
        fillcolor="rgba(26,26,26,0.04)",
    ))

    fig2.add_hline(y=uso_prom, line_dash="dot", line_color="#1a6abf",
                   annotation_text=f"Promedio {uso_prom:.1f}%",
                   annotation_position="left",
                   annotation_font=dict(size=10, color="#1a6abf"))

    fig2.update_layout(
        title=dict(text="<b>% de Uso de Capacidad — Día a Día</b>",
                   font=dict(size=14, color="#1a1816"), x=0.01),
        xaxis=dict(title="Fecha", tickangle=-30, showgrid=True,
                   gridcolor="#ebebeb", zeroline=False,
                   tickfont=dict(size=11)),
        yaxis=dict(title="% Uso", showgrid=True, gridcolor="#ebebeb",
                   zeroline=False, range=[0, max(115, daily["Uso_Pct"].max() + 15)],
                   ticksuffix="%"),
        legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center",
                    font=dict(size=12)),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        margin=dict(l=60, r=90, t=65, b=90),
        height=420,
        hovermode="x unified",
    )
    st.plotly_chart(fig2, use_container_width=True)

    # ── Tabla resumen ─────────────────────────────────────────────────────────
    with st.expander("📋 Ver tabla de datos del reporte", expanded=False):
        def uso_icon_html(v):
            if v >= 90:   return "✅", "#c6efce", "#276221"
            elif v >= 50: return "⚠️", "#ffeb9c", "#9c6500"
            else:         return "❌", "#ffc7ce", "#9c0006"

        filas_html = ""
        for i, row in daily[["fecha_str","Abiertos","Usados","Libres","Uso_Pct"]].iterrows():
            icon, bg_uso, fg_uso = uso_icon_html(row["Uso_Pct"])
            bg_row = "#ffffff" if i % 2 == 0 else "#f8f9fc"
            filas_html += f"""
            <tr style="background:{bg_row}">
                <td style="padding:8px 14px;font-weight:600;color:#333;white-space:nowrap">{row['fecha_str']}</td>
                <td style="padding:8px 14px;text-align:right;color:#1a6abf;font-weight:600">{row['Abiertos']:,.0f}</td>
                <td style="padding:8px 14px;text-align:right;color:#e07b00;font-weight:600">{row['Usados']:,.0f}</td>
                <td style="padding:8px 14px;text-align:right;color:#27a23b;font-weight:600">{row['Libres']:,.0f}</td>
                <td style="padding:8px 14px;text-align:center;background:{bg_uso};font-weight:700;color:{fg_uso};font-size:.9rem;white-space:nowrap">
                    {icon}&nbsp;{row['Uso_Pct']:.1f}%
                </td>
            </tr>"""

        tabla_html = f"""
        <style>
        .rep-table{{border-collapse:collapse;width:100%;font-family:'Segoe UI',Arial,sans-serif;font-size:.82rem}}
        .rep-table thead th{{background:#1a1816;color:#ffffff;padding:9px 14px;text-align:center;font-weight:700;font-size:.78rem;text-transform:uppercase;letter-spacing:.05em}}
        .rep-table thead th:first-child{{text-align:left}}
        .rep-table thead th:nth-child(2),.rep-table thead th:nth-child(3),.rep-table thead th:nth-child(4){{text-align:right}}
        .rep-table tbody tr:hover td{{filter:brightness(.96)}}
        .rep-table td{{border-bottom:1px solid #e8ecf5;border-right:1px solid #f0f2f8}}
        </style>
        <table class="rep-table">
            <thead>
                <tr>
                    <th>Fecha</th>
                    <th style="text-align:right">Cupos Abiertos</th>
                    <th style="text-align:right">Usados Liberados</th>
                    <th style="text-align:right">Libres Reales</th>
                    <th>% Uso</th>
                </tr>
            </thead>
            <tbody>{filas_html}</tbody>
        </table>"""
        st.markdown(tabla_html, unsafe_allow_html=True)

    # ── Desglose por Regional ──────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**📍 Evolución por Regional**")
    regionales_disp = sorted(d["Regional"].dropna().unique())
    if regionales_disp:
        color_map = {
            "R1": "#e61c24", "R2": "#1a6abf", "R3": "#27a23b",
            "R4": "#9b1fcc", "R5": "#e07b00",
        }
        fig3 = go.Figure()
        for reg in regionales_disp:
            dr = d[d["Regional"] == reg].copy()
            dr["fecha_d"] = dr["fecha"].dt.date
            daily_r = dr.groupby("fecha_d", as_index=False).agg(
                used_mins=("used_mins", "sum"),
                quota_mins=("quota_mins_num", "sum"),
            )
            daily_r["Uso_Pct"] = daily_r.apply(
                lambda r: round(r["used_mins"] / r["quota_mins"] * 100, 1)
                if r["quota_mins"] > 0 else 0, axis=1
            )
            daily_r = daily_r.sort_values("fecha_d")
            # Solo fecha sin hora
            daily_r["fecha_str"] = [fd.strftime("%d/%m/%Y") for fd in daily_r["fecha_d"]]
            col = color_map.get(reg, "#888888")
            fig3.add_trace(go.Scatter(
                x=daily_r["fecha_str"], y=daily_r["Uso_Pct"],
                name=reg,
                mode="lines+markers+text",
                line=dict(color=col, width=2),
                marker=dict(size=7, color=col),
                text=[f"{v:.1f}%" for v in daily_r["Uso_Pct"]],
                textposition="top center",
                textfont=dict(size=10, color=col, family="Segoe UI"),
                hovertemplate=f"<b>{reg}</b> | %{{x}}<br>Uso: <b>%{{y:.1f}}%</b><extra></extra>",
            ))

        fig3.add_hline(y=50, line_dash="dash", line_color="#e09000", line_width=1)
        fig3.add_hline(y=90, line_dash="dash", line_color="#276221", line_width=1)
        fig3.update_layout(
            title=dict(text="<b>% de Uso por Regional — Día a Día</b>",
                       font=dict(size=14, color="#1a1816"), x=0.01),
            xaxis=dict(title="Fecha", tickangle=-30, showgrid=True,
                       gridcolor="#ebebeb", tickfont=dict(size=11)),
            yaxis=dict(title="% Uso", showgrid=True, gridcolor="#ebebeb",
                       range=[0, 120], ticksuffix="%"),
            legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center",
                        font=dict(size=12)),
            plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
            margin=dict(l=60, r=20, t=65, b=90),
            height=430,
            hovermode="x unified",
        )




# ── VISTAS ────────────────────────────────────────────────────────────────────
_ultima = obtener_ultima_carga()
if _ultima:
    _ahora = (datetime.now() - timedelta(hours=5)).strftime('%Y-%m-%d %H:%M')
    st.markdown(
        f"""<div style="background:#fff;border-left:5px solid #e30613;border-radius:10px;
        padding:16px 24px;margin-bottom:18px;box-shadow:0 2px 8px rgba(0,0,0,.06);
        display:flex;align-items:center;justify-content:space-between;">
          <div style="font-size:20px;font-weight:700;color:#1a1816;letter-spacing:.3px;">
            Uso de la Capacidad en Cupos
          </div>
          <div style="background:#f7f8fa;border-radius:10px;padding:10px 18px;
          font-size:12.5px;line-height:1.8;color:#5a5a5a;">
            ⏱️ <b style="color:#3c3c3c;">Actualizado:</b> <span style="color:#e30613;">{_ahora} (COT)</span><br>
            <b style="color:#3c3c3c;">Última carga:</b> <span style="color:#e30613;">{_ultima.strftime('%Y-%m-%d %H:%M')}</span>
          </div>
        </div>""",
        unsafe_allow_html=True
    )


tabs_keys = ["📊 Reporting"] + list(FILTROS_PAGINA.keys()) + ["Carga de Cuotas (ETL)"]

tabs = st.tabs(tabs_keys)

for tab, pag in zip(tabs, tabs_keys):
    with tab:
        if pag == "Carga de Cuotas (ETL)" or pag == "Carga de Datos":
            render_carga_datos()

        elif pag == "📊 Reporting":
            if dff.empty:
                st.info("Sin datos para los filtros seleccionados.")
            else:
                render_reporting(dff)

        else:
            if dff.empty:
                st.info("Sin datos para los filtros seleccionados.")
            else:
                flt = FILTROS_PAGINA[pag]
                st.markdown(f"<div class='page-header'><h2>{pag.upper()}</h2></div>", unsafe_allow_html=True)
                
                # Resumen de última carga
                if ultima_actualizacion:
                    ts = pd.to_datetime(ultima_actualizacion)
                    ts = ts - timedelta(hours=5)
                    ts_str = ts.strftime('%d/%m/%Y %I:%M %p')
                    st.markdown(f"<div class='page-sub'>Última actualización BD: {ts_str} (Hora Colombia)</div>", unsafe_allow_html=True)
                
                # ── Filtro dinámico solo para Total Trabajos ──
                if pag == "Total Trabajos":
                    cats_disponibles = sorted(df["categoria"].dropna().unique())
                    st.markdown(
                        '<p style="font-weight:600;font-size:.85rem;margin-bottom:4px">'
                        '🔧 Filtrar por Categoría / Trabajo</p>',
                        unsafe_allow_html=True
                    )
                    col1, col2 = st.columns([5, 1])
                    with col1:
                        cats_sel = st.multiselect(
                            "Categoría",
                            options=cats_disponibles,
                            default=[],
                            placeholder=f"Todas las categorías ({len(cats_disponibles)} disponibles)",
                            label_visibility="collapsed",
                            key=f"cats_total_trabajos_{pag}"
                        )
                    with col2:
                        st.markdown("<br>", unsafe_allow_html=True)
                        if st.button("🗑️ Limpiar", key=f"limpiar_cats_{pag}"):
                            cats_sel = []

                    if cats_sel:
                        st.caption(f"Mostrando {len(cats_sel)} de {len(cats_disponibles)} categorías")
                        dff_pag = dff[dff["categoria"].isin(cats_sel)].copy()
                    else:
                        st.caption(f"Mostrando todas las categorías ({len(cats_disponibles)})")
                        dff_pag = dff.copy()
                else:
                    dff_pag = dff.copy()

                # Botones de filtro rápido de uso
                colA, colB, _ = st.columns([2,2,6])
                key_base = f"btn_{pag.replace(' ', '_')}"
                if key_base + "_u50" not in st.session_state:
                    st.session_state[key_base + "_u50"] = False
                if key_base + "_u95" not in st.session_state:
                    st.session_state[key_base + "_u95"] = False

                def toggle_u50(k=key_base):
                    st.session_state[k + "_u50"] = not st.session_state[k + "_u50"]
                def toggle_u95(k=key_base):
                    st.session_state[k + "_u95"] = not st.session_state[k + "_u95"]

                btn_u50_label = "❌ Quitar filtro < 50%" if st.session_state[key_base + "_u50"] else "⚠️ Filtrar Uso < 50%"
                btn_u95_label = "❌ Quitar filtro ≥ 95%" if st.session_state[key_base + "_u95"] else "✅ Filtrar Uso ≥ 95%"

                colA.button(btn_u50_label, key=key_base + "_b1", on_click=toggle_u50)
                colB.button(btn_u95_label, key=key_base + "_b2", on_click=toggle_u95)

                filt_50 = uso_menor_50 or st.session_state[key_base + "_u50"]
                filt_95 = st.session_state[key_base + "_u95"]

                render_pagina(dff_pag, pag, filtro_extra=flt, aplicar_filtro_uso=filt_50, aplicar_filtro_uso_95=filt_95)
