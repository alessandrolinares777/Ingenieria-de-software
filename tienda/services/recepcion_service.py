"""Pedidos y recepción de mercancía: compara lo solicitado contra lo recibido."""
from database.db import ahora, transaccion
from services.auth_service import requiere_rol


class RecepcionError(Exception):
    """La recepción no puede procesarse."""


class DiscrepanciaPendiente(Exception):
    """Hay diferencias que el usuario aún no ha revisado y aceptado."""
    def __init__(self, diferencias):
        super().__init__("Existen diferencias entre lo pedido y lo recibido.")
        self.diferencias = diferencias


def crear_pedido(conn, usuario, lineas):
    """lineas: lista de (producto_id, cantidad_pedida). Retorna el id del pedido."""
    requiere_rol(usuario, "encargado_inventario")
    if not lineas:
        raise RecepcionError("El pedido no tiene productos.")
    with transaccion(conn):
        pid = conn.execute(
            "INSERT INTO pedidos (fecha_hora, usuario_id) VALUES (?,?)",
            (ahora(), usuario["id"])).lastrowid
        for prod, cant in lineas:
            if cant <= 0:
                raise RecepcionError("Las cantidades deben ser positivas.")
            conn.execute(
                "INSERT INTO detalle_pedido (pedido_id, producto_id, cantidad_pedida)"
                " VALUES (?,?,?)", (pid, prod, cant))
    return pid


def comparar_pedido_vs_recibido(conn, pedido_id, recibidas):
    """
    recibidas: dict {producto_id: cantidad_recibida}. Lo no capturado cuenta como 0.
    Retorna: lista de diferencias (vacía si todo coincide).
    """
    filas = conn.execute(
        "SELECT d.producto_id, d.cantidad_pedida, p.nombre FROM detalle_pedido d"
        " JOIN productos p ON p.id = d.producto_id WHERE d.pedido_id = ?",
        (pedido_id,)).fetchall()
    if not filas:
        raise RecepcionError("El pedido no existe.")
    ids = {f["producto_id"] for f in filas}
    extra = set(recibidas) - ids
    if extra:
        raise RecepcionError(f"Productos que no pertenecen al pedido: {sorted(extra)}")
    difs = []
    for f in filas:
        rec = recibidas.get(f["producto_id"], 0)
        if rec != f["cantidad_pedida"]:
            difs.append({"producto_id": f["producto_id"], "nombre": f["nombre"],
                         "pedida": f["cantidad_pedida"], "recibida": rec,
                         "diferencia": rec - f["cantidad_pedida"]})
    return difs


def confirmar_recepcion(conn, usuario, pedido_id, recibidas, discrepancias_aceptadas=False):
    """
    Cierra la recepción y suma al inventario lo que REALMENTE llegó.
    Si hay diferencias y el usuario no las aceptó, lanza DiscrepanciaPendiente
    y no se modifica nada. Si las aceptó, se registran en 'discrepancias'.
    """
    requiere_rol(usuario, "encargado_inventario")
    with transaccion(conn):
        pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
        if pedido is None or pedido["estado"] != "pendiente":
            raise RecepcionError("El pedido no existe o ya fue recibido.")
        difs = comparar_pedido_vs_recibido(conn, pedido_id, recibidas)
        if difs and not discrepancias_aceptadas:
            raise DiscrepanciaPendiente(difs)

        fecha = ahora()
        for d in difs:
            conn.execute(
                "INSERT INTO discrepancias (pedido_id, producto_id, cantidad_pedida,"
                " cantidad_recibida, usuario_id, fecha_hora) VALUES (?,?,?,?,?,?)",
                (pedido_id, d["producto_id"], d["pedida"], d["recibida"],
                 usuario["id"], fecha))
        lineas = conn.execute(
            "SELECT producto_id FROM detalle_pedido WHERE pedido_id = ?", (pedido_id,)
        ).fetchall()
        for l in lineas:
            prod = l["producto_id"]
            rec = recibidas.get(prod, 0)
            actual = conn.execute(
                "SELECT stock FROM productos WHERE id = ?", (prod,)).fetchone()["stock"]
            conn.execute("UPDATE productos SET stock = ? WHERE id = ?", (actual + rec, prod))
            conn.execute(
                "UPDATE detalle_pedido SET cantidad_recibida = ? WHERE pedido_id = ? AND producto_id = ?",
                (rec, pedido_id, prod))
            conn.execute(
                "INSERT INTO movimientos_inventario (producto_id, tipo, cantidad_anterior,"
                " cantidad, cantidad_nueva, usuario_id, fecha_hora, referencia)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (prod, "entrada", actual, rec, actual + rec, usuario["id"], fecha,
                 f"Recepción pedido {pedido_id}"))
        conn.execute("UPDATE pedidos SET estado = 'recibido' WHERE id = ?", (pedido_id,))
    return difs
