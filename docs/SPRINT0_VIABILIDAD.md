# Sprint 0 · Informe de viabilidad

Es el sprint que debió ir primero y se hizo al final. Responde tres preguntas
que pueden invalidar todo lo construido: ¿es legal?, ¿hay margen que batir?, y
¿cuánto cuesta operarlo?

**Aviso sobre la parte legal:** esto es una lectura de la norma pública, no
asesoría jurídica. La conclusión es lo bastante consistente como para orientar
el diseño, y lo bastante importante como para que un abogado colombiano la
confirme antes de cobrar un peso a nadie.

---

## 1 · Legal: ¿puede existir este servicio en Colombia?

### Qué dice la norma

El marco es la **Ley 643 de 2001**, administrada por **Coljuegos**. Su artículo 5
define un juego de suerte y azar por **tres elementos que deben darse a la vez**:

> "…aquellos juegos en los cuales, según reglas predeterminadas por la ley y el
> reglamento, una persona, que actúa **en calidad de jugador**, realiza una
> **apuesta o paga por el derecho a participar**, […] que le ofrece a cambio un
> **premio**, en dinero o en especie […] no siendo este previsible con certeza,
> por estar determinado por la suerte, el azar o la casualidad."

Y el artículo 2 define el monopolio como la facultad exclusiva del Estado para

> "explotar, organizar, administrar, **operar**, controlar, fiscalizar, regular y
> vigilar todas las modalidades de juegos de suerte y azar".

### Qué significa para Crisbet

El monopolio recae sobre **operar el juego**: recibir apuestas y pagar premios.
Un servicio de pronósticos no cumple ninguno de los tres elementos del artículo 5:

| Elemento del art. 5 | ¿Lo hace Crisbet? |
|---|---|
| El usuario apuesta o paga por **participar en un juego** | No. Paga por información. |
| El servicio ofrece un **premio** | No. No paga nada a nadie. |
| El resultado depende del azar **del juego que opera** | No opera ningún juego. |

El suscriptor no es *jugador* frente a Crisbet: es cliente de un servicio de
análisis. La apuesta, si la hace, la hace en Betplay — un operador **sí**
licenciado por Coljuegos.

**Conclusión provisional:** un servicio de pronósticos que nunca recibe, gestiona
ni custodia dinero de apuestas **no requiere licencia de Coljuegos**, porque no
realiza la actividad monopolizada.

Un resumen automático que consulté durante esta investigación afirmaba lo
contrario — que cualquier tipster sin licencia sería ilegal. No lo sostiene el
texto de la ley: confunde *operar un juego* con *opinar sobre uno*.

### La línea que no se puede cruzar

El momento en que esto **sí** se vuelve operación de juego y exige licencia:

- Recibir dinero de suscriptores **para apostar por ellos**
- Administrar un bankroll común o un fondo de inversión en apuestas
- Pagar premios, repartir ganancias o garantizar rendimientos
- Cualquier forma de "apuesta gestionada"

Mientras el dinero de las apuestas nunca pase por tus manos, estás fuera del
monopolio. En cuanto pase, estás dentro y sin licencia.

### Riesgos reales que sí aplican

Estos no son de Coljuegos, y son los que de verdad pueden morder:

1. **Publicidad engañosa (SIC).** Prometer rentabilidad, enseñar solo los aciertos
   o presentar una expectativa como un hecho es competencia de la
   Superintendencia de Industria y Comercio, no de Coljuegos. Es el riesgo más
   probable de todos, y el más fácil de evitar: publicar el ROI real, también
   cuando es negativo.

2. **Promoción de juego.** Coljuegos emitió en 2023 la Resolución 20231000019054
   regulando publicidad de juegos por internet. El **Consejo de Estado suspendió
   parcialmente** esa resolución, con el argumento de que Coljuegos no tiene
   competencia para intervenir contratos entre operadores y terceros (medios,
   agencias). Un publicador independiente queda en zona gris; **un afiliado que
   cobra de la casa, no**: eso ya es promoción comercial y cambia el análisis
   entero.

3. **Datos personales (Ley 1581 de 2012).** Si hay suscriptores, hay tratamiento
   de datos: política de privacidad, autorización y finalidad declarada.

4. **Tributario.** Los ingresos por suscripción son renta ordinaria. No hay
   régimen especial de juego porque no se opera juego.

### Qué hacer, en concreto

- **Nunca** custodiar dinero de terceros. Es la línea roja y es binaria.
- **No** afiliarse a Betplay ni cobrar comisión por referidos, al menos hasta
  tener concepto jurídico: convierte el servicio en promotor comercial.
- Publicar el rendimiento real, sin seleccionar. Ya está implementado: la web
  muestra el ROI aunque sea negativo y advierte cuando la muestra es insuficiente.
- Aviso de mayoría de edad y línea de juego responsable en cada pieza. Hecho.
- Si pasa de experimento a producto con cobro: concepto jurídico escrito,
  política de datos y verificación de edad real.

---

## 2 · Estudio de cuotas: ¿cuánto margen hay que batir?

Medido sobre **20.533 partidos** de 8 ligas (2019-2026), cuotas de cierre reales:

| Casa | Overround medio | Margen |
|---|---|---|
| Pinnacle | 1,0292 | **2,92 %** |
| Media del mercado | 1,0510 | 5,10 % |
| Bet365 | 1,0580 | 5,80 % |

