using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Windows.Forms;

namespace NovaUI
{
    public static class Engine
    {
        // ── Pending items (queued before the window is created) ───────────────
        class PendingButton { public string Text; public int X, Y, W, H; public Action Handler; }
        class PendingLabel  { public string Id, Text; public int X, Y, W, H; }

        static readonly List<PendingButton> pendingButtons  = new List<PendingButton>();
        static readonly List<PendingLabel>  pendingLabels   = new List<PendingLabel>();
        static readonly List<string>        pendingMessages = new List<string>();

        // ── Named-label registry (id -> Label control) ────────────────────────
        static readonly Dictionary<string, Label> namedLabels = new Dictionary<string, Label>(StringComparer.OrdinalIgnoreCase);

        static readonly object sync = new object();

        static string pendingTitle    = "Nova App";
        static int    pendingWidth    = 640;
        static int    pendingHeight   = 480;
        static string pendingIconPath = null;

        static Thread                 uiThread  = null;
        static Form                   mainForm  = null;
        static SynchronizationContext uiContext = null;

        // ── Public API ────────────────────────────────────────────────────────

        public static void CreateWindow(string title, int width, int height)
        {
            lock (sync)
            {
                pendingTitle  = string.IsNullOrEmpty(title) ? pendingTitle : title;
                if (width  > 0) pendingWidth  = width;
                if (height > 0) pendingHeight = height;
            }
        }

        public static void SetIcon(string path)
        {
            lock (sync)
            {
                pendingIconPath = path;
                if (IsUIRunning())
                {
                    string captured = path;
                    uiContext.Post(delegate(object state) { ApplyIconToForm(captured); }, null);
                }
            }
        }

        // ── Buttons ───────────────────────────────────────────────────────────

        public static void AddButton(string text, int x, int y, Action handler)
        {
            AddButton(text, x, y, 100, 32, handler);
        }

        public static void AddButton(string text, int x, int y, int width, int height, Action handler)
        {
            lock (sync)
            {
                if (IsUIRunning())
                {
                    string  _text    = text;
                    int     _x = x, _y = y, _w = width, _h = height;
                    Action  _handler = handler;
                    uiContext.Post(delegate(object state)
                    {
                        CreateButtonOnForm(_text, _x, _y, _w, _h, _handler);
                    }, null);
                }
                else
                {
                    pendingButtons.Add(new PendingButton
                    {
                        Text    = text,
                        X       = x, Y = y,
                        W       = width  <= 0 ? 100 : width,
                        H       = height <= 0 ? 32  : height,
                        Handler = handler
                    });
                }
            }
        }

        // ── Labels (unnamed) ─────────────────────────────────────────────────

        public static void AddLabel(string text, int x, int y)
        {
            AddLabelInternal(null, text, x, y, 400, 24);
        }

        public static void AddLabel(string text, int x, int y, int width, int height)
        {
            AddLabelInternal(null, text, x, y, width, height);
        }

        // ── Labels (named) ───────────────────────────────────────────────────
        // Nova syntax:  named_label("id", "Initial text", x, y, w, h)

        public static void AddNamedLabel(string id, string text, int x, int y, int width, int height)
        {
            AddLabelInternal(id, text, x, y, width, height);
        }

        static void AddLabelInternal(string id, string text, int x, int y, int width, int height)
        {
            lock (sync)
            {
                if (IsUIRunning())
                {
                    string _id = id, _text = text;
                    int    _x = x, _y = y, _w = width, _h = height;
                    uiContext.Post(delegate(object state)
                    {
                        CreateLabelOnForm(_id, _text, _x, _y, _w, _h);
                    }, null);
                }
                else
                {
                    pendingLabels.Add(new PendingLabel
                    {
                        Id   = id,
                        Text = text,
                        X    = x, Y = y,
                        W    = width  <= 0 ? 400 : width,
                        H    = height <= 0 ? 24  : height
                    });
                }
            }
        }

        // ── set_label: update a named label's text at runtime ─────────────────
        // Nova syntax:  set_label("id", expr)

        public static void UpdateLabel(string id, string newText)
        {
            if (string.IsNullOrEmpty(id)) return;
            lock (sync)
            {
                if (IsUIRunning())
                {
                    string _id = id, _text = newText;
                    uiContext.Post(delegate(object state)
                    {
                        Label lbl;
                        if (namedLabels.TryGetValue(_id, out lbl))
                            lbl.Text = _text ?? "";
                    }, null);
                }
                else
                {
                    foreach (PendingLabel pl in pendingLabels)
                    {
                        if (string.Equals(pl.Id, id, StringComparison.OrdinalIgnoreCase))
                            pl.Text = newText ?? "";
                    }
                }
            }
        }

        // ── Messages ──────────────────────────────────────────────────────────

