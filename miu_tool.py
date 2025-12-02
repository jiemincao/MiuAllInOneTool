import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import serial
import serial.tools.list_ports
import time
import threading
import queue
import os
import sys
import color_def
from ota_panel import OTAPanel
from ping_panel import PingPanel
from topology_panel import TopologyPanel

def resource_path(relative_path):
    """ 獲取資源的絕對路徑，適用於開發環境和 PyInstaller 打包環境 """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# =====================================================================
#  主程式 MiuAllInOneTool
# =====================================================================
class MiuAllInOneTool:
    def __init__(self, root):
        self.root = root
        self.root.title("Miu All-In-One Tool v1.0.0")
        try:
            self.root.iconbitmap(resource_path("Rafel.ico"))
        except Exception as e:
            print(f"Warning: Could not load icon: {e}")
        self.root.geometry("1100x850") 
        self.root.configure(bg=color_def.COLOR_BG_MAIN)
        
        # --- 全域物件 ---
        self.ser = serial.Serial()
        self.is_connected = False
        self.uart_thread = None
        self._stop_uart_event = threading.Event()
        
        # [新增] 用於控制是否暫停主序列埠讀取執行緒的旗標 (給 OTA 用)
        self.pause_serial_reader = False

        # --- 資料佇列 ---
        self.ota_queue = queue.Queue()   
        self.text_queue = queue.Queue()  
        self.gui_update_queue = queue.Queue() 
        self.rx_queue = queue.Queue()

        # --- 日誌檔案相關變數 ---
        self.log_file = None
        self.log_filename = ""
        self.open_log_file()

        self.setup_styles()
        self.create_gui()
        
        self.root.after(100, self.process_text_queue)
        self.root.after(100, self.process_gui_updates)
        self.refresh_ports()
        
        self.log("Application started.")

    def open_log_file(self):
        try:
            log_dir = "logs"
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            self.log_filename = os.path.join(log_dir, f"session_{timestamp}.txt")
            self.log_file = open(self.log_filename, "a", encoding="utf-8")
            start_time_str = time.strftime("%Y-%m-%d %H:%M:%S")
            self.log_file.write(f"--- Log Session Started at {start_time_str} ---\n")
            print(f"Log file created: {self.log_filename}")
        except Exception as e:
            print(f"Error opening log file: {e}")
            messagebox.showerror("Log Error", f"Could not create log file:\n{e}")

    def setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure("TCombobox", fieldbackground=color_def.COLOR_BTN_BG, background=color_def.COLOR_BTN_BG, foreground=color_def.COLOR_TEXT, arrowcolor=color_def.COLOR_TEXT)
        self.style.configure("TNotebook", background=color_def.COLOR_BG_MAIN, tabmargins=[2, 5, 2, 0])
        self.style.configure("TNotebook.Tab", background=color_def.COLOR_BG_SUB, foreground=color_def.COLOR_TEXT, padding=[15, 8], font=("Arial", 10))
        self.style.map("TNotebook.Tab", background=[("selected", color_def.COLOR_BG_PANEL)], foreground=[("selected", color_def.COLOR_ACCENT_GREEN)])
        self.style.configure("TProgressbar", background=color_def.COLOR_ACCENT_GREEN, troughcolor=color_def.COLOR_BG_SUB, bordercolor=color_def.COLOR_BG_PANEL, lightcolor=color_def.COLOR_ACCENT_GREEN, darkcolor=color_def.COLOR_ACCENT_GREEN)
        self.style.configure("Treeview", background=color_def.COLOR_BG_SUB, foreground=color_def.COLOR_TEXT, fieldbackground=color_def.COLOR_BG_SUB, font=("Arial", 10))
        self.style.configure("Treeview.Heading", background=color_def.COLOR_BTN_BG, foreground=color_def.COLOR_TEXT, font=("Arial", 10, "bold"))
        self.style.map("Treeview", background=[('selected', color_def.COLOR_ACCENT_GREEN)], foreground=[('selected', color_def.COLOR_BTN_FG)])

    def create_gui(self):
        # 1. Header
        header_frame = tk.Frame(self.root, bg=color_def.COLOR_BG_SUB, padx=10, pady=10)
        header_frame.pack(side=tk.TOP, fill=tk.X)
        
        # 1.1 Connection Control
        conn_frame = tk.Frame(header_frame, bg=color_def.COLOR_BG_SUB)
        conn_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))
        tk.Label(conn_frame, text="COM Port:", bg=color_def.COLOR_BG_SUB, fg=color_def.COLOR_TEXT, font=("Arial", 11)).pack(side=tk.LEFT, padx=(0, 5))
        self.port_cb = ttk.Combobox(conn_frame, values=[], width=15, font=("Arial", 10))
        self.port_cb.pack(side=tk.LEFT, padx=(0, 5))
        btn_refresh_ports = tk.Button(conn_frame, text="↻", bg=color_def.COLOR_BTN_BG, fg=color_def.COLOR_BTN_FG, font=("Arial", 10, "bold"), relief=tk.FLAT, width=2, command=self.refresh_ports)
        btn_refresh_ports.pack(side=tk.LEFT, padx=(0, 15))
        tk.Label(conn_frame, text="Baud Rate:", bg=color_def.COLOR_BG_SUB, fg=color_def.COLOR_TEXT, font=("Arial", 11)).pack(side=tk.LEFT, padx=(0, 5))
        self.baud_cb = ttk.Combobox(conn_frame, values=["115200", "460800", "921600"], width=10, font=("Arial", 10))
        self.baud_cb.current(0)
        self.baud_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.btn_connect = tk.Button(conn_frame, text="Connect", bg=color_def.COLOR_ACCENT_GREEN, fg=color_def.COLOR_BTN_FG, width=12, font=("Arial", 10, "bold"), relief=tk.FLAT, command=self.toggle_connection)
        self.btn_connect.pack(side=tk.LEFT, padx=(0, 20))
        # [恢復] 狀態標籤
        self.status_label = tk.Label(conn_frame, text="Status: Disconnected", fg=color_def.COLOR_ACCENT_RED, bg=color_def.COLOR_BG_SUB, font=("Arial", 11, "bold"))
        self.status_label.pack(side=tk.LEFT)
        
        # [恢復] 分隔線
        tk.Frame(header_frame, bg="#555555", height=1).pack(fill='x', pady=(0, 10))
        
        # [恢復] 1.2 Dashboard
        dash_frame = tk.Frame(header_frame, bg=color_def.COLOR_BG_PANEL, padx=15, pady=10, relief=tk.RIDGE, bd=2)
        dash_frame.pack(side=tk.TOP, fill=tk.X)
        tk.Label(dash_frame, text="Device Role:", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT, font=("Arial", 12)).grid(row=0, column=0, padx=(0, 10), sticky="e")
        self.role_display = tk.Label(dash_frame, text="Unknown", bg=color_def.COLOR_BG_SUB, fg=color_def.COLOR_TEXT, relief=tk.FLAT, width=18, font=("Arial", 12, "bold"), pady=2)
        self.role_display.grid(row=0, column=1, padx=(0, 20))
        tk.Label(dash_frame, text="Network State:", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT, font=("Arial", 12)).grid(row=0, column=2, padx=(0, 10), sticky="e")
        self.state_display = tk.Label(dash_frame, text="Unknown", bg=color_def.COLOR_BG_SUB, fg=color_def.COLOR_TEXT, relief=tk.FLAT, width=18, font=("Arial", 12), pady=2)
        self.state_display.grid(row=0, column=3, padx=(0, 20))
        btn_refresh = tk.Button(dash_frame, text="↻ Refresh Status", bg=color_def.COLOR_BTN_BG, fg=color_def.COLOR_BTN_FG, font=("Arial", 10), relief=tk.FLAT, padx=10, command=self.refresh_dashboard)
        btn_refresh.grid(row=0, column=4)

        # 3. Log Area
        log_frame = tk.LabelFrame(self.root, text="System Log", bg=color_def.COLOR_BG_MAIN, fg=color_def.COLOR_TEXT, font=("Arial", 10, "bold"), labelanchor="nw")
        log_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, state='disabled', bg=color_def.COLOR_LOG_BG, fg=color_def.COLOR_LOG_FG, font=("Consolas", 10), insertbackground=color_def.COLOR_LOG_FG)
        self.log_text.pack(fill=tk.X, padx=2, pady=2)

        # 2. Notebook & Tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side=tk.TOP, expand=True, fill="both", padx=10, pady=(10, 0))
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        
        # Tab 1: Topology
        self.tab_topology = TopologyPanel(self.notebook, self.ser, self.log, self.rx_queue)
        self.notebook.add(self.tab_topology, text=" Topology Viewer ")
        
        # Tab 2: Ping Tool
        self.tab_ping = PingPanel(self.notebook, self.ser, self.log, self.rx_queue)
        self.notebook.add(self.tab_ping, text=" Ping Test ")

        # Tab 3: OTA Update
        # [關鍵修改] 傳遞 self (主程式實體) 給 OTAPanel
        self.tab_ota = OTAPanel(self, self.ser, self.log, self.ota_queue)
        self.notebook.add(self.tab_ota, text=" OTA Download ")

    # ===========================
    # 核心邏輯
    # ===========================
    def refresh_ports(self):
        self.log("Refreshing COM ports...")
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_cb['values'] = ports
        current = self.port_cb.get()
        if ports:
            if current not in ports: self.port_cb.current(0)
        else:
             self.port_cb.set("")

    def log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        formatted_msg = f"[{timestamp}] {msg}"
        self.text_queue.put(formatted_msg)
        if self.log_file and not self.log_file.closed:
            try:
                self.log_file.write(formatted_msg + "\n")
                self.log_file.flush()
            except Exception as e:
                print(f"Error writing to log file: {e}")

    def process_text_queue(self):
        while not self.text_queue.empty():
            msg = self.text_queue.get()
            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')
        self.root.after(100, self.process_text_queue)

    def process_gui_updates(self):
        try:
            while not self.gui_update_queue.empty():
                action, data = self.gui_update_queue.get_nowait()
                if action == "update_role":
                    self.update_dashboard_ui(data, "Attached")
                    self.log(f"[System] Dashboard updated: Role={data}")
        except queue.Empty:
            pass
        finally:
            self.root.after(200, self.process_gui_updates)

    def toggle_connection(self):
        if not self.is_connected:
            try:
                port = self.port_cb.get()
                if not port: return
                self.ser.port = port
                self.ser.baudrate = int(self.baud_cb.get())
                self.ser.timeout = 0.05
                self.ser.bytesize = serial.EIGHTBITS
                self.ser.parity = serial.PARITY_NONE
                self.ser.stopbits = serial.STOPBITS_ONE
                self.ser.rtscts = False
                self.ser.open()
                self.is_connected = True
                # [新增] 連線時重置旗標
                self.pause_serial_reader = False 
                self.btn_connect.config(text="Disconnect", bg=color_def.COLOR_ACCENT_RED)
                self.status_label.config(text="Status: Connected", fg=color_def.COLOR_ACCENT_GREEN)
                self.log(f"Connected to {self.ser.port} at {self.ser.baudrate}")
                self.start_uart_reader_thread()
                self.root.after(1000, self.refresh_dashboard)
            except Exception as e:
                messagebox.showerror("Connection Error", str(e))
                self.log(f"Connection failed: {e}")
        else:
            self.close_connection()

    def close_connection(self):
        if self.is_connected:
            self._stop_uart_event.set()
            if self.uart_thread and self.uart_thread.is_alive():
                self.uart_thread.join(timeout=1)
            if self.ser.is_open: self.ser.close()
            self.is_connected = False
            # [新增] 斷線時重置旗標
            self.pause_serial_reader = False
            self.btn_connect.config(text="Connect", bg=color_def.COLOR_ACCENT_GREEN)
            self.status_label.config(text="Status: Disconnected", fg=color_def.COLOR_ACCENT_RED)
            self.update_dashboard_ui("unknown", "Unknown")
            self.log("Connection closed.")

    def start_uart_reader_thread(self):
        self._stop_uart_event.clear()
        self.uart_thread = threading.Thread(target=self.uart_reader_task, daemon=True)
        self.uart_thread.start()

    # --- UART 讀取執行緒 ---
    def uart_reader_task(self):
        buffer = bytearray()
        line_buffer = bytearray()
        KNOWN_ROLES = ["leader", "router", "child", "detached", "disabled"]

        while not self._stop_uart_event.is_set() and self.ser.is_open:
            # [新增] 如果處於 OTA 模式，暫停讀取，讓 OTA Panel 獨占
            if self.pause_serial_reader:
                time.sleep(0.05)
                continue

            try:
                if self.ser.in_waiting > 0:
                    data = self.ser.read(self.ser.in_waiting)
                    
                    # --- 1. OTA 封包檢查 (雖然 OTA 模式下這段不會跑，但保留著以防萬一) ---
                    temp_buffer = buffer + data
                    ota_processed = False
                    while len(temp_buffer) >= 8:
                        if temp_buffer.startswith(bytes.fromhex('FFFCFCFF')):
                            packet_len = temp_buffer[4]
                            total_len = 5 + packet_len + 1
                            if len(temp_buffer) >= total_len:
                                packet = temp_buffer[:total_len]
                                temp_buffer = temp_buffer[total_len:]
                                self.ota_queue.put(packet)
                                ota_processed = True
                            else:
                                break
                        else:
                            break 
                    buffer = temp_buffer
                    
                    if ota_processed:
                        continue

                    # --- 2. 文字資料處理 ---
                    try:
                        next_ota_idx = temp_buffer.index(bytes.fromhex('FFFCFCFF'))
                        text_data_chunk = temp_buffer[:next_ota_idx]
                    except ValueError:
                        text_data_chunk = temp_buffer
                        buffer = bytearray()

                    line_buffer += text_data_chunk
                    
                    while b'\n' in line_buffer:
                        newline_idx = line_buffer.index(b'\n')
                        complete_line_bytes = line_buffer[:newline_idx+1]
                        line_buffer = line_buffer[newline_idx+1:]
                        
                        try:
                            decoded_line = complete_line_bytes.decode('utf-8', errors='ignore').strip()
                            if decoded_line:
                                self.log(f"[RX] {decoded_line}")
                                self.rx_queue.put(decoded_line)
                                lower_line = decoded_line.lower()
                                if lower_line in KNOWN_ROLES:
                                    self.gui_update_queue.put(("update_role", lower_line))
                        except Exception as e:
                            print(f"Error decoding line: {e}")
                            pass

                time.sleep(0.01)
            except Exception as e:
                self.log(f"UART Read Error: {e}")
                break

    def send_uart_cmd(self, cmd):
        if self.ser.is_open:
            self.ser.write(cmd.encode())
            self.log(f"[TX] {cmd.strip()}")
        else:
            messagebox.showwarning("Warning", "Not connected.")

    def refresh_dashboard(self):
        if not self.ser.is_open: return
        self.send_uart_cmd("ot state\r\n")

    def update_dashboard_ui(self, role, state):
        role_text = role.upper()
        role_fg, role_bg = color_def.COLOR_TEXT, color_def.COLOR_BG_SUB
        if role == "leader":
            role_fg, role_bg = "#FFFFFF", color_def.COLOR_ACCENT_RED
        elif role == "router":
            role_fg, role_bg = "#000000", color_def.COLOR_ACCENT_BLUE
        elif role == "child" or role == "detached" or role == "disabled":
            role_fg, role_bg = "#FFFFFF", color_def.COLOR_ACCENT_GREEN
            
        self.role_display.config(text=role_text, fg=role_fg, bg=role_bg)
        self.state_display.config(text=state.title(), fg=color_def.COLOR_ACCENT_GREEN)
        self.update_tabs_state(role)

    def update_tabs_state(self, role):
        pass
    
    def on_tab_changed(self, event):
        if hasattr(self, 'tab_topology'): self.tab_topology.stop_all_tasks()
        if hasattr(self, 'tab_ping'): self.tab_ping.stop_all_tasks()
        if hasattr(self, 'tab_ota'): self.tab_ota.stop_all_tasks()

    # --- [新增] 供 OTA Panel 呼叫的方法 ---
    def start_ota_mode(self):
        """ 暫停主程式的序列埠讀取，讓 OTA Panel 獨占控制 """
        if not self.pause_serial_reader:
            self.log("[System] Switching to OTA binary mode. Main reader paused.")
            self.status_label.config(text="Status: OTA Mode (Reader Paused)", fg=color_def.COLOR_ACCENT_BLUE)
            self.pause_serial_reader = True
            if self.ser and self.ser.is_open:
                self.ser.reset_input_buffer()

    def stop_ota_mode(self):
        """ 恢復主程式的序列埠讀取 """
        if self.pause_serial_reader:
            self.pause_serial_reader = False
            self.log("[System] OTA mode finished. Main reader resumed.")
            if self.ser and self.ser.is_open:
                 self.status_label.config(text="Status: Connected", fg=color_def.COLOR_ACCENT_GREEN)
            else:
                 self.status_label.config(text="Status: Disconnected", fg=color_def.COLOR_ACCENT_RED)

if __name__ == "__main__":
    root = tk.Tk()
    app = MiuAllInOneTool(root)
    
    def on_closing():
        app.close_connection()
        if app.log_file and not app.log_file.closed:
            end_time_str = time.strftime("%Y-%m-%d %H:%M:%S")
            app.log_file.write(f"--- Log Session Ended at {end_time_str} ---\n")
            app.log_file.close()
            print("Log file closed.")
        root.destroy()
        
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()