Y en **Betplay**, medido sobre la captura real (57.578 líneas):

| Mercado | Margen |
|---|---|
| Hándicap 3-Way | **13,61 %** |
| Total asiático | 9,22 % |
| Total de tiros de esquina | 9,14 % |
| Hándicap asiático | 9,00 % |

**Lo que esto significa.** Betplay cobra entre 3 y 5 veces más que Pinnacle. Eso
corta en dos direcciones: sus líneas están menos afinadas (más fácil encontrar
error), pero hay que superar un margen mucho mayor antes de ganar un peso. Cuál
de los dos efectos domina es empírico y aún no está resuelto.

**El Bet Builder no es la vía.** Sus combinados del mismo partido se pagan con un
ratio mediano de **0,68** frente al producto de las patas (n=129). Betplay modela
la correlación y además cobra por combinar: sobre el único combinado que pude
mapear exactamente al histórico, un **20,8 % adicional** al margen de cada pata.

---

## 3 · Baseline: ¿el modelo aporta algo?

El criterio honesto no es el acierto sino el Brier contra la cuota de cierre, en
el mismo conjunto de partidos. Validación walk-forward, 20.603 partidos, 7 folds:

| | Brier (medio/clase) | Acierto |
|---|---|---|
| **Mercado** (Pinnacle al cierre) | **0,19233** | 53,05 % |
| Residual (parte del mercado) | 0,19228 | — |
| Ensamble propio | 0,19698 | 51,47 % |
| GBM | 0,19734 | 51,46 % |
| Dixon-Coles solo | 0,19925 | 50,63 % |

**El dictamen es negativo y está medido dos veces.** El modelo propio no bate al
mercado en ningún fold, ninguna liga ni ningún tramo de cuota. Y la prueba de
discrepancias es concluyente: cuando el modelo se aparta ±12 puntos del mercado,
la realidad se queda donde decía el mercado.

El modelo residual —que parte de la cuota y solo aprende la desviación— empata
(−0,00005, indistinguible). Deja de destruir valor, pero no añade.

**Corrección al plan original:** el objetivo de "Brier < 0,21" era *más flojo que
el propio mercado*. Un modelo en 0,21 no tendría ventaja: tendría pérdidas. El
listón real es 0,1923.

---

## 4 · Presupuesto de operación

Lo que cuesta hoy y lo que costaría el siguiente paso:

| Concepto | Coste |
|---|---|
| GitHub Actions (repo público) | 0 € |
| GitHub Pages | 0 € |
| Feed de cuotas de Betplay (Kambi) | 0 € |
| Resultados (football-data.org, plan libre) | 0 € |
| Histórico de partidos (football-data.co.uk) | 0 € |
| **Total actual** | **0 €/mes** |
| API-Football (disparos de jugador, alineaciones, bajas) | ~19 €/mes |
| Concepto jurídico puntual | consulta única |

El sistema completo funciona sin coste. El único gasto con retorno plausible es
la API de pago, y **solo tiene sentido después** de que el paper trading diga si
hay algo que mejorar.

---

## 5 · Dictamen

**Legal: viable**, con una condición binaria — nunca custodiar dinero de
terceros — y una disciplina de comunicación que ya está implementada. Pendiente
de confirmación por abogado antes de cobrar.

**Técnico: no demostrado.** El criterio de salida que el propio plan fijó para el
Sprint 5 era ROI > 0 con comisión sobre ≥1.000 apuestas. Hoy no se cumple ni se
incumple: no hay datos. El paper trading, ya operativo, es lo que lo resolverá.

**Económico: sin riesgo actual.** Coste cero y sin dinero expuesto. Esa es la
razón por la que tiene sentido dejarlo correr y esperar en lugar de decidir ahora.

**Lo que este informe no puede decir:** si el sistema gana dinero. Nadie puede
decirlo todavía, y cualquier cifra que lo afirme hoy —incluido el +76 % de EV que
muestran los tickets— es una hipótesis del modelo, no un resultado.

---

## Fuentes

- [Ley 643 de 2001 · Gestor Normativo, Función Pública](https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=4168)
- [Ley 643 de 2001 · Ministerio de Salud (PDF)](https://www.minsalud.gov.co/sites/rid/Lists/BibliotecaDigital/RIDE/DE/DIJ/Ley_0643_de_2001.pdf)
- [Coljuegos · sitio oficial](https://www.coljuegos.gov.co/)
- [Operadores de juegos online autorizados por Coljuegos](https://www.coljuegos.gov.co/publicaciones/301721)
- [Coljuegos · resolución de juego responsable](https://www.coljuegos.gov.co/publicaciones/306507/coljuegos-anuncia-resolucion-para-impulsar-el-juego-responsable-en-la-industria-de-suerte-y-azar-en-colombia/)
- [Resolución 20231000019054 de 2023 · SUIN-Juriscol](https://www.suin-juriscol.gov.co/viewDocument.asp?id=30050294)
- [Consejo de Estado suspende límites a la publicidad de apuestas online](https://focusgn.com/latinoamerica/consejo-de-estado-de-colombia-suspende-limites-a-la-publicidad-de-apuestas-online-impuestos-por-coljuegos)
- [Publicidad de juegos bajo el Decreto 2124 de 2023](https://www.mundovideo.com.co/seccion-juridica/publicidad-de-juegos-en-colombia-limites-y-obligaciones-bajo-el-decreto-2124-de-2023/)
