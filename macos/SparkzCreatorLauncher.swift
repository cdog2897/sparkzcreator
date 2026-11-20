import AppKit
import Darwin
import Foundation

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let appURL = URL(string: "http://127.0.0.1:5173")!
    private let pollInterval: TimeInterval = 0.5
    /// Give up only after the launcher log has been silent this long.
    private let stallLimit: TimeInterval = 60
    /// Cold installs can keep logging for several minutes. Keep waiting while they do.
    private let maxStartup: TimeInterval = 10 * 60
    private var launcher: Process?
    private var logHandle: FileHandle?
    private var logURL: URL?
    private var isReady = false
    private var isQuitting = false
    private var didResolve = false
    private var ownsServers = true
    private var startupStarted = Date()
    private var lastProgress = Date()
    private var lastLogSize: UInt64 = 0

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
            try FileManager.default.createDirectory(
                at: logsDirectory,
                withIntermediateDirectories: true
            )
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
            self.logURL = logURL

            let handle = try FileHandle(forWritingTo: logURL)
            try handle.truncate(atOffset: 0)
            logHandle = handle

            var environment = ProcessInfo.processInfo.environment
            let existingPath = environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
            environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + existingPath
            environment["RUN_APP_NO_BROWSER"] = "1"
            environment["PYTHONUNBUFFERED"] = "1"

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
            startupStarted = Date()
            lastProgress = Date()
            try process.run()
            pollForReadiness()
        } catch {
            showError(
                title: "Sparkz Creator could not start",
                message: "The launcher failed: \(error.localizedDescription)"
            )
        }
    }

    private func pollForReadiness() {
        guard !isQuitting, !didResolve else { return }
        if ownsServers, let launcher, !launcher.isRunning {
            return
        }
        noteLogProgress()

        var request = URLRequest(url: appURL)
        request.timeoutInterval = 2
        request.cachePolicy = .reloadIgnoringLocalCacheData
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self, !self.isQuitting, !self.didResolve else { return }
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                if (200..<400).contains(statusCode) {
                    self.markReadyAndOpen()
                    return
                }

                self.noteLogProgress()
                let elapsed = Date().timeIntervalSince(self.startupStarted)
                let quietFor = Date().timeIntervalSince(self.lastProgress)
                if elapsed >= self.maxStartup {
                    self.showStartupError(
                        "Startup ran for \(Int(elapsed / 60)) minutes without the website coming up."
                    )
                    return
                }
                if quietFor >= self.stallLimit {
                    self.showStartupError(
                        "Startup stalled with no progress for \(Int(quietFor)) seconds."
                    )
                    return
                }

                DispatchQueue.main.asyncAfter(deadline: .now() + self.pollInterval) {
                    self.pollForReadiness()
                }
            }
        }.resume()
    }

    private func noteLogProgress() {
        guard let logURL else { return }
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: logURL.path),
              let size = attributes[.size] as? NSNumber else {
            return
        }
        let bytes = size.uint64Value
        if bytes > lastLogSize {
            lastLogSize = bytes
            lastProgress = Date()
        }
    }

    private func markReadyAndOpen() {
        guard !didResolve, !isQuitting else { return }
        didResolve = true
        isReady = true
        NSWorkspace.shared.open(appURL)
    }

    private func launcherDidStop(_ process: Process) {
        guard !isQuitting else { return }
        let status = process.terminationStatus
        launcher = nil

        if status == 0 {
            ownsServers = false
            confirmAlreadyRunning()
            return
        }
        if !didResolve {
            showStartupError("The launcher exited before the app was ready.")
        }
    }

    private func confirmAlreadyRunning() {
        var request = URLRequest(url: appURL)
        request.timeoutInterval = 2
        request.cachePolicy = .reloadIgnoringLocalCacheData
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self, !self.isQuitting else { return }
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                if (200..<400).contains(statusCode) {
                    self.markReadyAndOpen()
                } else if !self.didResolve {
                    self.showStartupError("The launcher finished, but the website is not responding.")
                }
            }
        }.resume()
    }

    private func logTail(maxLines: Int = 8) -> String {
        guard let logURL,
              let text = try? String(contentsOf: logURL, encoding: .utf8) else {
            return ""
        }
        let lines = text.split(whereSeparator: \.isNewline).map(String.init)
        return lines.suffix(maxLines).joined(separator: "\n")
    }

    private func showStartupError(_ reason: String) {
        let detail = logTail()
        let message: String
        if detail.isEmpty {
            message = "\(reason)\n\nCheck ~/Library/Logs/Sparkz Creator.log for details."
        } else {
            message = "\(reason)\n\n\(detail)"
        }
        showError(title: "Sparkz Creator could not start", message: message)
    }

    private func showError(title: String, message: String) {
        guard !isQuitting else { return }
        isQuitting = true
        didResolve = true
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
        if isReady {
            NSWorkspace.shared.open(appURL)
        }
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        isQuitting = true

        guard ownsServers, let launcher, launcher.isRunning else {
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
