"""Generación y programación de respaldos de la base de datos."""

import os
import sqlite3

from datetime import datetime


class BackupError(Exception):
    """Error durante la generación del respaldo."""


def generar_respaldo(ruta_bd="tienda.db", carpeta_destino="backups"):
    """
    Genera una copia de seguridad de la base de datos.

    El archivo se identifica con la fecha y hora de creación.
    Retorna la ruta del respaldo generado.
    """
    if not os.path.exists(ruta_bd):
        raise BackupError("No se encontró la base de datos.")

    os.makedirs(carpeta_destino, exist_ok=True)

    fecha = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nombre_archivo = f"respaldo_{fecha}.db"
    ruta_respaldo = os.path.join(carpeta_destino, nombre_archivo)

    try:
        with sqlite3.connect(ruta_bd) as origen:
            with sqlite3.connect(ruta_respaldo) as destino:
                origen.backup(destino)

        return ruta_respaldo

    except sqlite3.Error as error:
        if os.path.exists(ruta_respaldo):
            os.remove(ruta_respaldo)

        raise BackupError(
            f"No se pudo generar el respaldo: {error}"
        )


def iniciar_respaldos_diarios(ruta_bd="tienda.db",
                              carpeta_destino="backups",
                              hora=23, minuto=59):
    """
    Programa un respaldo automático todos los días.
    Por defecto se ejecuta a las 23:59.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()

    scheduler.add_job(
        generar_respaldo,
        trigger="cron",
        hour=hora,
        minute=minuto,
        args=[ruta_bd, carpeta_destino],
        id="respaldo_diario",
        replace_existing=True
    )

    scheduler.start()
    return scheduler
