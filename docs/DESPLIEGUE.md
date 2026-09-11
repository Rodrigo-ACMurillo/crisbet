# Publicar Crisbet en GitHub Pages

Guía paso a paso. Cada paso lleva las trampas que tiene, porque varias no dan
error claro: simplemente no pasa nada y cuesta entender por qué.

---

## Antes de empezar: público o privado

Esta decisión condiciona los pasos 3 y 5, y conviene tomarla ahora.

| | Repo **público** | Repo **privado** |
|---|---|---|
| GitHub Pages | Sí, gratis | **No** en el plan Free (exige Pro, 4 USD/mes) |
| Minutos de Actions | Ilimitados | 2.000/mes gratis |
| ¿Quién ve los tickets? | Cualquiera con el enlace | Solo tú |

El ciclo diario consume unos 15-20 minutos por corrida: ~600 min/mes. **En
privado con plan Free no cabe** (2.000 min suena a mucho, pero además Pages no
funcionaría). Así que:

- **Solo para ti** → repo privado + plan Free, pero **sin Pages**: el workflow
  genera los PNG y el sitio como *artefactos* descargables desde la pestaña
  Actions. Funciona, solo que no hay URL pública.
- **Con enlace compartible** → repo público. Entonces el aviso legal del pie de
  la página deja de ser decorativo: ver la sección final.

---

## Paso 1 · Crear el repo y hacer el primer push

El repo `Rodrigo-ACMurillo/crisbet` ya existe y ya hay un commit local
(`first up`) en la rama `master`. Lo que falla es la autenticación.

### El problema actual

```
remote: Permission to Rodrigo-ACMurillo/crisbet.git denied to Sr-Claude.
```

Git se autentica con un token guardado del usuario **`Sr-Claude`**, que no tiene
permiso de escritura en un repo de **`Rodrigo-ACMurillo`**. El `user.name` de git
(que también dice "Sr Claude") **no es la causa**: eso solo firma los commits.

Lo enturbia que haya **tres gestores de credenciales** configurados a la vez:

```bash
git config --get-all credential.helper
# manager
# store
# store
```

`manager` es el Administrador de credenciales de Windows y `store` es el fichero
`C:\Users\LENOVO\.git-credentials`. Borrar la credencial en uno **no basta**,
porque el otro la vuelve a ofrecer.

### Opción A — el repo es tuyo (`Rodrigo-ACMurillo`)

1. **Crea un token** en <https://github.com/settings/tokens> con la cuenta
   `Rodrigo-ACMurillo`:
   - *Tokens (classic)* → *Generate new token (classic)*
   - Scope: marca **`repo`** (basta ese; no hace falta `workflow` salvo que
     vayas a editar los ficheros de `.github/workflows/` desde otra máquina)
   - Copia el token: **solo se muestra una vez**

2. **Borra la credencial vieja** de los dos sitios:

   ```bash
   # fichero
   git credential-store --file ~/.git-credentials erase <<< "protocol=https
   host=github.com"

   # Administrador de credenciales de Windows
   cmdkey /delete:git:https://github.com
   ```

   Si `cmdkey` no la encuentra, ábrelo a mano: *Panel de control → Cuentas de
   usuario → Administrador de credenciales → Credenciales de Windows*, y borra
   las entradas que empiecen por `git:https://github.com`.

3. **Fuerza el usuario en la URL** para que no vuelva a coger el equivocado:

   ```bash
   git remote set-url origin https://Rodrigo-ACMurillo@github.com/Rodrigo-ACMurillo/crisbet.git
   ```

4. **Push.** Pedirá usuario y contraseña: el usuario es `Rodrigo-ACMurillo` y la
   **contraseña es el token**, no tu contraseña de GitHub.

   ```bash
   git push -u origin master
   ```

### Opción B — quieres seguir usando `Sr-Claude`

