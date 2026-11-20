
"""Gestión y carga masiva del inventario."""

import csv
import math

from database.db import ahora, transaccion
from services.auth_service import requiere_rol
from services.productos_service import validar_codigo_unico


class InventarioError(Exception):
    """Error relacionado con el inventario."""


def cargar_inventario_masivo(conn, usuario, ruta_archivo, tamano_bloque=100):
    """
    Importa productos desde un archivo CSV en bloques pequeños.

    Columnas requeridas:
    codigo_barras,nombre,categoria,precio,stock,stock_minimo

    Retorna la cantidad de productos importados.

    Si algún registro falla, se revierte toda la importación.
    """

    requiere_rol(usuario, "administrador", "encargado_inventario")

    if not isinstance(tamano_bloque, int) or tamano_bloque <= 0:
        raise InventarioError("El tamaño del bloque debe ser positivo.")

    columnas_requeridas = {
        "codigo_barras",
        "nombre",
        "categoria",
        "precio",
        "stock",
        "stock_minimo"
    }

    importados = 0
    bloque = []
    codigos_archivo = set()

    try:
        archivo = open(
            ruta_archivo,
            "r",
            encoding="utf-8-sig",
            newline=""
        )
    except (FileNotFoundError, OSError):
        raise InventarioError("No se pudo abrir el archivo CSV.")

    with archivo:
        lector = csv.DictReader(archivo)

        if not lector.fieldnames:
            raise InventarioError("El archivo CSV está vacío.")

        if len(lector.fieldnames) != len(set(lector.fieldnames)):
            raise InventarioError("El archivo contiene columnas duplicadas.")

        if not columnas_requeridas.issubset(set(lector.fieldnames)):
            raise InventarioError(
                "El archivo no contiene todas las columnas requeridas."
            )

        def procesar_bloque(registros):
            nonlocal importados

            for producto in registros:
                codigo, nombre, categoria, precio, stock, minimo = producto

                if not validar_codigo_unico(conn, codigo):
                    raise InventarioError(
                        f"El código {codigo} ya está registrado."
                    )

                cursor = conn.execute(
                    """
                    INSERT INTO productos
                    (codigo_barras, nombre, categoria, precio, stock, stock_minimo)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (codigo, nombre, categoria, precio, stock, minimo)
                )

                producto_id = cursor.lastrowid

                if stock > 0:
                    conn.execute(
                        """
                        INSERT INTO movimientos_inventario
                        (producto_id, tipo, cantidad_anterior, cantidad,
                         cantidad_nueva, usuario_id, fecha_hora, referencia)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            producto_id,
                            "entrada",
                            0,
                            stock,
                            stock,
                            usuario["id"],
                            ahora(),
                            "Carga masiva"
                        )
                    )

            importados += len(registros)

        # Una sola transacción para toda la importación.
        # Si algún registro falla, se revierte todo.
        with transaccion(conn):

            for numero, fila in enumerate(lector, start=2):

                if None in fila:
                    raise InventarioError(
                        f"La fila {numero} contiene columnas adicionales."
                    )

                codigo = (fila.get("codigo_barras") or "").strip()
                nombre = (fila.get("nombre") or "").strip()
                categoria = (fila.get("categoria") or "").strip()

                if not codigo or not nombre:
                    raise InventarioError(
                        f"Código o nombre vacío en la fila {numero}."
                    )

                if codigo in codigos_archivo:
                    raise InventarioError(
                        f"Código duplicado en el archivo: {codigo}."
                    )

                codigos_archivo.add(codigo)

                try:
                    precio = float(fila["precio"])
                    stock = int(fila["stock"])
                    minimo = int(fila["stock_minimo"])

                except (ValueError, TypeError):
                    raise InventarioError(
                        f"Valores numéricos inválidos en la fila {numero}."
                    )

                if not math.isfinite(precio):
                    raise InventarioError(
                        f"El precio no es válido en la fila {numero}."
                    )

                if precio < 0 or stock < 0 or minimo < 0:
                    raise InventarioError(
                        f"Valores negativos en la fila {numero}."
                    )

                bloque.append(
                    (codigo, nombre, categoria, precio, stock, minimo)
                )

                if len(bloque) >= tamano_bloque:
                    procesar_bloque(bloque)
                    bloque = []

            if bloque:
                procesar_bloque(bloque)

    return importados
