# Crisbet — Plan de desarrollo por sprints

Agente autónomo de análisis de fútbol y generación de tickets de apuestas.
Sprints de 2 semanas. 10 sprints ≈ 20 semanas.

---

## Decisiones de arquitectura fijadas antes del Sprint 1

**Canal: Telegram, no WhatsApp.** Razones concretas:

- La Bot API de Telegram es gratuita, sin aprobación previa, sin plantillas de mensaje preaprobadas y sin ventana de 24h para iniciar conversación. Un agente que envía 5 tickets diarios no solicitados es exactamente el caso que la política de WhatsApp Business restringe (exige plantilla aprobada de categoría "marketing" y cobra por conversación iniciada).
- Telegram soporta canales + grupos + bot en un solo producto, botones inline para registrar el resultado de cada ticket (la señal de entrenamiento), y envío de imágenes/PDF sin coste.
- Contenido de apuestas: Meta restringe activamente la promoción de juegos de azar en WhatsApp Business en varios mercados. Telegram no.
- Plan realista: Telegram en Sprint 7. WhatsApp vía proveedor (Twilio / 360dialog) solo si hay demanda comercial, en backlog post-lanzamiento.

**Métricas objetivo** (reemplazan "98% de acierto / cuota 50-100"):

| Métrica | Objetivo realista | Cómo se mide |
|---|---|---|
| Acierto en mercados principales (1X2, O/U 2.5) | 56-62% | backtest walk-forward |
| ROI sobre stake | +3% a +8% | yield acumulado, mín. 500 apuestas |
| Brier score (medio por clase, 1X2) | **< 0.1917** | calibración del modelo |
| Rango de cuota operado | 1.50 – 4.00 | filtro duro en el selector |
| Combinado "alta cuota" (producto secundario) | cuota 50-100, prob. real 2-8% **etiquetada** | Monte Carlo sobre el parlay |
| Drawdown máximo | < 25% del bankroll | simulación Kelly fraccionado |

**Corrección del objetivo de Brier (medida en el Sprint 3).** El "< 0.21" original
era más flojo que el propio mercado: la cuota de cierre de Pinnacle sobre 29.567
partidos da un Brier medio por clase de **0.1917**. Un modelo con 0.21 no tendría
edge, tendría pérdidas — batir al mercado es la condición, no aproximarse a él.
Ojo también con la convención: 0.1917 es el promedio sobre los 3 resultados; la
suma multiclase equivalente es 0.575. Comparar cifras de convenciones distintas
invalidaría el go/no-go.

Si el backtest del Sprint 5 no alcanza ROI > 0 con comisión incluida, el proyecto **no pasa** a dinero real. Ese es el criterio de go/no-go.

**"Sin alucinaciones"** no es una propiedad del LLM: se consigue quitando al LLM de la decisión numérica. El LLM solo redacta y extrae; la probabilidad la calcula un modelo estadístico determinista. Ver Sprint 6.

---

## Corrección de rumbo (2026-09-11)

El producto **no es** batir la cuota de cierre en 1X2 ni operar a la par del
mercado. Es emitir **2-5 tickets diarios de cuota combinada alta (25, 50, 100)**,
asumiendo que pueden perderse, combinando mercados variados —tiros de un
jugador, córners, goles— y no solo el 1X2. Todo el análisis de los sprints 4 y
4b midió contra el criterio equivocado; sigue siendo válido como diagnóstico del
modelo, pero no como criterio de producto.

Tres hechos medidos que condicionan el diseño:

**1. El combinado multiplica el EV, no lo crea.** Con patas al −4 % (lo medido en
1X2 con cuota de cierre), 5 patas dan −18,7 %. Con patas al +1,4 %, dan +7,2 %.
La calidad de cada pata es todo el juego; un combinado de patas malas es peor que
una simple mala.

**2. La correlación dentro del partido es real y grande.** Sobre 28.350 partidos,
48 de 55 pares de mercados del mismo encuentro están correlacionados de forma
significativa (z > 3): `gana_local` + `local_marca_2+` tiene lift 1,78;
`btts` + `over_2.5`, 1,49. Una casa que multiplique cuotas de patas
correlacionadas paga de más, y ese desajuste **no depende de predecir mejor que
nadie**: existe aunque cada pata esté cotizada a la perfección.

