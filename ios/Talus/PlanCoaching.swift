import SwiftUI

private struct CoachingEmptyBody: Encodable {}

struct DateAvailability: Codable, Hashable {
    let minutes: Int
    let terrain: String
    let locked: Bool
}

struct CoachingSession: Codable, Hashable {
    let day: String
    let kind: String
    let miles: Double
    let gainFt: Double
    let lossFt: Double
    let minutes: Double
    let benefit: String?
    let source: String?

    var summary: String {
        kind == "rest" ? "Rest" : "\(kind.replacingOccurrences(of: "_", with: " ")) · \(Fmt.mi(miles)) mi · \(Fmt.n0(gainFt)) ft up · \(Fmt.n0(lossFt)) ft down · \(Int(minutes.rounded())) min"
    }
}

struct CoachingActual: Codable, Hashable {
    let miles: Double
    let gainFt: Double
    let lossFt: Double
    let minutes: Double
    var summary: String { "\(Fmt.mi(miles)) mi · \(Fmt.n0(gainFt)) ft up · \(Fmt.n0(lossFt)) ft down · \(Int(minutes.rounded())) min" }
}

struct CoachingOutcome: Codable, Hashable {
    let day: String
    let intent: String
    let effort: Int?
    let reason: String?
    let planned: CoachingSession
    let actual: CoachingActual
}

struct PlanEvidence: Codable, Hashable {
    struct Milestone: Codable, Hashable { let day: String; let kind: String; let text: String }
    let label: String
    let activityDays: Int
    let lastActivity: String?
    let missing: [String]
    let assumptions: [String]
    let milestone: Milestone?
}

struct CoachingPlan: Codable {
    struct Reason: Codable { let key: String; let label: String }
    struct Suggestion: Codable { let reason: String; let text: String }
    struct Recent: Codable, Identifiable {
        let day: String
        let planned: CoachingSession
        let completed: Bool
        let outcome: CoachingOutcome?
        let actual: CoachingActual
        var id: String { day }
    }
    let date: String
    let sessions: [CoachingSession]
    let availability: [String: DateAvailability]
    let fixedDays: [String]
    let outcomes: [CoachingOutcome]
    let recentSessions: [Recent]
    let evidence: PlanEvidence
    let suggestions: [Suggestion]
    let reasons: [Reason]
    let undoId: Int?
}

struct RevisionPreview: Codable {
    struct Change: Codable, Identifiable {
        let day: String
        let before: CoachingSession
        let after: CoachingSession
        let why: String
        var id: String { day }
    }
    struct Week: Codable, Identifiable {
        let start: String
        let before: CoachingActual
        let after: CoachingActual
        var id: String { start }
    }
    let id: Int
    let reason: String
    let impact: String
    let availability: [String: DateAvailability]
    let changes: [Change]
    let weeks: [Week]
}

struct CoachingOutcomeSummary: Codable, Hashable {
    let reported: Int
    let met: Int
    let partial: Int
    let missed: Int
    let effortAverage: Double?
}

/// The same deterministic preview, acceptance and undo contract as the web app.
struct PlanCoachingCard: View {
    let onChange: () async -> Void
    let calendar: Schedule
    @EnvironmentObject private var api: API
    @State private var data: CoachingPlan?
    @State private var reason = "busy"
    @State private var day = Date()
    @State private var end = Date()
    @State private var minutes = "60"
    @State private var terrain = "any"
    @State private var locked = false
    @State private var remove = false
    @State private var expanded = false
    @State private var preview: RevisionPreview?
    @State private var busy = false
    @State private var error: String?
    @State private var notice: String?

    var body: some View {
        Card {
            SectionLabel("Coach plan tools")
            if let error { Text(error).font(.subheadline).foregroundStyle(Tone.bad.ink).accessibilityLabel("Error: \(error)") }
            if let notice { Text(notice).font(.subheadline).foregroundStyle(Tone.good.ink) }
            if let data {
                ForEach(Array(data.suggestions.enumerated()), id: \.offset) { _, suggestion in
                    Text(suggestion.text).font(.subheadline)
                    Button("Review sessions") { reason = suggestion.reason; expanded = true; preview = nil }
                }
                DisclosureGroup("Revise this week or set availability", isExpanded: $expanded) {
                    revisionFields(data)
                    if let preview { previewView(preview) }
                }
                if !data.availability.isEmpty {
                    DisclosureGroup("Saved availability and fixed dates") {
                        ForEach(data.availability.keys.sorted(), id: \.self) { d in
                            if let a = data.availability[d] {
                                Text("\(Fmt.day(d, .dateTime.month(.abbreviated).day())): \(a.minutes) min · \(a.terrain)\(a.locked ? " · fixed" : "")")
                                    .font(.caption)
                            }
                        }
                    }
                }
                if let undoId = data.undoId {
                    Button("Undo last accepted revision") {
                        Task { await perform {
                            try await api.post("/api/plan/revisions/\(undoId)/undo", CoachingEmptyBody())
                            preview = nil; try await refresh(); notice = "The last revision was undone."
                            await onChange()
                        } }
                    }
                }
                WorkoutOutcomesCard(data: data) { try? await refresh(); notice = "Workout feedback saved."; await onChange() }
                evidenceView(data.evidence)
            } else if error == nil { ProgressView("Reading your plan…") }
            else { Button("Try again") { Task { await perform { try await refresh() } } } }
        }
        .disabled(busy)
        .task(id: calendar) { preview = nil; await perform { try await refresh() } }
    }