Desde la cuenta `Rodrigo-ACMurillo`, en el repo: *Settings → Collaborators →
Add people* → invita a `Sr-Claude`. Esa cuenta tiene que **aceptar la invitación**
(le llega por correo y a <https://github.com/notifications>). Después el push
funciona sin tocar credenciales.

### Trampa: `master` frente a `main`

GitHub crea los repos nuevos con la rama por defecto **`main`**, y tú estás en
**`master`**. Si al crear el repo marcaste "Add a README", el repo tiene `main`
y tu push creará una rama `master` **paralela**, no la principal.

Eso importa mucho aquí, porque **los workflows solo se ejecutan desde la rama por
defecto**: ni el cron ni el botón "Run workflow" aparecerían.

Comprueba en *Settings → General → Default branch* qué rama es la principal. Si
dice `main` y tu código está en `master`, lo más simple es renombrar la local
antes del push:

```bash
git branch -M main
git push -u origin main
```

### Comprobación antes de pulsar

```bash
git status --porcelain          # debe estar vacío
git ls-files | wc -l            # 69 ficheros
git ls-files | grep -c "^\.env" # debe imprimir 0
```

Si ese último número **no es 0, para**: el `.env` con tu contraseña de Betplay
entraría al repo. Y una vez en el historial, borrarlo después no lo elimina —
queda en todos los clones y en la caché de GitHub.

---

## Paso 2 · Guardar el token de football-data.org como secreto

El workflow necesita `FOOTBALL_DATA_TOKEN` para liquidar los tickets (consulta
los resultados de los partidos). **Nunca va en el repo**: va en los secretos,
que GitHub cifra y oculta incluso en los logs.

1. En el repo: *Settings* (el del repo, no el de tu cuenta)
2. Barra lateral: *Secrets and variables* → **Actions**
3. Botón *New repository secret*
4. **Name**: `FOOTBALL_DATA_TOKEN` — exactamente así, en mayúsculas
5. **Secret**: el token de football-data.org
6. *Add secret*

El nombre tiene que coincidir con el del workflow:

```yaml
env:
  FOOTBALL_DATA_TOKEN: ${{ secrets.FOOTBALL_DATA_TOKEN }}
```

**Trampa:** si el nombre no coincide, no hay error. La variable llega vacía y el
paso de liquidar dice *"Falta FOOTBALL_DATA_TOKEN"* y falla — pero el resto del
workflow ya habrá corrido, así que se publica un sitio con tickets nuevos y el
historial congelado. Si ves que el ROI nunca se actualiza, mira aquí.

**Aparte:** rota ese token antes de guardarlo. Estuvo pegado en un chat, y
regenerarlo en football-data.org es gratis e instantáneo.

---

## Paso 3 · Activar Pages

*Settings → Pages → Build and deployment → Source*: elige **GitHub Actions**
(no "Deploy from a branch").

Esto es lo que hace que el paso `actions/deploy-pages@v4` del workflow tenga
dónde publicar. Con "Deploy from a branch" el workflow falla con un error de
permisos que no explica nada.

**Si el repo es privado y estás en plan Free**, aquí no habrá opción de Pages.
No es un fallo tuyo: es la limitación de la tabla del principio. Entonces borra
el job `publicar` del workflow y quédate con los artefactos:

```yaml
# elimina el job "publicar" entero y sustituye el último paso de "generar" por:
      - uses: actions/upload-artifact@v4
        with:
          name: sitio
          path: _sitio
```

La URL, cuando funciona, es
`https://rodrigo-acmurillo.github.io/crisbet/` y tarda 1-2 minutos en aparecer
tras el primer despliegue.

---

## Paso 4 · Lanzar el sondeo a mano

**Este es el paso que decide si todo lo demás sirve**, y por eso está separado.

1. Pestaña **Actions** del repo
2. Barra lateral izquierda: **Sondeo del feed**
3. Botón **Run workflow** (arriba a la derecha) → *Run workflow*
4. Espera ~1 minuto y abre la ejecución

### Qué estás comprobando

La captura de cuotas funciona desde tu PC, en Colombia. Los runners de GitHub
son **IPs de datacenter en Estados Unidos**, y Kambi —la plataforma sobre la que
corre Betplay— filtra por origen: durante la investigación devolvió `429` en
`eu-offering-api` y `403` en `settings-api`. Si también bloquea a los runners,
`capturar.py` fallará todos los días.

### Si el sondeo pasa

Verás las tres pruebas en verde y el veredicto:

> El feed responde desde aquí: la captura puede ejecutarse en este entorno.

Sigue al paso 5.

### Si el sondeo falla

Verás códigos `403` o `429` y:

> El feed NO responde desde aquí.

No es el final, pero cambia el reparto: **la captura corre en tu PC y GitHub solo
publica.**

1. En tu máquina, programa la captura con el Programador de tareas de Windows:

   ```
   Programa:    C:\...\python.exe
   Argumentos:  L:\crisbet\diario.py --solo capturar
   Iniciar en:  L:\crisbet
   ```

2. Sube lo capturado al repo, o ejecuta el ciclo completo en local y sube solo
   `_sitio/`. Dímelo y adapto el workflow para ese reparto.

---

## Paso 5 · Dejar que el cron haga el resto

El workflow `Tickets diarios` se ejecuta solo a las **11:00 UTC = 06:00 en
Colombia**, antes de los partidos europeos de la tarde. Para cambiarlo, edita la
línea del cron (siempre en UTC):

```yaml
on:
  schedule:
    - cron: "0 11 * * *"    # minuto hora día mes día-de-semana
```

### Cosas que conviene saber del cron de GitHub

- **Solo corre en la rama por defecto.** Si el workflow está en otra rama, no se
  ejecuta nunca y no avisa.
- **Se retrasa.** GitHub encola los cron en horas punta; retrasos de 5-20 minutos
  son normales. No lo pongas a 5 minutos de la hora de cierre de un mercado.
- **Se desactiva solo** en repos públicos tras **60 días sin actividad**. Llega
  un correo avisando. Un commit cualquiera lo reactiva.
- Puedes lanzarlo a mano cuando quieras desde *Actions → Tickets diarios → Run
  workflow*, y ahí te deja elegir cuota objetivo y número de tickets.

### El libro de tickets y por qué se cachea

El historial (`papel/libro/tickets.parquet`) **no está en el repo**: lo excluye el
`.gitignore` junto al resto de datos generados. El workflow lo guarda con
`actions/cache` y lo restaura en cada corrida.

Sin eso, cada ejecución empezaría con el libro vacío y el ROI real no se
acumularía nunca — que es lo único capaz de decir si el sistema gana o pierde.

**Trampa:** la caché de Actions **caduca a los 7 días sin uso**. Si el workflow
se para dos semanas, el historial se pierde. Por eso también se sube como
artefacto con 90 días de retención: si eso pasa, descargas `libro-tickets` de la
última ejecución buena y lo restauras en `papel/libro/`.

Si el proyecto va en serio, lo correcto es sacar el libro a un sitio de verdad
—una base de datos o un bucket— en vez de depender de una caché de CI.

---

## Qué esperar la primera semana

| Cuándo | Qué verás |
|---|---|
| Día 1 | 5 tickets, ROI "—", historial vacío |
| Días 2-3 | Primeros tickets liquidados, ROI muy ruidoso |
| Semana 1 | ~35 tickets: el ROI sigue sin significar nada |
| Mes 1 | ~150 tickets: empieza a distinguirse una tendencia |
| Mes 3+ | ~450 tickets: la cifra sostiene una decisión |

La página dice esto explícitamente mientras haya menos de 100 tickets
liquidados, en vez de enseñar un ROI prematuro como si fuera un resultado. Si el
primer día sale +300%, es un acierto con suerte, no una validación.

---

## Si el repo va a ser público

El pie de la página ya incluye lo que exige el Sprint 0 del plan: aviso de
mayoría de edad, línea de juego responsable (018000 113 113), y la declaración
de que el servicio **no gestiona ni custodia dinero de terceros** — que es la
condición para que un servicio de pronósticos sea legal en Colombia sin licencia
de Coljuegos.

Tres cosas que revisar tú antes de compartir el enlace:

1. La página lleva `<meta name="robots" content="noindex">`. Quítalo solo si
   quieres que Google la indexe, con lo que eso implica.
2. No hay verificación de edad real, solo un aviso. Si esto pasa de experimento
   a producto con suscriptores, hace falta de verdad (Sprint 9 del plan).
3. El aviso dice que el modelo **no ha demostrado batir al mercado**. Es cierto y
   está medido. No lo quites: es la diferencia entre publicar un experimento y
   vender una expectativa que los datos no respaldan.
