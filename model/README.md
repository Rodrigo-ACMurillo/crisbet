# Sprint 4 — Modelo de probabilidad

Probabilidades calibradas, no predicciones. El criterio de éxito no es acertar
más partidos: es dar probabilidades **mejores que las del mercado**.

```
dixon_coles.py   Poisson bivariante + corrección de marcadores bajos + decaimiento temporal
gbm.py           LightGBM multiclase sobre las 42 features del Sprint 3
calibration.py   isotónica por clase con control, y ensamblado ponderado
walkforward.py   folds por temporada — nunca k-fold aleatorio
metrics.py       Brier (las dos convenciones), log-loss, ECE, curva de calibración
train.py         orquesta todo y compara contra la cuota de cierre
test_model.py    47 comprobaciones sin red
```

## Uso

```bash
pip install -r requirements.txt
python train.py                      # las 8 ligas, ~12 min
python train.py --ligas E0           # solo Premier, ~1 min
python test_model.py
```

## Por qué dos modelos

**Dixon-Coles** produce una distribución completa de marcadores. De la misma
matriz salen 1X2, over/under, BTTS y cualquier hándicap, así que los mercados no
pueden contradecirse entre sí. Un clasificador independiente por mercado puede
afirmar a la vez que hay 60 % de over 2.5 y 70 % de que el partido acabe 1-0;
esta prueba lo comprueba explícitamente (`test_mercados_derivados_no_se_contradicen`).

**LightGBM** ve lo que Dixon-Coles no puede ver: forma, descanso, congestión de
calendario, fuerza relativa, Elo. Su punto débil es el contrario — aprende
cualquier regularidad, incluidas las espurias.

El ensamble pondera por log-loss en validación, no a mano.

## Rigor temporal

Cada temporada se predice usando solo lo anterior a su primer partido:

```
15-16 16-17 17-18 | 18-19     <- entrena con lo de la izquierda, predice 18-19
15-16 ...   18-19 | 19-20
```

El corte es **por kickoff, no por etiqueta de temporada**. Un partido aplazado
de la 22-23 que acaba jugándose en abril del 24 lleva información que en el corte
de septiembre del 23 no existía; separar por etiqueta lo colaría al entrenamiento.
`verificar_fold()` comprueba cada fold antes de usarlo y descarta los que violen
el orden; el informe registra las violaciones en lugar de silenciarlas.

Dentro del entrenamiento se recorta un tramo final de validación —también
anterior a la prueba— para la parada temprana del GBM, los pesos del ensamble y
la calibración. Ninguna de las tres cosas mira el periodo de prueba.

Un detalle fácil de pasar por alto: la fecha de referencia del decaimiento
temporal de Dixon-Coles es **el corte del fold**, no "hoy". Si fuese hoy, el peso
de cada partido histórico cambiaría según cuándo se ejecuta el script y los
resultados no serían reproducibles.

## La calibración lleva control

La isotónica se ajusta con la primera mitad de la validación y se **acepta solo
si mejora el Brier en la segunda mitad**. Sin ese control, la primera medición de
este sprint empeoraba el modelo: Brier 0,1972 frente a 0,1932 y el ECE casi
triplicado. El problema no era la isotónica sino aplicarla a ciegas a un modelo
que ya estaba razonablemente calibrado.

Decidir sobre las mismas filas con las que se ajustó siempre diría que sí.

## Resultado medido

Walk-forward sobre **20.603 partidos** de 8 ligas, 7 folds (2018-19 a 2024-25),
sin ninguna violación temporal.

| Modelo | Brier (medio/clase) | Log-loss | Acierto | ECE |
|---|---|---|---|---|
| **mercado** (Pinnacle al cierre) | **0,19233** | 0,9704 | 53,05 % | 0,0070 |
| ensamble | 0,19698 | 0,9912 | 51,47 % | 0,0097 |
| gbm | 0,19734 | 0,9933 | 51,46 % | 0,0094 |
| dixon_coles | 0,19925 | 1,0010 | 50,63 % | 0,0063 |

