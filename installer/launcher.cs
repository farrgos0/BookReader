// BookReader.exe — запуск сервера озвучки одним щелчком.
// Собирается установщиком из этого файла встроенным в Windows компилятором C# (.NET Framework 4).
using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Windows.Forms;

static class Launcher
{
    const int DefaultPort = 8765;
    // «этот компьютер»; именно 127.0.0.1 — библиотека читалки в браузере привязана к этому адресу
    const string Loopback = "127.0.0.1";

    [STAThread]
    static void Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        int port = ConfiguredPort();
        // сервер уже работает — просто открываем читалку в браузере
        if (PortOpen(port))
        {
            Open("http://" + Loopback + ":" + port + "/");
            return;
        }
        // запуск — через BookReader.bat: он проверяет, что Python и библиотеки на месте, и, если нет,
        // предлагает запустить установщик (а не падает с ошибкой в консоли)
        string bat = Path.Combine(dir, "BookReader.bat");
        if (!File.Exists(bat))
        {
            Fail("Не найден файл BookReader.bat рядом с BookReader.exe.\nBookReader.exe должен лежать в папке BookReader (ярлык на рабочем столе ведёт туда).");
            return;
        }
        string extra = args.Length > 0 ? " " + string.Join(" ", args) : "";
        var psi = new ProcessStartInfo("cmd.exe", "/c \"\"" + bat + "\"" + extra + "\"");
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = true; // своё окно консоли: в нём адрес для телефона и журнал сервера
        try { Process.Start(psi); }
        catch (Exception e)
        {
            Fail("Не удалось запустить сервер BookReader:\n" + e.Message + "\n\nЗапустите install.bat в папке BookReader ещё раз — он восстановит установку.");
        }
    }

    static bool PortOpen(int port)
    {
        try
        {
            using (var c = new TcpClient())
            {
                var r = c.BeginConnect(Loopback, port, null, null);
                return r.AsyncWaitHandle.WaitOne(400) && c.Connected;
            }
        }
        catch { return false; }
    }

    /// <summary>Порт из настроек сервера (server.json в папке данных BookReader), иначе 8765.</summary>
    static int ConfiguredPort()
    {
        try
        {
            string home = Environment.GetEnvironmentVariable("BOOKREADER_HOME");
            if (string.IsNullOrEmpty(home))
                home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BookReader");
            string cfg = Path.Combine(home, "server.json");
            if (!File.Exists(cfg)) return DefaultPort;
            var m = System.Text.RegularExpressions.Regex.Match(File.ReadAllText(cfg), "\"port\"\\s*:\\s*(\\d+)");
            int p;
            if (m.Success && int.TryParse(m.Groups[1].Value, out p) && p > 0 && p < 65536) return p;
        }
        catch { }
        return DefaultPort;
    }

    static void Open(string url)
    {
        try { Process.Start(url); } catch (Exception e) { Fail("Не удалось открыть браузер: " + e.Message); }
    }

    static void Fail(string text)
    {
        MessageBox.Show(text, "BookReader", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
