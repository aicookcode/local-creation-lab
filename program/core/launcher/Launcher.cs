using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("Local Media Studio")]
[assembly: AssemblyDescription("Offline audio and video creation workstation")]
[assembly: AssemblyVersion("7.1.1.0")]
[assembly: AssemblyFileVersion("7.1.1.0")]

static class Launcher
{
    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "start_workbench.ps1");
        if (!File.Exists(script))
        {
            MessageBox.Show("缺少启动脚本。请先将工作台压缩包完整解压。",
                "本地影音创作工作台", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        string runtime = Path.Combine(root, "program", "runtime");
        bool runtimeReady = File.Exists(Path.Combine(runtime, "python", "pythonw.exe"))
            && File.Exists(Path.Combine(runtime, "portrait-python310", "python.exe"))
            && File.Exists(Path.Combine(runtime, "qwen-tts", "llama-tts.exe"));
        try
        {
            string powershell = Path.Combine(Environment.SystemDirectory,
                "WindowsPowerShell", "v1.0", "powershell.exe");
            var start = new ProcessStartInfo(powershell,
                "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -Mode gui");
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = runtimeReady;
            Process.Start(start);
        }
        catch (Exception ex)
        {
            MessageBox.Show("工作台未能启动：" + ex.Message,
                "本地影音创作工作台", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
