# kai_l10n_ar_vat_ledger_pdf

Libro IVA (Compras/Ventas) de `l10n_ar_reports` en PDF A4 apaisado.

- Botón **Imprimir PDF** en el formulario del libro y **Imprimir › Libro IVA (PDF)**.
- Mismas líneas que el Excel (`account.ar.vat.line`): neto e IVA por alícuota, no gravado/exento,
  Perc. IVA, Perc. IIBB (tributo 07), otros/internos y total; totales y resumen por alícuota.
- Perc. IIBB y Otros se calculan en el módulo desde los apuntes de impuesto, así funciona con la
  versión de `l10n_ar_reports` que no trae la columna `perc_iibb`.

Depende de: `l10n_ar_reports`.
