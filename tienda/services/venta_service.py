"""Reglas de negocio del módulo de ventas."""
from database.db import ahora, transaccion
from services.auth_service import requiere_rol


class VentaError(Exception):
    """La venta no puede realizarse."""


def buscar_producto(conn, texto):
    """
    Busca por código de barras exacto; si no hay coincidencia, por nombre (parcial).
    Retorna: lista de productos activos (puede estar vacía).
    """
    texto = texto.strip()
    if not texto:
        return []
    por_codigo = conn.execute(
        "SELECT * FROM productos WHERE codigo_barras = ? AND activo = 1", (texto,)
    ).fetchall()
    if por_codigo:
        return por_codigo
    return conn.execute(
        "SELECT * FROM productos WHERE nombre LIKE ? AND activo = 1 ORDER BY nombre",
        (f"%{texto}%",),
    ).fetchall()


def registrar_venta(conn, usuario, items, metodo_pago_id, monto_recibido=None):
    """
    Registra una venta completa en una sola transacción.

    items: lista de (producto_id, cantidad). Los repetidos se agrupan.
    Si la cantidad pedida supera el stock, se vende solo lo existente y se avisa.
    Retorna: dict {folio, total, cambio, lineas, advertencias}.
    Errores: PermisoError, VentaError.
    """
    requiere_rol(usuario, "cajero")
    if not items:
        raise VentaError("La venta no tiene productos.")

    agrupados = {}
    for pid, cant in items:
        if int(cant) != cant or cant <= 0:
            raise VentaError("Las cantidades deben ser enteros positivos.")
        agrupados[pid] = agrupados.get(pid, 0) + int(cant)

    with transaccion(conn):
        metodo = conn.execute(
            "SELECT * FROM metodos_pago WHERE id = ? AND activo = 1", (metodo_pago_id,)
        ).fetchone()
        if metodo is None:
            raise VentaError("Método de pago no válido.")

        lineas, advertencias = [], []
        for pid, pedida in agrupados.items():
            p = conn.execute("SELECT * FROM productos WHERE id = ?", (pid,)).fetchone()
            if p is None or not p["activo"]:
                raise VentaError(f"El producto {pid} no existe o está inactivo.")
            vendida = min(pedida, p["stock"])
            if vendida < pedida:
                advertencias.append(
                    f"{p['nombre']}: se pidieron {pedida} pero solo hay {p['stock']}."
                )
            if vendida > 0:
                lineas.append({"producto": p, "cantidad": vendida})

        if not lineas:
            raise VentaError("Ningún producto tiene existencias disponibles.")

        total = round(sum(l["producto"]["precio"] * l["cantidad"] for l in lineas), 2)
        if metodo["es_efectivo"]:
            if monto_recibido is None or monto_recibido < total:
                raise VentaError("El monto recibido es menor al total.")
            cambio = round(monto_recibido - total, 2)
        else:
            monto_recibido, cambio = total, 0.0

        fecha = ahora()
        folio = conn.execute(
            "INSERT INTO ventas (fecha_hora, usuario_id, metodo_pago_id, total,"
            " monto_recibido, cambio) VALUES (?,?,?,?,?,?)",
            (fecha, usuario["id"], metodo_pago_id, total, monto_recibido, cambio),
        ).lastrowid

        for l in lineas:
            p, c = l["producto"], l["cantidad"]
            conn.execute(
                "INSERT INTO detalle_venta (folio, producto_id, cantidad, precio_unitario)"
                " VALUES (?,?,?,?)", (folio, p["id"], c, p["precio"]))
            nuevo = p["stock"] - c
            conn.execute("UPDATE productos SET stock = ? WHERE id = ?", (nuevo, p["id"]))
            conn.execute(
                "INSERT INTO movimientos_inventario (producto_id, tipo, cantidad_anterior,"
                " cantidad, cantidad_nueva, usuario_id, fecha_hora, referencia)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (p["id"], "salida", p["stock"], c, nuevo, usuario["id"], fecha,
                 f"Venta folio {folio}"))

    return {"folio": folio, "total": total, "cambio": cambio,
            "lineas": lineas, "advertencias": advertencias}
