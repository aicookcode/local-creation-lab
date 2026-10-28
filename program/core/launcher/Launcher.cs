using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
[assembly: AssemblyTitle("Local Media Studio")]
[assembly: AssemblyDescription("Offline audio and video creation workstation")]
[assembly: AssemblyVersion("7.0.0.0")]
[assembly: AssemblyFileVersion("7.0.0.0")]
static class Launcher {
 [STAThread]
 static void Main() {
  string root = AppDomain.CurrentDomain.BaseDirectory;
  string python = Path.Combine(root, "program", "runtime", "python", "pythonw.exe");
  string entry = Path.Combine(root, "program", "program_entry.py");
  if (!File.Exists(python) || !File.Exists(entry)) {
   MessageBox.Show("请完整解压或复制工作台文件夹，再打开本程序。缺少运行环境或入口文件。", "本地影音创作工作台", MessageBoxButtons.OK, MessageBoxIcon.Error);
   return;
  }
  try {
   var start = new ProcessStartInfo(python, "-X utf8 \"" + entry + "\"");
   start.WorkingDirectory = root;
   start.UseShellExecute = false;
   start.CreateNoWindow = true;
   start.EnvironmentVariables.Remove("PYTHONHOME");
   start.EnvironmentVariables.Remove("PYTHONPATH");
   start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
   start.EnvironmentVariables["PYTHONUTF8"] = "1";
   start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
   start.EnvironmentVariables["PYTHONPYCACHEPREFIX"] = Path.Combine(root,"cache","pycache");
   Process.Start(start);
  } catch (Exception ex) {
   MessageBox.Show("工作台未能启动：" + ex.Message, "本地影音创作工作台", MessageBoxButtons.OK, MessageBoxIcon.Error);
  }
 }
}
