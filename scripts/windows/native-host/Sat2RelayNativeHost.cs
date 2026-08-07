using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

internal static class Sat2RelayNativeHost
{
    private const int MaxMessageBytes = 1024 * 1024;

    private static string ProgramRoot
    {
        get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SAT2Relay"); }
    }

    private static string StartScript
    {
        get { return Path.Combine(ProgramRoot, "on-demand", "START_OR_REPAIR.ps1"); }
    }

    private static string ReadMessage()
    {
        Stream input = Console.OpenStandardInput();
        byte[] lengthBytes = new byte[4];
        int read = input.Read(lengthBytes, 0, 4);
        if (read == 0) return null;
        if (read != 4) throw new InvalidDataException("INVALID_NATIVE_MESSAGE_LENGTH");

        int length = BitConverter.ToInt32(lengthBytes, 0);
        if (length <= 0 || length > MaxMessageBytes) throw new InvalidDataException("NATIVE_MESSAGE_TOO_LARGE");

        byte[] payload = new byte[length];
        int offset = 0;
        while (offset < length)
        {
            int count = input.Read(payload, offset, length - offset);
            if (count <= 0) throw new EndOfStreamException("NATIVE_MESSAGE_TRUNCATED");
            offset += count;
        }
        return Encoding.UTF8.GetString(payload);
    }

    private static void WriteMessage(string json)
    {
        byte[] payload = Encoding.UTF8.GetBytes(json);
        byte[] length = BitConverter.GetBytes(payload.Length);
        Stream output = Console.OpenStandardOutput();
        output.Write(length, 0, length.Length);
        output.Write(payload, 0, payload.Length);
        output.Flush();
    }

    private static string JsonEscape(string value)
    {
        if (value == null) return "";
        StringBuilder b = new StringBuilder(value.Length + 16);
        foreach (char ch in value)
        {
            switch (ch)
            {
                case '\\': b.Append("\\\\"); break;
                case '"': b.Append("\\\""); break;
                case '\r': b.Append("\\r"); break;
                case '\n': b.Append("\\n"); break;
                case '\t': b.Append("\\t"); break;
                default:
                    if (ch < 32) b.Append("\\u" + ((int)ch).ToString("x4"));
                    else b.Append(ch);
                    break;
            }
        }
        return b.ToString();
    }

    private static string JsonResult(bool ok, bool running, string action, string code, string detail)
    {
        return "{" +
            "\"ok\":" + (ok ? "true" : "false") + "," +
            "\"running\":" + (running ? "true" : "false") + "," +
            "\"action\":\"" + JsonEscape(action) + "\"," +
            "\"code\":\"" + JsonEscape(code) + "\"," +
            "\"detail\":\"" + JsonEscape(detail) + "\"," +
            "\"host_version\":\"2.2.1\"" +
            "}";
    }

    private static string CommandFromJson(string json)
    {
        if (String.IsNullOrWhiteSpace(json)) return "";
        Match match = Regex.Match(json, "\\\"command\\\"\\s*:\\s*\\\"([A-Za-z0-9_-]+)\\\"");
        return match.Success ? match.Groups[1].Value : "";
    }

    private static bool RelayPortOpen(int timeoutMs)
    {
        try
        {
            using (TcpClient client = new TcpClient())
            {
                IAsyncResult result = client.BeginConnect("127.0.0.1", 8765, null, null);
                bool connected = result.AsyncWaitHandle.WaitOne(timeoutMs);
                if (!connected) return false;
                client.EndConnect(result);
                return true;
            }
        }
        catch
        {
            return false;
        }
    }

    private static int RunStartScript(out string detail)
    {
        detail = "";
        if (!File.Exists(StartScript))
        {
            detail = "START_OR_REPAIR.ps1 is missing. Re-run the Relay installer.";
            return 127;
        }

        string systemDir = Environment.GetFolderPath(Environment.SpecialFolder.System);
        string powershell = Path.Combine(systemDir, "WindowsPowerShell", "v1.0", "powershell.exe");
        if (!File.Exists(powershell)) powershell = "powershell.exe";

        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = powershell;
        info.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + StartScript + "\" -SkipPoll";
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.WindowStyle = ProcessWindowStyle.Hidden;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;

        using (Process process = Process.Start(info))
        {
            string stdout = process.StandardOutput.ReadToEnd();
            string stderr = process.StandardError.ReadToEnd();
            if (!process.WaitForExit(30000))
            {
                try { process.Kill(); } catch { }
                detail = "START_OR_REPAIR timed out.";
                return 124;
            }
            string combined = (stdout + "\n" + stderr).Trim();
            if (combined.Length > 800) combined = combined.Substring(combined.Length - 800);
            detail = combined;
            return process.ExitCode;
        }
    }

    public static int Main()
    {
        try
        {
            string message = ReadMessage();
            if (message == null) return 0;
            string command = CommandFromJson(message);

            if (command == "status")
            {
                bool running = RelayPortOpen(500);
                WriteMessage(JsonResult(true, running, "status", running ? "RUNNING" : "STOPPED", running ? "Relay listener is reachable." : "Relay listener is not running."));
                return 0;
            }

            if (command == "ensure_running")
            {
                if (RelayPortOpen(500))
                {
                    WriteMessage(JsonResult(true, true, "already_running", "RUNNING", "Relay is already running."));
                    return 0;
                }

                string detail;
                int exitCode = RunStartScript(out detail);
                if (exitCode != 0)
                {
                    WriteMessage(JsonResult(false, RelayPortOpen(500), "start_failed", "START_SCRIPT_FAILED", "Exit code " + exitCode + ". " + detail));
                    return 0;
                }

                for (int i = 0; i < 40; i++)
                {
                    if (RelayPortOpen(500))
                    {
                        WriteMessage(JsonResult(true, true, "started", "STARTED", "Relay started successfully."));
                        return 0;
                    }
                    Thread.Sleep(250);
                }

                WriteMessage(JsonResult(false, false, "start_timeout", "START_TIMEOUT", "Start script returned successfully but port 8765 did not become reachable."));
                return 0;
            }

            WriteMessage(JsonResult(false, RelayPortOpen(300), "rejected", "COMMAND_NOT_ALLOWED", "Only status and ensure_running are allowed."));
            return 0;
        }
        catch (Exception ex)
        {
            try { WriteMessage(JsonResult(false, false, "error", "NATIVE_HOST_ERROR", ex.Message)); } catch { }
            return 0;
        }
    }
}
