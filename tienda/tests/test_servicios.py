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

from services.sync_daemon import (
    SyncError,
    sincronizar_precios,
    iniciar_sincronizacion
)

from services.productos_service import (
    ProductoError,
    validar_codigo_unico,
    crear_producto,
    obtener_producto,
    listar_productos,
    modificar_producto,
    desactivar_producto
)

from services.backup_manager import (
    generar_respaldo,
    BackupError
)

from services.backup_manager import (
    BackupError,
    generar_respaldo,
    iniciar_respaldos_diarios
)

from unittest.mock import patch
from unittest.mock import MagicMock, patch
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


class TestProductos(BaseTest):

    def test_validar_codigo_unico(self):
        # Código disponible
        self.assertTrue(
            validar_codigo_unico(self.conn, "750300")
        )

        # Código ya registrado
        self.assertFalse(
            validar_codigo_unico(self.conn, "750100")
        )

        # Excluir el producto actual
        producto = self.conn.execute(
            "SELECT id FROM productos WHERE codigo_barras = '750100'"
        ).fetchone()

        self.assertTrue(
            validar_codigo_unico(
                self.conn, "750100", producto["id"]
            )
        )

        # Código vacío o nulo
        with self.assertRaises(ProductoError):
            validar_codigo_unico(self.conn, None)

        with self.assertRaises(ProductoError):
            validar_codigo_unico(self.conn, "   ")

    def test_crear_producto_correctamente(self):
        producto_id = crear_producto(
            self.conn,
            self.admin,
            "750300",
            "Leche Entera",
            "Lacteos",
            25.50,
            8
        )

        producto = obtener_producto(self.conn, producto_id)

        self.assertIsNotNone(producto)
        self.assertEqual(producto["codigo_barras"], "750300")
        self.assertEqual(producto["nombre"], "Leche Entera")
        self.assertEqual(producto["precio"], 25.50)
        self.assertEqual(producto["stock"], 0)
        self.assertEqual(producto["stock_minimo"], 8)

    def test_crear_producto_rechaza_datos_invalidos(self):
        # Código duplicado
        with self.assertRaises(ProductoError):
            crear_producto(
                self.conn, self.admin,
                "750100", "Producto Nuevo", "General", 20
            )

        # Nombre vacío
        with self.assertRaises(ProductoError):
            crear_producto(
                self.conn, self.admin,
                "750300", "   ", "General", 20
            )

        # Precio negativo
        with self.assertRaises(ProductoError):
            crear_producto(
                self.conn, self.admin,
                "750300", "Producto Nuevo", "General", -5
            )

        # Stock mínimo negativo
        with self.assertRaises(ProductoError):
            crear_producto(
                self.conn, self.admin,
                "750300", "Producto Nuevo", "General", 20, -1
            )

    def test_crear_producto_restringe_roles(self):
        with self.assertRaises(auth.PermisoError):
            crear_producto(
                self.conn,
                self.cajero,
                "750300",
                "Producto Nuevo",
                "General",
                20
            )

    def test_obtener_y_listar_productos(self):
        producto = obtener_producto(self.conn, 1)

        self.assertIsNotNone(producto)

        # Identificador inexistente
        self.assertIsNone(
            obtener_producto(self.conn, 99999)
        )

        # Por defecto solamente productos activos
        activos = listar_productos(self.conn)

        self.assertEqual(len(activos), 2)

        # Desactivar un producto
        producto_id = self.conn.execute(
            "SELECT id FROM productos WHERE codigo_barras = '750100'"
        ).fetchone()["id"]

        desactivar_producto(
            self.conn, self.admin, producto_id
        )

        activos = listar_productos(self.conn)
        todos = listar_productos(
            self.conn, incluir_inactivos=True
        )

        self.assertEqual(len(activos), 1)
        self.assertEqual(len(todos), 2)

    def test_modificar_producto_correctamente(self):
        producto_id = self.conn.execute(
            "SELECT id FROM productos WHERE codigo_barras = '750100'"
        ).fetchone()["id"]

        # Modificar sin cambiar el precio
        modificar_producto(
            self.conn,
            self.encargado,
            producto_id,
            "750100",
            "Coca-Cola 600ml",
            "Bebidas",
            8
        )

        producto = obtener_producto(
            self.conn, producto_id
        )

        self.assertEqual(producto["precio"], 18.0)
        self.assertEqual(producto["stock_minimo"], 8)

        # Modificar también el precio
        modificar_producto(
            self.conn,
            self.admin,
            producto_id,
            "750101",
            "Coca-Cola Familiar",
            "Bebidas",
            10,
            22.50
        )

        producto = obtener_producto(
            self.conn, producto_id
        )

        self.assertEqual(
            producto["codigo_barras"], "750101"
        )
        self.assertEqual(
            producto["nombre"], "Coca-Cola Familiar"
        )
        self.assertEqual(producto["precio"], 22.50)

    def test_modificar_producto_rechaza_datos_invalidos(self):
        producto_id = self.conn.execute(
            "SELECT id FROM productos WHERE codigo_barras = '750100'"
        ).fetchone()["id"]

        # Producto inexistente
        with self.assertRaises(ProductoError):
            modificar_producto(
                self.conn, self.admin, 99999,
                "750300", "Nuevo", "General", 5
            )

        # Código duplicado
        with self.assertRaises(ProductoError):
            modificar_producto(
                self.conn, self.admin, producto_id,
                "750200", "Nuevo", "General", 5
            )

        # Nombre vacío
        with self.assertRaises(ProductoError):
            modificar_producto(
                self.conn, self.admin, producto_id,
                "750100", "   ", "General", 5
            )

        # Stock mínimo negativo
        with self.assertRaises(ProductoError):
            modificar_producto(
                self.conn, self.admin, producto_id,
                "750100", "Nuevo", "General", -1
            )

        # Precio negativo
        with self.assertRaises(ProductoError):
            modificar_producto(
                self.conn, self.admin, producto_id,
                "750100", "Nuevo", "General", 5, -10
            )

        # Producto inactivo
        desactivar_producto(
            self.conn, self.admin, producto_id
        )

        with self.assertRaises(ProductoError):
            modificar_producto(
                self.conn, self.admin, producto_id,
                "750100", "Nuevo", "General", 5
            )

    def test_desactivar_producto_y_validar_permisos(self):
        producto_id = self.conn.execute(
            "SELECT id FROM productos WHERE codigo_barras = '750100'"
        ).fetchone()["id"]

        # El cajero no puede desactivar productos
        with self.assertRaises(auth.PermisoError):
            desactivar_producto(
                self.conn, self.cajero, producto_id
            )

        # El administrador sí puede
        resultado = desactivar_producto(
            self.conn, self.admin, producto_id
        )

        self.assertTrue(resultado)

        producto = obtener_producto(
            self.conn, producto_id
        )

        self.assertEqual(producto["activo"], 0)

        # No se puede desactivar dos veces
        with self.assertRaises(ProductoError):
            desactivar_producto(
                self.conn, self.admin, producto_id
            )

        # Producto inexistente
        with self.assertRaises(ProductoError):
            desactivar_producto(
                self.conn, self.admin, 99999
            )


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


    def test_rechaza_lista_de_precios_vacia_o_invalida(self):
        casos = [None, "precios", [], {}]

        for precios in casos:
            with self.subTest(precios=precios):
                with self.assertRaises(SyncError):
                    sincronizar_precios(
                        self.conn, self.admin, precios
                    )

    def test_rechaza_registro_que_no_es_diccionario(self):
        precios = ["750100"]

        with self.assertRaises(SyncError):
            sincronizar_precios(
                self.conn, self.admin, precios
            )

    def test_rechaza_registro_con_datos_faltantes(self):
        casos = [
            {"codigo_barras": "750100"},
            {"precio": 25.0},
            {}
        ]

        for registro in casos:
            with self.subTest(registro=registro):
                with self.assertRaises(SyncError):
                    sincronizar_precios(
                        self.conn, self.admin, [registro]
                    )

    def test_rechaza_codigo_vacio(self):
        precios = [
            {"codigo_barras": "   ", "precio": 25.0}
        ]

        with self.assertRaises(SyncError):
            sincronizar_precios(
                self.conn, self.admin, precios
            )

    
    def test_rechaza_precio_que_no_se_puede_convertir(self):
        casos = ["incorrecto", None, 10 ** 10000]

        for indice, precio in enumerate(casos):
          with self.subTest(caso=indice):
            precios = [
                {"codigo_barras": "750100", "precio": precio}
            ]

            with self.assertRaises(SyncError):
                sincronizar_precios(
                    self.conn, self.admin, precios
                )

    def test_rechaza_precio_infinito_o_negativo(self):
        casos = [
            float("inf"),
            float("-inf"),
            -10.0
        ]

        for precio in casos:
            with self.subTest(precio=precio):
                precios = [
                    {"codigo_barras": "750100", "precio": precio}
                ]

                with self.assertRaises(SyncError):
                    sincronizar_precios(
                        self.conn, self.admin, precios
                    )

    def test_rechaza_codigos_repetidos(self):
        precios = [
            {"codigo_barras": "750100", "precio": 20.0},
            {"codigo_barras": "750100", "precio": 30.0}
        ]

        with self.assertRaises(SyncError):
            sincronizar_precios(
                self.conn, self.admin, precios
            )

    def test_rechaza_producto_inactivo_y_revierte_cambios(self):
        self.conn.execute(
            "UPDATE productos SET activo = 0 "
            "WHERE codigo_barras = '750200'"
        )

        precios = [
            {"codigo_barras": "750100", "precio": 30.0},
            {"codigo_barras": "750200", "precio": 40.0}
        ]

        with self.assertRaises(SyncError):
            sincronizar_precios(
                self.conn, self.admin, precios
            )

        precio = self.conn.execute(
            "SELECT precio FROM productos "
            "WHERE codigo_barras = '750100'"
        ).fetchone()[0]

        self.assertEqual(precio, 18.0)

    def test_iniciar_sincronizacion_correctamente(self):
        with patch(
            "apscheduler.schedulers.background.BackgroundScheduler"
        ) as mock_scheduler:

            scheduler = mock_scheduler.return_value

            resultado = iniciar_sincronizacion(
                lambda: [],
                lambda: self.conn,
                lambda: self.admin
            )

            self.assertIs(resultado, scheduler)

            scheduler.add_job.assert_called_once()
            scheduler.start.assert_called_once()

            argumentos = scheduler.add_job.call_args

            self.assertEqual(
                argumentos.kwargs["trigger"], "interval"
            )
            self.assertEqual(
                argumentos.kwargs["hours"], 1
            )
            self.assertEqual(
                argumentos.kwargs["id"],
                "sincronizacion_precios"
            )

    def test_tarea_programada_sincroniza_correctamente(self):
        precios = [
            {"codigo_barras": "750100", "precio": 28.0}
        ]

        with patch(
            "apscheduler.schedulers.background.BackgroundScheduler"
        ) as mock_scheduler:

            scheduler = mock_scheduler.return_value

            iniciar_sincronizacion(
                lambda: precios,
                lambda: self.conn,
                lambda: self.admin
            )

            tarea = scheduler.add_job.call_args.args[0]

            with self.assertLogs(level="INFO") as registros:
                tarea()

            self.assertTrue(
                any(
                    "Sincronización completada" in mensaje
                    for mensaje in registros.output
                )
            )

            # La tarea debe cerrar la conexión al terminar.
            with self.assertRaises(sqlite3.ProgrammingError):
                self.conn.execute("SELECT 1")

    def test_tarea_programada_registra_error_y_cierra_conexion(self):
        with patch(
            "apscheduler.schedulers.background.BackgroundScheduler"
        ) as mock_scheduler:

            scheduler = mock_scheduler.return_value

            iniciar_sincronizacion(
                lambda: (_ for _ in ()).throw(
                    RuntimeError("Error de conexión con servidor")
                ),
                lambda: self.conn,
                lambda: self.admin
            )

            tarea = scheduler.add_job.call_args.args[0]

            with self.assertLogs(level="ERROR") as registros:
                tarea()

            self.assertTrue(
                any(
                    "Ocurrió un error" in mensaje
                    for mensaje in registros.output
                )
            )

            with self.assertRaises(sqlite3.ProgrammingError):
                self.conn.execute("SELECT 1")




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

    def test_generar_respaldo_maneja_error_durante_copia(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta_bd = os.path.join(carpeta, "tienda.db")
            carpeta_respaldos = os.path.join(carpeta, "backups")

            # Crear el archivo de origen
            open(ruta_bd, "w").close()

            origen = MagicMock()
            destino = MagicMock()

            def conectar(ruta):
                if ruta == ruta_bd:
                    return origen

                # Simular que se creó un archivo de respaldo parcial
                open(ruta, "w").close()
                return destino

            origen.backup.side_effect = sqlite3.OperationalError(
                "Error simulado durante la copia"
            )

            with patch(
                "services.backup_manager.sqlite3.connect",
                side_effect=conectar
            ):
                with self.assertRaises(BackupError):
                    generar_respaldo(ruta_bd, carpeta_respaldos)

            # Comprobar que se eliminó el respaldo incompleto
            self.assertEqual(os.listdir(carpeta_respaldos), [])

            origen.close.assert_called_once()
            destino.close.assert_called_once()

    def test_generar_respaldo_maneja_error_integridad(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta_bd = os.path.join(carpeta, "tienda.db")
            carpeta_respaldos = os.path.join(carpeta, "backups")

            open(ruta_bd, "w").close()

            origen = MagicMock()
            destino = MagicMock()

            def conectar(ruta):
                if ruta == ruta_bd:
                    return origen

                open(ruta, "w").close()
                return destino

            destino.execute.return_value.fetchone.return_value = (
                "integridad incorrecta",
            )

            with patch(
                "services.backup_manager.sqlite3.connect",
                side_effect=conectar
            ):
                with self.assertRaises(BackupError):
                    generar_respaldo(ruta_bd, carpeta_respaldos)

            self.assertEqual(os.listdir(carpeta_respaldos), [])

            origen.close.assert_called_once()
            destino.close.assert_called_once()

    def test_iniciar_respaldos_diarios_correctamente(self):
        with patch(
            "apscheduler.schedulers.background.BackgroundScheduler"
        ) as mock_scheduler:

            scheduler = mock_scheduler.return_value

            resultado = iniciar_respaldos_diarios(
                "tienda.db",
                "backups",
                hora=22,
                minuto=30
            )

            self.assertIs(resultado, scheduler)

            scheduler.add_job.assert_called_once()
            scheduler.start.assert_called_once()

            argumentos = scheduler.add_job.call_args

            self.assertEqual(argumentos.kwargs["trigger"], "cron")
            self.assertEqual(argumentos.kwargs["hour"], 22)
            self.assertEqual(argumentos.kwargs["minute"], 30)
            self.assertEqual(
                argumentos.kwargs["id"],
                "respaldo_diario"
            )

    def test_iniciar_respaldos_rechaza_hora_invalida(self):
        with self.assertRaises(BackupError):
            iniciar_respaldos_diarios(hora=25)

    def test_iniciar_respaldos_rechaza_minuto_invalido(self):
        with self.assertRaises(BackupError):
            iniciar_respaldos_diarios(minuto=60)

    def test_tarea_diaria_ejecuta_respaldo(self):
        with patch(
            "apscheduler.schedulers.background.BackgroundScheduler"
        ) as mock_scheduler, patch(
            "services.backup_manager.generar_respaldo",
            return_value="backups/respaldo.db"
        ) as mock_generar:

            scheduler = mock_scheduler.return_value

            iniciar_respaldos_diarios(
                "tienda.db",
                "backups",
                hora=23,
                minuto=59
            )

            tarea = scheduler.add_job.call_args.args[0]

            # Ejecutar manualmente la tarea programada
            tarea()

            mock_generar.assert_called_once_with(
                "tienda.db",
                "backups"
            )

    def test_tarea_diaria_registra_error(self):
        with patch(
            "apscheduler.schedulers.background.BackgroundScheduler"
        ) as mock_scheduler, patch(
            "services.backup_manager.generar_respaldo",
            side_effect=BackupError("Error simulado")
        ), self.assertLogs(level="ERROR"):

            scheduler = mock_scheduler.return_value

            iniciar_respaldos_diarios(
                "tienda.db",
                "backups"
            )

            tarea = scheduler.add_job.call_args.args[0]

            # Ejecutar la tarea y comprobar que registra el error
            tarea()






if __name__ == "__main__":
    unittest.main()
