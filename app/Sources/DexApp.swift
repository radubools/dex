import SwiftUI

@main
struct DexApp: App {
    @StateObject private var servers = Servers()
    @StateObject private var page = Page()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(servers)
                .environmentObject(page)
        }
        #if os(macOS)
        .commands {
            CommandMenu("Server") {
                Button("Choose Server…") { page.choosing = true }
                    .keyboardShortcut("s", modifiers: [.command, .shift])
                Button("Reload") { page.reload() }
                    .keyboardShortcut("r", modifiers: .command)
            }
        }
        #endif
    }
}

/// The web view's state the rest of the app needs: whether the server sheet
/// is up, the last load error, and a way to reload.
@MainActor
final class Page: ObservableObject {
    @Published var choosing = false
    @Published var error: String?
    var reload: () -> Void = {}
}

struct ContentView: View {
    @EnvironmentObject private var servers: Servers
    @EnvironmentObject private var page: Page

    var body: some View {
        Group {
            if let server = servers.current {
                ZStack {
                    DexWebView(url: server.url)
                        .id(server.url) // a new server is a fresh page
                        .ignoresSafeArea(.container, edges: .bottom)
                    if let error = page.error {
                        failure(error, server: server)
                    }
                }
            } else {
                ServerList()
            }
        }
        .sheet(isPresented: $page.choosing) {
            NavigationStack { ServerList(dismissable: true) }
                #if os(macOS)
                .frame(minWidth: 420, minHeight: 360)
                #endif
        }
    }

    private func failure(_ message: String, server: DexServer) -> some View {
        VStack(spacing: 12) {
            Text("Can't reach \(server.name)").font(.headline)
            Text(message).font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Text("Is Tailscale connected, and the dex server running?").font(.callout).foregroundStyle(.secondary)
            HStack {
                Button("Retry") { page.error = nil; page.reload() }
                Button("Choose Server…") { page.choosing = true }
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.background)
    }
}

struct ServerList: View {
    var dismissable = false
    @EnvironmentObject private var servers: Servers
    @EnvironmentObject private var page: Page
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var address = ""

    var body: some View {
        List {
            Section("dex servers on your tailnet") {
                ForEach(servers.all) { server in
                    Button {
                        open(server)
                    } label: {
                        HStack {
                            VStack(alignment: .leading) {
                                Text(server.name)
                                Text(server.url.absoluteString).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if server == servers.current { Image(systemName: "checkmark") }
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .contextMenu { Button("Remove", role: .destructive) { servers.remove(server) } }
                }
                .onDelete { $0.map { servers.all[$0] }.forEach(servers.remove) }
            }
            Section("Add a server") {
                TextField("Name (optional)", text: $name)
                TextField("Address, e.g. dex.example.ts.net:4317", text: $address)
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                    #endif
                    .autocorrectionDisabled()
                Button("Add and open") {
                    if let server = servers.add(name: name, address: address) {
                        name = ""; address = ""
                        open(server)
                    }
                }
                .disabled(address.isEmpty)
            }
            #if os(iOS)
            Section {
                Text("Two-finger double tap anywhere in dex to come back here.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            #endif
        }
        .navigationTitle("Dex")
        .toolbar {
            if dismissable {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
            }
        }
    }

    private func open(_ server: DexServer) {
        page.error = nil
        servers.current = server
        if dismissable { dismiss() }
    }
}
