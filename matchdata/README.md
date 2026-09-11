# Sprint 3 — Datos de partido canónicos y feature store

La verdad numérica del sistema, separada del texto rastreado en el Sprint 2.

```
providers.py   MatchProvider: football-data.co.uk (viva) | ApiFootball (declarada)
    └─ canonical.py   Match + OddsCierre + slug canónico de equipo
        └─ features.py   features punto-en-el-tiempo (leer el pasado, luego aprender)
            └─ build.py     warehouse/{matches,odds,features}/temporada=*/
                └─ verify.py   ¿hay señal? ¿está calibrada? ¿hay fuga?
```

## Uso

```bash
pip install -r requirements.txt

python build.py --ligas E0 E1 SP1 I1 D1 F1 N1 P1 --desde 2015-16 --hasta 2024-25
python verify.py
python test_leakage.py      # 34 comprobaciones, sin red
```

## La regla que sostiene todo

**Cada feature se calcula solo con partidos anteriores al pitido inicial.** No se
hace cumplir con revisiones manuales sino con la forma del bucle:

```python
for partido in ordenar_cronologico(partidos):
    filas.append(store.features_de(partido))   # lee el pasado
    store.actualizar(partido)                  # y solo después, aprende
```

La actualización no puede adelantar a la lectura porque ocurre una línea más
abajo. Dos fugas sutiles que el diseño evita a propósito:

- **Medias de liga de temporada completa.** "Fuerza de ataque relativa a la media
  de la liga" con la media final mete en la jornada 1 información de la 38. Aquí
  la media de liga es acumulada.
- **Normalización global.** Escalar una columna con la media de todo el dataset
  filtra el futuro. Este módulo no normaliza: esa decisión es del Sprint 4,
  dentro del fold.

La prueba que lo verifica es la de **invariancia por truncamiento**: las features
de los primeros N partidos salen idénticas se procese el dataset completo o solo
esos N. Si el futuro toca el pasado por cualquier vía, falla.

## Resultado medido

| | |
|---|---|
| Partidos | 29.577 (8 ligas, 10 temporadas, 2015-2025) |
| Líneas de cuota de cierre | 205.656 |
| Equipos canónicos | 240 |
| Features de entrada | 42, ninguna vacía |
| Filas con historial suficiente | 97,3 % |
| Pruebas anti-fuga | 34/34 |

**Overround por casa** (el margen que hay que batir antes de ganar un peso):

| Casa | Margen |
|---|---|
| Pinnacle | 2,67 % |
| Media del mercado | 4,88 % |
| Bet365 | 5,72 % |

**Benchmark del mercado** — Pinnacle al cierre, 29.567 partidos: acierta el
favorito el 53,3 % y su Brier medio por clase es **0,1917**. Este número obligó a
corregir el plan: el objetivo original de `< 0.21` era *más flojo que el propio
mercado*, y un modelo con 0,21 no tendría edge sino pérdidas.

**Señal del Elo**: Brier 0,1589 frente a 0,1829 de la tasa base — 13,2 % mejor.
Elo diff medio +69 cuando gana el local, -55 cuando no.

**Calibración del Elo**: sobreconfianza simétrica (+0,08 en los tramos bajos,
-0,06 en los altos). No es fuga: es la firma de un Elo sin modelo de empate, y es
exactamente lo que corrige la calibración isotónica del Sprint 4.

## Deuda conocida

- `elo_expectativa_local` es la **puntuación esperada** (victoria=1, empate=0,5),
  no P(victoria local). En un deporte con 25 % de empates son cosas distintas;
  medirla contra "gana o no gana" la deja sobrestimada ~15 puntos y el sesgo
  parece una fuga que no existe. Convertirla en P(1)/P(X)/P(2) es del Sprint 4.
- **xG es un proxy** (tiros a puerta × 0,32), no xG real. Llega con la API de pago.
- **Faltan** ausencias confirmadas, clima, viaje y árbitro como feature. Todas
  dependen del proveedor de pago; ninguna se ha inventado para rellenar el hueco.
- `mejor_disponible` da overround < 1 porque compone el mejor precio de cada
  resultado entre casas distintas. Sirve de referencia, no es operable.
