import AppKit
import Darwin
import Foundation

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let appURL = URL(string: "http://127.0.0.1:5173")!
    private var launcher: Process?
    private var logHandle: FileHandle?
    private var isReady = false
    private var isQuitting = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        startLauncher()
    }

    private func startLauncher() {
        let projectDirectory = Bundle.main.bundleURL.deletingLastPathComponent()
        let launcherURL = projectDirectory.appendingPathComponent("Run App.command")

        guard FileManager.default.isExecutableFile(atPath: launcherURL.path) else {
            showError(
                title: "Launcher not found",
                message: "Keep Sparkz Creator.app in the same folder as Run App.command."
            )
            return
        }

        do {
            let logsDirectory = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Logs", isDirectory: true)
            let logURL = logsDirectory.appendingPathComponent("Sparkz Creator.log")
            FileManager.default.createFile(atPath: logURL.path, contents: nil)

            let handle = try FileHandle(forWritingTo: logURL)
            try handle.truncate(atOffset: 0)
            logHandle = handle

            var environment = ProcessInfo.processInfo.environment
            let existingPath = environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
            environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + existingPath
            environment["RUN_APP_NO_BROWSER"] = "1"

            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/bash")
            process.arguments = [launcherURL.path]
            process.currentDirectoryURL = projectDirectory
            process.environment = environment
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = handle
            process.standardError = handle
            process.terminationHandler = { [weak self] stoppedProcess in
                DispatchQueue.main.async {
                    self?.launcherDidStop(stoppedProcess)
                }
            }

            launcher = process
            try process.run()
            pollForReadiness(attempt: 1)
        } catch {
            showError(
                title: "Sparkz Creator could not start",
                message: "The launcher failed: \(error.localizedDescription)"
            )
        }
    }

    private func pollForReadiness(attempt: Int) {
        guard let launcher, launcher.isRunning, !isQuitting else { return }

        var request = URLRequest(url: appURL)
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            guard let self else { return }
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0

            DispatchQueue.main.async {
                guard !self.isQuitting else { return }

                if (200..<400).contains(statusCode) {
                    self.isReady = true
                    NSWorkspace.shared.open(self.appURL)
                } else if attempt < 80, self.launcher?.isRunning == true {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                        self.pollForReadiness(attempt: attempt + 1)
                    }
                } else {
                    self.showError(
                        title: "Sparkz Creator could not start",
                        message: "Check ~/Library/Logs/Sparkz Creator.log for details."
                    )
                }
            }
        }.resume()
    }

    private func launcherDidStop(_ process: Process) {
        guard !isQuitting else { return }
        launcher = nil

        showError(
            title: isReady ? "Sparkz Creator stopped" : "Sparkz Creator could not start",
            message: "Check ~/Library/Logs/Sparkz Creator.log for details."
        )
    }

    private func showError(title: String, message: String) {
        isQuitting = true
        NSApp.activate(ignoringOtherApps: true)

        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = message
        alert.runModal()

        NSApp.terminate(nil)
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        if isReady, launcher?.isRunning == true {
            NSWorkspace.shared.open(appURL)
        }
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        isQuitting = true

        guard let launcher, launcher.isRunning else {
            return .terminateNow
        }

        let processID = launcher.processIdentifier
        launcher.terminate()

        DispatchQueue.global(qos: .userInitiated).async {
            let deadline = Date().addingTimeInterval(5)
            while launcher.isRunning, Date() < deadline {
                Thread.sleep(forTimeInterval: 0.1)
            }

            if launcher.isRunning {
                kill(processID, SIGKILL)
            }

            DispatchQueue.main.async {
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }

        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        try? logHandle?.close()
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.run()
