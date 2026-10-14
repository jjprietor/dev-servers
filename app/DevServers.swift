// Dev Servers — app de barra de menús para ver, detener y relanzar servidores locales.
// La lógica de procesos vive en devservers.py (incluido en el bundle); esta app es solo la interfaz.
import AppKit
import SwiftUI

// MARK: - Modelo

struct Server: Decodable, Identifiable, Hashable {
    let root: Int
    let name, group, kind, cwd, cmd, mem, age: String
    let ports: [Int]
    let cpu: Int
    let old, hot, web: Bool
    var id: Int { root }
    var portLabel: String { ports.prefix(2).map { ":\($0)" }.joined(separator: " ") }
}

struct Stopped: Decodable, Identifiable, Hashable {
    let id, name, group, kind, ago, command, cwd: String
    let ports: [Int]
    let log: String?
}

struct State: Decodable {
    var servers: [Server] = []
    var orphans: [Server] = []
    var stopped: [Stopped] = []
    var cleanable = 0
    var total_mem = ""
    var hot = false
}

struct BackendMessage: Decodable {
    let title, message: String
}

// MARK: - Backend

enum Backend {
    /// Primer python3 disponible; las apps de GUI no heredan el PATH de la terminal.
    static let python: String = {
        let env = ProcessInfo.processInfo.environment["DEVSERVERS_PYTHON"]
        let candidates = [env, "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        return candidates.compactMap { $0 }.first { FileManager.default.isExecutableFile(atPath: $0) }
            ?? "/usr/bin/python3"
    }()

    static let script: String = ProcessInfo.processInfo.environment["DEVSERVERS_SCRIPT"]
        ?? Bundle.main.path(forResource: "devservers", ofType: "py")
        ?? NSHomeDirectory() + "/.devservers/devservers.py"

    static func run(_ args: [String]) async -> Data {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: python)
                p.arguments = [script] + args
                var env = ProcessInfo.processInfo.environment
                env["DEVSERVERS_QUIET"] = "1"
                p.environment = env
                let out = Pipe()
                p.standardOutput = out
                p.standardError = FileHandle.nullDevice
                do { try p.run() } catch { cont.resume(returning: Data()); return }
                let data = out.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                cont.resume(returning: data)
            }
        }
    }
}

// MARK: - Store

@MainActor
final class Store: ObservableObject {
    @Published var state = State()
    @Published var loaded = false
    @Published var busy: Set<String> = []
    @Published var toast: String?
    private var timer: Timer?
    private var toastTask: Task<Void, Never>?

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() {
        Task {
            let data = await Backend.run(["json"])
            if let s = try? JSONDecoder().decode(State.self, from: data) {
                state = s
                loaded = true
            }
        }
    }

    func perform(_ key: String, _ args: [String]) {
        guard !busy.contains(key) else { return }
        busy.insert(key)
        Task {
            let data = await Backend.run(args)
            busy.remove(key)
            let lines = String(decoding: data, as: UTF8.self).split(separator: "\n")
            if let last = lines.last,
               let msg = try? JSONDecoder().decode(BackendMessage.self, from: Data(last.utf8)) {
                show("\(msg.title): \(msg.message)")
            }
            refresh()
        }
    }

    func stop(_ s: Server) { perform("stop-\(s.root)", ["stop", String(s.root)]) }
    func start(_ e: Stopped) { perform("start-\(e.id)", ["start", e.id]) }
    func forget(_ e: Stopped) { perform("forget-\(e.id)", ["forget", e.id]) }
    func clean() { perform("clean", ["clean", "--yes"]) }

    private func show(_ text: String) {
        toast = text
        toastTask?.cancel()
        toastTask = Task {
            try? await Task.sleep(for: .seconds(4))
            if !Task.isCancelled { toast = nil }
        }
    }
}

// MARK: - App

@main
struct DevServersApp: App {
    @StateObject private var store = Store()

    var body: some Scene {
        MenuBarExtra {
            ContentView().environmentObject(store)
        } label: {
            let count = store.state.servers.count
            let alert = store.state.hot || !store.state.orphans.isEmpty
            Image(systemName: alert ? "exclamationmark.triangle" : "server.rack")
            Text("\(count)")
        }
        .menuBarExtraStyle(.window)
    }
}

// MARK: - Vistas

struct ContentView: View {
    @EnvironmentObject var store: Store

    private var groups: [(String, [Server])] {
        var order: [String] = []
        var map: [String: [Server]] = [:]
        for s in store.state.servers {
            if map[s.group] == nil { order.append(s.group) }
            map[s.group, default: []].append(s)
        }
        return order.map { ($0, map[$0]!) }
    }