**El modelo no bate al mercado.** No lo hace en ninguno de los 7 folds, en
ninguna de las 8 ligas ni en ninguno de los 6 tramos de cuota. El ensamble pesa
casi al 50 % cada modelo (0,498 / 0,502), así que ninguno de los dos rescata al otro.

### La prueba que zanja la cuestión

Un Brier peor de media todavía dejaría sitio a un nicho rentable. La prueba de
discrepancias lo descarta: se toman los partidos donde el modelo se aparta del
mercado y se mira qué pasó de verdad.

| Discrepancia | n | Modelo dice | Mercado dice | Ocurrió |
|---|---|---|---|---|
| modelo +8 pts sobre el mercado | 5.639 | 0,405 | 0,288 | **0,283** |
| modelo −8 pts bajo el mercado | 5.421 | 0,416 | 0,540 | **0,553** |

Cuando el modelo grita "aquí hay value" y sube 12 puntos sobre el mercado, la
realidad se queda donde decía el mercado. En los seis cortes (tres umbrales × dos
direcciones) gana el mercado. **Cada discrepancia del modelo es un error suyo,
no una oportunidad.**

Traducido al Sprint 5: un selector de value construido sobre estas
probabilidades apostaría sistemáticamente en el lado equivocado y perdería el
margen de la casa en cada ticket. `diagnostico.py` reproduce este análisis.

### La calibración se desactivó sola, en los 7 folds

El control rechazó la isotónica en todos los folds ("no mejora en control"). Es
el comportamiento correcto: el ensamble ya llega con un ECE de 0,0097 y no hay
descalibración que corregir. El problema del modelo no es la magnitud de sus
probabilidades sino su **resolución** — no distingue partidos que el mercado sí
distingue.

## Sprint 4b - el mercado como punto de partida

De las vias abiertas se ataco la segunda: en vez de competir con el mercado desde
cero, **partir de el y aprender solo la desviacion**. `train_mercado.py`.

| | Brier | vs mercado |
|---|---|---|
| mercado (Pinnacle al cierre) | 0,19233 | - |
| `gbm_residual` (mercado como `init_score`) | **0,19228** | -0,00005 |
| `gbm_con_mercado` (mercado como feature mas) | 0,19312 | +0,00079 |

La diferencia entre las dos ultimas filas es el hallazgo de metodo. Meter la
cuota como una feature mas **empeora** al mercado: el arbol es libre de alejarse
y lo hace sin motivo. Ponerla como `init_score` obliga al modelo a arrancar
exactamente en la probabilidad del mercado, asi que cada desviacion tiene que
justificarse contra la funcion de perdida. Con esa formulacion la parada
temprana elige 1-5 rondas en varios folds: el modelo mira los datos y decide no
moverse.

De -0,0047 (Sprint 4, claramente peor) a -0,00005 (indistinguible). Ya no se
destruye valor.

### Las desviaciones si son informativas

La prueba de discrepancias, que en el Sprint 4 daba MERCADO en los 6 cortes,
ahora da MODELO en los 6:

| Discrepancia | n | Modelo | Mercado | Ocurrio |
|---|---|---|---|---|
| modelo +2 pts sobre el mercado | 1.842 | 0,5016 | 0,4730 | **0,4984** |
| modelo -2 pts bajo el mercado | 1.724 | 0,2633 | 0,2905 | **0,2749** |

Cuando el modelo sube 2 puntos sobre el mercado, se queda a 0,3 puntos de la
realidad y el mercado a 2,5. El modelo casi nunca se aparta -solo 80 de 88.700
lineas superan los 5 puntos- pero cuando lo hace, acierta.

### Supera el margen de la casa? Indicio, no prueba

`valor_potencial.py` simula apostar con **cuotas reales de cierre**, margen
incluido:

| Regla | n | ROI | IC 95 % |
|---|---|---|---|
| apostar todas las lineas | 61.809 | -4,06 % | [-5,4, -2,8] |
| exceso >= 0,01 | 6.690 | -0,25 % | [-3,2, +2,7] |
| exceso >= 0,02 | 1.842 | +1,39 % | [-3,8, +6,6] |
| **exceso >= 0,03** | **541** | **+11,23 %** | **[+1,7, +20,8]** |