    private func revisionFields(_ data: CoachingPlan) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("What changed?", selection: $reason) {
                ForEach(data.reasons, id: \.key) { Text($0.label).tag($0.key) }
            }
            DatePicker(reason == "availability" ? "From date" : "Session date", selection: $day,
                       in: Calendar.current.startOfDay(for: Date())...lastDate(reason == "availability" ? 366 : 13), displayedComponents: .date)
            if reason == "availability" {
                DatePicker("Through date", selection: $end, in: Calendar.current.startOfDay(for: day)...lastDate(366), displayedComponents: .date)
                Toggle("Restore recurring availability", isOn: $remove)
                if !remove {
                    LabeledField(label: "Available minutes (0 for a day off)", text: $minutes, keyboard: .numberPad)
                    Picker("Terrain", selection: $terrain) {
                        Text("Any terrain").tag("any"); Text("Flat only").tag("flat"); Text("Hills available").tag("hills")
                    }
                    Toggle("Keep the current session fixed", isOn: $locked)
                }
            }
            Text("Your own sessions stay fixed. Changes must fit time, terrain, recovery spacing and load checks.")
                .font(.caption).foregroundStyle(.secondary)
            Button("Preview changes") { Task { await buildPreview(data) } }.buttonStyle(.borderedProminent)
        }
        .padding(.top, 10)
        .onChange(of: reason) { preview = nil; if reason != "availability" && day > lastDate(13) { day = Date() } }
        .onChange(of: day) { preview = nil; if end < day { end = day } }
        .onChange(of: end) { preview = nil }
        .onChange(of: minutes) { preview = nil }
        .onChange(of: terrain) { preview = nil }
        .onChange(of: locked) { preview = nil }
        .onChange(of: remove) { preview = nil }
    }

    private func previewView(_ p: RevisionPreview) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Divider()
            Text("Review the changes").font(.headline).accessibilityAddTraits(.isHeader)
            ForEach(p.changes) { c in
                Text(Fmt.day(c.day, .dateTime.weekday(.wide).month(.abbreviated).day())).bold()
                Text("Before: \(c.before.summary)").foregroundStyle(.secondary)
                Text("After: \(c.after.summary)")
                Text(c.why)
                Divider()
            }
            if reason == "availability" {
                Text("Availability: \(Fmt.day(PlanText.iso(day), .dateTime.month(.abbreviated).day())) to \(Fmt.day(PlanText.iso(end), .dateTime.month(.abbreviated).day())) · \(remove ? "recurring calendar" : "\(minutes) min · \(terrain)\(locked ? " · fixed" : "")")")
            }
            if p.changes.isEmpty { Text("The next two weeks of sessions stay as shown.") }
            ForEach(p.weeks) { w in
                Text("Remaining week of \(Fmt.day(w.start, .dateTime.month(.abbreviated).day()))").bold()
                Text("Before: \(w.before.summary)").foregroundStyle(.secondary)
                Text("After: \(w.after.summary)")
            }
            Text(p.impact)
            Button("Accept changes") { Task { await perform {
                try await api.post("/api/plan/revisions/\(p.id)/accept", CoachingEmptyBody())
                preview = nil; try await refresh(); notice = "Plan changes accepted. Undo is available below."
                await onChange()
            } } }.buttonStyle(.borderedProminent)
            Button("Discard preview") { preview = nil }
        }
        .font(.subheadline)
    }

    private func evidenceView(_ e: PlanEvidence) -> some View {
        DisclosureGroup("Plan evidence · \(e.label)") {
            VStack(alignment: .leading, spacing: 8) {
                Text("\(e.activityDays) activity days in the last eight weeks. Latest activity: \(e.lastActivity.map { Fmt.day($0, .dateTime.month(.abbreviated).day()) } ?? "unavailable").")
                ForEach(e.missing, id: \.self) { Text("• \($0)") }
                ForEach(e.assumptions, id: \.self) { Text("• \($0)") }
                if let m = e.milestone {
                    Text("Next milestone: \(Fmt.day(m.day, .dateTime.weekday(.wide).month(.abbreviated).day())).").bold()
                    Text(m.text)
                }
            }.font(.caption).padding(.top, 8)
        }
    }

    private func lastDate(_ days: Int) -> Date { Calendar.current.date(byAdding: .day, value: days, to: Date())! }
    private func refresh() async throws { data = try await api.get("/api/plan/coaching", as: CoachingPlan.self) }
    private func perform(_ action: () async throws -> Void) async {
        guard !busy else { return }; busy = true; error = nil; notice = nil
        defer { busy = false }
        do { try await action() } catch { self.error = error.localizedDescription }
    }
    private func buildPreview(_ data: CoachingPlan) async {
        await perform {
            var availability: [String: Any] = [:]
            if reason == "availability" {
                let start = Calendar.current.startOfDay(for: day)
                let finish = Calendar.current.startOfDay(for: end)
                let span = Calendar.current.dateComponents([.day], from: start, to: finish).day ?? -1
                let cap = remove ? 0 : (Int(minutes) ?? -1)
                guard (0..<60).contains(span), (0...1440).contains(cap) else {
                    throw APIError.server(422, "Choose up to 60 days and 0 to 1440 available minutes.")
                }
                for k in 0...span {
                    let d = PlanText.iso(Calendar.current.date(byAdding: .day, value: k, to: start)!)
                    availability[d] = remove ? NSNull() : ["minutes": cap, "terrain": terrain, "locked": locked]
                }
            }
            let chosen = PlanText.iso(day)
            let target = reason == "availability" && day > lastDate(13) ? data.date : chosen
            let body = try JSONSerialization.data(withJSONObject: ["reason": reason, "day": target, "availability": availability])
            let reply = try await api.postRaw("/api/plan/revisions", json: body)
            preview = try api.decode(reply, as: RevisionPreview.self)
        }
    }
}

