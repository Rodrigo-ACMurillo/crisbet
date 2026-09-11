# Paper trading: el libro de tickets

Convierte el selector de "promete un EV" en "devolvió esto". Sin este registro,
cada ejecución produce un número optimista que nadie comprueba nunca.

```
liquidacion.py   lógica pura: dado un marcador, ¿qué paga cada pata?
resultados.py    marcadores reales desde football-data.org (incluye el descanso)
registro.py      libro de tickets: anotar / liquidar / resumen
```

## Uso

```bash
python registro.py anotar --tickets ../model/reports/tickets.json
python registro.py liquidar     # busca resultados y liquida lo vencido
python registro.py resumen      # ROI real frente al EV prometido
python test_liquidacion.py      # 48 comprobaciones, sin red
```

O todo junto desde la raíz: `python diario.py`

## Dos reglas que hacen honesto el ejercicio

**Se anota antes de conocer el resultado.** El libro guarda la cuota y la
probabilidad del modelo en el instante de emitir. Reconstruirlo después, con los
resultados a la vista, permitiría elegir sin querer los tickets que salieron
bien.

**Un ticket no se re-registra.** Su identificador sale del contenido, así que
volver a ejecutar el selector no duplica apuestas ni permite "mejorar" una
emisión anterior. Verificado: re-anotar los mismos 5 tickets registra 0.

## Una pata tiene cinco desenlaces, no dos

Gana entera, gana media, empata (devolución), pierde media, pierde entera. Las
líneas asiáticas de cuarto (−0,25, +0,75) reparten la apuesta en dos mitades.
Tratar eso como un binario infla el retorno medido — y sería el peor error
posible aquí, porque se estaría midiendo con la misma lente rota con la que se
apostó.

Todo se expresa como `(ganado, devuelto)`, fracciones del importe:

```
pago = ganado × cuota + devuelto
```

Una pata devuelta no tumba el combinado: su cuota pasa a valer 1.

**Si una sola pata no se sabe liquidar, el ticket queda pendiente.** Darla por
perdida falsearía el ROI a la baja; por ganada, al alza. Un combinado no se
liquida a medias.

## Verificado contra un partido real

Liverpool 2-2 Nottingham Forest (descanso 0-1):

| Mercado | Selección | Resultado |
|---|---|---|
| Resultado Final | 1 | PIERDE (fue empate) |
| Total de goles, más de 2.5 | — | GANA (4 goles) |
| Total de goles 1ª parte, menos de 1.5 | — | GANA (1 gol al descanso) |
| Hándicap asiático −0.5 | local | PIERDE |
| Ambos marcan | Sí | GANA |

## Lo que falta y no tiene atajo

El ROI real solo existe cuando se juegan los partidos. Con menos de 100 tickets
liquidados, la cifra no distingue una ventaja del azar — el resumen lo dice
explícitamente en lugar de presentar un ROI prematuro como si significara algo.
