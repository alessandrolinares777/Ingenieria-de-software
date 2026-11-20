
"""Generación y programación de respaldos de la base de datos."""

import os
import sqlite3
import logging

from contextlib import closing

from datetime import datetime


class BackupError(Exception):
    """Error durante la generación del respaldo."""



def generar_respaldo(ruta_bd="tienda.db", carpeta_destino="backups"):
    """Genera una copia de seguridad de la base de datos."""

    if not os.path.isfile(ruta_bd):
        raise BackupError(f"No existe la base de datos: {ruta_bd}")

    os.makedirs(carpeta_destino, exist_ok=True)

    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_respaldo = f"respaldo_{fecha}.db"
    ruta_respaldo = os.path.join(carpeta_destino, nombre_respaldo)

    origen = None
    destino = None

    try:
        origen = sqlite3.connect(ruta_bd)
        destino = sqlite3.connect(ruta_respaldo)

        # Copiar la base de datos
        origen.backup(destino)

        # Verificar la integridad antes de cerrar la conexión
        resultado = destino.execute(
            "PRAGMA integrity_check"
        ).fetchone()

        if resultado is None or resultado[0] != "ok":
            raise sqlite3.DatabaseError(
                "El respaldo no superó la verificación de integridad."
            )

        return ruta_respaldo

    except sqlite3.Error as e:
        if os.path.exists(ruta_respaldo):
            os.remove(ruta_respaldo)

        raise BackupError(
            f"No se pudo generar el respaldo: {e}"
        ) from e

    finally:
        if destino is not None:
            destino.close()

        if origen is not None:
            origen.close()
            

def iniciar_respaldos_diarios(ruta_bd="tienda.db",
                              carpeta_destino="backups",
                              hora=23, minuto=59):
    """
    Programa un respaldo automático todos los días.
    Por defecto se ejecuta a las 23:59.
    """

    from apscheduler.schedulers.background import BackgroundScheduler

    if not isinstance(hora, int) or not 0 <= hora <= 23:
        raise BackupError("La hora debe estar entre 0 y 23.")

    if not isinstance(minuto, int) or not 0 <= minuto <= 59:
        raise BackupError("Los minutos deben estar entre 0 y 59.")

    def tarea():
        try:
            ruta = generar_respaldo(ruta_bd, carpeta_destino)

            logging.info(
                "Respaldo generado correctamente: %s",
                ruta
            )

        except BackupError:
            logging.exception(
                "Ocurrió un error al generar el respaldo diario."
            )

    scheduler = BackgroundScheduler()

    scheduler.add_job(
        tarea,
        trigger="cron",
        hour=hora,
        minute=minuto,
        id="respaldo_diario",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )

    scheduler.start()

    return scheduler

