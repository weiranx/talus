import SwiftUI

// Planning with the coach (web/components/plan/PlanTalk.tsx, engine/talus/interview.py): a few
// questions, mostly answered with a tap, that fill the same inputs as the plan form. The engine
// drafts every number; after the draft, ask for changes in your own words.

/// A chip answer: one option, several (a multi group), or none yet.
enum TalkValue: Codable, Hashable {
    case one(String)
    case many([String])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let s = try? c.decode(String.self) { self = .one(s) } else { self = .many(try c.decode([String].self)) }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .one(let s): try c.encode(s)
        case .many(let a): try c.encode(a)
        }
    }

    var json: Any {
        switch self {
        case .one(let s): s
        case .many(let a): a
        }
    }

    func has(_ option: String) -> Bool {
        switch self {
        case .one(let s): s == option
        case .many(let a): a.contains(option)
        }
    }
}

struct TalkGroup: Codable, Hashable {
    let key: String
    let options: [String]
    let multi: Bool?
    let label: String?
    let value: TalkValue?
}

struct TalkAction: Codable, Hashable {
    /// accept, keep or retry.
    let id: String
    let label: String
}

struct TalkMessage: Codable, Hashable {
    let role: String
    let text: String
    let step: String?
    let groups: [TalkGroup]?
    let actions: [TalkAction]?
    let cost: AICost?
}

struct Talk: Codable, Hashable {
    let messages: [TalkMessage]
    let step: String
    /// setup or change.
    let mode: String
    let readsWithAi: Bool
    /// The AI coach is still building its version of the draft; the conversation says when it is ready.
    let building: Bool?

    var lastCoach: TalkMessage? { messages.last { $0.role == "coach" } }
    var lastCoachIndex: Int? { messages.lastIndex { $0.role == "coach" } }

    /// The chips' starting answers, from what the engine already knows.
    var startPicks: [String: TalkValue] {
        var out: [String: TalkValue] = [:]
        for g in lastCoach?.groups ?? [] { if let v = g.value { out[g.key] = v } }
        return out
    }
}

/// The conversation, loaded for its mode: "setup" before a plan is accepted, "change" after.
/// `onStep` reports the conversation's step (the page shows the draft beside it at "draft");
/// `onChange` reloads the page after each turn; `onDone` runs when the plan is decided.
struct PlanTalkCard: View {
    let mode: String
    var onStep: (String) -> Void = { _ in }
    let onChange: () async -> Void
    var onDone: () async -> Void = {}
    @EnvironmentObject private var api: API
    @State private var talk: Talk?
    @State private var failed: String?

    var body: some View {
        Group {
            if let talk {
                PlanTalkView(initial: talk, onStep: onStep, onChange: onChange, onDone: onDone)
            } else if let failed {
                VStack(alignment: .leading, spacing: 8) {
                    Text(failed).font(.subheadline).foregroundStyle(Tone.bad.ink)
                    Button("Try again") { Task { await load() } }.buttonStyle(.bordered)
                }
            } else {
                CoachPending(rows: 2, line: "The coach is getting ready.")
            }
        }
        .task { if talk == nil { await load() } }
    }

    private func load() async {
        do {
            let t = try await api.get("/api/plan/talk?mode=\(mode)", as: Talk.self)
            talk = t
            failed = nil
            onStep(t.step)
        } catch {
            failed = error.localizedDescription
        }
    }
}

private struct PlanTalkView: View {
    let onStep: (String) -> Void
    let onChange: () async -> Void
    let onDone: () async -> Void
    @EnvironmentObject private var api: API
    @State private var talk: Talk
    @State private var picks: [String: TalkValue]
    @State private var text = ""
    @State private var busy: String?
    @State private var error: String?
    @State private var picking = false
    @State private var contentHeight: CGFloat = 0

    private static let placeholder = ["objective": "Baldy 50k on Jan 2, 31 mi, 8,000 ft",
                                      "draft": "Ask a question, or tell me what to change", "stuck": "Or tell me what to change"]
    private static let maxHeight: CGFloat = 520

    init(initial: Talk, onStep: @escaping (String) -> Void, onChange: @escaping () async -> Void, onDone: @escaping () async -> Void) {
        self.onStep = onStep
        self.onChange = onChange
        self.onDone = onDone
        _talk = State(initialValue: initial)
        _picks = State(initialValue: initial.startPicks)
    }