    var body: some View {
        VStack(spacing: 0) {
            Header()
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if store.loaded && store.state.servers.isEmpty {
                        EmptyState()
                    }
                    ForEach(groups, id: \.0) { group, servers in
                        Section(title: group) {
                            ForEach(servers) { ServerRow(server: $0) }
                        }
                    }
                    if !store.state.orphans.isEmpty {
                        Section(title: "Procesos huérfanos", tint: .orange) {
                            ForEach(store.state.orphans) { OrphanRow(server: $0) }
                        }
                    }
                    if !store.state.stopped.isEmpty {
                        Section(title: "Detenidos recientemente") {
                            ForEach(store.state.stopped) { StoppedRow(entry: $0) }
                        }
                    }
                }
                .padding(.horizontal, 6)
                .padding(.vertical, 10)
            }
            .scrollBounceBehavior(.basedOnSize)
            .frame(maxHeight: 520)
            .fixedSize(horizontal: false, vertical: true)
            Divider()
            Footer()
        }
        .frame(width: 400)
        .onAppear { store.refresh() }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { _ in
            store.refresh()
        }
    }
}

struct Header: View {
    @EnvironmentObject var store: Store

    var body: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Servidores locales").font(.headline)
                Text(summary).font(.subheadline).foregroundStyle(.secondary).monospacedDigit()
            }
            Spacer()
            IconButton(symbol: "arrow.clockwise", help: "Actualizar") { store.refresh() }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
    }

    private var summary: String {
        let n = store.state.servers.count
        guard store.loaded else { return "Cargando…" }
        guard n > 0 else { return "Nada en marcha" }
        return "\(n) \(n == 1 ? "activo" : "activos") · \(store.state.total_mem)"
    }
}

struct Section<Content: View>: View {
    let title: String
    var tint: Color = .secondary
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(tint)
                .padding(.horizontal, 8)
                .padding(.bottom, 2)
            content
        }
    }
}

struct EmptyState: View {
    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: "checkmark.circle").font(.system(size: 28)).foregroundStyle(.green)
            Text("No hay servidores activos").font(.callout.weight(.medium))
            Text("Cuando arranques uno aparecerá aquí.").font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 18)
    }
}

/// Fila base con resaltado al pasar el ratón, como en los menús del sistema.
struct HoverRow<Content: View>: View {
    @ViewBuilder let content: (Bool) -> Content
    @SwiftUI.State private var hovering = false

    var body: some View {
        content(hovering)
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
            .background(
                RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .fill(hovering ? Color.primary.opacity(0.07) : .clear)
            )
            .onHover { hovering = $0 }
    }
}

struct ServerRow: View {
    @EnvironmentObject var store: Store
    let server: Server

    private var status: Color { server.hot ? .red : server.old ? .orange : .green }

    var body: some View {
        HoverRow { hovering in
            HStack(spacing: 10) {
                Circle().fill(status.gradient).frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(server.name).font(.body.weight(.medium)).lineLimit(1)
                        Badge(text: server.kind)
                    }
                    HStack(spacing: 4) {
                        Text(server.portLabel)
                        Text("·")
                        Text(server.age).foregroundStyle(server.old ? AnyShapeStyle(.orange) : AnyShapeStyle(.secondary))
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                }
                Spacer(minLength: 8)
                if hovering {
                    if server.web, let port = server.ports.first {
                        IconButton(symbol: "safari", help: "Abrir localhost:\(port)") {
                            NSWorkspace.shared.open(URL(string: "http://localhost:\(port)")!)
                        }
                    }
                    IconButton(symbol: "folder", help: "Mostrar carpeta") {
                        NSWorkspace.shared.open(URL(fileURLWithPath: server.cwd))
                    }
                } else {
                    VStack(alignment: .trailing, spacing: 2) {
                        Text("\(server.cpu) %")
                            .foregroundStyle(server.hot ? AnyShapeStyle(.red) : AnyShapeStyle(.secondary))
                        Text(server.mem).foregroundStyle(.secondary)
                    }
                    .font(.caption)
                    .monospacedDigit()
                }
                StopButton(busy: store.busy.contains("stop-\(server.root)"), help: "Detener") {
                    store.stop(server)
                }
            }
        }
        .contextMenu { ServerMenu(server: server) }
        .help(server.cmd)
    }
}

struct ServerMenu: View {
    @EnvironmentObject var store: Store
    let server: Server

    var body: some View {
        if server.web, let port = server.ports.first {
            Button("Abrir localhost:\(port)") { NSWorkspace.shared.open(URL(string: "http://localhost:\(port)")!) }
        }
        Button("Mostrar carpeta") { NSWorkspace.shared.open(URL(fileURLWithPath: server.cwd)) }
        Button("Copiar comando") { copy(server.cmd) }
        Button("Copiar PID (\(server.root))") { copy(String(server.root)) }
        Divider()
        Button("Detener", role: .destructive) { store.stop(server) }
    }
}