**3. Entre partidos distintos no hay tal cosa.** Ahí las patas sí son
independientes y la multiplicación es correcta. El edge estructural vive
únicamente dentro de un mismo partido.

**Bloqueo actual — es de campo, no de código.** Dos preguntas que solo se
responden mirando un operador real (Betplay, Wplay, Rushbet):

- ¿Acepta combinar varias patas del **mismo** partido, y a qué cuota? Si ofrece
  el producto de las patas, el edge por correlación está disponible. Si recotiza
  a la baja con su propio modelo, no lo está.
- ¿Qué mercados de jugador ofrece y con qué cuotas? No existe histórico gratuito
  de cuotas de props, así que hay que capturarlas para construirlo.

Sin esas dos respuestas, cualquier cifra de EV sobre combinados es un supuesto.

---

## Sprint 0 — Validación y fundamentos

**Objetivo:** saber si el negocio es viable antes de construirlo.

- Legal: revisar el régimen de Coljuegos. En Colombia solo operadores con licencia pueden ofrecer apuestas; un servicio de *pronósticos* (tipster) es legal pero no puede aceptar ni gestionar dinero de terceros. Definir el modelo: suscripción en COP por señales, sin custodia de fondos.
- Estudio de cuotas reales: registrar 4 semanas de cuotas de cierre de casas con licencia en Colombia (Betplay, Wplay, Rushbet) y medir el overround.
- Baseline honesto: modelo Dixon-Coles sobre 5 temporadas; medir acierto y ROI. Este número es el piso contra el que se compara todo lo demás.
- Presupuesto de infraestructura y de datos (API de estadísticas de pago).

**Entregable:** informe de viabilidad con el ROI baseline y el dictamen legal.
**Criterio de salida:** baseline documentado; modelo de negocio sin custodia de fondos definido.

---

## Sprint 1 — Pipeline de descubrimiento de fuentes ✅ (implementado)

**Objetivo:** dataset de hasta 1.000.000 de URLs reales, clasificadas y deduplicadas.

- Ingestor asíncrono: robots.txt → sitemaps XML → RSS → Common Crawl, con `aiohttp` y `Semaphore(50)`.
- Clasificador heurístico URL → (category, subcategory) según la taxonomía acordada.
- Normalización (elimina `utm_*`, `fbclid`, `ref`, `session_id`, fragmentos) y dedup SHA-256 en SQLite/WAL — en disco, reanudable, RAM constante.
- Escritura en streaming a JSONL con `orjson`; alternativa JSON particionada de 100k registros.
- 56 dominios semilla verificados de Europa y Latinoamérica.

**Estado:** código en `crawler/`, ejecutado y verificado (3.000 URLs reales, 0 duplicados).
**Entregable:** `dataset_futbol_1m.jsonl`.

---

## Sprint 2 — Ingesta de contenido y extracción estructurada ✅ (implementado)

**Objetivo:** convertir URLs en hechos con procedencia.

- Fetcher respetuoso: `robots.txt` por dominio, rate-limit por host, caché HTTP (ETag / Last-Modified), reintentos con backoff exponencial.
- Extracción de texto (trafilatura) y de tablas (pandas/lxml) para FBref, Understat, Transfermarkt.
- **Capa anti-alucinación:** todo hecho extraído guarda `source_url`, `extracted_at`, `raw_snippet`. Un hecho sin procedencia no entra al almacén.
- Almacén analítico: Parquet particionado por fecha + DuckDB para consulta.

**Estado:** código en `extractor/`, ejecutado y verificado (120 páginas, 16 dominios,
procedencia 100%, 46 respuestas 304 en la segunda pasada, 24/24 pruebas offline).
**Entregable:** tabla de hechos con procedencia al 100%.
**Límite conocido:** los sitios SPA (`js_rendered`) no entregan texto sin navegador
headless; quedan como contexto, no como fuente de verdad.

---

## Sprint 3 — Datos de partido canónicos y feature store ✅ (implementado)

**Objetivo:** la verdad numérica, separada del texto.

