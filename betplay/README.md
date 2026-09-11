# Capturador de cuotas de Betplay

Betplay corre sobre **Kambi**, cuyo cliente web lee las cuotas de una API JSON
pública. Este paquete la consulta a diario y acumula el histórico que no existe
gratis en ninguna parte — incluidas las líneas de jugador.

```
kambi.py      cliente del feed: rate-limit, reintentos, endpoints
esquema.py    JSON de Kambi -> filas con procedencia (eventos, cuotas, prepacks)
capturar.py   captura diaria -> almacen/ particionado por fecha
analizar.py   coste de combinar, margen por mercado, movimiento de línea
```

## Uso

```bash
pip install -r requirements.txt

python capturar.py                                   # ligas principales
python capturar.py --ligas liga_betplay_dimayor      # una liga
python capturar.py --todas --max-eventos 400         # todo lo abierto
python analizar.py
python test_betplay.py                               # 40 comprobaciones, sin red
```

## Sin credenciales, por diseño

**Este código nunca inicia sesión.** Las cuotas son públicas y el `robots.txt` de
betplay.com.co permite el rastreo (`Allow: /`). Entrar con una cuenta real para
leer datos públicos no aporta nada y expone la cuenta a una suspensión por
acceso automatizado, con el saldo dentro. Hay una prueba (`test_no_hay_credenciales`)
que falla si alguien mete `getenv`, `Authorization` o `Cookie` en el cliente.

Conviene revisar los términos de Betplay antes de operar esto de forma sostenida:
que `robots.txt` lo permita no equivale a que sus condiciones lo permitan.

## Datos de conexión

Averiguados leyendo el bundle del propio sitio, no adivinando:

| | |
|---|---|
| host | `us.offering-api.kambicdn.com` — región **US** |
| marca | `betplay` |
| query | `lang=es_CO&market=CO&client_id=200&channel_id=1` |
| cabeceras | `Referer` y `Origin` de betplay.com.co (sin ellas: 403) |

El host `eu-offering-api` **no sirve** para esta marca: devuelve 429 siempre, y
fue lo que me hizo perder un rato creyendo que era geobloqueo.

El ritmo está en **1 petición por segundo**, con suelo duro de 0,5 s. Un
capturador diario no tiene prisa; un feed público consultado con cabeza dura
meses, uno martilleado dura días.

## Qué se guarda

`almacen/{eventos,cuotas,prepacks}/fecha_captura=YYYY-MM-DD/*.parquet`

**Append-only a propósito.** Capturar el mismo partido dos veces no es un
duplicado que limpiar: es el movimiento de línea, que es justo lo que no se puede
reconstruir después.

Una trampa que cuesta ver: `participant` también trae **equipos**. En "Resultado
Final" el participante es el club, así que usar ese campo para detectar mercados
de jugador marcaba 960 líneas donde solo había 566. El discriminador correcto es
`eventParticipantId`, que solo aparece en mercados de jugador.

## Lo medido en la primera captura

8 partidos de Liga BetPlay → 2.101 líneas, 125 mercados, 566 de jugador
(267 jugadores), 129 combinados del Bet Builder.

**Coste de combinar** (`prePacks`, n=129): ratio mediano cuota/producto **0,68**.
El 73 % de los combinados se pagan por debajo del producto de sus patas.

| Nº patas | n | Ratio mediano |
|---|---|---|
| 2 | 57 | 0,73 |
| 3 | 39 | 1,00 |
| 4 | 32 | 0,57 |

Betplay **no multiplica ingenuamente**: modela la correlación. El edge
estructural por combinar patas correlacionadas —real en el fútbol, medido en
1,65x sobre 28.350 partidos— **no está disponible aquí**. Un ratio por debajo de
1 no prueba abuso por sí solo, porque las patas correlacionadas *deben* pagarse
por debajo del producto; lo que prueba es que no hay regalo.

**Margen por mercado** (mediana del overround):

| Mercado | Margen |
|---|---|
| Hándicap 3-Way | 13,61 % |
| Total asiático | 9,22 % |
| Total de Tiros de Esquina | 9,14 % |
| Hándicap Asiático | 9,00 % |

Para comparar: Pinnacle cobra 2,67 % en 1X2. Un margen alto es mala noticia para
apostar, pero suele venir con líneas menos afinadas — y ahí es donde queda algo
por buscar.

**Mercados de jugador en Liga BetPlay**: "Anotará", "Anotador del primer gol",
"Marca al menos 2 goles", "Marca al menos 3 goles". **No hay tiros a puerta de
jugador** en esta competición; habrá que comprobar si aparecen en ligas europeas.

## Lo que falta y cuándo

El movimiento de línea necesita **más de una captura** y no se puede reconstruir
hacia atrás. Con una captura diaria harán falta semanas antes de poder afirmar
nada con solidez sobre qué líneas están flojas. No hay atajo: es la naturaleza
del dato.
