CREATE DATABASE IF NOT EXISTS ofsc_cupos
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE ofsc_cupos;

CREATE TABLE IF NOT EXISTS uso_cupos (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    fecha               DATE NOT NULL,
    zona                VARCHAR(100),
    franja_horaria      VARCHAR(20),
    categoria           VARCHAR(100),
    quota_pct           DECIMAL(10,4),
    min_quota           DECIMAL(10,4),
    used_quota_pct      DECIMAL(10,4),
    weight              DECIMAL(10,4),
    estimated_quota_pct DECIMAL(10,4),
    stop_booking_pct    DECIMAL(10,4),
    status              VARCHAR(20),
    close_time          DATETIME,
    max_available       DECIMAL(10,4),
    other_activities    DECIMAL(10,4),
    quota_mins          DECIMAL(10,2),
    plan                DECIMAL(10,4),
    booked_activities   DECIMAL(10,4),
    used                DECIMAL(10,4),
    fecha_carga         DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_registro (fecha, zona, franja_horaria, categoria)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
