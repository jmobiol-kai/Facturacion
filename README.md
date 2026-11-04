# Facturacion

Módulos Odoo de facturación (rama `19.0`). Este repositorio es una carpeta de addons. En el servidor, `addons_path` debe apuntar a la raíz de este repositorio, no a la carpeta de un módulo.

- `kai_partner_account_summary`: en el formulario del contacto, botón «Movimientos» con la suma de importes absolutos (moneda de la compañía) de los comprobantes publicados del contacto y de sus hijos: facturas y notas de crédito de cliente y de proveedor, recibos de venta y de compra (`out_receipt`, `in_receipt`), cobros (`account.payment` de cliente, inbound, con asiento publicado) y pagos a proveedor (`account.payment` outbound). No es el saldo: una nota de crédito suma su total, no lo resta. Al pulsarlo abre el resumen de cuenta de esos documentos. Depende de `account` y `contacts`.
