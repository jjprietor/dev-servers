# Dev Servers

Una app nativa para la barra de menús de macOS que muestra todos los servidores de desarrollo que tienes corriendo (`next dev`, `vite`, `tsx watch`, postgres locales, `http.server`…) y te deja **detenerlos y relanzarlos con un click**.

Pensada para quien trabaja con muchos proyectos o *worktrees* a la vez y acaba con diez servers olvidados comiéndose la RAM, la batería y el swap.

## Qué hace

- **Ve todo lo que está escuchando** en un puerto TCP con tu usuario, agrupado por proyecto, con CPU, RAM y tiempo encendido.
- **Detiene el server completo**: no solo el proceso que tiene el puerto, también el `npm`/`pnpm` que lo lanzó y todos sus hijos. Si no responde a `SIGTERM` en 5 s, usa `SIGKILL`. Los postgres se paran con `pg_ctl stop -m fast`, sin perder datos.
- **Relanza con un click** lo que detuviste, con el mismo comando, la misma carpeta y las mismas variables de entorno (`PORT`, `DATABASE_URL`…).
- **Avisa de lo que sobra**: marca en naranja los servers con más de 48 h encendidos y en rojo los que pasan del 100 % de CPU.
- **Encuentra procesos huérfanos**: scripts de `node`/`tsx` que quedaron colgados sin puerto durante horas o días.
- **Limpieza en un click** de todo lo viejo y huérfano, con confirmación.

Ignora a propósito todo lo que vive en `/Applications`, `~/Library` y `/System` (Ollama, Docker Desktop, apps del sistema…), así que no puedes cerrar nada importante por error.

## Requisitos

- macOS 14 (Sonoma) o superior
- Xcode o las Command Line Tools (`xcode-select --install`), para compilar
- `python3`; vale el que viene con las Command Line Tools

## Instalación

```sh
git clone https://github.com/jjprietor/dev-servers.git
cd dev-servers
./install.sh --login
```

Esto compila la app, la instala en `~/Applications/Dev Servers.app`, la abre y la añade a los ítems de inicio de sesión. Sin `--login` no se abre sola al iniciar sesión.

Para actualizar: `git pull && ./install.sh`.

## Uso

Haz click en el icono de servidor de la barra de menús; el número que tiene al lado es la cantidad de servers activos.

| Acción | Cómo |
|---|---|
| Detener un server | Botón rojo ⏹ de la fila |
| Abrirlo en el navegador o ver su carpeta | Pasa el ratón por la fila |
| Copiar el comando o el PID | Click derecho en la fila |
| Relanzar uno detenido | Botón ▶︎ en *Detenidos recientemente* |
| Limpiar viejos y huérfanos | Botón del pie del panel (pide confirmación) |

El panel se actualiza cada 30 s y cada vez que lo abres.

### Desde la terminal

El backend también funciona como CLI:

```sh
./devservers.py list          # tabla de servers y huérfanos
./devservers.py stop <pid>    # detiene un server y lo guarda para relanzar
./devservers.py start <id>    # relanza uno detenido
./devservers.py clean         # detiene viejos y huérfanos (pide confirmación)
./devservers.py json          # estado completo en JSON
```

## Cómo funciona

```
Dev Servers.app (SwiftUI, MenuBarExtra)
        │  ejecuta y lee JSON
        ▼
devservers.py (Python, solo librería estándar)
        │  ps · lsof · sysctl(KERN_PROCARGS2) · pg_ctl
        ▼
     tus procesos
```

1. `lsof` lista los puertos en `LISTEN` de tu usuario.
2. Desde cada proceso sube por el árbol mientras el padre sea un *launcher* (`npm`, `pnpm`, `node`, `tsx`, `bun`…). Así encuentra el proceso que realmente lanzaste.
3. Para agruparlos usa la raíz del repo git de cada server (o los workspaces de [Conductor](https://conductor.build), si los usas).
4. Al detener uno, lee su `argv`, su entorno y su carpeta exactos con `sysctl(KERN_PROCARGS2)` y los guarda para poder relanzarlo idéntico.

## Privacidad

Todo es local: no hay red ni telemetría. Para poder relanzar, las variables de entorno de cada server detenido se guardan en `~/.devservers/stopped.json` con permisos `600`. **Pueden incluir secretos** (claves de API, URLs de base de datos), así que no compartas ese archivo. Pulsa *Quitar de la lista* en un server detenido para borrarlo de ahí.

## Configuración

| Variable de entorno | Para qué |
|---|---|
| `DEVSERVERS_HOME` | Carpeta de estado (por defecto `~/.devservers`) |
| `DEVSERVERS_PYTHON` | `python3` concreto que usará la app |
| `DEVSERVERS_SCRIPT` | Usar otro `devservers.py` (útil para desarrollar) |

Los umbrales están al principio de `devservers.py`: `OLD_HOURS` (48), `HOT_CPU` (100) y `ORPHAN_MIN_HOURS` (1).

## Desinstalar

```sh
./uninstall.sh           # quita la app y el ítem de inicio de sesión
./uninstall.sh --purge   # además borra ~/.devservers
```

## Limitaciones

- Solo macOS.
- Solo ve procesos de tu usuario; no puede gestionar servicios de `root` ni contenedores de Docker.
- Al relanzar un server iniciado con `npm`/`pnpm`, se usa el comando visible del proceso (por ejemplo `npm run dev`). Los argumentos con comillas y espacios pueden perderse en ese caso.

## Contribuir

Los issues y PRs son bienvenidos. La app está en `app/DevServers.swift` y el backend en `devservers.py`. Para probar cambios del backend sin recompilar:

```sh
pkill -x DevServers; DEVSERVERS_SCRIPT="$PWD/devservers.py" "$HOME/Applications/Dev Servers.app/Contents/MacOS/DevServers"
```

## Licencia

[MIT](LICENSE)
