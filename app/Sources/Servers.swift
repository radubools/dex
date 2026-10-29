import Foundation

/// A dex server the app can open: its page, served by dex itself.
struct DexServer: Codable, Hashable, Identifiable {
    var name: String
    var url: URL
    var id: URL { url }
}

/// The servers to choose from, and which one is open. Kept in UserDefaults,
/// so the app reopens where it was.
@MainActor
final class Servers: ObservableObject {
    /// The dex servers on the tailnet, by the page each serves. The MacBook
    /// Pro and the dex server are opened on the API's own port, 4317, which
    /// serves the built UI and the API from one origin; the Air's dex has its
    /// dev UI on 4318, which relays the API to 4317. Any other is added from
    /// the server sheet.
    static let presets: [DexServer] = [
        DexServer(name: "MacBook Pro 4", url: URL(string: "http://macbook-pro-4.tail4c49c.ts.net:4317/")!),
        DexServer(name: "Adrian's MacBook Air", url: URL(string: "http://adrians-macbook-air-m5.tailf2ae8.ts.net:4318/")!),
        DexServer(name: "dex (tailnet server)", url: URL(string: "http://dex.tailf2ae8.ts.net:4317/")!),
    ]

    @Published private(set) var all: [DexServer]
    @Published var current: DexServer? { didSet { save() } }

    private let defaults = UserDefaults.standard
    private static let listKey = "dex.servers"
    private static let currentKey = "dex.current"
    /// Presets this install has already been offered, so a preset added in a
    /// later version appears in a saved list, and one the reader removed
    /// stays removed.
    private static let offeredKey = "dex.offeredPresets"
    /// Addresses a preset used to have, and what it is now. A saved list and
    /// the open server follow the move, keeping the name the reader sees.
    private static let moved: [String: String] = [
        "http://macbook-pro-4.tail4c49c.ts.net:4318/": "http://macbook-pro-4.tail4c49c.ts.net:4317/",
    ]

    init() {
        let saved = (defaults.data(forKey: Self.listKey))
            .flatMap { try? JSONDecoder().decode([DexServer].self, from: $0) }
            .map { list in
                var seen = Set<URL>()
                return list.map { server in
                    guard let to = Self.moved[server.url.absoluteString], let url = URL(string: to) else { return server }
                    return DexServer(name: server.name, url: url)
                }
                .filter { seen.insert($0.url).inserted }
            }
        if let from = defaults.url(forKey: Self.currentKey),
           let to = Self.moved[from.absoluteString].flatMap(URL.init(string:)) {
            defaults.set(to, forKey: Self.currentKey)
        }
        var list = saved ?? Self.presets
        let offered = Set(defaults.stringArray(forKey: Self.offeredKey)
            ?? (saved ?? []).map(\.url.absoluteString))
        for preset in Self.presets where !offered.contains(preset.url.absoluteString) && !list.contains(where: { $0.url == preset.url }) {
            list.append(preset)
        }
        defaults.set(Self.presets.map(\.url.absoluteString) + Array(offered), forKey: Self.offeredKey)
        all = list
        // First launch opens the dex host straight away.
        current = defaults.url(forKey: Self.currentKey).flatMap { url in all.first { $0.url == url } }
            ?? (defaults.object(forKey: Self.currentKey) == nil ? all.first : nil)
        // didSet does not run in init, so a moved address or a new preset
        // would otherwise be redone in memory on every launch and never kept.
        save()
    }

    /// Adds a server from what was typed: a host, host:port or a full URL.
    /// Without a scheme it is http, as dex serves on the tailnet.
    @discardableResult
    func add(name: String, address: String) -> DexServer? {
        var text = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.contains("://") { text = "http://" + text }
        if !text.hasSuffix("/") { text += "/" }
        guard let url = URL(string: text), url.host != nil else { return nil }
        let server = DexServer(name: name.isEmpty ? (url.host ?? text) : name, url: url)
        if !all.contains(server) { all.append(server) }
        save()
        return server
    }

    func remove(_ server: DexServer) {
        all.removeAll { $0 == server }
        if current == server { current = nil }
        save()
    }

    private func save() {
        if let data = try? JSONEncoder().encode(all) { defaults.set(data, forKey: Self.listKey) }
        defaults.set(current?.url, forKey: Self.currentKey)
    }
}
