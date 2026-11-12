# MSB Default Tax and Account

## Descripción

Módulo completo que simplifica la gestión de impuestos y cuentas contables tanto en facturas de venta como en facturas de proveedor, permitiendo configurar valores por defecto específicos en cada diario contable.

## Problema que resuelve

### Para Ventas
- Evita tener que cambiar manualmente los impuestos en cada línea de factura de cliente
- Permite diferentes esquemas de impuestos por diario (ej: ventas locales vs exportación)
- Agiliza la creación de facturas con impuestos específicos por tipo de venta
- Automatiza la selección de cuentas contables según el diario

### Para Compras
- Evita cambios manuales de impuestos en facturas de proveedor
- Permite diferentes esquemas fiscales por tipo de compra (ej: materias primas vs servicios)
- Sincroniza los impuestos entre órdenes de compra y facturas de proveedor
- Automatiza la asignación de cuentas de gasto según el diario

### Control del Usuario
- Mantiene control total del usuario para modificaciones manuales
- Los valores por defecto son solo sugerencias que se pueden cambiar
- Funciona en tiempo real durante la captura de datos

## Características Principales

### Impuestos por Defecto
- **Para Ventas**: Configuración de impuestos por defecto en diarios de venta
- **Para Compras**: Configuración de impuestos por defecto en diarios de compra
- **Aplicación automática**: Al agregar productos en facturas, órdenes de venta y órdenes de compra
- **Prioridad del diario**: Los impuestos del diario prevalecen sobre los del producto
- **Sincronización**: Los impuestos se mantienen consistentes desde orden hasta factura

### Cuentas por Defecto
- **Cuenta del diario**: La cuenta por defecto del diario prevalece sobre la del producto
- **Configuración independiente**: Se puede activar/desactivar por separado para ventas y compras
- **Estándar Odoo**: Utiliza el campo `default_account_id` que ya existe en los diarios
- **Flexibilidad**: Diferentes cuentas por tipo de operación comercial

### Integración con Órdenes
- **Órdenes de Venta**: Preselección automática del diario más apropiado
- **Órdenes de Compra**: Campo de diario para planificar la facturación
- **Consistencia**: Los impuestos en órdenes coinciden con los de facturas posteriores
- **Vista previa**: Información visible del diario y sus impuestos en las líneas

### General
- **Control total**: El usuario siempre puede modificar manualmente cualquier valor
- **Solo UI**: Funciona únicamente en la interfaz (no afecta importaciones o API)
- **Información clara**: Muestra los impuestos del diario directamente en las líneas
- **Sin sorpresas**: Los cambios son inmediatos y visibles

## Instalación

1. Copiar el módulo a la carpeta de addons
2. Actualizar la lista de aplicaciones
3. Instalar "MSB Default Tax and Account"

## Configuración

### Configurar Diarios de Ventas

1. Ir a **Contabilidad > Configuración > Diarios**
2. Seleccionar un diario de tipo "Venta"
3. En la configuración encontrarás:

#### Cuentas por Defecto para Ventas
- **"Cuenta por Defecto"**: Campo estándar de Odoo para la cuenta que usarán las facturas
- **"Usar Cuenta por Defecto en Facturas"**: Activar para que prevalezca sobre la cuenta del producto

#### Impuestos por Defecto para Ventas
- **"Impuestos por Defecto para Facturas"**: Seleccionar los impuestos que se aplicarán automáticamente

### Configurar Diarios de Compras

1. Ir a **Contabilidad > Configuración > Diarios**
2. Seleccionar un diario de tipo "Compra"
3. En la configuración encontrarás:

#### Cuentas por Defecto para Compras
- **"Cuenta por Defecto"**: Campo estándar de Odoo para la cuenta que usarán las facturas de proveedor
- **"Usar Cuenta por Defecto en Compras"**: Activar para que prevalezca sobre la cuenta del producto

