import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var api: API
    @EnvironmentObject private var session: SessionModel
    @EnvironmentObject private var health: HealthSync

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    if health.enabled {
                        Button { Task { await health.sync(api) } } label: {
                            BusyLabel(title: health.running ? "Syncing…" : "Sync now", busy: health.running)
                        }
                            .disabled(health.running)
                        Toggle("Sync when Talus opens", isOn: $health.enabled)
                    } else {
                        Button("Connect Apple Health") { Task { await health.turnOn(api) } }
                    }
                    if let status = health.status {
                        Text(status).font(.footnote).foregroundStyle(.secondary)
                    }
                    if let last = health.lastSync {
                        LabeledContent("Last sync", value: last.formatted(.relative(presentation: .named)))
                    }
                } header: {
                    Text("Apple Health")
                } footer: {
                    Text("Sends workouts with route and heart rate, plus HRV, resting heart rate and sleep, to your Talus engine. Apple Watch data is in Health, and so is Garmin data once Garmin Connect is set to write to Health. The first sync reads the last \(HealthSync.firstSyncDays) days.")
                }

                Section("Engine") {
                    TextField("Server", text: $api.server)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .onSubmit { Task { await session.check(api) } }
                    if case .signedIn(let email?) = session.state {
                        LabeledContent("Signed in as", value: email)
                        Button("Sign out", role: .destructive) {
                            Task {
                                await api.signOut()
                                await session.check(api)
                            }
                        }
                    }
                }

                Section {
                    Text("Heart rate thresholds, zones, the AI coach and intervals.icu are set in the Talus web app.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
        }
    }
}
