import SwiftUI

/// Rename an activity, as web/components/ActivityName.tsx does. The coach can suggest a name;
/// what you save always wins, and an empty name goes back to the coach's or the device's.
struct RenameActivitySheet: View {
    let id: Int
    let by: String
    let saved: () async -> Void
    @EnvironmentObject private var api: API
    @EnvironmentObject private var session: SessionModel
    @Environment(\.dismiss) private var dismiss
    @State private var value: String
    @State private var busy: Busy?
    @State private var error: String?
    @FocusState private var focused: Bool

    private enum Busy { case save, suggest }

    /// The engine's naming.NAME_MAX.
    static let maxLength = 60

    init(id: Int, name: String?, by: String, saved: @escaping () async -> Void) {
        self.id = id
        self.by = by
        self.saved = saved
        _value = State(initialValue: name ?? "")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Activity name", text: $value)
                        .font(.title3.weight(.semibold))
                        .focused($focused)
                        .submitLabel(.done)
                        .onSubmit { Task { await save(value) } }
                        .onChange(of: value) { _, v in
                            if v.count > Self.maxLength { value = String(v.prefix(Self.maxLength)) }
                        }
                } footer: {
                    if let error { Text(error) }
                }
                Section {
                    Button { Task { await suggest() } } label: {
                        BusyLabel(title: busy == .suggest ? "Finding the place" : "Suggest with AI", busy: busy == .suggest)
                    }
                    if by == "you" {
                        Button("Use the automatic name") { Task { await save("") } }
                    }
                }
                .disabled(busy != nil)
            }
            .navigationTitle("Rename")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(busy != nil)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button { Task { await save(value) } } label: { BusyLabel(title: "Save", busy: busy == .save) }
                        .disabled(busy != nil || value.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .onAppear { focused = true }
        }
        .presentationDetents([.medium])
    }

    private func save(_ next: String) async {
        struct Body: Encodable { let name: String }
        busy = .save
        error = nil
        defer { busy = nil }
        do {
            try await api.put("/api/activities/\(id)/name", Body(name: next))
            await saved()
            dismiss()
        } catch APIError.signedOut {
            session.signedOut()
        } catch {
            self.error = "Could not save the name."
        }
    }

    private func suggest() async {
        busy = .suggest
        error = nil
        defer { busy = nil }
        do {
            let data = try await api.post("/api/activities/\(id)/name/suggest", [String: String]())
            let out = try api.decode(data, as: NameSuggestion.self)
            value = out.name
            if out.source != "coach" { error = "The AI coach isn't available, so this name comes from the place alone." }
        } catch APIError.signedOut {
            session.signedOut()
        } catch {
            self.error = "Could not get a suggestion."
        }
    }
}