#### Impuestos por Defecto para Compras
- **"Impuestos por Defecto para Compras"**: Seleccionar los impuestos que se aplicarán automáticamente

### Ejemplos de Configuración

#### Ventas: Locales vs Exportación
- **Diario "Ventas Locales"**: 
  - Cuenta: "Ventas Locales" (cuenta 4.1.1)
  - Impuestos: IVA 21%
  - ✅ Activar: "Usar Cuenta por Defecto en Facturas"
- **Diario "Ventas Exportación"**: 
  - Cuenta: "Ventas Exportación" (cuenta 4.1.2)  
  - Impuestos: Exento o 0%
  - ✅ Activar: "Usar Cuenta por Defecto en Facturas"

#### Compras: Materiales vs Servicios
- **Diario "Compra Materiales"**:
  - Cuenta: "Compra de Materiales" (cuenta 6.1.1)
  - Impuestos: IVA 21% Compras
  - ✅ Activar: "Usar Cuenta por Defecto en Compras"
- **Diario "Compra Servicios"**:
  - Cuenta: "Servicios Profesionales" (cuenta 6.2.1)
  - Impuestos: IVA 21% Servicios
  - ✅ Activar: "Usar Cuenta por Defecto en Compras"

#### Diferentes Canales
- **Diario "Tienda Física"**: Cuenta "Ventas Retail", IVA incluido
- **Diario "E-commerce"**: Cuenta "Ventas Online", IVA discriminado
- **Diario "Compras Urgentes"**: Cuenta "Gastos Especiales", tratamiento fiscal diferenciado

## Flujos de Trabajo

### Flujo de Ventas

1. **Usuario crea orden de venta**
   - El sistema sugiere automáticamente un diario con impuestos configurados
2. **Usuario puede cambiar el diario** según el tipo de cliente/venta
3. **Al agregar productos** → Se aplican automáticamente:
   - Impuestos del diario (si están configurados)
   - Usuario ve inmediatamente qué impuestos se están aplicando
4. **Al crear factura desde la orden**:
   - Se transfiere el diario de la orden
   - Se aplican cuenta e impuestos del diario (si están configurados)
5. **Usuario puede modificar** cualquier valor manualmente en cualquier momento

### Flujo de Compras

1. **Usuario crea orden de compra**
   - Puede seleccionar el diario de compras apropiado
   - El sistema sugiere uno con configuración de impuestos si existe
2. **Al agregar productos** → Se aplican automáticamente:
   - Impuestos del diario (si están configurados)
   - Los impuestos se muestran en tiempo real en las líneas
3. **Al crear factura de proveedor desde la orden**:
   - Se transfiere automáticamente el diario seleccionado
   - Se aplican cuenta e impuestos del diario de forma consistente
   - Los impuestos coinciden entre orden y factura
4. **Usuario mantiene control total** para ajustes manuales

## Lógica de Aplicación

### Para Cuentas Contables
```
VENTAS:
SI "Usar Cuenta por Defecto en Facturas" está activado 
   Y el diario tiene "Cuenta por Defecto" configurada:
    → Usar cuenta del diario (ignorar la del producto)
SINO:
    → Usar cuenta del producto (comportamiento normal de Odoo)

COMPRAS:
SI "Usar Cuenta por Defecto en Compras" está activado 
   Y el diario tiene "Cuenta por Defecto" configurada:
    → Usar cuenta del diario (ignorar la del producto)
SINO:
    → Usar cuenta del producto/categoría (comportamiento normal de Odoo)
```

### Para Impuestos
```
VENTAS:
SI el diario tiene "Impuestos por Defecto para Facturas" configurados:
    → Usar impuestos del diario (ignorar los del producto)
    → Se aplican también en órdenes de venta
SINO:
    → Usar impuestos del producto (comportamiento normal de Odoo)

COMPRAS:
SI el diario tiene "Impuestos por Defecto para Compras" configurados:
    → Usar impuestos del diario (ignorar los del producto)
    → Se aplican tanto en órdenes como en facturas de proveedor
    → Se mantiene consistencia entre orden y factura
SINO:
    → Usar impuestos del producto (comportamiento normal de Odoo)
```

