"""Validación de códigos y carga masiva de productos."""

import csv

from database.db import transaccion
from services.auth_service import requiere_rol


class ProductoError(Exception):
    """Error relacionado con los productos."""


def validar_codigo_unico(conn, codigo_barras):
    """
    Comprueba que el código de barras no esté registrado.
    Retorna True si está disponible y False si ya existe.
    """
    codigo_barras = str(codigo_barras).strip()

    if not codigo_barras:
        raise ProductoError("El código de barras no puede estar vacío.")

    resultado = conn.execute(
        "SELECT id FROM productos WHERE codigo_barras = ?",
        (codigo_barras,)
    ).fetchone()

    return resultado is None


def cargar_inventario_masivo(conn, usuario, ruta_csv):
    """
    Importa productos desde un archivo CSV.
    Solo puede ejecutarlo el administrador o el encargado de inventario.

    Columnas requeridas:
    codigo_barras,nombre,categoria,precio,stock,stock_minimo
    """
    requiere_rol(usuario, "administrador", "encargado_inventario")

    productos = []

    try:
        with open(ruta_csv, "r", encoding="utf-8-sig", newline="") as archivo:
            lector = csv.DictReader(archivo)

            columnas = {
                "codigo_barras", "nombre", "categoria",
                "precio", "stock", "stock_minimo"
            }

            if not lector.fieldnames or not columnas.issubset(lector.fieldnames):
                raise ProductoError("El CSV no contiene todas las columnas requeridas.")

            for numero, fila in enumerate(lector, start=2):
                codigo = fila["codigo_barras"].strip()
                nombre = fila["nombre"].strip()
                categoria = fila["categoria"].strip()

                if not codigo or not nombre:
                    raise ProductoError(
                        f"Falta el código o nombre del producto en la fila {numero}."
                    )

                try:
                    precio = float(fila["precio"])
                    stock = int(fila["stock"])
                    stock_minimo = int(fila["stock_minimo"])
                except (ValueError, TypeError):
                    raise ProductoError(
                        f"Precio o existencias inválidas en la fila {numero}."
                    )

                if precio < 0 or stock < 0 or stock_minimo < 0:
                    raise ProductoError(
                        f"Hay valores negativos en la fila {numero}."
                    )

                productos.append(
                    (codigo, nombre, categoria, precio, stock, stock_minimo)
                )

    except FileNotFoundError:
        raise ProductoError("No se encontró el archivo CSV.")

    if not productos:
        raise ProductoError("El archivo no contiene productos.")

    codigos = [p[0] for p in productos]

    if len(codigos) != len(set(codigos)):
        raise ProductoError("El archivo contiene códigos de barras repetidos.")

    insertados = 0

    with transaccion(conn):
        for codigo, nombre, categoria, precio, stock, stock_minimo in productos:

            if not validar_codigo_unico(conn, codigo):
                raise ProductoError(
                    f"El código de barras {codigo} ya está registrado."
                )

            conn.execute(
                """
                INSERT INTO productos
                (codigo_barras, nombre, categoria, precio, stock, stock_minimo)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (codigo, nombre, categoria, precio, stock, stock_minimo)
            )

            insertados += 1

    return insertados
