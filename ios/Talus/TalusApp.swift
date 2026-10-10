import SwiftUI

@main
struct TalusApp: App {
    @StateObject private var api = API()
    @StateObject private var session = SessionModel()
    @StateObject private var health = HealthSync()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(api)
                .environmentObject(session)
                .environmentObject(health)
                .tint(Color(hex: 0x15211D, dark: 0xE4EAE7))
        }
    }
}

/// Whether the app may show data: signed in, or an engine without accounts.
@MainActor
final class SessionModel: ObservableObject {
    enum State: Equatable { case checking, signedIn(email: String?), signedOut(hasAccounts: Bool, signupOpen: Bool), unreachable(String) }

    @Published var state: State = .checking

    func check(_ api: API) async {
        do {
            guard let status = try await api.authStatus() else {
                state = .signedIn(email: nil)
                return
            }
            state = status.account.map { .signedIn(email: $0.email) }
                ?? .signedOut(hasAccounts: status.hasAccounts, signupOpen: status.signupOpen)
        } catch {
            state = .unreachable(error.localizedDescription)
        }
    }

    func signedOut() {
        state = .signedOut(hasAccounts: true, signupOpen: true)
    }
}

struct RootView: View {
    @EnvironmentObject private var api: API
    @EnvironmentObject private var session: SessionModel
    @EnvironmentObject private var health: HealthSync
    @Environment(\.scenePhase) private var phase

    var body: some View {
        Group {
            switch session.state {
            case .checking:
                ProgressView()
            case .signedIn:
                MainTabs()
            case .signedOut(let hasAccounts, let signupOpen):
                SignInView(firstAccount: !hasAccounts, signupOpen: signupOpen)
            case .unreachable(let message):
                ServerView(message: message)
            }
        }
        .task { await session.check(api) }
        .onChange(of: phase) { _, now in
            if now == .active, case .signedIn = session.state { Task { await health.syncIfDue(api) } }
        }
    }
}

struct MainTabs: View {
    var body: some View {
        TabView {
            TodayView().tabItem { Label("Today", systemImage: "sun.horizon") }
            LoadView().tabItem { Label("Load", systemImage: "chart.xyaxis.line") }
            ActivitiesView().tabItem { Label("Activity", systemImage: "figure.hiking") }
            SettingsView().tabItem { Label("Settings", systemImage: "gearshape") }
        }
    }
}

struct SignInView: View {
    let firstAccount: Bool
    let signupOpen: Bool
    @EnvironmentObject private var api: API
    @EnvironmentObject private var session: SessionModel
    @State private var email = ""
    @State private var password = ""
    @State private var creating = false
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Email", text: $email)
                        .textContentType(.username).keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("Password", text: $password)
                        .textContentType(creating || firstAccount ? .newPassword : .password)
                } footer: {
                    if firstAccount {
                        Text("No account exists on this Talus yet. The first account owns the data already there.")
                    }
                }
                Section {
                    Button { Task { await submit() } } label: {
                        BusyLabel(title: creating || firstAccount ? "Create account" : "Sign in", busy: busy)
                    }
                        .disabled(email.isEmpty || password.isEmpty || busy)
                    if signupOpen && !firstAccount {
                        Button(creating ? "I have an account" : "Create an account") { creating.toggle() }
                    }
                }
                if let error {
                    Section { Text(error).foregroundStyle(Tone.bad.ink) }
                }
                Section("Engine") {
                    TextField("Server", text: $api.server)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                }
            }
            .navigationTitle(firstAccount ? "Set up Talus" : "Sign in to Talus")
        }
    }

    private func submit() async {
        busy = true
        defer { busy = false }
        do {
            try await api.signIn(email: email, password: password, create: creating || firstAccount)
            error = nil
            await session.check(api)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct ServerView: View {
    let message: String
    @EnvironmentObject private var api: API
    @EnvironmentObject private var session: SessionModel

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(message)
                } footer: {
                    Text("In the Simulator the engine on your Mac is at http://localhost:8000. On an iPhone, use your Mac's address on the same Wi-Fi, for example http://192.168.1.20:8000, and start the engine with `talus serve --host 0.0.0.0`.")
                }
                Section("Engine") {
                    TextField("Server", text: $api.server)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Connect") { Task { await session.check(api) } }
                }
            }
            .navigationTitle("Talus")
        }
    }
}