### Información Visual
- **Campos informativos**: Se muestran los impuestos del diario en las líneas de orden
- **Consistencia visual**: El usuario ve exactamente qué se aplicará
- **Control inmediato**: Los cambios se reflejan en tiempo real

## Compatibilidad

- **Odoo 19.0**
- **Multi-moneda** compatible
- **Multi-compañía** soportado
- **Posiciones fiscales** respetadas (se aplican mappings automáticamente)
- **Configuraciones existentes** totalmente respetadas

## Limitaciones

- Solo funciona en la interfaz de usuario (formularios)
- No afecta importaciones masivas o llamadas API
- No se aplica a facturas creadas programáticamente
- Los cambios de diario en documentos existentes no actualizan automáticamente las líneas ya creadas

## Soporte Técnico

**Autor**: Mariano Boisselier  
**Licencia**: AGPL-3  
**Estado de Desarrollo**: Beta  
**Módulo**: msb_default_tax_and_account

## Casos de Uso Reales

### Empresa de Servicios Múltiples

**Problema**: La empresa ofrece servicios gravados (21%) y exentos (0%), constantemente debe cambiar impuestos manualmente.

**Solución**:
- Diario "Servicios Gravados" → IVA 21%
- Diario "Servicios Exentos" → Sin impuestos

### Importador/Exportador

**Problema**: Ventas locales con IVA, exportaciones exentas, compras de insumos importados vs locales.

**Solución**:
- **Ventas**: Diario "Locales" (IVA 21%) vs "Exportaciones" (Exento)
- **Compras**: Diario "Importación" (tratamiento especial) vs "Compras Locales" (IVA estándar)

### Empresa con Múltiples Líneas de Negocio

**Problema**: Diferentes tratamientos fiscales y contables por línea de negocio.

**Solución**:
- Crear diarios específicos por línea de negocio (ventas Y compras)
- Configurar impuestos y cuentas correspondientes a cada una
- Mantener trazabilidad contable por línea

### Distribuidora con Diferentes Canales

**Problema**: Ventas mayoristas, minoristas y online requieren diferentes esquemas.

**Solución**:
- Diario "Mayorista" → Cuenta específica, IVA discriminado
- Diario "Minorista" → Cuenta retail, IVA incluido  
- Diario "E-commerce" → Cuenta online, tratamiento digital

## Preguntas Frecuentes

### ¿Puedo usar múltiples impuestos en un diario?
Sí, el campo soporta múltiples impuestos que se aplicarán todos a cada línea, tanto para ventas como para compras.

### ¿Funciona tanto para facturas de cliente como de proveedor?
Sí, el módulo maneja completamente ambos flujos: facturas de cliente (ventas) y facturas de proveedor (compras).

### ¿Los impuestos se mantienen consistentes desde orden hasta factura?
Sí, especialmente en compras donde los impuestos seleccionados en la orden de compra se mantienen al crear la factura de proveedor.

### ¿Qué pasa si cambio de diario en una orden existente?
Los impuestos no se actualizan automáticamente en líneas ya existentes, solo se aplican al agregar nuevos productos.

### ¿Puedo tener diferentes configuraciones para ventas y compras?
Completamente. Cada tipo de diario (venta/compra) tiene su propia configuración independiente de impuestos y cuentas.

### ¿Funciona con posiciones fiscales?
Sí, las posiciones fiscales se aplican normalmente sobre los impuestos del diario, manteniendo toda la funcionalidad estándar de Odoo.

### ¿Puedo desactivar temporalmente la función?
Sí, simplemente desactiva los checkboxes correspondientes o quita los impuestos de la configuración del diario.

### ¿Se puede usar en empresa multi-compañía?
Sí, respeta completamente las configuraciones por compañía y filtra impuestos y cuentas según corresponda.