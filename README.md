# 📊 Uso de Capacidad — OFSC Claro Región Occidente

Sistema para gestionar y visualizar el uso de capacidad/cuotas del sistema OFSC (Oracle Field Service Cloud) de Claro.

Compuesto por dos módulos:

| Módulo | Puerto | Descripción |
|--------|--------|-------------|
| **ETL Web** | `:8090` | Carga de archivos Excel de cuotas → MySQL |
| **Dashboard Streamlit** | `:8501` | Visualización interactiva de uso de capacidad |

---

## Arquitectura

```
[Excel OFSC] → [ETL :8090] → [MySQL :3309] → [Streamlit :8501]
```

- **Flask** (Python 3.11) — Servidor ETL con UI de carga
- **Streamlit** — Dashboard interactivo
- **MySQL 8.0** — Base de datos central
- **Docker Compose** — Orquestación de todos los servicios

---

## Estructura del proyecto

```
Uso_Capacidad/
├── docker-compose.yml          # MySQL + ETL + Streamlit
├── init.sql                    # Schema inicial de la BD
├── servidor/                   # Módulo ETL (puerto 8090)
│   ├── app.py                  # Flask app (API + UI)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── templates/
│       └── index.html          # Panel de administración
└── streamlit/                  # Dashboard (puerto 8501)
    ├── app.py                  # Streamlit app (602 líneas)
    ├── Dockerfile
    └── requirements.txt
```

---

## Instalación y despliegue

```bash
# Clonar repositorio
git clone git@github.com:jomalogi/Uso_Capacidad.git
cd Uso_Capacidad

# Levantar todos los servicios
docker-compose build
docker-compose up -d

# Ver logs
docker-compose logs -f
```

### URLs una vez desplegado:

| Servicio | URL |
|----------|-----|
| ETL (carga Excel) | `http://TU_IP:8090` |
| Dashboard | `http://TU_IP:8501` |
| MySQL | `TU_IP:3309` |

---

## Módulo 1: ETL — Carga de Cuotas (`:8090`)

### ¿Qué hace?
1. El usuario sube un archivo Excel (.xlsx) exportado desde OFSC
2. La app parsea las hojas y extrae cuotas por zona/franja/categoría
3. Los datos se insertan en MySQL (tabla `uso_cupos`) con manejo de duplicados
4. Muestra estadísticas: registros insertados, actualizados, errores

### Endpoints:

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/` | GET | UI de carga de Excel |
| `/upload` | POST | Subir y procesar archivo Excel |
| `/fechas` | GET | Fechas cargadas con estadísticas |

---

## Módulo 2: Dashboard Streamlit (`:8501`)

### ¿Qué muestra?
- Uso de capacidad por zona, franja horaria y tipo de trabajo
- Distribución por red (FTTH, HFC, FWA)
- Clasificación de actividades (Instalaciones, Arreglos, Traslados, etc.)
- Filtros interactivos por fecha, zona y categoría
- Tabla de ~38 tipos de trabajo con minutos estimados
- Zonas FTTH clasificadas (CALI, VACANA, TOLHUCA, etc.)

---

## Base de datos

- **Puerto:** 3309
- **Base de datos:** ofsc_cupos
- **Tabla principal:** `uso_cupos`

### Campos principales:

| Campo | Tipo | Descripción |
|-------|------|-------------|
| fecha | DATE | Fecha de la cuota |
| zona | VARCHAR | Zona/bucket de OFSC |
| franja_horaria | VARCHAR | Timeslot (ej: 08:00-10:00) |
| categoria | VARCHAR | Tipo de trabajo |
| quota_pct | DECIMAL | Porcentaje de cuota |
| used_quota_pct | DECIMAL | Cuota usada |
| quota_mins | DECIMAL | Minutos de cuota |
| booked_activities | DECIMAL | Actividades agendadas |
| used | DECIMAL | Cuota utilizada |

---

## Mantenimiento

```bash
# Ver logs de un servicio específico
docker logs ofsc_web -f        # ETL
docker logs streamlit_ofsc -f  # Dashboard

# Reiniciar todo
docker-compose restart

# Reconstruir tras cambios
docker-compose down && docker-compose build && docker-compose up -d
```
