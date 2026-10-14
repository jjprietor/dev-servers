#!/usr/bin/env python3
"""Dev Servers — backend de la app de barra de menús. También funciona como CLI.

  devservers.py list         tabla en terminal
  devservers.py json         estado completo en JSON (para la app nativa)
  devservers.py stop <pid>   detiene el server (y su árbol) y lo guarda para relanzar
  devservers.py start <id>   relanza un server detenido
  devservers.py forget <id>  lo quita de "detenidos"
  devservers.py clean        revisa y detiene servers viejos (> OLD_HOURS), salvo PostgreSQL
"""
import ctypes
import fcntl
import json
import os
import re
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
from contextlib import contextmanager
import time
import uuid
from pathlib import Path

HOME = Path.home()
STATE_DIR = Path(os.environ.get("DEVSERVERS_HOME", HOME / ".devservers"))
STOPPED_FILE = STATE_DIR / "stopped.json"
LOG_DIR = STATE_DIR / "logs"

OLD_HOURS = 48          # un server activo más tiempo que esto se marca como viejo
HOT_CPU = 100.0         # % de CPU del árbol a partir del cual se marca en rojo
ORPHAN_MIN_HOURS = 1    # antigüedad mínima para mostrar un posible huérfano
MAX_STOPPED = 20

# Ejecutables que lanzan o envuelven a un server; subimos por el árbol mientras el padre sea uno de estos.
LAUNCHERS = {"npm", "npx", "pnpm", "yarn", "bun", "bunx", "deno", "tsx", "turbo",
             "nodemon", "uv", "poetry", "railway", "dotenv"}
# Candidatos a huérfanos: la ausencia de puerto no demuestra que estén colgados.
ORPHAN_EXES = {"node", "bun", "deno", "railway", "esbuild"}
SYSTEM_PREFIXES = ("/System/", "/usr/libexec/", "/usr/sbin/", "/sbin/", "/Library/",
                   "/Applications/", str(HOME / "Applications") + "/", str(HOME / "Library") + "/")
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# ---------------------------------------------------------------- procesos

_libc = ctypes.CDLL("/usr/lib/libc.dylib", use_errno=True)


def _sysctl(mib, size):
    arr = (ctypes.c_int * len(mib))(*mib)
    buf = ctypes.create_string_buffer(size)
    sz = ctypes.c_size_t(size)
    if _libc.sysctl(arr, len(mib), buf, ctypes.byref(sz), None, ctypes.c_size_t(0)) != 0:
        return None
    return buf.raw[:sz.value]


_ARGMAX = struct.unpack("i", _sysctl([1, 8], 4) or struct.pack("i", 262144))[0]  # CTL_KERN, KERN_ARGMAX


def procargs(pid):
    """(exe, argv, env) exactos vía KERN_PROCARGS2, o None si no se puede leer."""
    raw = _sysctl([1, 49, pid], _ARGMAX)  # CTL_KERN, KERN_PROCARGS2
    if not raw or len(raw) < 8:
        return None
    argc = struct.unpack_from("i", raw)[0]
    exe, _, rest = raw[4:].partition(b"\0")
    parts = rest.lstrip(b"\0").split(b"\0")
    argv = [p.decode(errors="replace") for p in parts[:argc]]
    env = {}
    for p in parts[argc:]:
        if not p:
            break
        k, sep, v = p.decode(errors="replace").partition("=")
        if sep:
            env[k] = v
    return exe.decode(errors="replace"), argv, env


def is_mangled(argv):
    """node/npm reescriben su título (process.title): argv[0] pasa a ser 'npm exec next dev -p 3000'."""
    return bool(argv) and " " in argv[0] and not os.path.exists(argv[0]) and not any(argv[1:])


def parse_etime(s):
    days, _, rest = s.rpartition("-")
    nums = [int(x) for x in rest.split(":")]
    while len(nums) < 3:
        nums.insert(0, 0)
    h, m, sec = nums
    return (int(days) if days else 0) * 86400 + h * 3600 + m * 60 + sec


class BackendError(Exception):
    pass


def run(cmd, timeout=10):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, "LC_ALL": "C"})
    except (subprocess.SubprocessError, OSError) as error:
        raise BackendError(f"No se pudo ejecutar {cmd[0]}: {error}") from error
    # lsof y ps -p devuelven 1 cuando no encuentran procesos.
    no_matches = (cmd[0] == "lsof" or (cmd[0] == "ps" and "-p" in cmd))
    if result.returncode and not (no_matches and result.returncode == 1
                                  and not result.stdout.strip() and not result.stderr.strip()):
        raise BackendError(f"{cmd[0]}: {result.stderr.strip() or 'no se pudo consultar el sistema'}")
    return result.stdout


