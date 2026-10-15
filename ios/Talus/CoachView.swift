import SwiftUI

/// What the Coach tab reads: the conversation (shared with the Plan tab) and the training goals note.
struct CoachBundle {
    let chat: [ChatMessage]
    let note: String
}

/// A chat with the coach about anything in your training, not only the plan, under your training
/// goals. It shares one conversation with the chat on the Plan tab.
struct CoachView: View {
    @EnvironmentObject private var api: API

    static let suggestions = [
        "How have my miles and vert trended lately?",
        "What should I focus on this week?",
        "Am I doing enough easy running?",
        "Schedule a hill session for this week",
    ]

    var body: some View {
        NavigationStack {
            Loader(load: load) { b, reload in
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("Reads your weeks, runs, readiness and plan. To change your plan, use Plan.")
                            .font(.subheadline).foregroundStyle(.secondary)
                        TrainingNoteCard(initial: b.note)
                        ChatCard(initial: b.chat, full: true, suggestions: Self.suggestions, onConfirmed: reload)
                    }
                    .padding()
                }
                .refreshable { await reload() }
                .scrollDismissesKeyboard(.interactively)
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationTitle("Coach")
        }
    }

    private func load() async throws -> CoachBundle {
        struct Note: Decodable { let note: String }
        let chat = Task { try await api.get("/api/coach/chat", as: ChatLog.self) }
        let note = Task { try await api.get("/api/coach/note", as: Note.self) }
        return try await CoachBundle(chat: chat.value.messages, note: note.value.note)
    }
}

/// The athlete's own words on the training they want. Every coach answer reads it. The coach can
/// draft it from three short answers and recent training; nothing is saved until Save.
struct TrainingNoteCard: View {
    let initial: String
    @EnvironmentObject private var api: API
    @State private var saved: String
    @State private var note: String
    @State private var editing: Bool
    @State private var asking = false
    @State private var answers = Answers()
    @State private var busy: Busy?
    @State private var cost: AICost?
    @State private var message: String?

    enum Busy { case draft, save }

    /// The three questions behind a drafted note; keys go to the engine as snake_case.
    struct Answers: Encodable {
        var trainingFor = ""
        var wants = ""
        var avoid = ""
        var filled: Bool { [trainingFor, wants, avoid].contains { !$0.trimmingCharacters(in: .whitespaces).isEmpty } }
    }

    static let noteMax = 1500

    init(initial: String) {
        self.initial = initial
        _saved = State(initialValue: initial)
        _note = State(initialValue: initial)
        _editing = State(initialValue: initial.isEmpty)
    }

    var body: some View {
        Card {
            HStack {
                SectionLabel("Your training goals")
                Spacer()
                if !editing && !asking {
                    Button("Edit") { editing = true; message = nil }.font(.subheadline)
                }
            }
            if !editing && !asking {
                Text(saved).font(.subheadline).fixedSize(horizontal: false, vertical: true)
            }
            if editing && !asking { editor }
            if asking { questions }
            if let message {
                VStack(alignment: .leading, spacing: 2) {
                    Text(message)
                    if let cost { Text(cost.text).font(.caption2.monospacedDigit()) }
                }
                .font(.caption).foregroundStyle(.secondary)
            }
        }
        // A confirmed coach card can replace the note; take it when the page reloads.
        .onChange(of: initial) { _, now in
            saved = now
            if !editing && !asking { note = now }
        }
    }

    private var editor: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Tell the coach what kind of training you want. Every brief, activity read and chat answer follows it.")
                .font(.subheadline).foregroundStyle(.secondary)
            TextField("For example: I'm building for a spring 50k with lots of descent. I want one long day a week and more vert, and I'd rather hike steep climbs than run them.",
                      text: $note, axis: .vertical)
                .lineLimit(4...10)
                .textFieldStyle(.roundedBorder)
                .accessibilityLabel("Training goals")
                .onChange(of: note) { _, n in if n.count > Self.noteMax { note = String(n.prefix(Self.noteMax)) } }
            HStack(spacing: 8) {
                Button { Task { await save() } } label: { BusyLabel(title: "Save", busy: busy == .save) }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy != nil || note.trimmingCharacters(in: .whitespacesAndNewlines) == saved)
                Button("Draft with AI") { asking = true; message = nil }
                    .buttonStyle(.bordered)
                    .disabled(busy != nil)
                if !saved.isEmpty {
                    Button("Cancel") { note = saved; editing = false; message = nil }
                }
            }
            .font(.subheadline)
        }
    }

    private var questions: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Answer any of these. The coach adds what your recent training shows.")
                .font(.subheadline).foregroundStyle(.secondary)
            question("What are you training for?", "A spring 50k, a big Sierra day, general mountain fitness", $answers.trainingFor)
            question("What kind of training do you want?", "More vert, one long day a week, some speed work", $answers.wants)
            question("Anything to avoid or work around?", "Flat roads, a cranky knee, no runs before work on Mondays", $answers.avoid)
            HStack(spacing: 8) {
                Button { Task { await draft() } } label: { BusyLabel(title: "Draft my note", busy: busy == .draft) }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy != nil || !answers.filled)
                Button("Back") { asking = false }.disabled(busy != nil)
            }
            .font(.subheadline)
            if busy == .draft {
                Text("Reading your recent weeks").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func question(_ label: String, _ placeholder: String, _ text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(.subheadline.weight(.semibold))
            TextField(placeholder, text: text, axis: .vertical)
                .lineLimit(1...3)
                .textFieldStyle(.roundedBorder)
                .font(.subheadline)
                .onChange(of: text.wrappedValue) { _, v in if v.count > 300 { text.wrappedValue = String(v.prefix(300)) } }
        }
    }

    private func draft() async {
        struct Drafted: Decodable { let note: String; let source: String; let cost: AICost? }
        busy = .draft
        message = nil
        defer { busy = nil }
        do {
            let out = try api.decode(try await api.post("/api/coach/note/draft", answers), as: Drafted.self)
            note = out.note
            cost = out.cost
            asking = false
            editing = true
            message = out.source == "coach" ? "Drafted from your answers and your recent weeks. Edit it, then save."
                : "Written from your answers (the AI coach isn't available). Edit it, then save."
        } catch {
            message = "Could not draft the note. Is the engine running?"
        }
    }

    private func save() async {
        struct Body: Encodable { let note: String }
        busy = .save
        defer { busy = nil }
        do {
            try await api.put("/api/coach/note", Body(note: note))
            saved = note.trimmingCharacters(in: .whitespacesAndNewlines)
            editing = false
            message = "Saved. The coach reads this before every answer."
        } catch {
            message = "Could not save the note."
        }
    }
}

/// The gear in Today's toolbar. Settings opens as a sheet, so the tab bar keeps five tabs.
struct SettingsButton: ViewModifier {
    @State private var open = false

    func body(content: Content) -> some View {
        content
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { open = true } label: { Label("Settings", systemImage: "gearshape") }
                }
            }
            .sheet(isPresented: $open) {
                SettingsView().presentationDragIndicator(.visible)
            }
    }
}

extension View {
    func settingsButton() -> some View { modifier(SettingsButton()) }
}
