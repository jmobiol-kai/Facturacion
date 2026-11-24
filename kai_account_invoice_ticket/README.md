# kai_account_invoice_ticket

Ticket de 80 mm para facturas de cliente, notas de crédito y recibos de venta. Entra en **Imprimir › Imprimir ticket factura** y no reemplaza el PDF A4.

El layout sigue el recibo del Punto de Venta de Odoo 19 (`point_of_sale.OrderReceipt`, `ReceiptHeader` y `Orderline`), medido contra el ticket impreso del PdV (logo, «Ticket…», fecha, «Atendido por», cliente, cantidad + nombre + precio unitario, totales, pie y «Con la tecnología de Odoo»). No depende de `point_of_sale`: los estilos van en este módulo porque el PDF lo genera wkhtmltopdf.

En la factura, «Ticket …» pasa a ser el tipo de comprobante, la letra y el número (o el `name` si es un comprobante interno). «Atendido por» es el vendedor de la factura. El pie usa la dirección del punto de venta del diario (`l10n_ar_afip_pos_partner_id`) o, si no hay, el nombre del diario y el domicilio de la compañía. El CUIT se imprime sin guiones.

Los importes usan la misma lógica que el PDF argentino: `_l10n_ar_prices_and_taxes`, `_l10n_ar_get_invoice_totals_for_report` y, si corresponde, el régimen de transparencia fiscal. El lugar del QR de autofacturación del PdV queda para el QR de AFIP, el CAE y su vencimiento, y solo se imprime si esos campos existen y tienen valor.

El CSS va en `web.report_assets_common` (lo carga el `<head>` de `web.minimal_layout`) y también en atributos `style` de las celdas. Un `<style>` fuera de `div.article` no llega a wkhtmltopdf: Odoo se queda solo con ese div.

Depende de: `account`, `l10n_ar`, `l10n_latam_invoice_document`.
