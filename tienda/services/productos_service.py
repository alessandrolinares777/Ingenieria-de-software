
"""Gestión de productos y validación de códigos de barras."""

from database.db import transaccion
from services.auth_service import requiere_rol


class ProductoError(Exception):
    """Error relacionado con la gestión de productos."""


def validar_codigo_unico(conn, codigo_barras, producto_id=None):
    """
    Comprueba que el código de barras no esté registrado.

    producto_id permite excluir el producto actual
    cuando se está modificando su información.

    Retorna True si el código está disponible.
    """

    if codigo_barras is None:
        raise ProductoError("El código de barras es obligatorio.")

    codigo_barras = str(codigo_barras).strip()

    if not codigo_barras:
        raise ProductoError("El código de barras no puede estar vacío.")

    consulta = """
        SELECT id
        FROM productos
        WHERE codigo_barras = ?
    """

    parametros = [codigo_barras]

    if producto_id is not None:
        consulta += " AND id != ?"
        parametros.append(producto_id)

    resultado = conn.execute(consulta, parametros).fetchone()

    return resultado is None


def crear_producto(conn, usuario, codigo_barras, nombre,
                   categoria, precio, stock_minimo=5):
    """
    Registra un producto nuevo.

    Solamente pueden realizar esta operación:
    - Administrador.
    - Encargado de inventario.

    El stock inicial es cero. Las existencias se gestionan
    mediante el módulo de inventario.
    """

    requiere_rol(
        usuario,
        "administrador",
        "encargado_inventario"
    )

    codigo_barras = str(codigo_barras).strip()
    nombre = nombre.strip()

    if not nombre:
        raise ProductoError("El nombre es obligatorio.")

    if not validar_codigo_unico(conn, codigo_barras):
        raise ProductoError("El código de barras ya está registrado.")

    if precio < 0:
        raise ProductoError("El precio no puede ser negativo.")

    if stock_minimo < 0:
        raise ProductoError("El stock mínimo no puede ser negativo.")

    with transaccion(conn):

        cursor = conn.execute(
            """
            INSERT INTO productos
            (codigo_barras, nombre, categoria, precio,
             stock, stock_minimo)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (
                codigo_barras,
                nombre,
                categoria,
                precio,
                stock_minimo
            )
        )

        return cursor.lastrowid


def obtener_producto(conn, producto_id):
    """
    Obtiene un producto mediante su identificador.
    Retorna None si no existe.
    """

    producto = conn.execute(
        """
        SELECT *
        FROM productos
        WHERE id = ?
        """,
        (producto_id,)
    ).fetchone()

    return producto


def listar_productos(conn, incluir_inactivos=False):
    """
    Retorna todos los productos registrados.

    Por defecto solamente muestra productos activos.
    """

    if incluir_inactivos:
        consulta = """
            SELECT *
            FROM productos
            ORDER BY nombre
        """
        return conn.execute(consulta).fetchall()

    consulta = """
        SELECT *
        FROM productos
        WHERE activo = 1
        ORDER BY nombre
    """

    return conn.execute(consulta).fetchall()


def modificar_producto(conn, usuario, producto_id,
                       codigo_barras, nombre, categoria,
                       stock_minimo, precio=None):
    """
    Modifica la información de un producto.

    El precio es opcional:
    - Si no se proporciona, conserva el precio actual.
    - Si se proporciona, solamente administrador o encargado
      de inventario pueden modificarlo.

    El stock no se modifica aquí, ya que corresponde
    al módulo de inventario.
    """

    requiere_rol(
        usuario,
        "administrador",
        "encargado_inventario"
    )

    producto = obtener_producto(conn, producto_id)

    if producto is None:
        raise ProductoError("El producto no existe.")

    if not producto["activo"]:
        raise ProductoError("No se puede modificar un producto inactivo.")

    nombre = nombre.strip()

    if not nombre:
        raise ProductoError("El nombre es obligatorio.")

    if not validar_codigo_unico(conn, codigo_barras, producto_id):
        raise ProductoError("El código de barras ya está registrado.")

    if stock_minimo < 0:
        raise ProductoError("El stock mínimo no puede ser negativo.")

    if precio is not None and precio < 0:
        raise ProductoError("El precio no puede ser negativo.")

    if precio is None:
        precio = producto["precio"]

    with transaccion(conn):

        conn.execute(
            """
            UPDATE productos
            SET codigo_barras = ?,
                nombre = ?,
                categoria = ?,
                precio = ?,
                stock_minimo = ?
            WHERE id = ?
            """,
            (
                codigo_barras,
                nombre,
                categoria,
                precio,
                stock_minimo,
                producto_id
            )
        )

    return True


def desactivar_producto(conn, usuario, producto_id):
    """
    Desactiva un producto sin eliminarlo de la base de datos.
    Solamente puede hacerlo el administrador.
    """

    requiere_rol(usuario, "administrador")

    producto = obtener_producto(conn, producto_id)

    if producto is None:
        raise ProductoError("El producto no existe.")

    if not producto["activo"]:
        raise ProductoError("El producto ya está desactivado.")

    with transaccion(conn):

        conn.execute(
            """
            UPDATE productos
            SET activo = 0
            WHERE id = ?
            """,
            (producto_id,)
        )

    return True
