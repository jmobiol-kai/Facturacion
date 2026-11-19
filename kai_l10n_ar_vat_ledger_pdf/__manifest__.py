{
    "name": "Libro IVA en PDF (Argentina)",
    "version": "19.0.1.0.0",
    "summary": "Imprime el Libro IVA Compras/Ventas (account.vat.ledger) en PDF A4 apaisado",
    "description": """
Agrega un reporte PDF al Libro IVA de l10n_ar_reports:

* Botón "Imprimir PDF" en el formulario del libro y entrada "Libro IVA (PDF)" en el menú Imprimir.
* Una fila por comprobante (mismas líneas que el Excel: account.ar.vat.line) con neto por alícuota,
  no gravado/exento, IVA por alícuota, Perc. IVA, Perc. IIBB, otros/internos y total.
* Perc. IIBB (tributo AFIP 07) y Otros se calculan en este módulo desde los apuntes de impuesto,
  así funciona con cualquier versión de l10n_ar_reports (con o sin columna perc_iibb).
* Totales, resumen por alícuota y otros conceptos. Sirve para compras y ventas.
""",
    "category": "Accounting/Localizations/Reporting",
    "author": "Kaizendo",
    "website": "https://kaizendo.com.ar",
    "license": "LGPL-3",
    "depends": ["l10n_ar_reports"],
    "data": [
        "report/account_vat_ledger_pdf.xml",
        "views/account_vat_ledger_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
