"""Pruebas automáticas. Ejecutar desde la carpeta tienda/:  python -m unittest -v"""
import unittest
import csv
import os
import sqlite3
import tempfile

from services.inventario_service import (
    cargar_inventario_masivo,
    InventarioError
)

from services.sync_daemon import (
    sincronizar_precios,
    SyncError
)

from services.backup_manager import (
    generar_respaldo,
    BackupError
)

from contextlib import closing
from database.db import get_connection, init_db
from services import auth_service as auth
from services.venta_service import registrar_venta, buscar_producto, VentaError
from services.recepcion_service import (crear_pedido, confirmar_recepcion,
                                        DiscrepanciaPendiente)



class BaseTest(unittest.TestCase):
    def setUp(self):
        self.conn = c = get_connection(":memory:")
        init_db(c)

        # Crear los usuarios
        auth.crear_usuario(c, "A01", "Ana", "admin123", "administrador")
        auth.crear_usuario(c, "C01", "Carlos", "caja123", "cajero")
        auth.crear_usuario(c, "I01", "Irma", "inv123", "encargado_inventario")

        # Iniciar sesión con los usuarios
        self.admin = auth.iniciar_sesion(c, "A01", "admin123")
        self.cajero = auth.iniciar_sesion(c, "C01", "caja123")
        self.encargado = auth.iniciar_sesion(c, "I01", "inv123")

        # Crear métodos de pago
        c.execute(
            "INSERT INTO metodos_pago (nombre, es_efectivo) "
            "VALUES ('Efectivo',1),('Tarjeta',0)"
        )

        # Crear productos de prueba
        c.execute(
            "INSERT INTO productos "
            "(codigo_barras,nombre,categoria,precio,stock) VALUES"
            " ('750100','Coca-Cola 600ml','Bebidas',18.0,10),"
            " ('750200','Sabritas Original','Botanas',17.5,2)"
        )

    def stock(self, pid):
        return self.conn.execute(
            "SELECT stock FROM productos WHERE id=?", (pid,)
        ).fetchone()[0]


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


class TestInventario(BaseTest):

    def test_carga_masiva_correcta(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "productos.csv")

            with open(ruta, "w", newline="", encoding="utf-8") as archivo:
                escritor = csv.writer(archivo)
                escritor.writerow([
                    "codigo_barras", "nombre", "categoria",
                    "precio", "stock", "stock_minimo"
                ])
                escritor.writerow([
                    "750300", "Galletas", "Botanas", 15.5, 20, 5
                ])

            cantidad = cargar_inventario_masivo(
                self.conn, self.encargado, ruta
            )

            self.assertEqual(cantidad, 1)

            producto = self.conn.execute(
                "SELECT * FROM productos WHERE codigo_barras = '750300'"
            ).fetchone()

            self.assertIsNotNone(producto)
            self.assertEqual(producto["stock"], 20)

    def test_rechaza_codigo_duplicado_y_revierte_importacion(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "productos.csv")

            with open(ruta, "w", newline="", encoding="utf-8") as archivo:
                escritor = csv.writer(archivo)
                escritor.writerow([
                    "codigo_barras", "nombre", "categoria",
                    "precio", "stock", "stock_minimo"
                ])
                escritor.writerow([
                    "750300", "Galletas", "Botanas", 15.5, 20, 5
                ])
                escritor.writerow([
                    "750100", "Producto duplicado", "Bebidas", 20, 10, 5
                ])

            with self.assertRaises(InventarioError):
                cargar_inventario_masivo(
                    self.conn, self.encargado, ruta, tamano_bloque=1
                )

            resultado = self.conn.execute(
                "SELECT COUNT(*) FROM productos WHERE codigo_barras = '750300'"
            ).fetchone()[0]

            self.assertEqual(resultado, 0)


class TestSincronizacion(BaseTest):

    def test_actualiza_precios_correctamente(self):
        precios = [
            {"codigo_barras": "750100", "precio": 25.50},
            {"codigo_barras": "750200", "precio": 18.00}
        ]

        cantidad = sincronizar_precios(
            self.conn, self.admin, precios
        )

        self.assertEqual(cantidad, 2)

        precio = self.conn.execute(
            "SELECT precio FROM productos WHERE codigo_barras = '750100'"
        ).fetchone()[0]

        self.assertEqual(precio, 25.50)

    def test_revierte_cambios_si_un_producto_no_existe(self):
        precios = [
            {"codigo_barras": "750100", "precio": 30.00},
            {"codigo_barras": "999999", "precio": 50.00}
        ]

        with self.assertRaises(SyncError):
            sincronizar_precios(
                self.conn, self.admin, precios
            )

        precio = self.conn.execute(
            "SELECT precio FROM productos WHERE codigo_barras = '750100'"
        ).fetchone()[0]

        self.assertEqual(precio, 18.0)

    def test_rechaza_precios_invalidos(self):
        precios = [
            {"codigo_barras": "750100", "precio": float("nan")}
        ]

        with self.assertRaises(SyncError):
            sincronizar_precios(
                self.conn, self.admin, precios
            )



class TestRespaldos(unittest.TestCase):

    def test_generar_respaldo_correctamente(self):
        with tempfile.TemporaryDirectory() as carpeta:

            ruta_bd = os.path.join(carpeta, "tienda.db")
            carpeta_respaldos = os.path.join(carpeta, "backups")

            # Crear una base de datos de prueba
            conn = get_connection(ruta_bd)
            init_db(conn)
            conn.close()

            # Generar el respaldo
            ruta_respaldo = generar_respaldo(
                ruta_bd, carpeta_respaldos
            )

            # Comprobar que el archivo existe
            self.assertTrue(os.path.isfile(ruta_respaldo))

            # Comprobar la integridad del respaldo
            with closing(sqlite3.connect(ruta_respaldo)) as respaldo:
                resultado = respaldo.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]

            self.assertEqual(resultado, "ok")

    def test_error_si_no_existe_base_de_datos(self):
        with tempfile.TemporaryDirectory() as carpeta:

            ruta_inexistente = os.path.join(
                carpeta, "no_existe.db"
            )

            # Comprobar que se genera el error esperado
            with self.assertRaises(BackupError):
                generar_respaldo(
                    ruta_inexistente, carpeta
                )





if __name__ == "__main__":
    unittest.main()