El +11,23 % es tentador y **no lo doy por bueno**. Las tres pruebas de robustez:

- **Correccion por multiples comparaciones.** Se examinaron 13 reglas; z = 2,30 y
  el liston corregido esta en 2,89. **No sobrevive.**
- **Concentracion temporal.** El 76 % de las apuestas salen de una sola temporada
  (2024-25). Quitando la mejor, el IC pasa a incluir el cero (-0,16).
- **Concentracion de premios.** Las 10 mayores ganancias aportan el 49 % del
  beneficio, sobre 541 apuestas.

A favor queda una cosa: las 4 temporadas con apuestas dan ROI positivo, y la
prueba de discrepancias es independiente de esta busqueda de umbrales.

**Veredicto: indicio, no prueba.** Justifica ejecutar el Sprint 5 con protocolo
estricto -umbral elegido con datos anteriores al periodo evaluado- y no
justifica arriesgar un peso.

### Restriccion operativa que hereda el Sprint 5

Estas son cuotas **de cierre**. Un modelo anclado a ellas solo puede operar en el
instante previo al pitido, no la vispera, y el precio que se consigue entonces es
peor. El Sprint 5 tiene que medir con cuotas de apertura o penalizar
explicitamente el precio de cierre; si no, el ROI simulado sera optimista por
construccion.

## Vias abiertas antes de reintentar el Sprint 5

Por orden de valor esperado, no de esfuerzo:

1. **Información que el mercado tiene y el modelo no.** Alineaciones confirmadas
   y bajas se publican una hora antes del pitido y mueven la cuota. El modelo
   compite a ciegas contra un mercado que sí las conoce. Esto exige la API de
   pago y es, con diferencia, la vía más prometedora.
2. ~~**Usar la cuota como feature.**~~ **Hecho** - ver Sprint 4b arriba. Resultado:
   empate en Brier, desviaciones informativas, indicio de ROI sin prueba estadistica.
3. **Mercados menos eficientes.** El margen de Pinnacle en 1X2 es del 2,67 %:
   está entre los mercados más afinados que existen. Córners, tarjetas, ligas
   menores y mercados de jugador tienen márgenes mayores y menos volumen
   profesional corrigiéndolos.
4. **xG real en lugar del proxy por tiros a puerta.** Mejora la entrada de ambos
   modelos, pero por sí solo no cierra una brecha de 0,0047.

Lo que **no** hay que hacer es ajustar hiperparámetros hasta que el número salga:
con 20.603 partidos, buscar en el espacio de configuraciones hasta batir 0,19233
produciría un ganador por azar, no un edge.

## Deuda conocida

- **Coste de ajuste de Dixon-Coles.** Con 240 equipos son 482 parámetros y
  `L-BFGS-B` aproxima el gradiente por diferencias finitas: cada paso cuesta 483
  evaluaciones de la verosimilitud sobre ~25.000 partidos. De ahí los ~12 minutos
  de la ejecución completa frente a ~1 minuto de una sola liga. El gradiente
  analítico es derivable a mano y reduciría eso en un orden de magnitud; no se ha
  hecho porque el sprint no depende de ello, pero el Sprint 8 reentrena cada
  semana y ahí sí importará.
- El GBM entrena un solo modelo global para las 8 ligas. Un modelo por liga
  tendría menos datos pero más homogeneidad; no se ha medido cuál gana.
- La conversión de Elo a P(1)/P(X)/P(2) que quedó pendiente del Sprint 3 sigue
  sin hacerse: el GBM usa `elo_diff` y `elo_expectativa_local` como features
  crudas y aprende la relación por su cuenta.
- Sin xG real ni ausencias confirmadas (dependen de la API de pago), el modelo
  compite contra un mercado que **sí** conoce las alineaciones. Parte de la
  distancia con la cuota de cierre es exactamente esa información que falta.