    private var groups: [TalkGroup] { talk.lastCoach?.groups ?? [] }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            conversation
            HStack(alignment: .bottom, spacing: 8) {
                TextField(Self.placeholder[talk.step] ?? "Or type your answer", text: $text, axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.roundedBorder)
                    .disabled(busy != nil)
                    .onSubmit { submitText() }
                    .accessibilityLabel("Your answer")
                Button("Send") { submitText() }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy != nil || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if talk.step == "objective" {
                Button { picking = true } label: { Label("Upload GPX", systemImage: "doc.badge.plus") }
                    .buttonStyle(.bordered)
                    .disabled(busy != nil)
                    .fileImporter(isPresented: $picking, allowedContentTypes: [.gpx, .xml]) { result in
                        if case .success(let url) = result { Task { await upload(url) } }
                    }
            }
            if let error { Text(error).font(.subheadline).foregroundStyle(Tone.bad.ink) }
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("The engine drafts every number from your training."
                     + (talk.readsWithAi ? " Typed answers are read by the AI coach; taps cost nothing." : ""))
                Button("Start over") { Task { await restart() } }.disabled(busy != nil)
            }
            .font(.caption).foregroundStyle(.secondary)
        }
        .task(id: talk.building == true && busy == nil ? talk.messages.count : -1) { await poll() }
    }

    /// The messages in a pane that keeps the newest in view, without scrolling the page.
    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    let lastIndex = talk.lastCoachIndex
                    ForEach(Array(talk.messages.enumerated()), id: \.offset) { i, m in
                        message(m, active: i == lastIndex && busy == nil, faded: m.role == "coach" && i < (lastIndex ?? 0) - 1)
                    }
                    if talk.building == true && busy == nil { status("The AI coach is building its version.") }
                    if let busy { status(busy) }
                    Color.clear.frame(height: 1).id("end")
                }
                .background(GeometryReader { g in Color.clear.preference(key: TalkHeight.self, value: g.size.height) })
            }
            .frame(height: min(max(contentHeight, 60), Self.maxHeight))
            .scrollBounceBehavior(.basedOnSize)
            .onPreferenceChange(TalkHeight.self) { contentHeight = $0 }
            .onAppear { proxy.scrollTo("end", anchor: .bottom) }
            .onChange(of: talk.messages.count) { withAnimation { proxy.scrollTo("end", anchor: .bottom) } }
            .onChange(of: busy) { withAnimation { proxy.scrollTo("end", anchor: .bottom) } }
            .onChange(of: contentHeight) { proxy.scrollTo("end", anchor: .bottom) }
        }
    }

    @ViewBuilder
    private func message(_ m: TalkMessage, active: Bool, faded: Bool) -> some View {
        let mine = m.role == "athlete"
        VStack(alignment: mine ? .trailing : .leading, spacing: 8) {
            HStack {
                if mine { Spacer(minLength: 40) }
                VStack(alignment: .leading, spacing: 6) {
                    if mine { Text(m.text) } else { CoachText(text: m.text) }
                    if let cost = m.cost { Text(cost.text).font(.caption2.monospacedDigit()).foregroundStyle(.secondary) }
                }
                .font(.subheadline)
                .padding(.horizontal, 12).padding(.vertical, 8)
                .foregroundStyle(mine ? Color(uiColor: .systemBackground) : .primary)
                .background(mine ? Color.primary : Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
                .opacity(faded ? 0.85 : 1)
                if !mine { Spacer(minLength: 40) }
            }
            if active, let gs = m.groups, !gs.isEmpty { chips(gs) }
            if active, let acts = m.actions, !acts.isEmpty {
                HStack(spacing: 10) {
                    ForEach(Array(acts.enumerated()), id: \.offset) { k, a in
                        let label = Text(a.label)
                        if k == 0 {
                            Button { act(a) } label: { label }.buttonStyle(.borderedProminent)
                        } else {
                            Button { act(a) } label: { label }.buttonStyle(.bordered)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: mine ? .trailing : .leading)
    }

    private func chips(_ gs: [TalkGroup]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(gs, id: \.key) { g in
                VStack(alignment: .leading, spacing: 6) {
                    if let label = g.label {
                        Text(label + (g.multi == true ? " (pick any)" : "")).font(.subheadline.weight(.semibold))
                    }
                    ChipFlow(spacing: 6) {
                        ForEach(g.options, id: \.self) { o in
                            let on = picks[g.key]?.has(o) ?? false
                            Button { tap(g, o) } label: {
                                Text(o).font(.subheadline)
                                    .padding(.horizontal, 12).frame(minHeight: 34)
                                    .foregroundStyle(on ? Color(uiColor: .systemBackground) : .primary)
                                    .background(on ? Color.primary : .clear, in: Capsule())
                                    .overlay(Capsule().stroke(Color.secondary.opacity(on ? 0 : 0.35)))
                            }
                            .buttonStyle(.plain)
                            .accessibilityAddTraits(on ? .isSelected : [])
                        }
                    }
                }
                .accessibilityElement(children: .contain)
                .accessibilityLabel(g.label ?? "Answers")
            }
            if gs.count > 1 || gs.contains(where: { $0.multi == true }) {
                Button("Next") { send(["answers": answersJSON(picks)], pending: "One moment.") }
                    .buttonStyle(.borderedProminent)
            }
        }
    }

    private func status(_ line: String) -> some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text(line).font(.subheadline).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
        .accessibilityElement(children: .combine)
    }

    // MARK: Turns

    private func answersJSON(_ p: [String: TalkValue]) -> [String: Any] { p.mapValues(\.json) }

    private func tap(_ g: TalkGroup, _ option: String) {
        if g.multi == true {
            var now: [String] = if case .many(let a) = picks[g.key] { a } else { [] }
            now = now.contains(option) ? now.filter { $0 != option } : now + [option]
            picks[g.key] = .many(now)
            return
        }
        picks[g.key] = .one(option)
        // A question with a single one-tap answer goes straight on.
        if groups.count == 1 { send(["answers": answersJSON(picks)], pending: talk.step == "body" ? "Drafting your plan." : "One moment.") }
    }

    private func act(_ a: TalkAction) {
        send(["action": a.id], pending: a.id == "accept" ? "Putting it on your calendar." : "One moment.")
    }

    private func submitText() {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, busy == nil else { return }
        var body: [String: Any] = ["text": t]
        if !groups.isEmpty { body["answers"] = answersJSON(picks) }
        send(body, pending: talk.step == "objective" || talk.step == "draft" ? "The coach is reading that." : "One moment.", optimistic: t)
    }

    private func send(_ body: [String: Any], pending: String, optimistic: String? = nil) {
        Task { await post(body, pending: pending, optimistic: optimistic) }
    }

    private func post(_ body: [String: Any], pending: String, optimistic: String? = nil) async {
        guard busy == nil else { return }
        busy = pending
        error = nil
        let before = talk
        if let optimistic {
            talk = Talk(messages: talk.messages + [TalkMessage(role: "athlete", text: optimistic, step: nil, groups: nil, actions: nil, cost: nil)],
                        step: talk.step, mode: talk.mode, readsWithAi: talk.readsWithAi, building: talk.building)
        }
        do {
            let json = try JSONSerialization.data(withJSONObject: body)
            let next = try api.decode(try await api.postRaw("/api/plan/talk", json: json), as: Talk.self)
            busy = nil
            took(next)
            text = ""
            if next.step == "done" { await onDone() }
            await onChange()
        } catch {
            busy = nil
            talk = before
            self.error = Self.message(error)
        }
    }

    private func took(_ next: Talk) {
        talk = next
        picks = next.startPicks
        onStep(next.step)
    }

    static func message(_ error: Error) -> String {
        switch error {
        case APIError.server(let status, let detail) where detail.hasPrefix("The engine answered"):
            return status >= 500 ? "The engine couldn't answer that (\(status)). Try again, or start over." : detail
        case APIError.unreachable: return "Could not reach the coach. Is the engine running?"
        default: return error.localizedDescription
        }
    }

    private func upload(_ url: URL) async {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        busy = "Reading the route."
        error = nil
        do {
            let data = try Data(contentsOf: url)
            let res = try await api.upload("/api/plan/route", file: data, contentType: "application/gpx+xml")
            guard var route = try JSONSerialization.jsonObject(with: res) as? [String: Any] else { throw APIError.server(422, "") }
            if (route["name"] as? String ?? "").isEmpty {
                route["name"] = url.deletingPathExtension().lastPathComponent
            }
            busy = nil
            await post(["route": route], pending: "Reading the route.")
        } catch APIError.server(_, let detail) where !detail.isEmpty && !detail.hasPrefix("The engine answered") {
            busy = nil
            error = detail
        } catch {
            busy = nil
            self.error = "Could not read that GPX file."
        }
    }

    private func restart() async {
        struct Mode: Encodable { let mode: String }
        do {
            let next = try api.decode(try await api.post("/api/plan/talk/restart", Mode(mode: talk.mode)), as: Talk.self)
            took(next)
            error = nil
        } catch {
            self.error = "Could not start over. Is the engine running?"
        }
    }

    /// While the AI coach builds its version of the draft, check back every 4 seconds until it's ready.
    private func poll() async {
        guard talk.building == true, busy == nil else { return }
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(4))
            if Task.isCancelled { return }
            guard let next = try? await api.get("/api/plan/talk?mode=\(talk.mode)", as: Talk.self) else { continue }
            if next.building != true {
                took(next)
                await onChange()
                return
            }
            if next.messages.count != talk.messages.count { took(next) }
        }
    }
}

private struct TalkHeight: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

/// Chips that wrap onto as many lines as they need.
private struct ChipFlow: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews)
        let w = rows.map { $0.width }.max() ?? 0
        let h = rows.reduce(0) { $0 + $1.height } + spacing * CGFloat(max(rows.count - 1, 0))
        return CGSize(width: proposal.width ?? w, height: h)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(width: bounds.width, subviews) {
            var x = bounds.minX
            for i in row.items {
                let s = subviews[i].sizeThatFits(.unspecified)
                subviews[i].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
                x += s.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row { var items: [Int] = []; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func arrange(width: CGFloat, _ subviews: Subviews) -> [Row] {
        var rows: [Row] = [Row()]
        for i in subviews.indices {
            let s = subviews[i].sizeThatFits(.unspecified)
            let add = rows[rows.count - 1].items.isEmpty ? s.width : rows[rows.count - 1].width + spacing + s.width
            if add > width && !rows[rows.count - 1].items.isEmpty {
                rows.append(Row(items: [i], width: s.width, height: s.height))
            } else {
                rows[rows.count - 1].items.append(i)
                rows[rows.count - 1].width = add
                rows[rows.count - 1].height = max(rows[rows.count - 1].height, s.height)
            }
        }
        return rows
    }
}