        public static void ShowMessage(string message)
        {
            lock (sync)
            {
                if (IsUIRunning())
                {
                    string _msg = message;
                    uiContext.Post(delegate(object state)
                    {
                        try
                        {
                            MessageBox.Show(mainForm, _msg,
                                mainForm != null ? mainForm.Text : "Message",
                                MessageBoxButtons.OK, MessageBoxIcon.Information);
                        }
                        catch { MessageBox.Show(_msg); }
                    }, null);
                }
                else
                {
                    pendingMessages.Add(message);
                }
            }
        }

        // ── Run / Start ───────────────────────────────────────────────────────

        public static void Run()
        {
            lock (sync)
            {
                if (IsUIRunning()) return;

                ManualResetEvent started = new ManualResetEvent(false);

                uiThread = new Thread(delegate()
                {
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);

                    mainForm = new Form();
                    mainForm.Text          = pendingTitle;
                    mainForm.StartPosition = FormStartPosition.CenterScreen;
                    mainForm.ClientSize    = new Size(pendingWidth, pendingHeight);

                    ApplyIconToForm(pendingIconPath);

                    lock (sync)
                    {
                        foreach (PendingLabel pl in pendingLabels)
                            CreateLabelOnForm(pl.Id, pl.Text, pl.X, pl.Y, pl.W, pl.H);
                        pendingLabels.Clear();

                        foreach (PendingButton pb in pendingButtons)
                            CreateButtonOnForm(pb.Text, pb.X, pb.Y, pb.W, pb.H, pb.Handler);
                        pendingButtons.Clear();
                    }

                    mainForm.Shown += delegate(object s, EventArgs e)
                    {
                        lock (sync)
                        {
                            foreach (string msg in pendingMessages)
                            {
                                try
                                {
                                    MessageBox.Show(mainForm, msg, mainForm.Text,
                                        MessageBoxButtons.OK, MessageBoxIcon.Information);
                                }
                                catch { MessageBox.Show(msg); }
                            }
                            pendingMessages.Clear();
                        }
                    };

                    uiContext = SynchronizationContext.Current
                                ?? new WindowsFormsSynchronizationContext();
                    started.Set();
                    Application.Run(mainForm);

                    uiContext = null;
                    mainForm  = null;
                });

                uiThread.IsBackground = false;
                uiThread.SetApartmentState(ApartmentState.STA);
                uiThread.Name = "NovaUI-UIThread";
                uiThread.Start();
                started.WaitOne(2000);
            }

            if (uiThread != null)
                uiThread.Join();
        }

        public static void Start()
        {
            Run();
        }

        public static void ExitApp()
        {
            if (IsUIRunning())
            {
                uiContext.Post(delegate(object state)
                {
                    try   { mainForm.Close(); }
                    catch { Application.Exit(); }
                }, null);
            }
        }

        // ── Private helpers ───────────────────────────────────────────────────

        static bool IsUIRunning()
        {
            return uiThread != null && uiThread.IsAlive && uiContext != null && mainForm != null;
        }

        static void ApplyIconToForm(string path)
        {
            try
            {
                if (mainForm == null) return;
                Icon ic = null;
                if (!string.IsNullOrEmpty(path))
                {
                    string p = Path.IsPathRooted(path) ? path
                               : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, path);
                    if (File.Exists(p)) ic = new Icon(p);
                }
                mainForm.Icon = ic ?? SystemIcons.Application;
            }
            catch { }
        }

        static void CreateButtonOnForm(string text, int x, int y, int width, int height, Action handler)
        {
            if (mainForm == null) return;
            try
            {
                Button btn = new Button();
                btn.Text     = text ?? "";
                btn.Size     = new Size(width  <= 0 ? 100 : width, height <= 0 ? 32 : height);
                btn.Location = new Point(x, y);

                if (handler != null)
                {
                    Action captured = handler;
                    btn.Click += delegate(object s, EventArgs e)
                    {
                        try   { captured(); }
                        catch (Exception ex)
                        {
                            MessageBox.Show(mainForm, ex.ToString(), "Handler Exception",
                                MessageBoxButtons.OK, MessageBoxIcon.Error);
                        }
                    };
                }
                mainForm.Controls.Add(btn);
            }
            catch { }
        }

        static void CreateLabelOnForm(string id, string text, int x, int y, int width, int height)
        {
            if (mainForm == null) return;
            try
            {
                Label lbl = new Label();
                lbl.Text     = text ?? "";
                lbl.Size     = new Size(width  <= 0 ? 400 : width, height <= 0 ? 24 : height);
                lbl.Location = new Point(x, y);
                mainForm.Controls.Add(lbl);

                if (!string.IsNullOrEmpty(id))
                    namedLabels[id] = lbl;
            }
            catch { }
        }
    }
}
