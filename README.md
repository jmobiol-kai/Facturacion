# Facturacion

Módulos Odoo de facturación (rama `19.0`). Este repositorio es una carpeta de addons. En el servidor, `addons_path` debe apuntar a la raíz de este repositorio, no a la carpeta de un módulo.

- `kai_partner_account_summary`: en el formulario del contacto, botón «Movimientos» con el total Debe − Haber (moneda de la compañía) de los comprobantes publicados del contacto y de sus hijos. El Debe suma las ventas y los pagos a proveedor: facturas de cliente (`out_invoice`), recibos de venta (`out_receipt`), pagos a proveedor (`account.payment` outbound de proveedor, con asiento publicado) y notas de crédito de proveedor (`in_refund`). El Haber suma las compras y los cobros: facturas de proveedor (`in_invoice`), recibos de compra (`in_receipt`), cobros (`account.payment` inbound de cliente) y notas de crédito de cliente (`out_refund`). La columna sale del tipo de documento, no del signo de `amount_total_signed` ni de `amount_company_currency_signed`. No es el saldo del mayor. Al pulsarlo abre el resumen de esos documentos, filtrado al contacto y a sus hijos, con las columnas Debe, Haber y Saldo. El Saldo de cada línea es Debe − Haber (el mismo signo que el botón); la suma del pie de Saldo coincide con el botón. La factura de cliente queda en el Debe y su cobro en el Haber. No es un saldo acumulado, porque el listado incluye hijos y admite filtros. Depende de `account` y `contacts`.

- `kai_purchase_bill_by_order`: Factura de proveedor con un renglón por orden de compra en lugar del detalle de productos (asistente Crear facturas de proveedor y ajuste en Compras).

- `kai_sale_invoice_by_order`: Factura un renglón por orden de venta en lugar del detalle de productos (opción en el asistente Crear facturas y ajuste en Ventas).

- `msb_default_tax_and_account`: en el diario de venta o de compra, usa la cuenta por defecto del diario en las líneas de factura y reemplaza impuestos según el mapeo del diario (impuesto original → reemplazo). Depende de `account`, `sale` y `purchase`.

- `kai_payment_multi_branch`: un pago o recibo de una compañía de la entidad legal cancela facturas y notas de crédito o débito de otras sucursales del mismo CUIT, aunque todas sean de una sola sucursal. El banco queda en quien paga, cada sucursal cierra su deuda con un asiento puente y las cuentas corrientes intercompañía se netean. La acción «Regularizar pago existente» corrige los pagos ya publicados que habían conciliado esa deuda sin puente. Depende de `account_payment_pro`.