- Integrar una API de estadísticas de pago (API-Football o StatsBomb) como **fuente de verdad** para resultados, alineaciones y xG. El crawl de noticias es contexto, no verdad.
- Feature store con rigor temporal: cada feature se calcula solo con información disponible *antes* del pitido inicial. Auditar fuga de datos (leakage) de forma explícita.
- Features: forma ponderada, xG/xGA rodante, fuerza ataque/defensa, descanso entre partidos, viaje, ausencias confirmadas, Elo, clima, árbitro.
- Historial de cuotas de cierre por mercado (benchmark de eficiencia).

**Estado:** código en `matchdata/`. 29.577 partidos de 8 ligas y 10 temporadas,
205.656 líneas de cuota de cierre, 240 equipos. 34/34 pruebas anti-fuga.
**Entregable:** feature store versionado + suite de tests anti-leakage.
**Desviación:** la API de pago no está contratada, así que la fuente de verdad es
football-data.co.uk (gratuita: resultados, estadísticas de tiro y cuotas de cierre).
Queda tras la interfaz `MatchProvider`; `ApiFootball` está declarada y aportará xG
real, alineaciones y bajas al contratarse. Sin ella faltan xG (hoy proxy por tiros
a puerta), ausencias, clima y viaje.

---

## Sprint 4 — Modelo de probabilidad ✅ (implementado — resultado negativo)

**Objetivo:** probabilidades calibradas, no predicciones.

- Modelo base: Dixon-Coles bivariado / Poisson bayesiano (pymc o statsmodels).
- Modelo discriminativo: LightGBM multiclase por mercado (1X2, O/U, BTTS, hándicap asiático).
- Ensamble + calibración isotónica; medir Brier y log-loss, no solo accuracy.
- Validación **walk-forward** por temporada. Nunca k-fold aleatorio sobre series temporales.

**Estado:** código en `model/`. Walk-forward sobre 20.603 partidos, 7 folds, sin
violaciones temporales. 47/47 pruebas.
**Entregable:** modelo con Brier 0,19698 y calibración documentada.
**Resultado:** el modelo **no bate al mercado** (0,19698 frente a 0,19233 de la cuota
de cierre de Pinnacle). No lo bate en ningún fold, liga ni tramo de cuota. La prueba
de discrepancias es concluyente: en los 6 cortes, cuando el modelo se aparta del
mercado, el mercado tiene razón.

**Consecuencia para el Sprint 5:** un selector de value sobre estas probabilidades
apostaría sistemáticamente en el lado equivocado. El Sprint 5 **no debe ejecutarse**
como está: primero hay que cerrar la brecha del Sprint 4. Vías abiertas, por valor
esperado: (1) alineaciones y bajas confirmadas —exige la API de pago—, (2) usar la
cuota implícita como feature y aprender a corregirla, (3) mercados con más margen
que el 1X2 de Pinnacle, (4) xG real. Ver `model/README.md`.

---

## Sprint 5 — Selector de value, staking y backtest — GO/NO-GO

**Objetivo:** demostrar ROI positivo antes de arriesgar un peso.

- Detector de value: `edge = p_modelo × cuota − 1`. Umbral mínimo de edge (p. ej. 5%) y filtro de cuota 1.50-4.00.
- Staking: Kelly fraccionado (¼ Kelly), tope por ticket del 2% del bankroll.
- Backtest con **cuotas de cierre reales**, comisión y slippage incluidos. Simular también el peor caso (cuotas disponibles un 5% peores).
- Generador del combinado de alta cuota (50-100) por Monte Carlo, con probabilidad real y pérdida esperada mostradas sin adornos.
- Todo en COP: bankroll, stake, P&L, con tipo de cambio histórico.

**Entregable:** informe de backtest con yield, drawdown e intervalos de confianza.
**Criterio de salida (duro):** ROI > 0 con comisión, sobre ≥ 1.000 apuestas simuladas. Si no se cumple, se vuelve al Sprint 4; no se avanza.

---

## Sprint 6 — Agente autónomo y orquestación

**Objetivo:** el bucle diario sin intervención humana.

