import SwiftUI

// The admin's coach model choice, one for every account (web/components/CoachModelsCard.tsx).

extension AdminOverview {
    struct CoachModels: Codable, Hashable {
        struct Model: Codable, Hashable { let id: String; let label: String }
        struct Estimates: Codable, Hashable { let brief: [String: CostEstimate]; let analysis: [String: CostEstimate] }
        let briefModel: String
        let analysisModel: String
        /// Picked on the admin page; false means TALUS_COACH_MODEL or the defaults.
        let chosen: Bool
        let env: String?
        let models: [Model]
        let estimate: Estimates
    }
}

struct CoachModelsCard: View {
    let data: AdminOverview.CoachModels
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var brief: String
    @State private var analysis: String
    @State private var busy = false
    @State private var message: (ok: Bool, text: String)?

    init(data: AdminOverview.CoachModels, reload: @escaping () async -> Void) {
        self.data = data
        self.reload = reload
        _brief = State(initialValue: data.briefModel)
        _analysis = State(initialValue: data.analysisModel)
    }

    var body: some View {
        Card {
            SectionLabel("Coach models")
            Text("One choice for every account. Each account still picks its coaching style, its cap and whether the coach is on.")
                .font(.caption).foregroundStyle(.secondary)
            mode("Daily brief and quick replies", "Today's call, the Load take, activity names and reading typed answers.",
                 selection: $brief, estimates: data.estimate.brief)
            mode("Longer reads", "Workout analysis, plan, weekly review and coach chat.",
                 selection: $analysis, estimates: data.estimate.analysis)
            if !data.chosen {
                Text("Not picked here yet, so these are \(data.env != nil ? "the TALUS_COACH_MODEL setting" : "the defaults").")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Button { Task { await save() } } label: { BusyLabel(title: busy ? "Saving" : "Save models", busy: busy) }
                .buttonStyle(.borderedProminent)
                .disabled(busy || (brief == data.briefModel && analysis == data.analysisModel))
            if let message { FormMessage(ok: message.ok, text: message.text) }
        }
    }

    private func mode(_ label: String, _ when: String, selection: Binding<String>, estimates: [String: CostEstimate]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Picker(label, selection: selection) {
                ForEach(data.models, id: \.id) { Text($0.label).tag($0.id) }
            }
            .font(.subheadline)
            .disabled(busy)
            Text("\(when) \(Self.costLine(estimates[selection.wrappedValue])).")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    static func costLine(_ e: CostEstimate?) -> String {
        guard let e else { return "" }
        let call = SettingsText.money(e.perCall)
        return "\(call.hasPrefix("under") ? "Under 1¢" : "About \(call)") a call, \(SettingsText.money(e.perMonth)) a month on your account"
    }

    private func save() async {
        struct Body: Encodable { let briefModel: String; let analysisModel: String }
        busy = true
        defer { busy = false }
        message = nil
        do {
            try await api.put("/api/admin/coach-models", Body(briefModel: brief, analysisModel: analysis))
            message = (true, "Saved. Every account's next coach reply uses these models.")
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}
