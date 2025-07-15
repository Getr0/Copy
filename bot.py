import tkinter as tk;import customtkinter as ctk;from tkinter import messagebox;import re;import os;import sqlite3;import datetime;import threading;import asyncio;import json;import glob;import logging;import requests;from flask import Flask, jsonify, request;from telethon import TelegramClient, events, types;from telethon.errors import SessionPasswordNeededError, RPCError;from telethon.sessions import SQLiteSession;import concurrent.futures;API_ID = 24670509;API_HASH = '0ca1de09bc2b41dfd98168b84cc88d7b';DB_NAME = 'signals.db';SESSIONS_DIR = 'sessions';LOGGING_LEVEL = logging.INFO;logging.basicConfig(level=LOGGING_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s');log = logging.getLogger('werkzeug');log.setLevel(logging.ERROR);PIP_SIZE_XAUUSD = 0.1;DEFAULT_SL_PIPS = 40.0;INITIAL_TRADE_1_TP_PIPS = 40.0;INITIAL_TRADE_2_DEFAULT_TP_PIPS = 200.0;REENTRY_TP_PIPS = 40.0;SIGNAL_TYPE_INITIAL_T1 = "INITIAL_T1";SIGNAL_TYPE_INITIAL_T2_DEFAULT = "INITIAL_T2_DEFAULT";SIGNAL_TYPE_UPDATE_T2 = "UPDATE_T2";SIGNAL_TYPE_RE_ENTRY = "RE_ENTRY";SIGNAL_TYPE_IGNORE = "IGNORE";SIGNAL_TYPE_UNKNOWN = "UNKNOWN";SIGNAL_TYPE_STANDARD = "STANDARD";db_lock = threading.Lock()
def _check_and_add_column(cursor, table_name, column_name, column_type):
    cursor.execute(f"PRAGMA table_info({table_name})");columns = [row[1] for row in cursor.fetchall()]
    if column_name not in columns:
        logging.info(f"Aktualizuji databázi: Přidávám sloupec '{column_name}' do tabulky '{table_name}'.");cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
def init_db():
    with db_lock, sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("PRAGMA foreign_keys=off;")

        # Create a new, clean 'signals' table
        c.execute('''
            CREATE TABLE IF NOT EXISTS signals_new (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               signal_group_id TEXT,
               trade_label TEXT,
               symbol TEXT NOT NULL,
               action TEXT NOT NULL,
               entry_price REAL,
               sl_value REAL,
               sl_value_type TEXT,
               tp_value REAL,
               tp_value_type TEXT,
               status TEXT NOT NULL,
               ticket INTEGER,
               signal_type TEXT,
               timestamp DATETIME NOT NULL
            )
        ''')

        # Drop the old 'signals' table if it exists
        c.execute("DROP TABLE IF EXISTS signals")
        # Rename the new table to 'signals'
        c.execute("ALTER TABLE signals_new RENAME TO signals")
        logging.info("Tabulka 'signals' byla vytvořena/přestavěna na zjednodušenou strukturu.")

        # Create the new 'trade_actions' table
        c.execute('''
            CREATE TABLE IF NOT EXISTS trade_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_signal_id INTEGER,
                target_ticket INTEGER,
                action_type TEXT NOT NULL,
                trigger_event TEXT NOT NULL,
                trigger_ticket INTEGER,
                params_json TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logging.info("Tabulka 'trade_actions' zkontrolována/vytvořena.")

        c.execute("PRAGMA foreign_keys=on;")
        c.execute("DELETE FROM signals WHERE status = 'new' OR status IS NULL")
        conn.commit()
class SessionManager:
    def __init__(self, base_dir=SESSIONS_DIR):
        self.sessions_dir = base_dir;os.makedirs(self.sessions_dir, exist_ok=True)
    def _clean_phone_number(self, phone): return re.sub(r"\D", "", phone)
    def get_session_path(self, phone): return os.path.join(self.sessions_dir, self._clean_phone_number(phone))
    def get_saved_phone_numbers(self):
        phone_numbers = []
        for session_file in glob.glob(os.path.join(self.sessions_dir, "*.session")):
            phone_numbers.append("+" + os.path.basename(session_file).replace(".session", ""));return phone_numbers
    def remove_phone_number(self, phone):
        session_path = self.get_session_path(phone) + ".session"
        if os.path.exists(session_path):
            try:
                os.remove(session_path);logging.info(f"Session soubor {session_path} úspěšně smazán.");return True
            except OSError as e:
                logging.error(f"Nepodařilo se smazat session soubor {session_path}: {e}");return False
        else:
            logging.warning(f"Session soubor {session_path} pro smazání nenalezen.");return False
from parsing_logic import parse_sniper_pro, parse_standard_signal
from parsing_logic import (SIGNAL_TYPE_IGNORE, SIGNAL_TYPE_UNKNOWN,
                           SIGNAL_TYPE_RE_ENTRY, SIGNAL_TYPE_INITIAL,
                           SIGNAL_TYPE_UPDATE_SLTP, SIGNAL_TYPE_STANDARD)

CONFIG_FILE = 'parsing_config.json'

def load_parsing_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Return a default structure if file is missing or corrupt
        return {
            "SniperPro": {
                "trade_1": {"sl_pips": 40.0, "tp_pips": 40.0},
                "trade_2": {"default_tp_pips": 200.0, "reentry_tp_pips": 40.0},
                "functions": {
                    "be_active": True, "ts_active": True, "ts_type": "Classic",
                    "classic_ts": {"start_pips": 20.0, "step_pips": 10.0, "distance_pips": 15.0},
                    "convergent_ts": {"activation_start_pips": 30.0, "converge_factor": 0.5, "min_stop_distance_pips": 10.0}
                }
            },
            "Standard": {
                "functions": {
                    "be_active": False, "ts_active": False, "ts_type": "Classic",
                    "classic_ts": {"start_pips": 20.0, "step_pips": 10.0, "distance_pips": 15.0},
                    "convergent_ts": {"activation_start_pips": 30.0, "converge_factor": 0.5, "min_stop_distance_pips": 10.0}
                }
            }
        }

def save_parsing_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except IOError as e:
        logging.error(f"Failed to save parsing config: {e}")

class TelegramBotApp(ctk.CTk):
    def __init__(self):
        super().__init__();self.title("Telegram Signal Monitor"); self.geometry("1000x700");ctk.set_appearance_mode("dark");self.BG_COLOR, self.FRAME_COLOR, self.TEXT_COLOR = "#2B2939", "#363347", "#EAEAEA";self.ACCENT_COLOR, self.ACCENT_HOVER_COLOR = "#E91E63", "#C2185B";self.ENTRY_BG_COLOR, self.RED_COLOR, self.RED_HOVER_COLOR = "#22212C", "#D32F2F", "#B71C1C";self.FONT_NORMAL, self.FONT_BOLD = ("Segoe UI", 12), ("Segoe UI", 12, "bold");self.FONT_TITLE, self.FONT_LOG = ("Segoe UI", 18, "bold"), ("Consolas", 10);self.session_manager = SessionManager();self.client_loop, self.client_thread, self.main_client = None, None, None;self.client_running = False;self.monitoring_handlers, self.monitoring_states = {}, {};self.parsing_methods = {};self.channel_contexts = {};self.processed_message_ids = set();self.message_id_lock = threading.Lock()
        self.parsing_config = load_parsing_config()
        self.function_defaults = {};self._init_default_function_settings();self._create_widgets();self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _init_default_function_settings(self):
        self.parsing_config = load_parsing_config()
        parser_types = list(self.parsing_config.keys())
        for p_type in parser_types:
            config = self.parsing_config[p_type]['functions']
            self.function_defaults[p_type] = {
                'be_active': tk.BooleanVar(value=config.get('be_active', False)),
                'ts_active': tk.BooleanVar(value=config.get('ts_active', False)),
                'ts_type': tk.StringVar(value=config.get('ts_type', 'Classic')),
                'classic_ts_start_pips': tk.DoubleVar(value=config.get('classic_ts', {}).get('start_pips', 20.0)),
                'classic_ts_step_pips': tk.DoubleVar(value=config.get('classic_ts', {}).get('step_pips', 10.0)),
                'classic_ts_distance_pips': tk.DoubleVar(value=config.get('classic_ts', {}).get('distance_pips', 15.0)),
                'convergent_activation_start_pips': tk.DoubleVar(value=config.get('convergent_ts', {}).get('activation_start_pips', 30.0)),
                'convergent_converge_factor': tk.DoubleVar(value=config.get('convergent_ts', {}).get('converge_factor', 0.5)),
                'convergent_min_stop_distance_pips': tk.DoubleVar(value=config.get('convergent_ts', {}).get('min_stop_distance_pips', 10.0))
            }
    def _create_widgets(self):
        self.grid_columnconfigure(0, weight=1);self.grid_rowconfigure(3, weight=1);top_controls_frame = ctk.CTkFrame(self, fg_color="transparent");top_controls_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(15,10));login_frame = ctk.CTkFrame(top_controls_frame, fg_color=self.FRAME_COLOR, corner_radius=8);login_frame.pack(side="left", fill="x", expand=True, padx=(0,10));login_frame.grid_columnconfigure(1, weight=1);ctk.CTkLabel(login_frame, text="Tel. číslo:", font=self.FONT_BOLD).grid(row=0, column=0, padx=(10,5), pady=10, sticky="w");self.phone_entry_var = tk.StringVar();self.phone_entry = ctk.CTkEntry(login_frame, textvariable=self.phone_entry_var, font=self.FONT_NORMAL,fg_color=self.ENTRY_BG_COLOR, border_width=0, corner_radius=6);self.phone_entry.grid(row=0, column=1, sticky="ew", padx=0, pady=10);manage_button = ctk.CTkButton(login_frame, text="Vybrat / Spravovat", command=self._show_phone_selector,font=self.FONT_NORMAL, fg_color=self.BG_COLOR, hover_color=self.ENTRY_BG_COLOR,corner_radius=6, width=140);manage_button.grid(row=0, column=2, padx=5, pady=10);connect_button = ctk.CTkButton(login_frame, text="Připojit", command=self._connect_telegram,font=self.FONT_BOLD, fg_color=self.ACCENT_COLOR, hover_color=self.ACCENT_HOVER_COLOR,corner_radius=6, width=100);connect_button.grid(row=0, column=3, padx=(0,10), pady=10);functions_button = ctk.CTkButton(top_controls_frame, text="⚙️ Funkce", command=self._show_functions_dialog,font=self.FONT_BOLD, fg_color=self.FRAME_COLOR, hover_color=self.ENTRY_BG_COLOR,corner_radius=6, width=120);functions_button.pack(side="left", padx=(0,0), pady=10);channels_header_frame = ctk.CTkFrame(self, fg_color="transparent");channels_header_frame.grid(row=2, column=0, sticky="ew", padx=15, pady=(10, 5));channels_header_frame.grid_columnconfigure(0, weight=1);ctk.CTkLabel(channels_header_frame, text="Kanály a skupiny", font=self.FONT_TITLE, anchor="w").grid(row=0, column=0, sticky="w");self.refresh_button = ctk.CTkButton(channels_header_frame, text="🔄 Obnovit", command=self._load_dialogs,state=tk.DISABLED, font=self.FONT_NORMAL, fg_color=self.FRAME_COLOR,hover_color=self.ENTRY_BG_COLOR, corner_radius=6, width=100);self.refresh_button.grid(row=0, column=1, sticky="e");self.channels_list_frame = ctk.CTkScrollableFrame(self, fg_color=self.FRAME_COLOR, corner_radius=8);self.channels_list_frame.grid(row=3, column=0, sticky="nsew", padx=15, pady=(0,10));self.channels_list_frame.grid_columnconfigure(0, weight=1);log_frame = ctk.CTkFrame(self, fg_color=self.FRAME_COLOR, corner_radius=8);log_frame.grid(row=4, column=0, sticky="ew", padx=15, pady=(0,15));log_frame.grid_columnconfigure(0, weight=1);ctk.CTkLabel(log_frame, text="Protokol událostí", font=self.FONT_BOLD, anchor="w").grid(row=0, column=0, padx=10, pady=(8,4), sticky="w");self.log_text = ctk.CTkTextbox(log_frame, state=tk.DISABLED, font=self.FONT_LOG,fg_color=self.ENTRY_BG_COLOR, corner_radius=6, border_width=0,wrap="word", height=120);self.log_text.grid(row=1, column=0, sticky="ew", padx=10, pady=(0,10))
    def _update_log(self, text, level="INFO"):
        def task():
            current_text = self.log_text.get("1.0", tk.END);lines = current_text.splitlines()
            if len(lines) > 1000:
                current_text = "\n".join(lines[-1000:]) + "\n";self.log_text.configure(state=tk.NORMAL);self.log_text.delete("1.0", tk.END);self.log_text.insert("1.0", current_text)
            self.log_text.configure(state=tk.NORMAL);self.log_text.insert(tk.END, f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {text}\n");self.log_text.configure(state=tk.DISABLED); self.log_text.see(tk.END)
        self.after(0, task);logging.log(getattr(logging, level.upper(), logging.INFO), text)
    def _get_auth_code_or_pass(self, title, future_to_set):
        self._update_log(f"Otevírám dialog pro: {title}", "DEBUG");dialog = ctk.CTkToplevel(self);dialog.title(title);dialog.geometry("380x200");dialog.resizable(False, False);dialog.transient(self);dialog.grab_set();dialog.configure(fg_color=self.BG_COLOR);dialog.attributes("-topmost", True);main_frame = ctk.CTkFrame(dialog, fg_color="transparent");main_frame.pack(padx=20, pady=20, fill="both", expand=True);ctk.CTkLabel(main_frame, text=f"Zadejte {title.lower()}:", font=self.FONT_BOLD).pack(pady=(0, 10));entry_var = tk.StringVar();entry = ctk.CTkEntry(main_frame, textvariable=entry_var,show="*" if "heslo" in title.lower() else "",font=self.FONT_NORMAL, fg_color=self.ENTRY_BG_COLOR,border_width=0, corner_radius=6);entry.pack(pady=5, fill='x');entry.focus()
        def on_submit(event=None):
            value = entry_var.get();self._update_log(f"Dialog '{title}': Potvrzeno s hodnotou '{value if title != '2FA heslo' else '********'}'.", "DEBUG")
            if not future_to_set.done(): future_to_set.set_result(value)
            else: self._update_log(f"Dialog '{title}': Future již byla nastavena, druhé potvrzení ignorováno.", "WARNING")
            dialog.destroy()
        def on_dialog_close():
            self._update_log(f"Dialog '{title}': Uzavřen křížkem.", "DEBUG")
            if not future_to_set.done(): future_to_set.set_result(None)
            else: self._update_log(f"Dialog '{title}': Future již byla nastavena, uzavření křížkem po potvrzení.", "DEBUG")
            dialog.destroy()
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent");btn_frame.pack(pady=(15,0), fill='x');btn_frame.grid_columnconfigure((0,1), weight=1);cancel_btn = ctk.CTkButton(btn_frame, text="Zrušit", font=self.FONT_NORMAL,fg_color=self.FRAME_COLOR, hover_color=self.ENTRY_BG_COLOR,command=on_dialog_close, corner_radius=6);cancel_btn.grid(row=0, column=0, sticky='ew', padx=(0,5), pady=5);confirm_btn = ctk.CTkButton(btn_frame, text="Potvrdit", font=self.FONT_BOLD,fg_color=self.ACCENT_COLOR, hover_color=self.ACCENT_HOVER_COLOR,command=on_submit, corner_radius=6);confirm_btn.grid(row=0, column=1, sticky='ew', padx=(5,0), pady=5);dialog.bind("<Return>", on_submit);dialog.protocol("WM_DELETE_WINDOW", on_dialog_close);self.wait_window(dialog)
        if not future_to_set.done():
            self._update_log(f"Dialog '{title}': Uzavřen bez nastavení future (fallback). Nastavuji None.", "WARNING");future_to_set.set_result(None)
    def _connect_telegram(self):
        phone_number = self.phone_entry_var.get()
        if not re.match(r'^\+\d+$', phone_number):
            messagebox.showerror("Chyba", "Zadejte platné telefonní číslo v mezinárodním formátu (např. +420123456789).", parent=self);return
        if self.client_thread and self.client_thread.is_alive():
            self._update_log("Detekováno běžící klientské vlákno. Pokouším se ho nejprve řádně ukončit...", "INFO");self._shutdown_client()
            if self.client_thread and self.client_thread.is_alive():
                 self._update_log("VAROVÁNÍ: Předchozí klientské vlákno se nepodařilo plně ukončit. Nové připojení může selhat.", "WARNING")
        if self.client_running:
            self._update_log("Klient byl stále označen jako běžící, volám _shutdown_client znovu (pojistka).", "DEBUG");self._shutdown_client()
        self._update_log("Resetuji interní stavy pro nové připojení.", "DEBUG");self.monitoring_handlers, self.monitoring_states = {}, {};self.parsing_methods = {};self.channel_contexts = {};
        for widget in self.channels_list_frame.winfo_children(): widget.destroy()
        self._update_log(f"Spouštím nové připojení pro {phone_number}...");self.client_loop = asyncio.new_event_loop();self.client_thread = threading.Thread(target=self._client_worker, args=(phone_number,), daemon=True);self.client_thread.start();self.after(100, self._check_client_connection)
    def _client_worker(self, phone_number):
        asyncio.set_event_loop(self.client_loop)
        try:
            self.client_loop.run_until_complete(self._run_client(phone_number))
        except Exception as e:
            self._update_log(f"Kritická chyba nebo přerušení v klientském vlákně: {e}", "ERROR")
        finally:
            if self.main_client and self.main_client.is_connected():
                try:
                    self.client_loop.run_until_complete(self.main_client.disconnect());self._update_log("Klient byl odpojen ve finally bloku workeru.", "INFO")
                except Exception as e:
                    self._update_log(f"Chyba při odpojování klienta ve finally bloku workeru: {e}", "ERROR")
            if self.client_loop.is_running():
                self.client_loop.call_soon_threadsafe(self.client_loop.stop)
            if not self.client_loop.is_running():
                self._update_log(f"Asyncio smyčka ({id(self.client_loop)}) byla zastavena, uzavírám ji.", "DEBUG");self.client_loop.close()
            self.main_client = None;self.client_running = False;self.after(0, lambda: self.refresh_button.configure(state=tk.DISABLED));self._update_log("Klientské vlákno bylo definitivně ukončeno.", "INFO")
    async def _run_client(self, phone_number):
        session_path = self.session_manager.get_session_path(phone_number);client = TelegramClient(SQLiteSession(session_path), API_ID, API_HASH, loop=self.client_loop);connected_successfully = False;authorized_successfully = False
        try:
            self._update_log(f"Pokus o připojení k Telegramu pro {phone_number}...");await client.connect()
            if not client.is_connected():
                self._update_log("Nepodařilo se připojit k Telegramu.", "ERROR");return
            self._update_log("Úspěšně připojeno k Telegramu.");connected_successfully = True
            if not await client.is_user_authorized():
                self._update_log("Uživatel není autorizován. Zahajuji proces autorizace...");phone_code_hash = None
                try:
                    self._update_log(f"Zasílám ověřovací kód na {phone_number}...");sent_code_obj = await client.send_code_request(phone_number);phone_code_hash = sent_code_obj.phone_code_hash;self._update_log("Ověřovací kód byl odeslán.")
                except RPCError as e:
                    self._update_log(f"Chyba při zasílání kódu (RPCError): {e}", "ERROR")
                    if "PHONE_NUMBER_INVALID" in str(e).upper():
                         messagebox.showerror("Chyba Autorizace", f"Zadané telefonní číslo {phone_number} je neplatné.", parent=self)
                    return
                except Exception as e:
                    self._update_log(f"Obecná chyba při zasílání kódu: {e}", "ERROR");return
                future_code = self.client_loop.create_future();self.after(0, self._get_auth_code_or_pass, "ověřovací kód", future_code);self._update_log("Čekám na zadání ověřovacího kódu od uživatele (future)...", "DEBUG");code = await future_code
                if not code:
                    self._update_log(f"Uživatel nezadal ověřovací kód (výsledek dialogu: '{code}'). Autorizace zrušena.", "WARNING");return
                self._update_log(f"Ověřovací kód '{code}' přijat od uživatele. Pokouším se přihlásit.", "INFO")
                try:
                    await client.sign_in(phone_number, code, phone_code_hash=phone_code_hash);authorized_successfully = True;self._update_log("Přihlášení pomocí kódu bylo úspěšné.")
                except SessionPasswordNeededError:
                    self._update_log("Vyžadováno heslo pro dvoufázové ověření (2FA).");future_password = self.client_loop.create_future();self.after(0, self._get_auth_code_or_pass, "2FA heslo", future_password);self._update_log("Čekám na zadání 2FA hesla od uživatele (future)...", "DEBUG");password = await future_password
                    if not password:
                        self._update_log(f"Uživatel nezadal 2FA heslo (výsledek dialogu: '{password}'). Autorizace zrušena.", "WARNING");return
                    self._update_log("2FA heslo přijato. Pokouším se přihlásit pomocí 2FA hesla...", "INFO")
                    try:
                        await client.sign_in(password=password);authorized_successfully = True;self._update_log("Přihlášení pomocí 2FA hesla bylo úspěšné.")
                    except RPCError as e_2fa:
                        self._update_log(f"Chyba při přihlašování pomocí 2FA hesla (RPCError): {e_2fa}", "ERROR")
                        if "PASSWORD_HASH_INVALID" in str(e_2fa).upper():
                             messagebox.showerror("Chyba Autorizace", "Zadané 2FA heslo je neplatné.", parent=self)
                        return
                    except Exception as e_2fa_generic:
                        self._update_log(f"Obecná chyba při přihlašování pomocí 2FA hesla: {e_2fa_generic}", "ERROR");return
                except RPCError as e_code:
                    logging.debug(f"Pokus o přihlášení s kódem '{code}' selhal.");self._update_log(f"Chyba při přihlašování pomocí kódu (RPCError): {e_code}", "ERROR")
                    if "PHONE_CODE_INVALID" in str(e_code).upper():
                         messagebox.showerror("Chyba Autorizace", "Zadaný ověřovací kód je neplatný.", parent=self)
                    elif "PHONE_CODE_EXPIRED" in str(e_code).upper():
                         messagebox.showerror("Chyba Autorizace", "Ověřovací kód vypršel. Zkuste to prosím znovu.", parent=self)
                    elif "FLOOD_WAIT" in str(e_code).upper():
                        wait_time = re.search(r"FLOOD_WAIT_(\d+)", str(e_code));wait_msg = f"Příliš mnoho pokusů. Zkuste to prosím znovu za {wait_time.group(1) if wait_time else 'chvíli'}.";messagebox.showerror("Chyba Autorizace", wait_msg, parent=self)
                    return
                except Exception as e_code_generic:
                    logging.debug(f"Pokus o přihlášení s kódem '{code}' selhal s obecnou chybou.");self._update_log(f"Obecná chyba při přihlašování pomocí kódu: {e_code_generic}", "ERROR");return
            else:
                authorized_successfully = True;self._update_log("Uživatel je již autorizován.")
            if authorized_successfully:
                me = await client.get_me()
                if me:
                    self._update_log(f"Úspěšně přihlášen jako: {me.first_name} {me.last_name or ''} (ID: {me.id})");self.main_client = client;self.client_running = True;self.after(0, lambda: self.refresh_button.configure(state=tk.NORMAL));self.after(0, self._load_dialogs);await client.run_until_disconnected()
                else:
                    self._update_log("Nepodařilo se získat informace o přihlášeném uživateli.", "ERROR")
        except ConnectionError as e:
            self._update_log(f"Chyba připojení: {e}. Zkontrolujte internetové připojení.", "ERROR")
        except RPCError as e:
            self._update_log(f"Obecná chyba RPC během běhu klienta: {e}", "ERROR")
        except Exception as e:
            if isinstance(e, asyncio.CancelledError):
                self._update_log("Běh klienta byl zrušen.", "INFO")
            else:
                self._update_log(f"Neočekávaná chyba během běhu klienta: {e}", "CRITICAL")
        finally:
            self._update_log("Vstupuji do `finally` bloku _run_client.", "DEBUG")
            if client.is_connected() and connected_successfully:
                self._update_log("Odpojuji klienta v `finally` bloku _run_client...", "INFO");await client.disconnect();self._update_log("Klient odpojen.", "INFO")
            if not (self.main_client and self.client_running):
                self.main_client = None;self.client_running = False;self.after(0, lambda: self.refresh_button.configure(state=tk.DISABLED))
            self._update_log(f"Ukončuji _run_client pro {phone_number}. client_running={self.client_running}", "INFO")
    def _check_client_connection(self):
        if self.client_running and self.main_client and self.main_client.is_connected():
            self.refresh_button.configure(state=tk.NORMAL)
        elif self.client_thread and self.client_thread.is_alive():
            self.after(200, self._check_client_connection)
        else:
            self._update_log("Připojení selhalo nebo bylo ukončeno (kontrola).", "WARNING");self.refresh_button.configure(state=tk.DISABLED)
    def _load_dialogs(self):
        if not self.main_client or not self.client_loop or not self.client_running or not self.main_client.is_connected():
            self._update_log("Klient není připraven nebo připojen pro načtení dialogů.", "WARNING");self.refresh_button.configure(state=tk.DISABLED);return
        self._update_log("Aktualizuji seznam kanálů...");self.refresh_button.configure(state=tk.DISABLED)
        async def get_dialogs_task():
            try:
                return await self.main_client.get_dialogs(limit=None)
            except RPCError as e:
                self._update_log(f"Chyba při komunikaci s Telegramem (get_dialogs): {e}", "ERROR");return []
            except Exception as e:
                self._update_log(f"Neočekávaná chyba při načítání dialogů: {e}", "ERROR");return []
        def on_dialogs_loaded(future):
            try:
                dialogs = future.result()
                if dialogs is not None:
                    self._display_dialogs(dialogs)
                else:
                    self._update_log("Načítání dialogů selhalo, nebyly vráceny žádné výsledky.", "ERROR")
            except Exception as e:
                self._update_log(f"Chyba ve zpracování výsledků načítání dialogů: {e}", "ERROR")
            finally:
                if self.client_running and self.main_client and self.main_client.is_connected():
                    self.refresh_button.configure(state=tk.NORMAL)
        future = asyncio.run_coroutine_threadsafe(get_dialogs_task(), self.client_loop);future.add_done_callback(on_dialogs_loaded)
    def _display_dialogs(self, dialogs):
        for widget in self.channels_list_frame.winfo_children(): widget.destroy()
        header_frame = ctk.CTkFrame(self.channels_list_frame, fg_color="transparent");header_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=5);header_frame.grid_columnconfigure(0, weight=1);ctk.CTkLabel(header_frame, text="Název kanálu/skupiny", font=self.FONT_BOLD).grid(row=0, column=0, sticky="w", padx=5);ctk.CTkLabel(header_frame, text="Metoda parsování", font=self.FONT_BOLD).grid(row=0, column=1, sticky="w", padx=10);ctk.CTkLabel(header_frame, text="Akce", font=self.FONT_BOLD).grid(row=0, column=2, sticky="w", padx=10);temp_filtered_dialogs = []
        if dialogs:
            for d in dialogs:
                if d and d.entity:
                    is_broadcast_channel = d.is_channel and hasattr(d.entity, 'broadcast') and d.entity.broadcast and not (hasattr(d.entity, 'megagroup') and d.entity.megagroup);is_group_type = d.is_group
                    if is_broadcast_channel or is_group_type:
                        temp_filtered_dialogs.append(d)
                elif d:
                    if d.is_channel or d.is_group:
                        logging.warning(f"Dialog '{d.name}' (ID: {d.id}) nemá atribut 'entity', používám obecnější filtr.");temp_filtered_dialogs.append(d)
            filtered_dialogs = temp_filtered_dialogs
        else:
            filtered_dialogs = []
        filtered_dialogs.sort(key=lambda d: d.name.lower() if d and d.name else "")
        for i, dialog in enumerate(filtered_dialogs):
            dialog_frame = ctk.CTkFrame(self.channels_list_frame, fg_color="transparent", corner_radius=0);dialog_frame.grid(row=i + 1, column=0, sticky="ew", pady=(2,0));dialog_frame.grid_columnconfigure(0, weight=1);display_name = dialog.name if len(dialog.name) < 50 else dialog.name[:47] + "...";name_label = ctk.CTkLabel(dialog_frame, text=display_name, anchor='w', font=self.FONT_NORMAL);name_label.grid(row=0, column=0, sticky="ew", padx=10);parse_method_var = tk.StringVar(value=self.parsing_methods.get(dialog.id, "SniperPro"));method_combobox = ctk.CTkComboBox(dialog_frame,variable=parse_method_var,values=["SniperPro", "Standardní"],state="readonly",font=self.FONT_NORMAL,width=150,corner_radius=8,fg_color=self.ENTRY_BG_COLOR,border_width=0,button_color=self.ACCENT_COLOR,button_hover_color=self.ACCENT_HOVER_COLOR,command=lambda choice, d_id=dialog.id: self._update_parsing_method(d_id, choice));method_combobox.grid(row=0, column=1, padx=10);is_monitoring = self.monitoring_states.get(dialog.id, False);btn_text = "Zastavit" if is_monitoring else "Monitorovat";btn_fg_color = self.RED_COLOR if is_monitoring else self.ACCENT_COLOR;btn_hover_color = self.RED_HOVER_COLOR if is_monitoring else self.ACCENT_HOVER_COLOR;monitor_button = ctk.CTkButton(dialog_frame, text=btn_text,width=120,fg_color=btn_fg_color,hover_color=btn_hover_color,font=self.FONT_NORMAL,corner_radius=8);monitor_button.grid(row=0, column=2, padx=10);monitor_button.configure(command=lambda d=dialog, b=monitor_button: self._toggle_monitoring(d, b))
        self._update_log(f"Nalezeno a zobrazeno {len(filtered_dialogs)} relevantních dialogů.")
        if not filtered_dialogs:
            no_dialogs_label = ctk.CTkLabel(self.channels_list_frame, text="Nebyly nalezeny žádné kanály nebo skupiny.",font=self.FONT_NORMAL, text_color=self.TEXT_COLOR, anchor='center');no_dialogs_label.grid(row=1, column=0, sticky="ew", pady=20)
    def _update_parsing_method(self, dialog_id, choice):
        self._update_log(f"Metoda parsování pro kanál ID {dialog_id} změněna na '{choice}'.");self.parsing_methods[dialog_id] = choice
        if self.monitoring_states.get(dialog_id, False) and self.main_client and self.client_loop:
            self._update_log("Změna metody pro aktivně monitorovaný kanál. Pro aplikaci změny restartujte monitorování (Zastavit > Monitorovat).", "INFO")
    def _toggle_monitoring(self, dialog, button):
        if not self.main_client or not self.client_loop or not self.client_running:
            self._update_log("Nelze (de)aktivovat monitorování, klient není připraven.", "WARNING");return
        dialog_id = dialog.id;is_currently_monitoring = self.monitoring_states.get(dialog_id, False);new_monitoring_state = not is_currently_monitoring;self.monitoring_states[dialog_id] = new_monitoring_state;current_method = self.parsing_methods.get(dialog_id, "SniperPro")
        if new_monitoring_state:
            self.after(0, lambda: button.configure(text="Zastavit",fg_color=self.RED_COLOR,hover_color=self.RED_HOVER_COLOR));coro = self._start_message_processor(dialog, current_method)
        else:
            self.after(0, lambda: button.configure(text="Monitorovat",fg_color=self.ACCENT_COLOR,hover_color=self.ACCENT_HOVER_COLOR));coro = self._stop_monitoring(dialog)
        asyncio.run_coroutine_threadsafe(coro, self.client_loop)
    async def _start_message_processor(self, dialog, parse_method: str):
        dialog_id = dialog.id
        if not self.main_client:
            self._update_log(f"Nelze spustit monitorování pro '{dialog.name}', klient není aktivní.", "ERROR");self.monitoring_states[dialog_id] = False;return
        self._update_log(f"Spouštím monitorování pro '{dialog.name}' (ID: {dialog_id}) s metodou parsování: '{parse_method}'")
        if dialog_id in self.monitoring_handlers and self.monitoring_handlers[dialog_id] is not None:
            self._update_log(f"Handler pro '{dialog.name}' již existuje. Nejprve ho odstraňuji.", "WARNING")
            try:
                self.main_client.remove_event_handler(self.monitoring_handlers[dialog_id])
            except Exception as e:
                self._update_log(f"Chyba při odstraňování starého handleru pro '{dialog.name}': {e}", "ERROR")
            del self.monitoring_handlers[dialog_id]
        @self.main_client.on(events.NewMessage(chats=dialog_id))
        async def handler(event):
            if not hasattr(event, 'message') or not event.message or not hasattr(event.message, 'id') or not hasattr(event.message, 'text') or not event.message.text:
                return
            message_id = event.message.id;message_text = event.message.text
            with self.message_id_lock:
                if message_id in self.processed_message_ids:
                    self._update_log(f"HANDLER_DUPLICATE_SKIP: Zpráva ID: {message_id} (dialog: {dialog_id}) již byla zpracována. Přeskakuji.", "WARNING");return
                if len(self.processed_message_ids) > 1000:
                    try:
                        self.processed_message_ids.pop()
                    except KeyError:
                        pass
                self.processed_message_ids.add(message_id)
            self._update_log(f"HANDLER_PROCEED: Zpráva ID: {message_id}, Dialog ID: {dialog_id}, Text: \"{message_text[:50].replace('\n', ' ')}\"", "ERROR");active_parse_method = parse_method;self._update_log(f"Nová zpráva z '{dialog.name}' (ID: {dialog_id}). Metoda: '{active_parse_method}'. Zpráva: \"{message_text[:100].replace('\n', ' ')}\"", "DEBUG");parsed_data = None
            if active_parse_method == "SniperPro":
                parsed_data = parse_sniper_pro(message_text)
            elif active_parse_method == "Standard":
                parsed_data = parse_standard_signal(message_text)
            else:
                self._update_log(f"Neznámá metoda parsování '{active_parse_method}' pro '{dialog.name}'. Zpráva ignorována.", "WARNING");return

            if not parsed_data or parsed_data.get('type') == SIGNAL_TYPE_UNKNOWN:
                self._update_log(f"Zpráva z '{dialog.name}' ({active_parse_method}) nebyla rozpoznána nebo neznámého typu. Důvod: {parsed_data.get('reason', 'N/A') if parsed_data else 'Parser nevrátil data'}", "DEBUG");return
            if parsed_data['type'] == SIGNAL_TYPE_IGNORE:
                self._update_log(f"Zpráva z '{dialog.name}' ({active_parse_method}) ignorována: {parsed_data.get('reason', '')}", "DEBUG");return

            channel_context = self.channel_contexts.setdefault(dialog_id, {})
            current_signal_type_from_parser = parsed_data['type']

            # --- Standard Signal Processing ---
            if current_signal_type_from_parser == SIGNAL_TYPE_STANDARD:
                self._update_log(f"HANDLER_STD_SAVE: Zpráva ID: {message_id}, Data: {parsed_data}", "ERROR");self._update_log(f"STANDARD signál: {parsed_data['symbol']} {parsed_data['action']}", "INFO");std_signal_group_id = f"{dialog_id}_{parsed_data['symbol']}_STD_{int(datetime.datetime.now().timestamp())}";channel_context['last_initial_symbol'] = parsed_data['symbol'];channel_context['last_initial_action'] = parsed_data['action'];channel_context['last_initial_entry_price'] = parsed_data['entry_price_ref'];channel_context['last_signal_group_id'] = std_signal_group_id;tp_prices = parsed_data.get('tp_prices', []);main_tp_price = tp_prices[0] if tp_prices else None;optional_tp2_price = tp_prices[1] if len(tp_prices) > 1 else None;self._save_signal_data(symbol=parsed_data['symbol'],action=parsed_data['action'],entry_price=parsed_data['entry_price_ref'],signal_group_id=std_signal_group_id,trade_label="STD_TRADE",signal_type=SIGNAL_TYPE_STANDARD,sl_price=parsed_data.get('sl_price'),tp_price=main_tp_price,tp2_price_optional=optional_tp2_price);return

            # --- SniperPro Signal Processing ---
            if active_parse_method == "SniperPro":
                config = self.parsing_config.get("SniperPro", {})
                if not config:
                    self._update_log("Chyba: Chybí konfigurace pro SniperPro v parsing_config.json", "ERROR"); return

                if current_signal_type_from_parser == SIGNAL_TYPE_INITIAL:
                    symbol = parsed_data['symbol']; action = parsed_data['action']; entry_price_ref = parsed_data['entry_price_ref']
                    signal_group_id = f"{dialog_id}_{symbol}_{action}_{int(datetime.datetime.now().timestamp())}"
                    channel_context.update({
                        'last_initial_symbol': symbol, 'last_initial_action': action,
                        'last_initial_entry_price': entry_price_ref, 'last_signal_group_id': signal_group_id
                    })
                    self._update_log(f"SNIPERPRO INITIAL: {symbol} {action} @ {entry_price_ref}. GroupID: {signal_group_id}", "INFO")

                    # --- Uložit Signály ---
                    t1_config = config.get('trade_1', {})
                    self._save_signal_data(
                        symbol=symbol, action=action, entry_price=entry_price_ref,
                        signal_group_id=signal_group_id, trade_label="T1_AUTO",
                        signal_type=SIGNAL_TYPE_INITIAL_T1, sl_pips=t1_config.get('sl_pips'),
                        tp_pips=t1_config.get('tp_pips')
                    )

                    t2_config = config.get('trade_2', {})
                    self._save_signal_data(
                        symbol=symbol, action=action, entry_price=entry_price_ref,
                        signal_group_id=signal_group_id, trade_label="T2_AUTO",
                        signal_type=SIGNAL_TYPE_INITIAL_T2_DEFAULT, sl_pips=t1_config.get('sl_pips'),
                        tp_pips=t2_config.get('default_tp_pips')
                    )

                    # --- Uložiť Akcie pre T2 ---
                    if t1_signal_id and t2_signal_id:
                        func_config = config.get('functions', {})
                        if func_config.get('be_active'):
                            be_params = {'offset_pips': 1.0, 'entry_price': entry_price_ref}
                            self._save_trade_action(target_signal_id=t2_signal_id, action_type='SET_BE',
                                                    trigger_event='ON_TICKET_PROFIT', params=be_params)

                        if func_config.get('ts_active'):
                            ts_type = func_config.get('ts_type', 'Classic')
                            ts_params_config = func_config.get(f"{ts_type.lower()}_ts", {})
                            ts_params = {"ts_type": ts_type, **ts_params_config}
                            self._save_trade_action(target_signal_id=t2_signal_id, action_type='ACTIVATE_TS',
                                                    trigger_event='ON_TICKET_PROFIT', params=ts_params)
                    else:
                        self._update_log(f"Chyba: Nepodařilo se uložit T1 nebo T2 signál pro GroupID {signal_group_id}. Akce nebyly vytvořeny.", "ERROR")

                elif current_signal_type_from_parser == 'UPDATE_SLTP':
                active_symbol = channel_context.get('last_initial_symbol');active_action = channel_context.get('last_initial_action');active_group_id = channel_context.get('last_signal_group_id')
                if not (active_symbol and active_action and active_group_id):
                    self._update_log(f"SNIPERPRO UPDATE_SLTP z '{dialog.name}': chybí kontext z INITIAL. Ignoruji.", "WARNING");return
                tp_prices_from_update = parsed_data.get('tp_prices', [])
                if not tp_prices_from_update:
                    self._update_log(f"SNIPERPRO UPDATE_SLTP pro {active_symbol} (Grp: {active_group_id}): žádné TP hodnoty. Ignoruji.", "WARNING");return
                relevant_tp_for_t2 = None
                if active_action == "BUY":
                    relevant_tp_for_t2 = max(tp_prices_from_update) if tp_prices_from_update else None
                elif active_action == "SELL":
                    relevant_tp_for_t2 = min(tp_prices_from_update) if tp_prices_from_update else None
                if relevant_tp_for_t2 is None:
                    self._update_log(f"SNIPERPRO UPDATE_SLTP pro {active_symbol} {active_action} (Grp: {active_group_id}): nelze určit TP2 z {tp_prices_from_update}. Ignoruji.", "WARNING");return
                self._update_log(f"SNIPERPRO UPDATE_SLTP (pro T2): {active_symbol} {active_action} (Grp: {active_group_id}). Nové TP: {relevant_tp_for_t2}. SL (40pips) zůstává.", "INFO");self.after(0, self._schedule_db_update_t2_tp,active_group_id, "T2_AUTO", relevant_tp_for_t2)
            elif current_signal_type_from_parser == 'RE_ENTRY':
                symbol_from_re_entry = parsed_data['symbol'];action_for_re_entry = channel_context.get('last_initial_action');last_ctx_symbol = channel_context.get('last_initial_symbol')
                if not action_for_re_entry:
                    self._update_log(f"SNIPERPRO RE_ENTRY pro {symbol_from_re_entry} z '{dialog.name}': chybí kontext akce z INITIAL. Ignoruji.", "WARNING");return
                if symbol_from_re_entry != last_ctx_symbol:
                    self._update_log(f"SNIPERPRO RE_ENTRY symbol '{symbol_from_re_entry}' se liší od kontextu '{last_ctx_symbol}'. Akce '{action_for_re_entry}' z kontextu kanálu použita.", "WARNING")
                re_signal_group_id = f"{dialog_id}_{symbol_from_re_entry}_RE_{int(datetime.datetime.now().timestamp())}";self._update_log(f"SNIPERPRO RE_ENTRY: {symbol_from_re_entry} {action_for_re_entry}. SL: {parsed_data['sl_price']}. GrpID: {re_signal_group_id}", "INFO");self._save_signal_data(symbol=symbol_from_re_entry, action=action_for_re_entry,signal_group_id=re_signal_group_id, trade_label="RE_AUTO",signal_type=SIGNAL_TYPE_RE_ENTRY,sl_price=parsed_data['sl_price'],tp_pips=REENTRY_TP_PIPS)
        try:
            self.main_client.add_event_handler(handler);self.monitoring_handlers[dialog_id] = handler;self._update_log(f"Monitorování pro '{dialog.name}' úspěšně spuštěno.", "INFO")
        except Exception as e:
            self._update_log(f"Chyba při přidávání event handleru pro '{dialog.name}': {e}", "ERROR");self.monitoring_states[dialog_id] = False
    async def _stop_monitoring(self, dialog):
        dialog_id = dialog.id
        if dialog_id in self.monitoring_handlers and self.monitoring_handlers[dialog_id] is not None:
            if self.main_client:
                try:
                    self.main_client.remove_event_handler(self.monitoring_handlers[dialog_id]);self._update_log(f"Monitorování pro '{dialog.name}' (ID: {dialog_id}) bylo zastaveno.", "INFO")
                except Exception as e:
                    self._update_log(f"Chyba při odebírání event handleru pro '{dialog.name}': {e}", "WARNING")
            del self.monitoring_handlers[dialog_id]
        else:
            self._update_log(f"Nebylo aktivní žádné monitorování pro '{dialog.name}' (ID: {dialog_id}) k zastavení.", "DEBUG")
        self.monitoring_states[dialog_id] = False
    def _schedule_db_update_t2_tp(self, signal_group_id, trade_label_to_find, new_tp_price):
        success = self._update_db_trade_tp_status(signal_group_id=signal_group_id,trade_label=trade_label_to_find,new_tp_price=new_tp_price,new_signal_type=SIGNAL_TYPE_UPDATE_T2,new_status="new")
        if success:
            self._update_log(f"DB: Obchod '{trade_label_to_find}' (Group: {signal_group_id}) úspěšně aktualizován s TP: {new_tp_price}, status 'new'.", "INFO")
        else:
            self._update_log(f"DB: Nepodařilo se aktualizovat obchod '{trade_label_to_find}' (Group: {signal_group_id}).", "ERROR")
    def _update_db_trade_tp_status(self, signal_group_id: str, trade_label: str, new_tp_price: float, new_signal_type: str, new_status: str) -> bool:
        if signal_group_id is None or trade_label is None or new_tp_price is None:
            logging.error(f"Chybějící parametry pro _update_db_trade_tp_status: sgid={signal_group_id}, label={trade_label}, tp={new_tp_price}");return False
        with db_lock, sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            try:
                c.execute("""SELECT id, status FROM signals WHERE signal_group_id = ? AND trade_label = ?""",(signal_group_id, trade_label));trade_row = c.fetchone()
                if not trade_row:
                    logging.warning(f"Pokus o aktualizaci TP pro neexistující obchod: Group ID: {signal_group_id}, Label: {trade_label}");return False
                c.execute('''UPDATE signals SET tp_value = ?, tp_value_type = ?, signal_type = ?, status = ?, timestamp = ? WHERE signal_group_id = ? AND trade_label = ?''',(new_tp_price, "PRICE", new_signal_type, new_status,datetime.datetime.now(), signal_group_id, trade_label));conn.commit()
                if c.rowcount > 0:
                    logging.info(f"Obchod (Group: {signal_group_id}, Label: {trade_label}) aktualizován: TP na {new_tp_price}, Typ na {new_signal_type}, Status na {new_status}.");return True
                else:
                    logging.error(f"Nepodařilo se aktualizovat řádek v DB pro Group ID: {signal_group_id}, Label: {trade_label}, ačkoliv byl nalezen.");return False
            except sqlite3.Error as e:
                logging.error(f"Chyba DB při aktualizaci TP obchodu (Group: {signal_group_id}, Label: {trade_label}): {e}");return False
    def _save_signal_data(self, symbol: str, action: str, signal_type: str,
                          signal_group_id: str | None = None, trade_label: str | None = None,
                          entry_price: float = 0, sl_price: float | None = None,
                          tp_price: float | None = None, sl_pips: float | None = None,
                          tp_pips: float | None = None):
        sl_val, sl_val_type, tp_val, tp_val_type = None, None, None, None
        if sl_pips is not None: sl_val, sl_val_type = sl_pips, "PIPS"
        elif sl_price is not None: sl_val, sl_val_type = sl_price, "PRICE"
        if tp_pips is not None: tp_val, tp_val_type = tp_pips, "PIPS"
        elif tp_price is not None: tp_val, tp_val_type = tp_price, "PRICE"

        with db_lock, sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            try:
                sql = '''INSERT INTO signals
                             (signal_group_id, trade_label, symbol, action, entry_price,
                              sl_value, sl_value_type, tp_value, tp_value_type,
                              status, signal_type, timestamp)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
                params = (
                    signal_group_id, trade_label, symbol, action, entry_price,
                    sl_val, sl_val_type, tp_val, tp_val_type,
                    'new', signal_type, datetime.datetime.now()
                )
                c.execute(sql, params)
                conn.commit()
                last_id = c.lastrowid
                self._update_log(f"Signál uložen do DB (ID: {last_id}): {signal_type} - {symbol} {action}", "INFO")
                return last_id
            except sqlite3.Error as e:
                logging.error(f"Chyba při ukládání signálu do DB: {e}.")
                self._update_log(f"Chyba DB při ukládání signálu {symbol} {action}", "ERROR")
                return None

    def _save_trade_action(self, target_signal_id: int, action_type: str, trigger_event: str,
                           params: dict | None = None):
        params_json_str = json.dumps(params) if params else None
        with db_lock, sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            try:
                c.execute("""
                    INSERT INTO trade_actions
                        (target_signal_id, action_type, trigger_event, params_json, status)
                    VALUES (?, ?, ?, ?, 'PENDING')
                """, (target_signal_id, action_type, trigger_event, params_json_str))
                conn.commit()
                self._update_log(f"DB: Uložena akce '{action_type}' pro signal_id {target_signal_id}", "INFO")
                return c.lastrowid
            except sqlite3.Error as e:
                self._update_log(f"DB Error: Nepodařilo se uložit akci {action_type} pro signal_id {target_signal_id}: {e}", "ERROR")
                return None

    def _show_phone_selector(self):
        selector_window = ctk.CTkToplevel(self);selector_window.title("Správa telefonních čísel");selector_window.geometry("450x420");selector_window.transient(self);selector_window.grab_set();selector_window.resizable(False, False);selector_window.configure(fg_color=self.BG_COLOR);phone_numbers = self.session_manager.get_saved_phone_numbers();selected_phone_var = tk.StringVar(value=self.phone_entry_var.get());saved_frame = ctk.CTkFrame(selector_window, fg_color=self.FRAME_COLOR, corner_radius=8);saved_frame.pack(fill='x', padx=15, pady=(15,10));ctk.CTkLabel(saved_frame, text="Uložená čísla", font=self.FONT_BOLD).pack(anchor="w", padx=10, pady=(8,4));listbox_frame = ctk.CTkFrame(saved_frame, fg_color=self.ENTRY_BG_COLOR, corner_radius=6);listbox_frame.pack(fill="x", expand=True, padx=10, pady=(0,10));phone_listbox = tk.Listbox(listbox_frame, height=5, bg=self.ENTRY_BG_COLOR, fg=self.TEXT_COLOR,borderwidth=0, highlightthickness=0, selectbackground=self.ACCENT_COLOR,font=self.FONT_NORMAL, relief='flat', exportselection=False,selectforeground=self.TEXT_COLOR)
        for phone in phone_numbers: phone_listbox.insert(tk.END, phone)
        phone_listbox.pack(fill="x", expand=True, padx=5, pady=5);current_phone_in_list = self.phone_entry_var.get()
        if current_phone_in_list in phone_numbers:
            try:
                idx = phone_numbers.index(current_phone_in_list);phone_listbox.selection_set(idx);phone_listbox.activate(idx);phone_listbox.see(idx);selected_phone_var.set(current_phone_in_list)
            except ValueError: pass
        def on_listbox_select(event):
            widget = event.widget
            if widget.curselection(): selected_phone_var.set(widget.get(widget.curselection()[0]))
            else: selected_phone_var.set("")
        phone_listbox.bind('<<ListboxSelect>>', on_listbox_select);new_frame = ctk.CTkFrame(selector_window, fg_color=self.FRAME_COLOR, corner_radius=8);new_frame.pack(fill='x', padx=15, pady=10);new_frame.grid_columnconfigure(0, weight=1);ctk.CTkLabel(new_frame, text="Nové číslo (formát +420...):", font=self.FONT_NORMAL).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8,2));new_phone_entry = ctk.CTkEntry(new_frame, font=self.FONT_NORMAL, fg_color=self.ENTRY_BG_COLOR, border_width=0, corner_radius=6);new_phone_entry.grid(row=1, column=0, sticky="ew", padx=(10,5), pady=(0,10));new_phone_entry.focus();add_button = ctk.CTkButton(new_frame, text="Přidat/Vybrat", command=lambda: add_or_select_phone(),fg_color=self.ACCENT_COLOR, hover_color=self.ACCENT_HOVER_COLOR,font=self.FONT_NORMAL, corner_radius=6, width=110);add_button.grid(row=1, column=1, sticky="e", padx=(0,10), pady=(0,10))
        def add_or_select_phone():
            new_phone = new_phone_entry.get().strip()
            if not re.match(r'^\+\d{10,}$', new_phone):
                messagebox.showwarning("Neplatný formát", "Zadejte platné telefonní číslo v mezinárodním formátu (např. +420123456789).", parent=selector_window);return
            if new_phone not in phone_numbers:
                phone_listbox.insert(tk.END, new_phone);phone_numbers.append(new_phone)
            try:
                idx = phone_numbers.index(new_phone);phone_listbox.selection_clear(0, tk.END);phone_listbox.selection_set(idx);phone_listbox.activate(idx);phone_listbox.see(idx);selected_phone_var.set(new_phone)
            except ValueError: pass
            new_phone_entry.delete(0, tk.END)
        button_frame = ctk.CTkFrame(selector_window, fg_color="transparent");button_frame.pack(fill='x', padx=15, pady=(15,15));button_frame.grid_columnconfigure((0,1), weight=1)
        def confirm_and_connect():
            phone_to_connect = selected_phone_var.get()
            if not phone_to_connect:
                messagebox.showinfo("Informace", "Nevybrali jste žádné telefonní číslo.", parent=selector_window);return
            current_active_phone = self.phone_entry_var.get()
            if current_active_phone != phone_to_connect or not self.client_running:
                self.phone_entry_var.set(phone_to_connect);self._update_log(f"Vybráno telefonní číslo: {phone_to_connect}. Pokouším se připojit.");self._connect_telegram()
            else:
                self._update_log(f"Telefonní číslo {phone_to_connect} je již aktivní.")
            selector_window.destroy()
        def delete_selected_phone():
            selection_indices = phone_listbox.curselection()
            if not selection_indices:
                messagebox.showinfo("Informace", "Nevybrali jste žádné číslo ke smazání.", parent=selector_window);return
            phone_to_delete_idx = selection_indices[0];phone_to_delete = phone_numbers[phone_to_delete_idx]
            if messagebox.askyesno("Potvrdit smazání", f"Opravdu chcete odstranit session a záznam pro {phone_to_delete}?", parent=selector_window):
                if self.session_manager.remove_phone_number(phone_to_delete):
                    self._update_log(f"Session pro {phone_to_delete} byla odstraněna.", "INFO");original_index = -1
                    try:
                        original_index = phone_numbers.index(phone_to_delete);del phone_numbers[original_index];phone_listbox.delete(original_index)
                    except ValueError:
                        self._update_log(f"Chyba: {phone_to_delete} nebylo nalezeno v interním seznamu pro smazání z GUI.", "ERROR")
                    if selected_phone_var.get() == phone_to_delete:
                        selected_phone_var.set("")
                    if self.phone_entry_var.get() == phone_to_delete:
                        self.phone_entry_var.set("");self._update_log(f"Aktivní session pro {phone_to_delete} byla smazána.", "INFO")
                        if self.client_running:
                            self._update_log("Odpojuji aktivního klienta.", "INFO");self._shutdown_client()
                        for widget in self.channels_list_frame.winfo_children(): widget.destroy()
                        self.refresh_button.configure(state=tk.DISABLED)
                else:
                    messagebox.showerror("Chyba", f"Nepodařilo se odstranit session soubor pro {phone_to_delete}.", parent=selector_window)
        delete_btn = ctk.CTkButton(button_frame, text="Odstranit vybrané",font=self.FONT_NORMAL, fg_color=self.RED_COLOR,hover_color=self.RED_HOVER_COLOR, command=delete_selected_phone, corner_radius=6);delete_btn.grid(row=0, column=0, sticky='ew', padx=(0,5), pady=5);confirm_btn = ctk.CTkButton(button_frame, text="Potvrdit a Připojit",font=self.FONT_BOLD, fg_color=self.ACCENT_COLOR,hover_color=self.ACCENT_HOVER_COLOR, command=confirm_and_connect, corner_radius=6);confirm_btn.grid(row=0, column=1, sticky='ew', padx=(5,0), pady=5);selector_window.bind("<Return>", lambda event: confirm_and_connect());new_phone_entry.bind("<Return>", lambda event: add_or_select_phone());selector_window.attributes("-topmost", True)
    def _shutdown_client(self):
        self._update_log("Zahajuji ukončení Telegram klienta...", "INFO");self.after(0, lambda: self.refresh_button.configure(state=tk.DISABLED))
        if self.client_loop and not self.client_loop.is_closed() and self.main_client:
            active_monitoring_ids = list(self.monitoring_handlers.keys())
            for dialog_id in active_monitoring_ids:
                class DummyDialog: id = dialog_id; name = f"ID {dialog_id}"
                asyncio.run_coroutine_threadsafe(self._stop_monitoring(DummyDialog()), self.client_loop)
        if self.main_client and self.main_client.is_connected():
            self._update_log("Odpojuji klienta od Telegramu...", "INFO")
            if self.client_loop and not self.client_loop.is_closed():
                disconnect_future = asyncio.run_coroutine_threadsafe(self.main_client.disconnect(), self.client_loop)
                try:
                    disconnect_future.result(timeout=5);self._update_log("Klient úspěšně odpojen.", "INFO")
                except concurrent.futures.TimeoutError:
                    self._update_log("Timeout při odpojování klienta.", "WARNING")
                except Exception as e:
                    self._update_log(f"Chyba při odpojování klienta: {e}", "ERROR")
        self.main_client = None;self.client_running = False
        if self.client_loop and not self.client_loop.is_closed():
             self.client_loop.call_soon_threadsafe(self.client_loop.stop)
        if self.client_thread and self.client_thread.is_alive():
             self._update_log("Čekám na ukončení klientského vlákna...", "DEBUG");self.client_thread.join(timeout=5)
             if self.client_thread.is_alive():
                 self._update_log("Klientské vlákno se nepodařilo korektně ukončit v časovém limitu.", "WARNING")
        self.client_thread = None
        if self.client_loop and self.client_loop.is_closed():
            self._update_log(f"Asyncio smyčka ({id(self.client_loop)}) byla uzavřena (kontrola v shutdown).", "DEBUG")
        self.client_loop = None;self._update_log("Telegram klient a jeho vlákno byly ukončeny.", "INFO");self.after(0, lambda: [widget.destroy() for widget in self.channels_list_frame.winfo_children()])
    def _show_functions_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Výchozí Nastavení Funkcí Obchodů")
        dialog.geometry("650x550")
        dialog.transient(self); dialog.grab_set(); dialog.resizable(True, True)
        dialog.configure(fg_color=self.BG_COLOR)

        # This will hold the tk variables for the dialog
        self.dialog_vars = {}

        content_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        content_frame.pack(padx=15, pady=15, fill="both", expand=True)
        content_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(content_frame, text="Typ parsování:", font=self.FONT_BOLD).grid(row=0, column=0, padx=(0,5), pady=10, sticky="w")

        parser_type_options = list(self.parsing_config.keys())
        self.current_parser_type_var_dialog = tk.StringVar(value=parser_type_options[0] if parser_type_options else "")

        parser_type_dropdown = ctk.CTkComboBox(content_frame, variable=self.current_parser_type_var_dialog,
                                               values=parser_type_options, state="readonly",
                                               font=self.FONT_NORMAL, command=self._on_parser_type_selected_in_dialog)
        parser_type_dropdown.grid(row=0, column=1, columnspan=3, padx=0, pady=10, sticky="ew")

        self.settings_area_frame = ctk.CTkFrame(content_frame, fg_color=self.FRAME_COLOR, corner_radius=6)
        self.settings_area_frame.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(5,10))
        self.settings_area_frame.grid_columnconfigure(1, weight=1)
        content_frame.grid_rowconfigure(1, weight=1)

        # Save button
        save_button = ctk.CTkButton(content_frame, text="Uložit a Zavřít", command=lambda: self._save_functions_dialog(dialog))
        save_button.grid(row=2, column=0, columnspan=4, pady=(10,0), sticky="ew")

        self._on_parser_type_selected_in_dialog(self.current_parser_type_var_dialog.get())
        dialog.attributes("-topmost", True)

    def _on_parser_type_selected_in_dialog(self, selected_parser_type: str):
        for widget in self.settings_area_frame.winfo_children():
            widget.destroy()

        if not selected_parser_type or selected_parser_type not in self.parsing_config:
            ctk.CTkLabel(self.settings_area_frame, text="Vyberte platný typ parsování.", font=self.FONT_NORMAL).pack(padx=10, pady=10)
            return

        config = self.parsing_config[selected_parser_type].get('functions', {})
        self.dialog_vars = {
            'be_active': tk.BooleanVar(value=config.get('be_active', False)),
            'ts_active': tk.BooleanVar(value=config.get('ts_active', False)),
            'ts_type': tk.StringVar(value=config.get('ts_type', 'Classic')),
            'classic_ts_start_pips': tk.DoubleVar(value=config.get('classic_ts', {}).get('start_pips', 20.0)),
            'classic_ts_step_pips': tk.DoubleVar(value=config.get('classic_ts', {}).get('step_pips', 10.0)),
            'classic_ts_distance_pips': tk.DoubleVar(value=config.get('classic_ts', {}).get('distance_pips', 15.0)),
            'convergent_activation_start_pips': tk.DoubleVar(value=config.get('convergent_ts', {}).get('activation_start_pips', 30.0)),
            'convergent_converge_factor': tk.DoubleVar(value=config.get('convergent_ts', {}).get('converge_factor', 0.5)),
            'convergent_min_stop_distance_pips': tk.DoubleVar(value=config.get('convergent_ts', {}).get('min_stop_distance_pips', 10.0))
        }

        # --- Create Widgets ---
        be_check = ctk.CTkCheckBox(self.settings_area_frame, text="Aktivovat Breakeven", variable=self.dialog_vars['be_active'], font=self.FONT_NORMAL, fg_color=self.ACCENT_COLOR, hover_color=self.ACCENT_HOVER_COLOR)
        be_check.grid(row=0, column=0, columnspan=2, padx=10, pady=7, sticky="w")

        ts_activation_check = ctk.CTkCheckBox(self.settings_area_frame, text="Aktivovat Trailing Stop", variable=self.dialog_vars['ts_active'], font=self.FONT_NORMAL, fg_color=self.ACCENT_COLOR, hover_color=self.ACCENT_HOVER_COLOR, command=self._update_ts_params_visibility)
        ts_activation_check.grid(row=1, column=0, padx=10, pady=7, sticky="w")

        self.ts_type_dropdown_dialog = ctk.CTkComboBox(self.settings_area_frame, variable=self.dialog_vars['ts_type'], values=["Classic", "Convergent"], state="readonly", font=self.FONT_NORMAL, width=150, command=self._update_ts_params_visibility)
        self.ts_type_dropdown_dialog.grid(row=1, column=1, padx=10, pady=7, sticky="w")

        self.ts_params_frame_dialog = ctk.CTkFrame(self.settings_area_frame, fg_color="transparent")
        self.ts_params_frame_dialog.grid(row=2, column=0, columnspan=4, sticky="nsew", padx=5, pady=5)
        self.ts_params_frame_dialog.grid_columnconfigure(1, weight=1)
        self.ts_params_frame_dialog.grid_columnconfigure(3, weight=1)

        self._update_ts_params_visibility()

    def _update_ts_params_visibility(self, *args):
        # Clear previous params
        for widget in self.ts_params_frame_dialog.winfo_children():
            widget.destroy()

        is_ts_active = self.dialog_vars['ts_active'].get()
        self.ts_type_dropdown_dialog.configure(state=tk.NORMAL if is_ts_active else tk.DISABLED)

        if not is_ts_active:
            return

        selected_ts_type = self.dialog_vars['ts_type'].get()

        if selected_ts_type == "Classic":
            ctk.CTkLabel(self.ts_params_frame_dialog, text="Classic TS - Start (pips):", font=self.FONT_NORMAL).grid(row=0, column=0, padx=5, pady=5, sticky="w");ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=self.dialog_vars['classic_ts_start_pips'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=0, column=1, padx=5, pady=5, sticky="w");ctk.CTkLabel(self.ts_params_frame_dialog, text="Krok (pips):", font=self.FONT_NORMAL).grid(row=0, column=2, padx=5, pady=5, sticky="w");ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=self.dialog_vars['classic_ts_step_pips'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=0, column=3, padx=5, pady=5, sticky="w");ctk.CTkLabel(self.ts_params_frame_dialog, text="Distance (pips):", font=self.FONT_NORMAL).grid(row=1, column=0, padx=5, pady=5, sticky="w");ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=self.dialog_vars['classic_ts_distance_pips'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=1, column=1, padx=5, pady=5, sticky="w")
        elif selected_ts_type == "Convergent":
            ctk.CTkLabel(self.ts_params_frame_dialog, text="Convergent TS - Aktivace (pips):", font=self.FONT_NORMAL).grid(row=0, column=0, padx=5, pady=5, sticky="w");ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=self.dialog_vars['convergent_activation_start_pips'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=0, column=1, padx=5, pady=5, sticky="w");ctk.CTkLabel(self.ts_params_frame_dialog, text="Converge Faktor (0-1):", font=self.FONT_NORMAL).grid(row=0, column=2, padx=5, pady=5, sticky="w");ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=self.dialog_vars['convergent_converge_factor'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=0, column=3, padx=5, pady=5, sticky="w");ctk.CTkLabel(self.ts_params_frame_dialog, text="Min. odstup SL (pips):", font=self.FONT_NORMAL).grid(row=1, column=0, padx=5, pady=5, sticky="w");ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=self.dialog_vars['convergent_min_stop_distance_pips'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=1, column=1, padx=5, pady=5, sticky="w")

    def _save_functions_dialog(self, dialog):
        parser_type = self.current_parser_type_var_dialog.get()
        if not parser_type:
            dialog.destroy()
            return

        # Update the main config object from the dialog's tk variables
        self.parsing_config[parser_type]['functions'] = {
            'be_active': self.dialog_vars['be_active'].get(),
            'ts_active': self.dialog_vars['ts_active'].get(),
            'ts_type': self.dialog_vars['ts_type'].get(),
            'classic_ts': {
                'start_pips': self.dialog_vars['classic_ts_start_pips'].get(),
                'step_pips': self.dialog_vars['classic_ts_step_pips'].get(),
                'distance_pips': self.dialog_vars['classic_ts_distance_pips'].get()
            },
            'convergent_ts': {
                'activation_start_pips': self.dialog_vars['convergent_activation_start_pips'].get(),
                'converge_factor': self.dialog_vars['convergent_converge_factor'].get(),
                'min_stop_distance_pips': self.dialog_vars['convergent_min_stop_distance_pips'].get()
            }
        }

        save_parsing_config(self.parsing_config)
        self._init_default_function_settings() # Reload settings into the app
        self._update_log(f"Nastavení funkcí pro '{parser_type}' bylo uloženo.", "INFO")
        dialog.destroy()
    def _on_closing(self):
        self._update_log("Aplikace se ukončuje...");
        if self.client_running or (self.client_thread and self.client_thread.is_alive()):
            self._shutdown_client()
        self.destroy()
flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    return "Signal Server is running."
@flask_app.route('/signals')
def get_new_signals():
    logging.info(f"Příchozí požadavek na /signals od {request.remote_addr}");signals_to_send = []
    try:
        with db_lock, sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row; c = conn.cursor();c.execute("SELECT * FROM signals WHERE status = 'new' ORDER BY timestamp ASC");signals_to_send = [dict(row) for row in c.fetchall()];return jsonify(signals_to_send)
    except sqlite3.Error as e:
        logging.error(f"Chyba databáze při /signals: {e}");return jsonify({"status": "error", "message": "Database error"}), 500
    except Exception as e:
        logging.error(f"Obecná chyba při /signals: {e}");return jsonify({"status": "error", "message": "Internal server error"}), 500
@flask_app.route('/report_trade', methods=['POST'])
def report_trade():
    logging.info(f"Příchozí požadavek na /report_trade od {request.remote_addr}")
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    signal_id = data.get('id')
    ticket = data.get('ticket')

    if signal_id is None or ticket is None:
        return jsonify({"status": "error", "message": "Missing 'id' or 'ticket'"}), 400

    with db_lock, sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        conn.row_factory = sqlite3.Row

        c.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
        signal_row = c.fetchone()

        if not signal_row:
            return jsonify({"status": "error", "message": "Signal not found"}), 404

        # Update signal status to 'open' and save the ticket
        c.execute("UPDATE signals SET status = 'open', ticket = ? WHERE id = ?", (ticket, signal_id))
        log_msg = f"Signál ID {signal_id} označen jako 'open' s ticketem {ticket}."

        # If a T2 trade is opened, find pending actions and link them to its ticket
        if signal_row['trade_label'] == 'T2_AUTO':
            c.execute("UPDATE trade_actions SET target_ticket = ? WHERE target_signal_id = ?", (ticket, signal_id))
            if c.rowcount > 0:
                log_msg += f" {c.rowcount} akcí bylo navázáno na target_ticket {ticket}."

        # If a T1 trade is opened, find pending actions for the corresponding T2 and set the trigger
        elif signal_row['trade_label'] == 'T1_AUTO':
            c.execute("""
                UPDATE trade_actions
                SET trigger_ticket = ?
                WHERE target_signal_id IN
                    (SELECT id FROM signals WHERE signal_group_id = ? AND trade_label = 'T2_AUTO')
            """, (ticket, signal_row['signal_group_id']))
            if c.rowcount > 0:
                log_msg += f" {c.rowcount} akcí pro T2 bylo aktualizováno s trigger ticketem {ticket}."

        conn.commit()
        logging.info(log_msg)
        return jsonify({"status": "ok", "message": "Trade reported and actions updated."})

