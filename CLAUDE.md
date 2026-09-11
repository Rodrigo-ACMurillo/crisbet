# Crisbet

Agente de análisis de fútbol y generación de tickets de apuestas. Plan completo
en [docs/PLAN_SPRINTS.md](docs/PLAN_SPRINTS.md).

## Al empezar una sesión

Si el usuario escribe **"continúa"**, **"sigue"**, **"desarrolla"** o similar:

1. Lee **[ESTADO.json](ESTADO.json)** — y solo eso. Contiene el sprint activo,
   qué está hecho, qué falla, qué se corrigió y cuál es el siguiente paso.
2. Lee el README del componente del sprint activo (`crawler/README.md`,
   `extractor/README.md`) únicamente si vas a tocar ese código.
3. No recorras el proyecto entero para reconstruir el contexto: `ESTADO.json`
   existe precisamente para evitarlo.

## Al terminar un desarrollo

Actualiza `ESTADO.json` antes de cerrar: `sprint_actual`, `sprint_actual_estado`,
`siguiente_paso`, `actualizado`, y añade lo aprendido a `problemas_corregidos`
y `limitaciones_conocidas`. Un estado desactualizado es peor que no tenerlo,
porque la siguiente sesión confía en él sin verificarlo.

## Convenciones del proyecto

- Código y comentarios en español, sin tildes en los identificadores.
- Cada sprint vive en su propio paquete con `README.md` y pruebas ejecutables.
- Las pruebas no tocan la red: `python extractor/test_offline.py`.
- Ningún dato entra al almacén sin `source_url`, `extracted_at` y `raw_snippet`.
- El LLM nunca calcula una probabilidad, una cuota ni un stake.
