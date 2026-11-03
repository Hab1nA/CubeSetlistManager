import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Group {
            if model.connection == nil {
                ConnectView()
            } else {
                RemoteView()
            }
        }
        .animation(.easeInOut(duration: 0.2), value: model.connection == nil)
    }
}
