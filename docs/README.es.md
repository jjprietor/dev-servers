# Dev Servers

Una app de barra de menús para ver, detener y relanzar servidores locales, agrupados por proyecto y worktree. Muestra puertos, CPU, memoria y tiempo de ejecución.

Interfaz nativa en SwiftUI, backend Python sin dependencias externas. Sin cuenta ni telemetría.

[English](../README.md)

## Instalación

Requiere macOS 14 o superior, Python 3.9 o superior y las Command Line Tools de Xcode.

```sh
xcode-select --install  # si todavía no tienes las herramientas de Apple
git clone https://github.com/jjprietor/dev-servers.git
cd dev-servers
./install.sh
```

Se instala en `~/Applications/Dev Servers.app` y se abre. Con `./install.sh --login` también se añade al inicio de sesión. La compilación usa una firma ad hoc; no se ofrece todavía una descarga notarizada por Apple.

Para actualizar:

```sh
git pull --ff-only
./install.sh
```

## Uso

- **Detener:** botón de parada de cada fila. Guarda el comando, la carpeta y el entorno para relanzar.
- **Relanzar:** botón de reproducción en *Detenidos recientemente*.
- **Abrir navegador o carpeta:** pasa el cursor sobre la fila. El navegador aparece para tipos de servidor web reconocidos.
- **Copiar comando o PID:** clic derecho.
- **Revisar antiguos:** muestra qué servidores de más de 48 horas se van a detener. Excluye PostgreSQL y posibles huérfanos.
- **Posibles huérfanos:** procesos sin puerto que podrían requerir revisión. Pueden ser tareas válidas; detenerlos pide confirmación individual.

La lista se actualiza al abrir el panel y cada 30 segundos. Naranja indica antigüedad; rojo, al menos 100 % de CPU del árbol. Ninguno demuestra que el proceso sea prescindible.

## Parada y relanzamiento

Los servidores normales reciben `SIGTERM`; si no terminan en cinco segundos, se usa `SIGKILL`. Se comprueba su hora de inicio antes de enviar señales para reducir el riesgo de actuar sobre un PID reutilizado.

PostgreSQL se detiene exclusivamente con su `pg_ctl stop -m fast`. Si falta su configuración o falla la operación, se muestra el error. No se fuerza su parada con señales.

El relanzamiento usa la información guardada y rechaza puertos ocupados o carpetas que ya no existen. Algunos lanzadores reescriben sus argumentos; las comillas o parte del entorno pueden no recuperarse. Que el proceso siga vivo tras dos segundos no garantiza que la aplicación esté lista para recibir peticiones.

## Privacidad

Todo se gestiona localmente. `~/.devservers/stopped.json` guarda variables de entorno que **pueden contener secretos**. Las carpetas de estado tienen permisos `700`; historial, bloqueo y logs, `600`. Las escrituras son atómicas y las acciones de la app y la CLI se serializan.

*Quitar de la lista* elimina el entorno guardado de esa entrada. Los logs permanecen hasta borrarlos manualmente o usar `./uninstall.sh --purge`; también pueden contener información sensible. No compartas la carpeta de estado en un issue.

## Terminal y configuración

```sh
./devservers.py list
./devservers.py json
./devservers.py stop <pid>
./devservers.py start <id>
./devservers.py forget <id>
./devservers.py clean
```

`clean` pide confirmación. `clean --yes` la omite. Los errores devuelven un código de salida distinto de cero.

| Variable | Uso |
| --- | --- |
| `DEVSERVERS_HOME` | Carpeta de estado; por defecto `~/.devservers` |
| `DEVSERVERS_PYTHON` | Ejecutable Python que usará la app |
| `DEVSERVERS_SCRIPT` | Backend alternativo para desarrollo |

Las apps abiertas desde Finder no heredan el entorno de la terminal. Para usar estas variables, lanza el ejecutable desde esa terminal.

## Límites

Solo gestiona procesos de tu usuario en macOS. No gestiona servicios de root ni contenedores Docker. Excluye ejecutables en directorios habituales de apps y del sistema, pero puede mostrar otros servicios TCP que no sean de desarrollo: revisa su comando y carpeta antes de detenerlos.

No supervisa los servidores relanzados, no rota sus logs ni se actualiza automáticamente.

## Desarrollo

```sh
python3 -m unittest discover -s tests -v
DEVSERVERS_INTEGRATION=1 python3 -m unittest discover -s tests -v
./build.sh
```

La prueba de integración crea su propio servidor temporal y comprueba detección, parada y relanzamiento conservando puerto, entorno y carpeta. [Cómo contribuir](../CONTRIBUTING.md).

## Desinstalar

```sh
./uninstall.sh
./uninstall.sh --purge  # elimina también los archivos de estado conocidos y logs
```

La desinstalación no detiene tus servidores de desarrollo.

[Licencia MIT](../LICENSE)
