import SwiftUI

/// Something the coach offered to do from the chat: schedule, move or delete one of your runs,
/// replace the training goals note, sync, or apply suggested thresholds. Nothing runs until Confirm.
struct CoachAction: Codable, Hashable, Identifiable {
    let id: Int
    let kind: String
    let summary: String
    /// proposed, running, done, dismissed or failed.
    let status: String
    let result: String?
}

/// Ask the coach. Every number in an answer is checked against your data before it shows. One
/// conversation, shown compact on the Plan tab ("Ask the coach") and full on the Coach tab, with
/// starter questions while it's empty. `onConfirmed` runs after a coach card is confirmed, since
/// the calendar, goals or loads may have changed.
struct ChatCard: View {
    let initial: [ChatMessage]
    var full = false
    var suggestions: [String] = []
    var onConfirmed: () async -> Void = {}
    @EnvironmentObject private var api: API
    @State private var messages: [ChatMessage]
    @State private var question = ""
    @State private var busy = false
    @State private var error: String?

    init(initial: [ChatMessage], full: Bool = false, suggestions: [String] = [],
         onConfirmed: @escaping () async -> Void = {}) {
        self.initial = initial
        self.full = full
        self.suggestions = suggestions
        self.onConfirmed = onConfirmed
        _messages = State(initialValue: initial)
    }

    var body: some View {
        ScrollViewReader { proxy in
            Card {
                HStack {
                    SectionLabel(full ? "Chat" : "Ask the coach")
                    Spacer()
                    if !messages.isEmpty { Button("Clear") { Task { await clear() } }.font(.subheadline) }
                }
                if messages.isEmpty {
                    Text(full
                         ? "Ask anything about your training, or ask the coach to do something: add or move a run, update your goals, sync. You confirm each change first."
                         : "Ask about your plan, your loads or your objective. For example: can I swap Saturday's long day to Sunday?")
                        .font(.subheadline).foregroundStyle(.secondary)
                    if !suggestions.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(suggestions, id: \.self) { s in
                                Button(s) { Task { await send(s) } }
                                    .font(.subheadline)
                                    .multilineTextAlignment(.leading)
                                    .padding(.horizontal, 12).padding(.vertical, 8)
                                    .overlay(Capsule().stroke(Color.secondary.opacity(0.35)))
                                    .buttonStyle(.plain)
                                    .disabled(busy)
                            }
                        }
                    }
                }
                ForEach(Array(messages.enumerated()), id: \.offset) { _, m in
                    bubble(m)
                }
                if busy {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("The coach is reading your numbers.").font(.caption).foregroundStyle(.secondary)
                    }
                }
                HStack {
                    TextField("Ask a question", text: $question, axis: .vertical)
                        .lineLimit(1...4)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { Task { await send(question) } }
                        .onChange(of: question) { _, q in if q.count > 1000 { question = String(q.prefix(1000)) } }
                    Button("Ask") { Task { await send(question) } }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                .id("chat-end")
                if let error { Text(error).font(.caption).foregroundStyle(Tone.bad.ink) }
            }
            // Keep the newest message and the question field in view as the conversation grows.
            .onChange(of: messages.count) { _, _ in withAnimation { proxy.scrollTo("chat-end", anchor: .bottom) } }
            // A reload of the page brings the same conversation as asked from the other tab.
            .onChange(of: initial) { _, now in if !busy { messages = now } }
        }
    }

    private func bubble(_ m: ChatMessage) -> some View {
        let mine = m.role == "athlete"
        return HStack {
            if mine { Spacer(minLength: 40) }
            VStack(alignment: .leading, spacing: 6) {
                if mine { Text(m.text) } else { CoachText(text: m.text) }
                ForEach(m.actions ?? []) { a in
                    ActionCard(initial: a, onConfirmed: onConfirmed).id(a.id)
                }
                if !mine, m.source == "coach", let id = m.logId {
                    Text("AI coach, numbers checked").font(.caption2).foregroundStyle(.secondary)
                    if let cost = m.cost { Text(cost.text).font(.caption2.monospacedDigit()).foregroundStyle(.secondary) }
                    CoachFeedback(logId: id, initial: nil)
                }
            }
            .font(.subheadline)
            .padding(.horizontal, 12).padding(.vertical, 8)
            .foregroundStyle(mine ? Color(uiColor: .systemBackground) : .primary)
            .background(mine ? Color.primary : Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
            if !mine { Spacer(minLength: 40) }
        }
    }

    private func send(_ text: String) async {
        struct Question: Encodable { let question: String }
        let q = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !busy else { return }
        let before = messages
        busy = true
        error = nil
        messages.append(ChatMessage(id: -1, createdAt: "", role: "athlete", text: q, source: nil, logId: nil, cost: nil))
        question = ""
        defer { busy = false }
        do {
            let data = try await api.post("/api/coach/chat", Question(question: q))
            messages = try api.decode(data, as: ChatLog.self).messages
        } catch {
            messages = before
            question = q
            self.error = "Could not reach the coach. \(error.localizedDescription)"
        }
    }

    private func clear() async {
        if (try? await api.delete("/api/coach/chat")) != nil { messages = [] }
    }
}

/// A card the coach offered in a reply. Confirm runs it, Not now skips it; the result stays on the card.
struct ActionCard: View {
    var onConfirmed: () async -> Void = {}
    @EnvironmentObject private var api: API
    @State private var a: CoachAction
    @State private var busy = false
    @State private var note: String?

    init(initial: CoachAction, onConfirmed: @escaping () async -> Void = {}) {
        self.onConfirmed = onConfirmed
        _a = State(initialValue: initial)
    }

    /// Same wording as web/components/ActionCard.tsx.
    static func label(_ status: String) -> String {
        ["done": "Done", "dismissed": "Skipped", "failed": "Didn't work", "running": "Working"][status] ?? status
    }

    var body: some View {
        let open = a.status == "proposed"
        VStack(alignment: .leading, spacing: 8) {
            Text(a.summary).font(.subheadline)
            if open {
                HStack(spacing: 8) {
                    Button { Task { await act("confirm") } } label: { BusyLabel(title: "Confirm", busy: busy) }
                        .buttonStyle(.borderedProminent)
                    Button("Not now") { Task { await act("dismiss") } }
                        .buttonStyle(.bordered)
                }
                .controlSize(.small)
                .disabled(busy)
                if let note { Text(note).font(.caption).foregroundStyle(Tone.bad.ink) }
            } else {
                Text(Self.label(a.status) + (a.status != "dismissed" ? a.result.map { ": \($0)" } ?? "" : ""))
                    .font(.caption)
                    .foregroundStyle(a.status == "failed" ? Tone.bad.ink : .secondary)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color.secondary.opacity(0.3)))
    }

    private func act(_ verb: String) async {
        struct Empty: Encodable {}
        struct Outcome: Decodable { let status: String; let result: String? }
        busy = true
        defer { busy = false }
        do {
            let out = try api.decode(try await api.post("/api/coach/actions/\(a.id)/\(verb)", Empty()), as: Outcome.self)
            a = CoachAction(id: a.id, kind: a.kind, summary: a.summary, status: out.status, result: out.result)
            note = nil
        } catch {
            note = "Could not reach the engine. Try again."
            return
        }
        if verb == "confirm" { await onConfirmed() }
    }
}