def process_identity(pid):
    fields = run(["ps", "-p", str(pid), "-o", "lstart=,stat="]).split()
    if len(fields) < 6 or fields[-1].startswith("Z"):
        return ""
    return " ".join(fields[:5])


class Snapshot:
    """Foto de los procesos del usuario: tabla de ps, árbol, puertos y cwd."""

    def __init__(self):
        self.uid = os.getuid()
        self.procs = {}
        self.children = {}
        for line in run(["ps", "-axo", "pid=,ppid=,uid=,etime=,rss=,%cpu=,lstart=,command="]).splitlines():
            f = line.split(None, 11)
            if len(f) < 12:
                continue
            pid, ppid, uid = int(f[0]), int(f[1]), int(f[2])
            self.procs[pid] = dict(pid=pid, ppid=ppid, uid=uid, age=parse_etime(f[3]),
                                   rss=int(f[4]), cpu=float(f[5].replace(",", ".")), cmd=f[11], started=" ".join(f[6:11]))
            self.children.setdefault(ppid, []).append(pid)
        self.ports = {}
        out = run(["lsof", "-nP", "-a", "-u", str(self.uid), "-iTCP", "-sTCP:LISTEN", "-Fpn"])
        pid = None
        for line in out.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            elif line.startswith("n") and pid:
                port = int(line.rsplit(":", 1)[1])
                self.ports.setdefault(pid, set()).add(port)
        self._args = {}
        self._cwd = {}

    def args(self, pid):
        if pid not in self._args:
            self._args[pid] = procargs(pid)
        return self._args[pid]

    def exe(self, pid):
        a = self.args(pid)
        if a:
            return a[0]
        cmd = self.procs.get(pid, {}).get("cmd", "")
        return cmd.split()[0] if cmd else ""

    def name(self, pid):
        return os.path.basename(self.exe(pid))

    def load_cwds(self, pids):
        pids = [p for p in pids if p not in self._cwd]
        if not pids:
            return
        out = run(["lsof", "-a", "-d", "cwd", "-p", ",".join(map(str, pids)), "-Fpn"])
        pid = None
        for line in out.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            elif line.startswith("n") and pid:
                self._cwd[pid] = line[1:]

    def cwd(self, pid):
        self.load_cwds([pid])
        return self._cwd.get(pid, "")

    def tree(self, root):
        """Descendientes de root (incluido), padres antes que hijos."""
        out, stack = [], [root]
        while stack:
            p = stack.pop()
            if p in self.procs and p not in out and self.procs[p]["uid"] == self.uid:
                out.append(p)
                stack.extend(self.children.get(p, []))
        return out

    def root_of(self, pid):
        if self.name(pid) == "postgres":
            return pid  # el postmaster es la unidad; lo gestiona pg_ctl
        while True:
            pp = self.procs.get(pid, {}).get("ppid", 1)
            par = self.procs.get(pp)
            if pp <= 1 or not par or par["uid"] != self.uid:
                return pid
            # npm/pnpm aparecen como "node" o con el título reescrito
            pname = self.name(pp)
            ptitle = par["cmd"].split()[0] if par["cmd"] else ""
            title = os.path.basename(ptitle)
            args = self.args(pp)
            # node is a runtime, not necessarily a launcher (editors and agents use it too).
            node_launcher = (pname == "node" and args is not None and len(args[1]) > 1
                             and os.path.basename(args[1][1]) in {
                                 "npm-cli.js", "npx-cli.js", "pnpm.cjs", "pnpm.js", "yarn.js",
                                 "turbo", "nodemon.js", "tsx"})
            if pname not in LAUNCHERS and title not in LAUNCHERS and not node_launcher:
                return pid
            if self.is_system(pp):
                return pid
            pid = pp

    def is_system(self, pid):
        """Apps instaladas y binarios del sistema (Ollama, Conductor, ControlCenter…) no se tocan."""
        exe = self.exe(pid)
        # python.org instala su intérprete en /Library; no es un servicio del sistema.
        if re.fullmatch(r"/Library/Frameworks/Python\.framework/Versions/[^/]+/"
                        r"(?:bin/python[0-9.]*|Resources/Python\.app/Contents/MacOS/Python)", exe):
            return False
        return exe.startswith(SYSTEM_PREFIXES)