@flask_app.route('/get_pending_actions', methods=['GET'])
def get_pending_actions():
    with db_lock, sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM trade_actions WHERE status = 'PENDING'")
        actions = [dict(row) for row in c.fetchall()]
        return jsonify(actions)

@flask_app.route('/update_action_status', methods=['POST'])
def update_action_status():
    data = request.get_json(silent=True)
    if not data or 'action_id' not in data or 'status' not in data:
        return jsonify({"status": "error", "message": "Invalid request"}), 400

    action_id = data['action_id']
    new_status = data['status']

    if new_status not in ['EXECUTED', 'CANCELLED']:
        return jsonify({"status": "error", "message": "Invalid status"}), 400

    with db_lock, sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("UPDATE trade_actions SET status = ? WHERE id = ?", (new_status, action_id))
        conn.commit()
        if c.rowcount > 0:
            logging.info(f"Stav akce ID {action_id} aktualizován na '{new_status}'.")
            return jsonify({"status": "ok"})
        else:
            return jsonify({"status": "error", "message": "Action not found"}), 404

def run_flask():
    try:
        from waitress import serve
        serve(flask_app, host='0.0.0.0', port=5000)
    except ImportError:
        logging.warning("Waitress není nainstalován, používám vývojový server Flask.")
        flask_app.run(host='0.0.0.0', port=5000, use_reloader=False, threaded=True)
    except Exception as e:
        logging.error(f"Nepodařilo se spustit Flask server: {e}")

@flask_app.route('/get_pending_actions', methods=['GET'])
def get_pending_actions():
    with db_lock, sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM trade_actions WHERE status = 'PENDING'")
        actions = [dict(row) for row in c.fetchall()]
        return jsonify(actions)

@flask_app.route('/update_action_status', methods=['POST'])
def update_action_status():
    data = request.get_json(silent=True)
    if not data or 'action_id' not in data or 'status' not in data:
        return jsonify({"status": "error", "message": "Invalid request"}), 400

    action_id = data['action_id']
    new_status = data['status']

    if new_status not in ['EXECUTED', 'CANCELLED']:
        return jsonify({"status": "error", "message": "Invalid status"}), 400

    with db_lock, sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("UPDATE trade_actions SET status = ? WHERE id = ?", (new_status, action_id))
        conn.commit()
        if c.rowcount > 0:
            logging.info(f"Stav akce ID {action_id} aktualizován na '{new_status}'.")
            return jsonify({"status": "ok"})
        else:
            return jsonify({"status": "error", "message": "Action not found"}), 404

if __name__ == "__main__":
    init_db();flask_thread = threading.Thread(target=run_flask, daemon=True);flask_thread.start();app = TelegramBotApp();app.mainloop()
