# Fantasy Studio

Seguimiento de **LaLiga Fantasy** (oficial) con dos modos:

- **LaLiga Hypermotion** (Segunda División) — modo principal, azul eléctrico
- **LaLiga EA Sports** (Primera División) — modo rojo

## Qué hace

- **Resumen**: jornada actual, tu economía (invertido, valor, beneficio latente y realizado), alertas de lesionados/sancionados de tu plantilla, jugadores vigilados, top subidas/bajadas del día y calendario de la jornada.
- **Mercado**: todos los jugadores de la competición con valor de mercado, puntos, media, forma, puntos por millón y variaciones de precio (hoy / 7 días). Filtros por posición y equipo. Ficha de cada jugador con gráfico del histórico de valor y puntos por jornada.
- **Mi Plantilla**: añade los jugadores que compras con su precio real de compra; la app calcula la plusvalía de cada uno contra su valor de mercado actual. Al vender, indicas el precio y el beneficio queda registrado.
- **Movimientos**: libro de compras y ventas con beneficio realizado y ROI.

## De dónde salen los datos

De la API oficial de LaLiga Fantasy (`fantasy-api.llt-services.com`), que es pública para datos de jugadores. Un workflow de GitHub Actions ([datos.yml](.github/workflows/datos.yml)) los descarga varias veces al día y los publica en [`data/`](data/), acumulando además el **histórico diario de valor de mercado** de cada jugador. El botón «Actualizar» de la app intenta además traer datos en vivo a través de un proxy CORS.

Tus datos personales (plantilla, movimientos, vigilados) **no salen de tu dispositivo**: viven en el localStorage del navegador. Usa Exportar/Importar (menú ⋮) para copias de seguridad o para pasarlos a otro móvil.

## Uso en el móvil

Abre la web en el navegador del móvil y usa «Añadir a pantalla de inicio» para tenerla como app.
