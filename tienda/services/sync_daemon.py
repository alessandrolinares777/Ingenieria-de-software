
"""Sincronización periódica de precios."""

import logging
import math

from database.db import transaccion
from services.auth_service import requiere_rol


class SyncError(Exception):
    """Error durante la sincronización de precios."""


def sincronizar_precios(conn, usuario, lista_precios):
    """
    Actualiza los precios recibidos desde el servidor central.

    Retorna la cantidad de productos actualizados.
    """

    requiere_rol(usuario, "administrador")

    if not isinstance(lista_precios, list) or not lista_precios:
        raise SyncError("No se recibieron precios para sincronizar.")

    codigos = set()
    precios_validados = []

    # Validar todos los datos antes de modificar la base de datos.
    for numero, elemento in enumerate(lista_precios, start=1):

        if not isinstance(elemento, dict):
            raise SyncError(
                f"Registro inválido en la posición {numero}."
            )

        if "codigo_barras" not in elemento or "precio" not in elemento:
            raise SyncError(
                f"Faltan datos en el registro {numero}."
            )

        codigo = str(elemento["codigo_barras"] or "").strip()

        if not codigo:
            raise SyncError(
                f"El código de barras del registro {numero} está vacío."
            )

        try:
            precio = float(elemento["precio"])
        except (ValueError, TypeError, OverflowError):
            raise SyncError(
                f"El precio del producto {codigo} no es válido."
            )

        if not math.isfinite(precio) or precio < 0:
            raise SyncError(
                f"El precio del producto {codigo} es inválido."
            )

        if codigo in codigos:
            raise SyncError(
                f"El código {codigo} aparece repetido en la sincronización."
            )

        codigos.add(codigo)
        precios_validados.append((codigo, precio))

    actualizados = 0

    # Todos los cambios se realizan en una sola transacción.
    with transaccion(conn):

        for codigo, precio in precios_validados:

            resultado = conn.execute(
                """
                UPDATE productos
                SET precio = ?
                WHERE codigo_barras = ? AND activo = 1
                """,
                (precio, codigo)
            )

            if resultado.rowcount == 0:
                raise SyncError(
                    f"No existe un producto activo con código {codigo}."
                )

            actualizados += 1

    return actualizados


def iniciar_sincronizacion(obtener_precios, obtener_conexion,
                           obtener_usuario):
    """
    Programa la sincronización automática cada 5 minutos.

    obtener_precios: consulta el servidor central.
    obtener_conexion: devuelve una conexión SQLite nueva.
    obtener_usuario: devuelve un usuario administrador.
    """

    from apscheduler.schedulers.background import BackgroundScheduler

    def tarea():
        conn = None

        try:
            conn = obtener_conexion()

            precios = obtener_precios()
            usuario = obtener_usuario()

            cantidad = sincronizar_precios(
                conn,
                usuario,
                precios
            )

            logging.info(
                "Sincronización completada. Productos actualizados: %s",
                cantidad
            )

        except Exception:
            logging.exception(
                "Ocurrió un error durante la sincronización de precios."
            )

        finally:
            if conn is not None:
                conn.close()

    scheduler = BackgroundScheduler()

    scheduler.add_job(
        tarea,
        trigger="interval",
        minutes=5,
        id="sincronizacion_precios",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )

    scheduler.start()

    return scheduler