def launchd_pids():
    pids = set()
    for line in run(["launchctl", "list"]).splitlines()[1:]:
        f = line.split()
        if f and f[0].isdigit():
            pids.add(int(f[0]))
    return pids


# ---------------------------------------------------------------- descripción

WS_RE = re.compile(r"/conductor/workspaces/([^/]+)/([^/]+)(?:/\.context/([^/]+))?(?:/(?:apps|packages|services)/([^/]+))?")
APP_DIRS = ("apps", "packages", "services")


def project_root(path):
    """Carpeta raíz del repo (la que contiene .git) que contiene a path, o None."""
    p = Path(path)
    for d in [p, *p.parents]:
        if d == HOME or str(d) == "/":
            return None
        if (d / ".git").exists():
            return d
    return None


def describe(cwd, texts):
    """(grupo, nombre) legibles: workspace de Conductor, repo git o carpeta."""
    for t in [cwd, *texts]:
        m = WS_RE.search(t)
        if m:
            repo, ws, ctx, app = m.groups()
            return f"{repo} · {ws}" + (f"/{ctx}" if ctx else ""), app or ws
    paths = [cwd] + [w for t in texts for w in t.split() if w.startswith("/")]
    for path in paths:
        root = project_root(path) if path else None
        if root:
            rel = Path(path).relative_to(root).parts
            app = rel[1] if len(rel) > 1 and rel[0] in APP_DIRS else root.name
            return root.name, app
    return "otros", os.path.basename(cwd.rstrip("/")) or cwd or "?"


def kind_of(cmd):
    c = cmd.lower()
    for key, label in (("next", "next"), ("vite", "vite"), ("astro", "astro"),
                       ("http.server", "http"), ("railway", "railway"), ("tsx", "tsx"), ("uvicorn", "uvicorn"),
                       ("bun", "bun"), ("fake-supabase", "mock")):
        if key in c:
            return label
    return os.path.basename(cmd.split()[0]) if cmd else "?"


def fmt_age(s):
    if s >= 86400:
        return f"{s // 86400}d{(s % 86400) // 3600}h"
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}"
    return f"{s // 60}m"


def fmt_mem(kb):
    return f"{kb / 1048576:.1f}G" if kb >= 1048576 else f"{kb // 1024}M"


# ---------------------------------------------------------------- servers

def collect():
    """Devuelve (servers, huérfanos). Cada uno es un dict con root, árbol, métricas y descripción."""
    snap = Snapshot()
    me = os.getpid()
    roots = {}
    for pid in snap.ports:
        if pid not in snap.procs or snap.is_system(pid):
            continue
        root = snap.root_of(pid)
        if snap.is_system(root) or root == me:
            continue
        roots.setdefault(root, set()).update(snap.ports[pid])
    roots.pop(me, None)

    in_trees = set()
    for r in roots:
        in_trees.update(snap.tree(r))

    managed = launchd_pids()
    orphan_roots = []
    for pid, p in snap.procs.items():
        if (p["ppid"] == 1 and p["uid"] == snap.uid and pid not in in_trees and pid not in managed
                and p["age"] >= ORPHAN_MIN_HOURS * 3600 and not snap.is_system(pid)
                and snap.name(pid) in ORPHAN_EXES):
            orphan_roots.append(pid)

    snap.load_cwds(list(roots) + orphan_roots)

    def build(root, ports):
        tree = snap.tree(root)
        cmd = snap.procs[root]["cmd"]
        texts = [snap.procs[p]["cmd"] for p in tree]
        group, name = describe(snap.cwd(root), texts)
        return dict(root=root, started=snap.procs[root]["started"], tree=tree, ports=sorted(ports), cmd=cmd, kind="postgres" if snap.name(root) == "postgres" else kind_of(" ".join(texts)),
                    group=group, name=name, cwd=snap.cwd(root), age=snap.procs[root]["age"],
                    cpu=sum(snap.procs[p]["cpu"] for p in tree),
                    rss=sum(snap.procs[p]["rss"] for p in tree))

    servers = sorted((build(r, ps) for r, ps in roots.items()), key=lambda s: (s["group"], s["name"]))
    orphans = sorted((build(r, ()) for r in orphan_roots), key=lambda s: -s["age"])
    return snap, servers, orphans