struct OrphanRow: View {
    @EnvironmentObject var store: Store
    let server: Server

    var body: some View {
        HoverRow { _ in
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(server.name).font(.body.weight(.medium)).lineLimit(1)
                        Badge(text: server.kind)
                    }
                    Text("Sin puerto · \(server.age) colgado")
                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                }
                Spacer(minLength: 8)
                Text(server.mem).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                StopButton(busy: store.busy.contains("stop-\(server.root)"), help: "Matar proceso") {
                    store.stop(server)
                }
            }
        }
        .contextMenu { Button("Copiar comando") { copy(server.cmd) } }
        .help(server.cmd)
    }
}

struct StoppedRow: View {
    @EnvironmentObject var store: Store
    let entry: Stopped

    var body: some View {
        HoverRow { hovering in
            HStack(spacing: 10) {
                Circle().strokeBorder(.secondary, lineWidth: 1.2).frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(entry.name).font(.body).foregroundStyle(.secondary).lineLimit(1)
                        Badge(text: entry.kind)
                    }
                    Text(([entry.ports.first.map { ":\($0)" }, "hace \(entry.ago)"] as [String?]).compactMap { $0 }
                        .joined(separator: " · "))
                        .font(.caption).foregroundStyle(.tertiary).monospacedDigit()
                }
                Spacer(minLength: 8)
                if hovering {
                    IconButton(symbol: "xmark", help: "Quitar de la lista") { store.forget(entry) }
                }
                ActionButton(symbol: "play.fill", tint: .accentColor,
                             busy: store.busy.contains("start-\(entry.id)"), help: "Relanzar") {
                    store.start(entry)
                }
            }
        }
        .contextMenu {
            Button("Relanzar") { store.start(entry) }
            if let log = entry.log {
                Button("Ver log") { NSWorkspace.shared.open(URL(fileURLWithPath: log)) }
            }
            Button("Copiar comando") { copy(entry.command) }
            Divider()
            Button("Quitar de la lista") { store.forget(entry) }
        }
        .help("\(entry.group)\n\(entry.command)")
    }
}

struct Footer: View {
    @EnvironmentObject var store: Store
    @SwiftUI.State private var confirming = false

    var body: some View {
        HStack(spacing: 10) {
            if let toast = store.toast {
                Text(toast).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    .transition(.opacity)
            } else if store.state.cleanable > 0 {
                if confirming {
                    Button("Detener \(store.state.cleanable)") {
                        confirming = false
                        store.clean()
                    }
                    .buttonStyle(.borderedProminent).tint(.red).controlSize(.small)
                    Button("Cancelar") { confirming = false }.controlSize(.small)
                } else {
                    Button {
                        confirming = true
                        Task { try? await Task.sleep(for: .seconds(5)); confirming = false }
                    } label: {
                        Label("Limpiar viejos y huérfanos (\(store.state.cleanable))", systemImage: "sparkles")
                    }
                    .controlSize(.small)
                    .disabled(store.busy.contains("clean"))
                }
            } else {
                Label("Nada viejo que limpiar", systemImage: "checkmark.circle")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Menu {
                Button("Abrir carpeta de logs") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: NSHomeDirectory() + "/.devservers"))
                }
                Divider()
                Button("Salir de Dev Servers") { NSApp.terminate(nil) }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
        }
        .animation(.easeOut(duration: 0.15), value: store.toast)
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
    }
}

// MARK: - Componentes

struct Badge: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Capsule().fill(.quaternary))
    }
}

struct IconButton: View {
    let symbol: String
    let help: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .medium))
                .frame(width: 22, height: 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .foregroundStyle(.secondary)
        .help(help)
    }
}

struct ActionButton: View {
    let symbol: String
    let tint: Color
    let busy: Bool
    let help: String
    let action: () -> Void
    @SwiftUI.State private var hovering = false

    var body: some View {
        Button(action: action) {
            ZStack {
                if busy {
                    ProgressView().controlSize(.small).scaleEffect(0.7)
                } else {
                    Image(systemName: symbol).font(.system(size: 10, weight: .bold))
                }
            }
            .frame(width: 22, height: 22)
            .foregroundStyle(hovering ? .white : tint)
            .background(Circle().fill(hovering ? tint : tint.opacity(0.14)))
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .disabled(busy)
        .onHover { hovering = $0 }
        .help(help)
    }
}

struct StopButton: View {
    let busy: Bool
    let help: String
    let action: () -> Void

    var body: some View {
        ActionButton(symbol: "stop.fill", tint: .red, busy: busy, help: help, action: action)
    }
}

func copy(_ text: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
}
