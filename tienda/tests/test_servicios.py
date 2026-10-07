"""Pruebas automáticas. Ejecutar desde la carpeta tienda/:  python -m unittest -v"""
import unittest
from database.db import get_connection, init_db
from services import auth_service as auth
from services.venta_service import registrar_venta, buscar_producto, VentaError
from services.recepcion_service import (crear_pedido, confirmar_recepcion,
                                        DiscrepanciaPendiente)


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.conn = c = get_connection(":memory:")
        init_db(c)
        auth.crear_usuario(c, "A01", "Ana", "admin123", "administrador")
        auth.crear_usuario(c, "C01", "Carlos", "caja123", "cajero")
        auth.crear_usuario(c, "I01", "Irma", "inv123", "encargado_inventario")
        c.execute("INSERT INTO metodos_pago (nombre, es_efectivo) VALUES ('Efectivo',1),('Tarjeta',0)")
        c.execute("INSERT INTO productos (codigo_barras,nombre,categoria,precio,stock) VALUES"
                  " ('750100','Coca-Cola 600ml','Bebidas',18.0,10),"
                  " ('750200','Sabritas Original','Botanas',17.5,2)")
        self.cajero = auth.iniciar_sesion(c, "C01", "caja123")
        self.encargado = auth.iniciar_sesion(c, "I01", "inv123")

    def stock(self, pid):
        return self.conn.execute("SELECT stock FROM productos WHERE id=?", (pid,)).fetchone()[0]


class TestAuth(BaseTest):
    def test_login_correcto(self):
        self.assertEqual(self.cajero["rol"], "cajero")

    def test_login_incorrecto(self):
        with self.assertRaises(auth.AuthError):
            auth.iniciar_sesion(self.conn, "C01", "mala")

    def test_cajero_no_puede_crear_pedido(self):
        with self.assertRaises(auth.PermisoError):
            crear_pedido(self.conn, self.cajero, [(1, 5)])


class TestVentas(BaseTest):
    def test_agrupa_calcula_cambio_y_descuenta(self):
        r = registrar_venta(self.conn, self.cajero, [(1, 1), (1, 2)], 1, monto_recibido=100)
        self.assertEqual(r["total"], 54.0)
        self.assertEqual(r["cambio"], 46.0)
        self.assertEqual(self.stock(1), 7)

    def test_limita_a_existencias_y_avisa(self):
        r = registrar_venta(self.conn, self.cajero, [(2, 5)], 2)
        self.assertEqual(r["lineas"][0]["cantidad"], 2)
        self.assertTrue(r["advertencias"])
        self.assertEqual(self.stock(2), 0)

    def test_venta_fallida_no_deja_cambios(self):
        with self.assertRaises(VentaError):
            registrar_venta(self.conn, self.cajero, [(1, 1)], 1, monto_recibido=5)
        self.assertEqual(self.stock(1), 10)

    def test_busqueda_por_codigo_y_por_nombre(self):
        self.assertEqual(buscar_producto(self.conn, "750100")[0]["nombre"], "Coca-Cola 600ml")
        self.assertEqual(len(buscar_producto(self.conn, "sabri")), 1)


class TestRecepcion(BaseTest):
    def test_bloquea_hasta_aceptar_discrepancia(self):
        pid = crear_pedido(self.conn, self.encargado, [(1, 20)])
        with self.assertRaises(DiscrepanciaPendiente) as e:
            confirmar_recepcion(self.conn, self.encargado, pid, {1: 18})
        self.assertEqual(e.exception.diferencias[0]["diferencia"], -2)
        self.assertEqual(self.stock(1), 10)          # no se tocó el inventario
        confirmar_recepcion(self.conn, self.encargado, pid, {1: 18},
                            discrepancias_aceptadas=True)
        self.assertEqual(self.stock(1), 28)          # entró lo que realmente llegó
        n = self.conn.execute("SELECT COUNT(*) FROM discrepancias").fetchone()[0]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
