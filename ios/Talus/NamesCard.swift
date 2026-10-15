import SwiftUI

/// GET /api/names/backfill: whether the coach names activities, and the one-time pass over past ones.
struct NamesStatus: Codable, Hashable {
    let enabled: Bool
    let pending: Int
    let estimateUsd: Double
    let running: Bool
    let named: Int
    let total: Int

    var cost: String { estimateUsd < 0.01 ? "under a cent" : String(format: "about $%.2f", estimateUsd) }
    var pendingText: String { "\(pending) past \(pending == 1 ? "activity" : "activities")" }
}

/// AI activity names: on or off, and a one-time pass that names past activities (web/components/NamesCard.tsx).
struct NamesCard: View {
    @EnvironmentObject private var api: API
    @State private var s: NamesStatus?
    @State private var error: String?
    @State private var confirming = false

    var body: some View {
        Group {
            if let s {
                Card {
                    SectionLabel("Activity names")
                    Toggle(isOn: Binding(get: { s.enabled }, set: { on in Task { await toggle(on) } })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Let the coach name activities").fontWeight(.semibold)
                            Text("Replaces names like “Morning Run” with the peak or place and the kind of day. A name you typed, here or on your watch, is kept.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if s.running {
                        Text("Naming past activities: \(s.named) of \(s.total).").font(.caption).foregroundStyle(.secondary)
                    } else if s.enabled && s.pending > 0 {
                        HStack(spacing: 10) {
                            Button("Name \(s.pendingText)") { confirming = true }.buttonStyle(.bordered)
                            Text(Fmt.capitalized(s.cost)).font(.caption).foregroundStyle(.secondary)
                        }
                    } else if s.enabled {
                        Text("Every activity with a generic name has a coach's name.").font(.caption).foregroundStyle(.secondary)
                    }
                    if let error { FormMessage(ok: false, text: error) }
                }
                .confirmationDialog("Name \(s.pendingText) with the coach?", isPresented: $confirming, titleVisibility: .visible) {
                    Button("Name them") { Task { await backfill() } }
                } message: {
                    Text("This costs \(s.cost) and runs in the background.")
                }
            }
        }
        .task(id: s?.running) {
            if s == nil { await load() }
            // Reads the progress every 2 seconds while the pass runs.
            while s?.running == true && !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                if !Task.isCancelled { await load() }
            }
        }
    }

    private func load() async {
        if let v = try? await api.get("/api/names/backfill", as: NamesStatus.self) { s = v }
    }

    private func toggle(_ enabled: Bool) async {
        struct Body: Encodable { let enabled: Bool }
        error = nil
        do {
            try await api.put("/api/settings/names", Body(enabled: enabled))
        } catch {
            self.error = "Could not save."
        }
        await load()
    }

    private func backfill() async {
        error = nil
        do {
            s = try api.decode(try await api.post("/api/names/backfill", [String: String]()), as: NamesStatus.self)
        } catch {
            self.error = "Could not start."
        }
    }
}