def is_old(s):
    return s["age"] > OLD_HOURS * 3600


# ---------------------------------------------------------------- estado de detenidos

def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise BackendError(f"Carpeta de estado no válida: {path}")
    path.chmod(0o700)


@contextmanager
def state_lock():
    private_dir(STATE_DIR)
    fd = os.open(STATE_DIR / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def load_stopped():
    try:
        items = json.loads(STOPPED_FILE.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise BackendError(f"No se pudo leer {STOPPED_FILE}. Conserva el archivo y revisa su contenido.") from error
    fields = {"id": str, "name": str, "group": str, "kind": str, "cwd": str,
              "argv": list, "env": dict, "ports": list, "stopped_at": int}
    if not isinstance(items, list):
        raise BackendError("El historial no contiene una lista válida.")
    for item in items:
        if (not isinstance(item, dict)
                or any(not isinstance(item.get(k), t) for k, t in fields.items())
                or not re.fullmatch(r"[a-f0-9]{8}", item["id"])
                or not item["argv"] or not all(isinstance(a, str) and "\0" not in a for a in item["argv"])
                or not all(isinstance(k, str) and isinstance(v, str) and "=" not in k
                           and "\0" not in k + v for k, v in item["env"].items())
                or not all(type(port) is int and 0 < port < 65536 for port in item["ports"])):
            raise BackendError("El historial contiene una entrada no válida; no se ha modificado.")
    return items


def save_stopped(items):
    # El llamador mantiene state_lock durante toda la operación de lectura y escritura.
    private_dir(STATE_DIR)
    fd, name = tempfile.mkstemp(prefix=".stopped-", dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w") as temp:
            json.dump(items[:MAX_STOPPED], temp, indent=1)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(name, STOPPED_FILE)
    finally:
        Path(name).unlink(missing_ok=True)


def restart_spec(snap, s):
    """Cómo relanzar el server: argv + env + cwd exactos del proceso raíz."""
    root = s["root"]
    a = snap.args(root)
    if a is None:
        argv, env = shlex.split(s["cmd"]), {}
    else:
        _, argv, env = a
    if is_mangled(argv):
        argv = shlex.split(argv[0])
    if not env:
        # el título reescrito borra el env del raíz; lo tomamos del primer descendiente legible
        for p in s["tree"][1:]:
            d = snap.args(p)
            if d and d[2]:
                env = d[2]
                break
    return dict(argv=argv, env=env, cwd=s["cwd"])


# ---------------------------------------------------------------- acciones

def notify(title, msg):
    if os.environ.get("DEVSERVERS_QUIET"):
        print(json.dumps({"title": title, "message": msg}))
    else:
        print(f"{title}: {msg}")


def same_process(pid, started):
    return bool(started) and process_identity(pid) == started


def kill_tree(snap, pids, grace=5.0):
    identities = {pid: snap.procs[pid]["started"] for pid in pids}

    def send(pid, sig):
        if same_process(pid, identities[pid]):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    for pid in reversed(pids):
        send(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(same_process(p, identities[p]) for p in pids):
        time.sleep(0.2)
    forced = [p for p in pids if same_process(p, identities[p])]
    for pid in forced:
        send(pid, signal.SIGKILL)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and any(same_process(p, identities[p]) for p in pids):
        time.sleep(0.1)
    if any(same_process(p, identities[p]) for p in pids):
        raise BackendError("Algunos procesos siguen activos. Actualiza la lista antes de reintentar.")
    return forced


def stop_postgres(snap, s):
    a = snap.args(s["root"])
    if not a:
        raise BackendError("No se pudo leer la configuración de PostgreSQL; no se ha detenido.")
    exe, argv, env = a
    data = env.get("PGDATA")
    for index, arg in enumerate(argv):
        if arg in ("-D", "--pgdata") and index + 1 < len(argv):
            data = argv[index + 1]
        elif arg.startswith("--pgdata="):
            data = arg.split("=", 1)[1]
        elif arg.startswith("-D") and len(arg) > 2:
            data = arg[2:]
    if not data:
        raise BackendError("No se encontró PGDATA. Detén PostgreSQL con su gestor habitual.")
    if not os.path.isabs(data):
        data = os.path.join(s["cwd"], data)
    pg_ctl = os.path.join(os.path.dirname(exe), "pg_ctl")
    run([pg_ctl, "stop", "-D", data, "-m", "fast", "-w", "-t", "15"], timeout=20)


def do_stop(snap, s, record=True):
    if not same_process(s["root"], s["started"]):
        raise BackendError("El proceso cambió o ya terminó. Actualiza la lista.")
    entry = None
    if record:
        spec = restart_spec(snap, s)
        if not spec["argv"] or not spec["cwd"]:
            raise BackendError("No se pudo guardar el comando para relanzarlo; no se ha detenido.")
        entry = dict(id=uuid.uuid4().hex[:8], group=s["group"], name=s["name"], kind=s["kind"],
                     ports=s["ports"], stopped_at=int(time.time()), **spec)
        # Guarda antes de detener: un fallo de disco no debe dejar el server sin historial.
        items = load_stopped()
        key = (entry["cwd"], entry["argv"])
        save_stopped([entry] + [e for e in items if (e["cwd"], e["argv"]) != key])
    try:
        if snap.name(s["root"]) == "postgres":
            stop_postgres(snap, s)
            return []
        return kill_tree(snap, s["tree"])
    except (BackendError, OSError):
        if entry and snap.name(s["root"]) == "postgres":
            save_stopped(items)
        raise


def find_by_root(servers, pid):
    return next((s for s in servers if s["root"] == pid), None)


def cmd_stop(pid, expected=None):
    snap, servers, orphans = collect()
    s = find_by_root(servers + orphans, pid)
    if not s:
        notify("Dev Servers", f"El proceso {pid} ya no está activo.")
        return
    if expected is not None and s["started"] != expected:
        raise BackendError("El proceso cambió. Actualiza la lista antes de detenerlo.")
    forced = do_stop(snap, s, record=bool(s["ports"]))
    label = f"{s['name']}" + (f" :{s['ports'][0]}" if s["ports"] else "")
    notify("Detenido", label + (" (forzado con kill -9)" if forced else ""))


def cmd_start(entry_id):
    items = load_stopped()
    e = next((x for x in items if x["id"] == entry_id), None)
    if not e:
        raise BackendError("El servidor ya no está en el historial.")
    snap = Snapshot()
    busy = [p for p in e["ports"] if any(p in ps for ps in snap.ports.values())]
    if busy:
        raise BackendError(f"{e['name']}: el puerto {busy[0]} ya está en uso.")
    if not os.path.isdir(e["cwd"]):
        raise BackendError(f"{e['name']}: la carpeta ya no existe.")
    env = dict(e["env"]) or {"HOME": str(HOME)}
    env.setdefault("PATH", DEFAULT_PATH)
    private_dir(LOG_DIR)
    log_path = LOG_DIR / f"{e['id']}.log"
    fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "ab") as log:
        os.fchmod(log.fileno(), 0o600)
        log.write(f"\n=== {time.strftime('%F %T')} relanzado: {shlex.join(e['argv'])}\n".encode())
        log.flush()
        try:
            proc = subprocess.Popen(e["argv"], cwd=e["cwd"], env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as err:
            raise BackendError(f"{e['name']}: {err}") from err
    time.sleep(2)
    if proc.poll() is not None:
        raise BackendError(f"{e['name']}: revisa el log en {log_path}")
    save_stopped([x for x in items if x["id"] != entry_id])
    notify("Relanzado", f"{e['name']}" + (f" :{e['ports'][0]}" if e["ports"] else ""))


def cmd_forget(entry_id):
    save_stopped([x for x in load_stopped() if x["id"] != entry_id])


def confirm(text):
    script = (f"display dialog {json.dumps(text)} with title \"Dev Servers\" "
              "buttons {\"Cancelar\", \"Limpiar\"} default button \"Cancelar\" cancel button \"Cancelar\"")
    return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0


def cmd_clean(ask=True, expected=None):
    snap, servers, _ = collect()
    # Las bases de datos y los posibles huérfanos requieren revisión individual.
    targets = [s for s in servers if is_old(s) and snap.name(s["root"]) != "postgres"]
    if expected is not None:
        targets = [s for s in targets if [s["root"], s["started"]] in expected]
    if not targets:
        notify("Dev Servers", "Nada que limpiar.")
        return
    lines = [f"• {s['name']} :{s['ports'][0]} ({fmt_age(s['age'])})" for s in targets]
    if ask and not confirm("Se detendrán estos servidores:\n\n" + "\n".join(lines)):
        return
    failures = []
    for s in targets:
        try:
            do_stop(snap, s)
        except (BackendError, OSError) as error:
            failures.append(f"{s['name']}: {error}")
    if failures:
        raise BackendError("Algunos servidores no se detuvieron. " + "; ".join(failures))
    notify("Limpieza hecha", f"{len(targets)} servidores detenidos.")


# ---------------------------------------------------------------- salida

def server_line(s, width):
    port = ",".join(map(str, s["ports"][:2]))
    return (f"{s['name'][:width]:<{width}} {s['kind'][:8]:<9}{(':' + port) if port else '':<7}"
            f"{s['cpu']:>5.0f}%{fmt_mem(s['rss']):>7}{fmt_age(s['age']):>7}")


def fmt_mem_long(kb):
    return f"{kb / 1048576:.1f} GB".replace(".", ",") if kb >= 1048576 else f"{kb // 1024} MB"


def fmt_age_long(s):
    if s >= 86400:
        return f"{s // 86400}d {(s % 86400) // 3600}h"
    if s >= 3600:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{max(s // 60, 1)}m"


def render_json():
    """Estado completo para la app nativa."""
    _, servers, orphans = collect()

    def item(s):
        return dict(root=s["root"], started=s["started"], name=s["name"], group=s["group"], kind=s["kind"], ports=s["ports"],
                    cpu=round(s["cpu"]), mem=fmt_mem_long(s["rss"]), age=fmt_age_long(s["age"]),
                    cwd=s["cwd"], cmd=s["cmd"], old=is_old(s), hot=s["cpu"] >= HOT_CPU,
                    web=s["kind"] in {"next", "vite", "astro", "http", "uvicorn"} and bool(s["ports"]))

    now = int(time.time())
    stopped = [dict(id=e["id"], name=e["name"], group=e["group"], kind=e["kind"], ports=e["ports"],
                    ago=fmt_age_long(now - e["stopped_at"]), command=shlex.join(e["argv"]), cwd=e["cwd"],
                    log=str(LOG_DIR / f"{e['id']}.log") if (LOG_DIR / f"{e['id']}.log").exists() else None)
               for e in load_stopped()[:10]]
    print(json.dumps(dict(
        servers=[item(s) for s in servers], orphans=[item(s) for s in orphans], stopped=stopped,
        cleanable=sum(is_old(s) and s["kind"] != "postgres" for s in servers),
        total_mem=fmt_mem_long(sum(s["rss"] for s in servers)),
        hot=any(s["cpu"] >= HOT_CPU for s in servers + orphans),
    )))


def render_list():
    _, servers, orphans = collect()
    width = min(max([len(s["name"]) for s in servers + orphans] + [12]), 26)
    group = None
    for s in servers:
        if s["group"] != group:
            group = s["group"]
            print(f"\n{group}")
        flag = " 🔥" if s["cpu"] >= HOT_CPU else (" ⏳" if is_old(s) else "")
        print(f"  {s['root']:>6}  {server_line(s, width)}{flag}")
    if orphans:
        print("\nPosibles huérfanos sin puerto (revisar antes de detener)")
        for s in orphans:
            print(f"  {s['root']:>6}  {s['name'][:width]:<{width}} {fmt_age(s['age']):>7}  {s['cmd'][:80]}")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "json":
        render_json()
    elif cmd == "list":
        render_list()
    elif cmd == "stop" and len(argv) > 2:
        cmd_stop(int(argv[2]), argv[3] if len(argv) > 3 else None)
    elif cmd == "start" and len(argv) > 2:
        cmd_start(argv[2])
    elif cmd == "forget" and len(argv) > 2:
        cmd_forget(argv[2])
    elif cmd == "clean":
        expected = json.loads(argv[argv.index("--targets") + 1]) if "--targets" in argv else None
        cmd_clean(ask="--yes" not in argv, expected=expected)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    try:
        if sys.platform != "darwin":
            raise BackendError("Dev Servers requiere macOS.")
        if len(sys.argv) > 1 and sys.argv[1] in {"stop", "start", "forget", "clean"}:
            with state_lock():
                result = main(sys.argv)
        else:
            result = main(sys.argv)
    except (BackendError, OSError, ValueError, IndexError) as error:
        if os.environ.get("DEVSERVERS_QUIET"):
            print(json.dumps({"title": "No se pudo completar la operación", "message": str(error)}))
        else:
            print(f"Error: {error}", file=sys.stderr)
        result = 1
    sys.exit(result)
