"""Sincronización periódica de precios."""

from database.db import transaccion
from services.auth_service import requiere_rol


class SyncError(Exception):
    """Error durante la sincronización de precios."""


def sincronizar_precios(conn, usuario, lista_precios):
    """
    Actualiza los precios recibidos desde el servidor central.

    lista_precios:
    [
        {"codigo_barras": "750100", "precio": 25.50},
        {"codigo_barras": "750200", "precio": 18.00}
    ]

    Retorna la cantidad de productos actualizados.
    """
    requiere_rol(usuario, "administrador")

    if not lista_precios:
        raise SyncError("No se recibieron precios para sincronizar.")

    codigos = set()

    for elemento in lista_precios:
        codigo = str(elemento["codigo_barras"]).strip()
        precio = float(elemento["precio"])

        if not codigo:
            raise SyncError("Se recibió un código de barras vacío.")

        if precio < 0:
            raise SyncError(
                f"El precio del producto {codigo} es inválido."
            )

        if codigo in codigos:
            raise SyncError(
                f"El código {codigo} aparece repetido en la sincronización."
            )

        codigos.add(codigo)

    actualizados = 0

    with transaccion(conn):
        for elemento in lista_precios:
            codigo = str(elemento["codigo_barras"]).strip()
            precio = float(elemento["precio"])

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
    Programa la sincronización automática cada hora.

    obtener_precios: función que consulta el servidor central.
    obtener_conexion: función que devuelve una conexión SQLite nueva.
    obtener_usuario: función que devuelve un usuario administrador.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    def tarea():
        conn = obtener_conexion()

        try:
            precios = obtener_precios()
            usuario = obtener_usuario()
            sincronizar_precios(conn, usuario, precios)
        finally:
            conn.close()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        tarea,
        trigger="interval",
        hours=1,
        id="sincronizacion_precios",
        replace_existing=True
    )

    scheduler.start()
    return scheduler
