# Facturacion

Módulos Odoo de facturación. Una rama por versión (`17.0` para Odoo 17 Community).

## Módulos

| Módulo | Rama | Odoo | Descripción |
| --- | --- | --- | --- |
| `kai_check_debit` | `17.0` | 17.0 CE | Debitar cheque diferido: línea de extracto que baja el Balance del banco y salda Cheques diferidos a pagar. |

## kai_check_debit

Dependencia verificada: `l10n_latam_check` (Odoo 17.0 Community), que ya depende de `account_check_printing` y `account`. No depende de `account_payment`: ese módulo es el puente de pagos en portal, y los pagos contables viven en `account`.

En 17.0 el cheque propio no es un modelo aparte. Es un `account.payment` con método `check_printing` y el diario marcado como cheques electrónicos/diferidos (`l10n_latam_manual_checks`). Al publicarlo, la línea de liquidez acredita `outstanding_account_id` (cuenta pendiente del método de pago, o la cuenta outstanding de pagos de la compañía).

**Debitar cheque** (visible en el pago publicado, mientras el saldo diferido siga abierto y no haya otro débito vigente) abre un asistente de fecha y diario bancario y crea una `account.bank.statement.line`:

- Importe negativo (salida), en la moneda del diario. Si el cheque está en otra moneda, el importe en esa moneda va en `amount_currency`.
- Fecha: la del asistente (por defecto, hoy; no anterior al pago).
- `payment_ref`: número de cheque y nombre del pago.
- Sin `statement_id`, así que suma en el Balance del tablero junto con el resto de líneas no ligadas a un extracto (`current_statement_balance`).
- Asiento: crédito a `default_account_id` del diario (una sola vez, con `statement_line_id`, por eso no entra en Operaciones varias) y débito a la cuenta diferida del pago, por el saldo abierto, sin revaluar a la fecha de débito.
- Conciliación de esa contrapartida con el crédito original (`account.move.line.reconcile`). En 17.0 Community `account.bank.statement.line` no tiene `reconcile()`.
- Vínculo de auditoría: `account.move.kai_check_debit_payment_id` en el asiento de la línea. No se usa `account.move.payment_id` (eso vuelve a publicar el pago y falla porque ya está publicado).

No hay un asiento varios paralelo que también acredite el banco.

Para anular el débito y volver a habilitar el botón: abrir el asiento, restablecerlo a borrador y cancelarlo, o eliminar la línea de extracto. Cancelar deja el asiento en estado `cancel`, que el Balance del tablero no suma. No usar Revertir: el reverso no saca el importe de Balance y el botón sigue bloqueado hasta cancelar también ese reverso. Con el débito vigente, el pago no vuelve a borrador ni se cancela.

### Actualización desde 17.0.1.0.0 / 17.0.1.0.1

Esas versiones publicaban un asiento varios (débito a la cuenta diferida, crédito al banco) vinculado con `account.move.kai_check_debit_payment_id`. Ese crédito cae en Operaciones varias. El módulo **no** lo convierte solo al actualizar: crear además la línea de extracto acreditaría el banco por segunda vez.

Mientras ese asiento siga publicado y sin reversión publicada, el botón no aparece.

Para pasar al flujo nuevo, en cada pago ya debitado:

1. Abrir el asiento de débito (botón estadístico Débito cheque).
2. Anularlo: restablecer a borrador y cancelar, o dejar publicada una reversión completa. Al anular se deshace la conciliación y el saldo diferido vuelve a quedar abierto. Esos dos apuntes se netean dentro de Operaciones varias.
3. Usar Debitar cheque de nuevo. El débito nuevo es la transacción bancaria y actualiza Balance.

No hace falta tocar los pagos que todavía no se debitaron.
