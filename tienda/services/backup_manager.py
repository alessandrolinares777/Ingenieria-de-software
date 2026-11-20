
"""Generación y programación de respaldos de la base de datos."""

import os
import sqlite3
import logging

from datetime import datetime


class BackupError(Exception):
    """Error durante la generación del respaldo."""


def generar_respaldo(ruta_bd="tienda.db", carpeta_destino="backups"):
    """
    Genera una copia de seguridad de la base de datos.

    El archivo se identifica con la fecha y hora de creación.
    Retorna la ruta del respaldo generado.
    """

    if not os.path.isfile(ruta_bd):
        raise BackupError("No se encontró la base de datos.")

    try:
        os.makedirs(carpeta_destino, exist_ok=True)
    except OSError as error:
        raise BackupError(
            f"No se pudo crear la carpeta de respaldos: {error}"
        )

    fecha = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nombre_archivo = f"respaldo_{fecha}.db"
    ruta_respaldo = os.path.join(carpeta_destino, nombre_archivo)

    try:
        with sqlite3.connect(ruta_bd) as origen:
            with sqlite3.connect(ruta_respaldo) as destino:
                origen.backup(destino)

                resultado = destino.execute(
                    "PRAGMA integrity_check"
                ).fetchone()

                if not resultado or resultado[0] != "ok":
                    raise BackupError(
                        "El respaldo generado no superó la verificación."
                    )

        return ruta_respaldo

    except (sqlite3.Error, OSError, BackupError) as error:

        if os.path.exists(ruta_respaldo):
            try:
                os.remove(ruta_respaldo)
            except OSError:
                logging.exception(
                    "No se pudo eliminar el respaldo incompleto."
                )

        if isinstance(error, BackupError):
            raise

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