- Orquestador (Prefect o Temporal): ingesta → features → inferencia → selección → 5 tickets → entrega, con reintentos y alertas.
- **El LLM (Claude) tiene un rol acotado, por diseño:** (a) extrae datos de texto no estructurado devolviendo JSON con tool use forzado y schema validado; (b) redacta la justificación del ticket *a partir* de los números que el modelo ya calculó. El LLM nunca produce una probabilidad, una cuota ni un stake.
- Guardarraíles: validación de esquema en toda salida del LLM, verificación de que cada cifra citada existe en el feature store, rechazo del ticket si falla.
- Log de decisión completo por ticket: features, probabilidad, cuota, edge, stake.

**Entregable:** cron diario produciendo 5 tickets auditables.

---

## Sprint 7 — Bot de Telegram

**Objetivo:** el producto que el usuario toca.

- Bot con `python-telegram-bot`: `/tickets`, `/historial`, `/bankroll`, `/stats`, `/suscripcion`.
- Entrega programada de los 5 tickets diarios, con la hora de cierre de mercado visible.
- Botones inline **Ganó / Perdió / Anulado** → esta es la señal de entrenamiento.
- Ficha de ticket: mercado, cuota, probabilidad del modelo, edge, stake en COP, ganancia potencial en COP y la justificación redactada.
- Aviso de riesgo obligatorio y enlace de juego responsable en cada mensaje.

**Entregable:** bot en producción con suscripciones.

---

## Sprint 8 — Memoria y aprendizaje continuo

**Objetivo:** que el agente mejore con cada resultado.

- **Memoria episódica:** tabla de cada ticket con resultado liquidado, cuota obtenida y P&L real en COP. Esta es la memoria de apuestas acertadas que pediste.
- **Memoria semántica:** embeddings (pgvector) de análisis y contexto de partido, para recuperar situaciones análogas ("visitante con 3 bajas defensivas jugando en altura").
- **Bucle de aprendizaje:** reentrenamiento semanal automático incorporando los resultados nuevos; comparación campeón/retador; promoción solo si el retador gana en Brier **y** en ROI sobre un holdout reciente.
- Detección de deriva (drift): alerta si la calibración se degrada o si el edge medio cae.
- Análisis de atribución: qué ligas, mercados y rangos de cuota generan el ROI real → el selector se reenfoca hacia ellos.

**Entregable:** reentrenamiento automático con promoción basada en evidencia.

---

## Sprint 9 — Observabilidad, riesgo y cumplimiento

**Objetivo:** operar sin sorpresas.

- Dashboard: ROI vivo, acierto por mercado, calibración, drawdown, bankroll en COP.
- Cortacircuitos automático: pausa la emisión si el drawdown supera el 20%, si la calibración se rompe, o si una fuente de datos lleva más de N horas caída.
- Cumplimiento: aviso de riesgo, verificación de mayoría de edad, autoexclusión, tratamiento de datos personales (Ley 1581 de 2012), y la aclaración explícita de que el servicio entrega pronósticos y **no** gestiona dinero de apuestas.
- Runbook de incidentes y respaldos.

**Entregable:** operación monitorizada con límites de riesgo automáticos.

---

## Sprint 10 — Paper trading, piloto y escala

**Objetivo:** confrontar el modelo con el mercado real.

- **Paper trading, 6 semanas:** tickets reales, dinero ficticio, cuotas registradas en el momento de la emisión. Comparar ROI simulado contra ROI real.
- Piloto con bankroll pequeño (p. ej. 500.000 COP) solo si el paper trading confirma ROI positivo.
- Escalado del crawler al objetivo de 1M de URLs, con rotación de proxies y caché distribuida.
- Tests de carga del bot; facturación de suscripciones en COP (Wompi / PayU).

**Entregable:** informe de paper trading y decisión documentada de escalar o parar.

---

## Qué no prometer al usuario final

- Nada de "98% de acierto". Se publica el acierto histórico real y el yield.
- Cada ticket muestra la probabilidad real de ganar, también en los combinados de cuota alta.
- El margen de error se comunica como intervalo de confianza, no como una cifra fija del 5%.
- Una racha ganadora no es evidencia de edge; solo el volumen lo es.