private struct WorkoutOutcomesCard: View {
    let data: CoachingPlan
    let onSaved: () async -> Void
    @State private var day = ""
    private var selected: CoachingPlan.Recent? { data.recentSessions.first { $0.day == day } ?? data.recentSessions.last }
    var body: some View {
        DisclosureGroup("Did your workouts achieve their purpose?") {
            if let selected {
                Picker("Session", selection: Binding(get: { selected.day }, set: { day = $0 })) {
                    ForEach(data.recentSessions) { s in
                        Text("\(Fmt.day(s.day, .dateTime.month(.abbreviated).day())) · \(s.planned.kind)\(s.outcome == nil ? "" : " · reported")").tag(s.day)
                    }
                }
                WorkoutOutcomeFields(selected: selected, onSaved: onSaved).id(selected.day + String(describing: selected.outcome))
            } else { Text("Feedback opens when a planned training day arrives.").font(.caption) }
            Text("Intent is your assessment. Missing feedback stays unknown. Sync completed activities before rating them.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

private struct WorkoutOutcomeFields: View {
    let selected: CoachingPlan.Recent
    let onSaved: () async -> Void
    @EnvironmentObject private var api: API
    @State private var intent: String
    @State private var effort: String
    @State private var reason: String
    @State private var busy = false
    @State private var error: String?
    @State private var saved = false
    init(selected: CoachingPlan.Recent, onSaved: @escaping () async -> Void) {
        self.selected = selected; self.onSaved = onSaved
        _intent = State(initialValue: selected.outcome?.intent ?? (selected.completed ? "met" : "missed"))
        _effort = State(initialValue: selected.outcome?.effort.map(String.init) ?? "")
        _reason = State(initialValue: selected.outcome?.reason ?? "")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Planned: \(selected.planned.summary)").font(.subheadline)
            if let benefit = selected.planned.benefit { Text(benefit).font(.caption) }
            if selected.planned.source == "planned_reference" { Text("This older plan has a weekly reference rather than a saved session snapshot.").font(.caption).foregroundStyle(.secondary) }
            Text("Completed: \(selected.completed ? selected.actual.summary : "No synced activity")").font(.subheadline)
            Picker("Did it do its job?", selection: $intent) {
                if selected.completed { Text("Met the purpose").tag("met"); Text("Partly").tag("partial") }
                else { Text("Missed session").tag("missed") }
            }
            if intent != "missed" { LabeledField(label: "Perceived effort, 1–10 (optional)", text: $effort, keyboard: .numberPad) }
            Picker("What affected it?", selection: $reason) {
                Text("Nothing to add").tag(""); Text("Schedule / busy").tag("busy"); Text("Too hard").tag("too_hard")
                Text("Sore legs").tag("sore"); Text("Terrain access").tag("terrain"); Text("Other").tag("other")
            }
            Button(busy ? "Saving…" : "Save workout feedback") { Task { await save() } }.buttonStyle(.bordered)
            if let error { Text(error).foregroundStyle(Tone.bad.ink).font(.caption) }
            if saved { Text("Workout feedback saved.").font(.caption).foregroundStyle(Tone.good.ink) }
        }.disabled(busy)
    }
    private func save() async {
        guard !busy else { return }; busy = true; error = nil; saved = false
        defer { busy = false }
        struct Body: Encodable { let day: String; let intent: String; let effort: Int?; let reason: String? }
        do {
            if intent != "missed" && !effort.isEmpty && !(Int(effort).map { (1...10).contains($0) } ?? false) {
                throw APIError.server(422, "Effort is a whole number from 1 to 10.")
            }
            try await api.put("/api/plan/outcomes", Body(day: selected.day, intent: intent,
                              effort: intent == "missed" ? nil : Int(effort), reason: reason.isEmpty ? nil : reason))
            saved = true; await onSaved()
        } catch { self.error = error.localizedDescription }
    }
}
