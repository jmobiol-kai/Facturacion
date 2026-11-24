{
    "name": "Ticket de factura 80 mm",
    "version": "19.0.1.0.1",
    "summary": "Imprime la factura de cliente en formato ticket de 80 mm, con el mismo layout que el recibo del Punto de Venta",
    "description": """
Agrega «Imprimir ticket factura» al menú Imprimir de las facturas de cliente,
notas de crédito y recibos de venta. El PDF es un ticket de 80 mm (impresora
térmica) y no reemplaza la factura A4.

El layout copia el recibo del Punto de Venta de Odoo 19 (OrderReceipt): logo,
comprobante, fecha, atendido por, cliente, renglones con cantidad y precio
unitario, totales, pagos y pie con sucursal, CUIT y «Con la tecnología de Odoo».
Los precios e impuestos siguen la lógica de la factura argentina (letra A
discriminada, B/C/X con IVA incluido). El QR y el CAE se imprimen solo si el
comprobante tiene esos campos con valor.
""",
    "category": "Accounting/Localizations",
    "author": "Kaizendo",
    "website": "https://kaizendo.com.ar",
    "license": "LGPL-3",
    "depends": [
        "account",
        "l10n_ar",
        "l10n_latam_invoice_document",
    ],
    "data": [
        "report/report_invoice_ticket.xml",
    ],
    "assets": {
        "web.report_assets_common": [
            "kai_account_invoice_ticket/static/src/scss/invoice_ticket.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
