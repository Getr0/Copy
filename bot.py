import tkinter as tk
import customtkinter as ctk
from tkinter import messagebox
import re
import os
import sqlite3
import datetime
import threading
import asyncio
import json
import glob
import logging
import requests # <-- Přidán potřebný import
from flask import Flask, jsonify, request
from telethon import TelegramClient, events, types # Přidán import types
from telethon.errors import SessionPasswordNeededError, RPCError
from telethon.sessions import SQLiteSession
import concurrent.futures # Vráceno - používá se v _shutdown_client

API_ID = 24670509
API_HASH = '0ca1de09bc2b41dfd98168b84cc88d7b'
DB_NAME = 'signals.db'
SESSIONS_DIR = 'sessions'
LOGGING_LEVEL = logging.INFO

logging.basicConfig(level=LOGGING_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

PIP_SIZE_XAUUSD = 0.1
DEFAULT_SL_PIPS = 40.0
INITIAL_TRADE_1_TP_PIPS = 40.0
INITIAL_TRADE_2_DEFAULT_TP_PIPS = 200.0
REENTRY_TP_PIPS = 40.0

SIGNAL_TYPE_INITIAL_T1 = "INITIAL_T1"
SIGNAL_TYPE_INITIAL_T2_DEFAULT = "INITIAL_T2_DEFAULT"
SIGNAL_TYPE_UPDATE_T2 = "UPDATE_T2"
SIGNAL_TYPE_RE_ENTRY = "RE_ENTRY"
SIGNAL_TYPE_IGNORE = "IGNORE"
SIGNAL_TYPE_UNKNOWN = "UNKNOWN"
SIGNAL_TYPE_STANDARD = "STANDARD" # Přidána konstanta pro standardní typ

db_lock = threading.Lock()

def _check_and_add_column(cursor, table_name, column_name, column_type):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    if column_name not in columns:
        logging.info(f"Aktualizuji databázi: Přidávám sloupec '{column_name}' do tabulky '{table_name}'.")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

def init_db():
    with db_lock, sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signals'")
        if not c.fetchone():
            c.execute('''CREATE TABLE signals (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           symbol TEXT,
                           action TEXT,
                           entry_price REAL,
                           sl REAL,
                           tp1 REAL,
                           tp2 REAL,
                           tp3 REAL,
                           timestamp DATETIME,
                           status TEXT,
                           ticket INTEGER,
                           signal_group_id TEXT,
                           trade_label TEXT,
                           signal_type TEXT,
                           sl_value REAL,
                           tp_value REAL,
                           sl_value_type TEXT,
                           tp_value_type TEXT,
                           tp2_value REAL,
                           tp2_value_type TEXT,
                           be_active TEXT DEFAULT 'FALSE',
                           ts_active TEXT DEFAULT 'FALSE',
                           be_trigger_condition_type TEXT,
                           be_trigger_target_ticket INTEGER,
                           ts_trigger_condition_type TEXT,
                           ts_trigger_target_ticket INTEGER,
                           ts_start_pips REAL,
                           ts_step_pips REAL,
                           ts_distance_pips REAL,
                           is_tp1_for_be_ts TEXT DEFAULT 'FALSE'
                           )''')
            logging.info("Nová tabulka 'signals' vytvořena s novou strukturou (včetně BE/TS polí).")
        else:
            c.execute("PRAGMA table_info(signals)")
            columns = [row[1] for row in c.fetchall()]
            if 'signal_type' not in columns or 'be_active' not in columns: # Check for one of old and one of new
                logging.warning("Detekována stará struktura tabulky 'signals' nebo chybí BE/TS pole. Přejmenovávám na 'signals_old' a vytvářím novou.")
                try:
                    c.execute("ALTER TABLE signals RENAME TO signals_old")
                    logging.info("Stará tabulka 'signals' přejmenována na 'signals_old'.")
                except sqlite3.OperationalError as e:
                    logging.error(f"Nepodařilo se přejmenovat starou tabulku 'signals': {e}. Možná 'signals_old' již existuje.")
                c.execute('''CREATE TABLE signals (
                               id INTEGER PRIMARY KEY AUTOINCREMENT,
                               symbol TEXT,
                               action TEXT,
                               entry_price REAL,
                               timestamp DATETIME,
                               status TEXT,
                               ticket INTEGER,
                               signal_group_id TEXT,
                               trade_label TEXT,
                               signal_type TEXT,
                               sl_value REAL,
                               tp_value REAL,
                               sl_value_type TEXT,
                               tp_value_type TEXT,
                               tp2_value REAL,
                               tp2_value_type TEXT,
                               be_active TEXT DEFAULT 'FALSE',
                               ts_active TEXT DEFAULT 'FALSE',
                               be_trigger_condition_type TEXT,
                               be_trigger_target_ticket INTEGER,
                               ts_trigger_condition_type TEXT,
                               ts_trigger_target_ticket INTEGER,
                               ts_start_pips REAL,
                               ts_step_pips REAL,
                               ts_distance_pips REAL,
                               is_tp1_for_be_ts TEXT DEFAULT 'FALSE'
                               )''')
                logging.info("Nová tabulka 'signals' vytvořena po přejmenování staré (včetně BE/TS polí).")
            else:
                logging.info("Tabulka 'signals' existuje s očekávanou strukturou, kontroluji a přidávám BE/TS sloupce pokud chybí.")
                _check_and_add_column(c, 'signals', 'status', 'TEXT')
                _check_and_add_column(c, 'signals', 'ticket', 'INTEGER')
                _check_and_add_column(c, 'signals', 'signal_group_id', 'TEXT')
                _check_and_add_column(c, 'signals', 'trade_label', 'TEXT')
                _check_and_add_column(c, 'signals', 'signal_type', 'TEXT')
                _check_and_add_column(c, 'signals', 'sl_value', 'REAL')
                _check_and_add_column(c, 'signals', 'tp_value', 'REAL')
                _check_and_add_column(c, 'signals', 'sl_value_type', 'TEXT')
                _check_and_add_column(c, 'signals', 'tp_value_type', 'TEXT')
                _check_and_add_column(c, 'signals', 'tp2_value', 'REAL')
                _check_and_add_column(c, 'signals', 'tp2_value_type', 'TEXT')
                # Add new BE/TS columns
                _check_and_add_column(c, 'signals', 'be_active', "TEXT DEFAULT 'FALSE'")
                _check_and_add_column(c, 'signals', 'ts_active', "TEXT DEFAULT 'FALSE'")
                _check_and_add_column(c, 'signals', 'be_trigger_condition_type', 'TEXT')
                _check_and_add_column(c, 'signals', 'be_trigger_target_ticket', 'INTEGER')
                _check_and_add_column(c, 'signals', 'ts_trigger_condition_type', 'TEXT')
                _check_and_add_column(c, 'signals', 'ts_trigger_target_ticket', 'INTEGER')
                _check_and_add_column(c, 'signals', 'ts_start_pips', 'REAL')
                _check_and_add_column(c, 'signals', 'ts_step_pips', 'REAL')
                _check_and_add_column(c, 'signals', 'ts_distance_pips', 'REAL')
                _check_and_add_column(c, 'signals', 'is_tp1_for_be_ts', "TEXT DEFAULT 'FALSE'")

        c.execute("DELETE FROM signals WHERE status = 'new' OR status IS NULL")
        conn.commit()

        c.execute('''
            CREATE TABLE IF NOT EXISTS trade_functions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_db_id INTEGER,
                ticket_id INTEGER,
                function_type TEXT NOT NULL CHECK(function_type IN ('BE', 'TS')),
                ts_type TEXT CHECK(ts_type IN ('CLASSIC', 'CONVERGENT')),
                activation_condition_type TEXT NOT NULL CHECK(activation_condition_type IN ('ON_CLOSE_TICKET', 'MANUAL', 'IMMEDIATE')),
                activation_target_ticket INTEGER,
                is_active TEXT NOT NULL DEFAULT 'FALSE' CHECK(is_active IN ('TRUE', 'FALSE')),
                params_json TEXT,
                tp_target_price REAL,
                status_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (signal_db_id) REFERENCES signals (id) ON DELETE CASCADE
            )
        ''')
        logging.info("Tabulka 'trade_functions' zkontrolována/vytvořena.")

        c.execute('''
            CREATE TRIGGER IF NOT EXISTS update_trade_functions_updated_at
            AFTER UPDATE ON trade_functions
            FOR EACH ROW
            BEGIN
                UPDATE trade_functions SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
            END;
        ''')
        logging.info("Trigger 'update_trade_functions_updated_at' zkontrolován/vytvořen.")
        conn.commit()


class SessionManager:
    def __init__(self, base_dir=SESSIONS_DIR):
        self.sessions_dir = base_dir
        os.makedirs(self.sessions_dir, exist_ok=True)
    def _clean_phone_number(self, phone): return re.sub(r"\D", "", phone)
    def get_session_path(self, phone): return os.path.join(self.sessions_dir, self._clean_phone_number(phone))
    def get_saved_phone_numbers(self):
        phone_numbers = []
        for session_file in glob.glob(os.path.join(self.sessions_dir, "*.session")):
            phone_numbers.append("+" + os.path.basename(session_file).replace(".session", ""))
        return phone_numbers
    def remove_phone_number(self, phone):
        session_path = self.get_session_path(phone) + ".session"
        if os.path.exists(session_path):
            try:
                os.remove(session_path)
                logging.info(f"Session soubor {session_path} úspěšně smazán.")
                return True
            except OSError as e:
                logging.error(f"Nepodařilo se smazat session soubor {session_path}: {e}")
                return False
        else:
            logging.warning(f"Session soubor {session_path} pro smazání nenalezen.")
            return False

def parse_sniper_pro(message_text: str) -> dict | None:
    message_text_cleaned = message_text.strip()

    re_ignore_pips = re.compile(r"^\d+\s+pips\s+ruining\s*✅", re.IGNORECASE)
    re_ignore_book_profit = re.compile(r"^Book\s+some\s+profit", re.IGNORECASE)
    re_ignore_reentry_closed = re.compile(r"^(Not\s+active\s+re\s*entry\s+closed|Closed\s+re\s*entry)", re.IGNORECASE)
    if re_ignore_pips.search(message_text_cleaned) or \
       re_ignore_book_profit.search(message_text_cleaned) or \
       re_ignore_reentry_closed.search(message_text_cleaned):
        return {'type': SIGNAL_TYPE_IGNORE, 'reason': 'Matched ignore pattern'}

    re_reentry = re.compile(
        r"FOR\s+(GOLD|XAUUSD)\s+REE\s+ENTRY"
        r"(?:[\s\S]*?)"  # Jakýkoliv text mezi "ENTRY" a "WITH SL" (non-capturing, líný)
        r"WITH\s+SL\s*[:\s]?\s*([\d\.]+)",
        re.IGNORECASE | re.DOTALL # DOTALL je zde důležitý pro [\s\S]
    )
    match_reentry = re_reentry.search(message_text_cleaned)
    if match_reentry:
        symbol_raw = match_reentry.group(1).upper()
        symbol = "XAUUSD" if symbol_raw == "GOLD" else symbol_raw
        try:
            sl_price = float(match_reentry.group(2))
            return {'type': SIGNAL_TYPE_RE_ENTRY, 'symbol': symbol, 'sl_price': sl_price}
        except ValueError:
            logging.warning(f"Chyba konverze ceny SL v re-entry signálu: {match_reentry.group(2)}")
            return {'type': SIGNAL_TYPE_UNKNOWN, 'reason': 'Re-entry SL price conversion error'}

    re_initial = re.compile(
        r"^(GOLD|XAUUSD)\s+(BUY|SELL|SEEL)\s+([\d\.]+)(?:\s+[\d\.]+)?(?:\s+small\s+lot)?$",
        re.IGNORECASE
    )
    match_initial = re_initial.search(message_text_cleaned)
    if match_initial:
        has_sl = re.search(r"Sl\s*[:\s]?\s*[\d\.]+", message_text_cleaned, re.IGNORECASE)
        has_tp = re.search(r"Tp\s*[:\s]?\s*[\d\.]+", message_text_cleaned, re.IGNORECASE)
        if has_sl and has_tp:
            logging.debug(f"Text odpovídá vzoru INITIAL, ale obsahuje SL/TP. Zkouším jako UPDATE_SLTP: {message_text_cleaned[:50]}")
        else:
            symbol_raw = match_initial.group(1).upper()
            symbol = "XAUUSD" if symbol_raw == "GOLD" else symbol_raw
            action_raw = match_initial.group(2).upper()
            action = "SELL" if action_raw == "SEEL" else action_raw
            try:
                entry_price_ref = float(match_initial.group(3))
                return {'type': 'INITIAL', 'symbol': symbol, 'action': action, 'entry_price_ref': entry_price_ref}
            except ValueError:
                logging.warning(f"Chyba konverze referenční ceny v iniciálním signálu: {match_initial.group(3)}")
                return {'type': SIGNAL_TYPE_UNKNOWN, 'reason': 'Initial signal entry price conversion error'}
    sl_pattern_general = r"Sl\s*[:\s]?\s*([\d\.]+)"
    match_sl_general = re.search(sl_pattern_general, message_text_cleaned, re.IGNORECASE)

    if match_sl_general:
        sl_price_str = match_sl_general.group(1)
        tp_matches_all = re.findall(r"Tp\s*[:\s]?\s*([\d\.]+)", message_text_cleaned, re.IGNORECASE)

        if tp_matches_all:
            try:
                sl_price = float(sl_price_str)
                tp_prices = [float(tp_str) for tp_str in tp_matches_all]
                return {'type': 'UPDATE_SLTP', 'sl_price': sl_price, 'tp_prices': tp_prices}
            except ValueError:
                logging.warning(f"Chyba konverze cen v UPDATE_SLTP (general): SL='{sl_price_str}', TPs='{tp_matches_all}'")
                return {'type': SIGNAL_TYPE_UNKNOWN, 'reason': 'SL/TP update price conversion error (general)'}
        else:
            logging.debug(f"UPDATE_SLTP: Našlo SL='{sl_price_str}', ale žádné TP v celé zprávě. Nepovažuji za platný UPDATE_SLTP.")

    logging.debug(f"Zpráva nebyla rozpoznána žádným parserem SniperPro: '{message_text_cleaned}'")
    return {'type': SIGNAL_TYPE_UNKNOWN, 'reason': 'No SniperPro pattern matched'}

def parse_standard_signal(message_text: str) -> dict | None:
    message_text_lower = message_text.lower()
    lines = message_text_lower.split('\n')
    cleaned_lines = [line for line in lines if not ("pips ruining" in line or "book some profit" in line or "closed re entry" in line or "not active" in line)]
    message_text_cleaned_for_standard = "\n".join(cleaned_lines).strip()
    if not message_text_cleaned_for_standard:
        return None

    match_pattern1 = re.search(
        r'^(?P<action>buy|sell)\s+(?P<symbol>[a-z0-9/]+)\s+(?P<entry_price>[\d\.]+)',
        message_text_cleaned_for_standard,
        re.IGNORECASE
    )
    if match_pattern1:
        data = match_pattern1.groupdict()
        symbol = data['symbol'].upper().replace('/', '')
        action = data['action'].upper()
        try:
            entry_price = float(data['entry_price'])
            sl_match = re.search(r'sl\s*[:\s]?\s*([\d\.]+)', message_text_lower)
            sl = float(sl_match.group(1)) if sl_match else None
            tp_matches = re.findall(r'tp\d?\s*[:\s]?\s*([\d\.]+)', message_text_lower)
            tp_values = [float(tp) for tp in tp_matches] if tp_matches else []
            return {
                'type': 'STANDARD', 'symbol': symbol, 'action': action,
                'entry_price_ref': entry_price, 'sl_price': sl, 'tp_prices': tp_values
            }
        except ValueError:
            logging.warning(f"Chyba konverze čísel ve standardním parseru (formát 1) pro: {message_text}")
            return None

    match_pattern2 = re.search(
        r'^(?P<symbol>[a-z0-9/]+)\s+(?P<action>buy|sell)(?:\s+(?:limit|stop))?\s+(?P<entry_price>[\d\.]+)',
        message_text_cleaned_for_standard,
        re.IGNORECASE
    )
    if match_pattern2:
        data = match_pattern2.groupdict()
        symbol = data['symbol'].upper().replace('/', '')
        action = data['action'].upper()
        try:
            entry_price = float(data['entry_price'])
            sl_match = re.search(r'sl\s*[:\s]?\s*([\d\.]+)', message_text_lower)
            sl = float(sl_match.group(1)) if sl_match else None
            tp_matches = re.findall(r'tp\d?\s*[:\s]?\s*([\d\.]+)', message_text_lower)
            tp_values = [float(tp) for tp in tp_matches] if tp_matches else []
            return {
                'type': 'STANDARD', 'symbol': symbol, 'action': action,
                'entry_price_ref': entry_price, 'sl_price': sl, 'tp_prices': tp_values
            }
        except ValueError:
            logging.warning(f"Chyba konverze čísel ve standardním parseru (formát 2) pro: {message_text}")
            return None
    return None

class TelegramBotApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Telegram Signal Monitor"); self.geometry("1000x700")
        ctk.set_appearance_mode("dark")
        self.BG_COLOR, self.FRAME_COLOR, self.TEXT_COLOR = "#2B2939", "#363347", "#EAEAEA"
        self.ACCENT_COLOR, self.ACCENT_HOVER_COLOR = "#E91E63", "#C2185B"
        self.ENTRY_BG_COLOR, self.RED_COLOR, self.RED_HOVER_COLOR = "#22212C", "#D32F2F", "#B71C1C"
        self.FONT_NORMAL, self.FONT_BOLD = ("Segoe UI", 12), ("Segoe UI", 12, "bold")
        self.FONT_TITLE, self.FONT_LOG = ("Segoe UI", 18, "bold"), ("Consolas", 10)
        self.session_manager = SessionManager()
        self.client_loop, self.client_thread, self.main_client = None, None, None
        self.client_running = False
        self.monitoring_handlers, self.monitoring_states = {}, {}
        self.parsing_methods = {} # Stores parsing method per channel_id
        self.channel_contexts = {}
        self.processed_message_ids = set()
        self.message_id_lock = threading.Lock()
        self.function_defaults = {}
        self._init_default_function_settings()


        self._create_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _init_default_function_settings(self):
        parser_types = ["SniperPro", "Standardní"]
        for p_type in parser_types:
            self.function_defaults[p_type] = {
                'be_active': tk.BooleanVar(value=True),
                'ts_active': tk.BooleanVar(value=True),
                'ts_type': tk.StringVar(value="Classic"), # Default to Classic
                'classic_ts_start_pips': tk.DoubleVar(value=20.0),
                'classic_ts_step_pips': tk.DoubleVar(value=10.0),
                'classic_ts_distance_pips': tk.DoubleVar(value=15.0),
                'convergent_activation_start_pips': tk.DoubleVar(value=30.0),
                'convergent_converge_factor': tk.DoubleVar(value=0.5), # Range 0-1
                'convergent_min_stop_distance_pips': tk.DoubleVar(value=10.0)
            }

    def _create_widgets(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1) # Adjusted row for channels_list_frame
        top_controls_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_controls_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(15,10))

        login_frame = ctk.CTkFrame(top_controls_frame, fg_color=self.FRAME_COLOR, corner_radius=8)
        login_frame.pack(side="left", fill="x", expand=True, padx=(0,10))
        login_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(login_frame, text="Tel. číslo:", font=self.FONT_BOLD).grid(row=0, column=0, padx=(10,5), pady=10, sticky="w")
        self.phone_entry_var = tk.StringVar()
        self.phone_entry = ctk.CTkEntry(login_frame, textvariable=self.phone_entry_var, font=self.FONT_NORMAL,
                                        fg_color=self.ENTRY_BG_COLOR, border_width=0, corner_radius=6)
        self.phone_entry.grid(row=0, column=1, sticky="ew", padx=0, pady=10)

        manage_button = ctk.CTkButton(login_frame, text="Vybrat / Spravovat", command=self._show_phone_selector,
                                      font=self.FONT_NORMAL, fg_color=self.BG_COLOR, hover_color=self.ENTRY_BG_COLOR,
                                      corner_radius=6, width=140)
        manage_button.grid(row=0, column=2, padx=5, pady=10)

        connect_button = ctk.CTkButton(login_frame, text="Připojit", command=self._connect_telegram,
                                       font=self.FONT_BOLD, fg_color=self.ACCENT_COLOR, hover_color=self.ACCENT_HOVER_COLOR,
                                       corner_radius=6, width=100)
        connect_button.grid(row=0, column=3, padx=(0,10), pady=10)

        functions_button = ctk.CTkButton(top_controls_frame, text="⚙️ Funkce", command=self._show_functions_dialog,
                                         font=self.FONT_BOLD, fg_color=self.FRAME_COLOR, hover_color=self.ENTRY_BG_COLOR,
                                         corner_radius=6, width=120)
        functions_button.pack(side="left", padx=(0,0), pady=10)
        channels_header_frame = ctk.CTkFrame(self, fg_color="transparent")
        channels_header_frame.grid(row=2, column=0, sticky="ew", padx=15, pady=(10, 5)) # Adjusted row
        channels_header_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(channels_header_frame, text="Kanály a skupiny", font=self.FONT_TITLE, anchor="w").grid(row=0, column=0, sticky="w")

        self.refresh_button = ctk.CTkButton(channels_header_frame, text="🔄 Obnovit", command=self._load_dialogs,
                                            state=tk.DISABLED, font=self.FONT_NORMAL, fg_color=self.FRAME_COLOR,
                                            hover_color=self.ENTRY_BG_COLOR, corner_radius=6, width=100)
        self.refresh_button.grid(row=0, column=1, sticky="e")

        self.channels_list_frame = ctk.CTkScrollableFrame(self, fg_color=self.FRAME_COLOR, corner_radius=8)
        self.channels_list_frame.grid(row=3, column=0, sticky="nsew", padx=15, pady=(0,10)) # Adjusted row
        self.channels_list_frame.grid_columnconfigure(0, weight=1)

        log_frame = ctk.CTkFrame(self, fg_color=self.FRAME_COLOR, corner_radius=8)
        log_frame.grid(row=4, column=0, sticky="ew", padx=15, pady=(0,15)) # Adjusted row
        log_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_frame, text="Protokol událostí", font=self.FONT_BOLD, anchor="w").grid(row=0, column=0, padx=10, pady=(8,4), sticky="w")
        self.log_text = ctk.CTkTextbox(log_frame, state=tk.DISABLED, font=self.FONT_LOG,
                                       fg_color=self.ENTRY_BG_COLOR, corner_radius=6, border_width=0,
                                       wrap="word", height=120)
        self.log_text.grid(row=1, column=0, sticky="ew", padx=10, pady=(0,10))

    def _update_log(self, text, level="INFO"):
        def task():
            current_text = self.log_text.get("1.0", tk.END)
            lines = current_text.splitlines()
            if len(lines) > 1000:
                current_text = "\n".join(lines[-1000:]) + "\n"
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.delete("1.0", tk.END)
                self.log_text.insert("1.0", current_text)

            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {text}\n")
            self.log_text.configure(state=tk.DISABLED); self.log_text.see(tk.END)
        self.after(0, task)
        logging.log(getattr(logging, level.upper(), logging.INFO), text)

    def _get_auth_code_or_pass(self, title, future_to_set):
        self._update_log(f"Otevírám dialog pro: {title}", "DEBUG")
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("380x200")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(fg_color=self.BG_COLOR)
        dialog.attributes("-topmost", True)

        main_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        main_frame.pack(padx=20, pady=20, fill="both", expand=True)

        ctk.CTkLabel(main_frame, text=f"Zadejte {title.lower()}:", font=self.FONT_BOLD).pack(pady=(0, 10))
        entry_var = tk.StringVar()
        entry = ctk.CTkEntry(main_frame, textvariable=entry_var,
                             show="*" if "heslo" in title.lower() else "",
                             font=self.FONT_NORMAL, fg_color=self.ENTRY_BG_COLOR,
                             border_width=0, corner_radius=6)
        entry.pack(pady=5, fill='x')
        entry.focus()

        def on_submit(event=None):
            value = entry_var.get()
            self._update_log(f"Dialog '{title}': Potvrzeno s hodnotou '{value if title != '2FA heslo' else '********'}'.", "DEBUG")
            if not future_to_set.done(): future_to_set.set_result(value)
            else: self._update_log(f"Dialog '{title}': Future již byla nastavena, druhé potvrzení ignorováno.", "WARNING")
            dialog.destroy()

        def on_dialog_close():
            self._update_log(f"Dialog '{title}': Uzavřen křížkem.", "DEBUG")
            if not future_to_set.done(): future_to_set.set_result(None)
            else: self._update_log(f"Dialog '{title}': Future již byla nastavena, uzavření křížkem po potvrzení.", "DEBUG")
            dialog.destroy()

        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(pady=(15,0), fill='x')
        btn_frame.grid_columnconfigure((0,1), weight=1)

        cancel_btn = ctk.CTkButton(btn_frame, text="Zrušit", font=self.FONT_NORMAL,
                                   fg_color=self.FRAME_COLOR, hover_color=self.ENTRY_BG_COLOR,
                                   command=on_dialog_close, corner_radius=6)
        cancel_btn.grid(row=0, column=0, sticky='ew', padx=(0,5), pady=5)

        confirm_btn = ctk.CTkButton(btn_frame, text="Potvrdit", font=self.FONT_BOLD,
                                    fg_color=self.ACCENT_COLOR, hover_color=self.ACCENT_HOVER_COLOR,
                                    command=on_submit, corner_radius=6)
        confirm_btn.grid(row=0, column=1, sticky='ew', padx=(5,0), pady=5)

        dialog.bind("<Return>", on_submit)
        dialog.protocol("WM_DELETE_WINDOW", on_dialog_close)
        self.wait_window(dialog)
        if not future_to_set.done():
            self._update_log(f"Dialog '{title}': Uzavřen bez nastavení future (fallback). Nastavuji None.", "WARNING")
            future_to_set.set_result(None)

    def _connect_telegram(self):
        phone_number = self.phone_entry_var.get()
        if not re.match(r'^\+\d+$', phone_number):
            messagebox.showerror("Chyba", "Zadejte platné telefonní číslo v mezinárodním formátu (např. +420123456789).", parent=self)
            return

        if self.client_thread and self.client_thread.is_alive():
            self._update_log("Detekováno běžící klientské vlákno. Pokouším se ho nejprve řádně ukončit...", "INFO")
            self._shutdown_client()
            if self.client_thread and self.client_thread.is_alive():
                 self._update_log("VAROVÁNÍ: Předchozí klientské vlákno se nepodařilo plně ukončit. Nové připojení může selhat.", "WARNING")

        if self.client_running:
            self._update_log("Klient byl stále označen jako běžící, volám _shutdown_client znovu (pojistka).", "DEBUG")
            self._shutdown_client()

        self._update_log("Resetuji interní stavy pro nové připojení.", "DEBUG")
        self.monitoring_handlers, self.monitoring_states = {}, {}
        self.parsing_methods = {}
        self.channel_contexts = {}
        for widget in self.channels_list_frame.winfo_children(): widget.destroy()

        self._update_log(f"Spouštím nové připojení pro {phone_number}...")
        self.client_loop = asyncio.new_event_loop()
        self.client_thread = threading.Thread(target=self._client_worker, args=(phone_number,), daemon=True)
        self.client_thread.start()
        self.after(100, self._check_client_connection)

    def _client_worker(self, phone_number):
        asyncio.set_event_loop(self.client_loop)
        try:
            self.client_loop.run_until_complete(self._run_client(phone_number))
        except Exception as e:
            self._update_log(f"Kritická chyba nebo přerušení v klientském vlákně: {e}", "ERROR")
        finally:
            if self.main_client and self.main_client.is_connected():
                try:
                    self.client_loop.run_until_complete(self.main_client.disconnect())
                    self._update_log("Klient byl odpojen ve finally bloku workeru.", "INFO")
                except Exception as e:
                    self._update_log(f"Chyba při odpojování klienta ve finally bloku workeru: {e}", "ERROR")

            if self.client_loop.is_running():
                self.client_loop.call_soon_threadsafe(self.client_loop.stop)

            if not self.client_loop.is_running():
                self._update_log(f"Asyncio smyčka ({id(self.client_loop)}) byla zastavena, uzavírám ji.", "DEBUG")
                self.client_loop.close()

            self.main_client = None
            self.client_running = False
            self.after(0, lambda: self.refresh_button.configure(state=tk.DISABLED))
            self._update_log("Klientské vlákno bylo definitivně ukončeno.", "INFO")

    async def _run_client(self, phone_number):
        session_path = self.session_manager.get_session_path(phone_number)
        client = TelegramClient(SQLiteSession(session_path), API_ID, API_HASH, loop=self.client_loop)
        connected_successfully = False
        authorized_successfully = False

        try:
            self._update_log(f"Pokus o připojení k Telegramu pro {phone_number}...")
            await client.connect()
            if not client.is_connected():
                self._update_log("Nepodařilo se připojit k Telegramu.", "ERROR")
                return
            self._update_log("Úspěšně připojeno k Telegramu.")
            connected_successfully = True

            if not await client.is_user_authorized():
                self._update_log("Uživatel není autorizován. Zahajuji proces autorizace...")
                phone_code_hash = None
                try:
                    self._update_log(f"Zasílám ověřovací kód na {phone_number}...")
                    sent_code_obj = await client.send_code_request(phone_number)
                    phone_code_hash = sent_code_obj.phone_code_hash
                    self._update_log("Ověřovací kód byl odeslán.")
                except RPCError as e:
                    self._update_log(f"Chyba při zasílání kódu (RPCError): {e}", "ERROR")
                    if "PHONE_NUMBER_INVALID" in str(e).upper():
                         messagebox.showerror("Chyba Autorizace", f"Zadané telefonní číslo {phone_number} je neplatné.", parent=self)
                    return
                except Exception as e:
                    self._update_log(f"Obecná chyba při zasílání kódu: {e}", "ERROR")
                    return

                future_code = self.client_loop.create_future()
                self.after(0, self._get_auth_code_or_pass, "ověřovací kód", future_code)
                self._update_log("Čekám na zadání ověřovacího kódu od uživatele (future)...", "DEBUG")
                code = await future_code

                if not code:
                    self._update_log(f"Uživatel nezadal ověřovací kód (výsledek dialogu: '{code}'). Autorizace zrušena.", "WARNING")
                    return

                self._update_log(f"Ověřovací kód '{code}' přijat od uživatele. Pokouším se přihlásit.", "INFO")
                try:
                    await client.sign_in(phone_number, code, phone_code_hash=phone_code_hash)
                    authorized_successfully = True
                    self._update_log("Přihlášení pomocí kódu bylo úspěšné.")
                except SessionPasswordNeededError:
                    self._update_log("Vyžadováno heslo pro dvoufázové ověření (2FA).")
                    future_password = self.client_loop.create_future()
                    self.after(0, self._get_auth_code_or_pass, "2FA heslo", future_password)
                    self._update_log("Čekám na zadání 2FA hesla od uživatele (future)...", "DEBUG")
                    password = await future_password

                    if not password:
                        self._update_log(f"Uživatel nezadal 2FA heslo (výsledek dialogu: '{password}'). Autorizace zrušena.", "WARNING")
                        return

                    self._update_log("2FA heslo přijato. Pokouším se přihlásit pomocí 2FA hesla...", "INFO")
                    try:
                        await client.sign_in(password=password)
                        authorized_successfully = True
                        self._update_log("Přihlášení pomocí 2FA hesla bylo úspěšné.")
                    except RPCError as e_2fa:
                        self._update_log(f"Chyba při přihlašování pomocí 2FA hesla (RPCError): {e_2fa}", "ERROR")
                        if "PASSWORD_HASH_INVALID" in str(e_2fa).upper():
                             messagebox.showerror("Chyba Autorizace", "Zadané 2FA heslo je neplatné.", parent=self)
                        return
                    except Exception as e_2fa_generic:
                        self._update_log(f"Obecná chyba při přihlašování pomocí 2FA hesla: {e_2fa_generic}", "ERROR")
                        return
                except RPCError as e_code:
                    logging.debug(f"Pokus o přihlášení s kódem '{code}' selhal.")
                    self._update_log(f"Chyba při přihlašování pomocí kódu (RPCError): {e_code}", "ERROR")
                    if "PHONE_CODE_INVALID" in str(e_code).upper():
                         messagebox.showerror("Chyba Autorizace", "Zadaný ověřovací kód je neplatný.", parent=self)
                    elif "PHONE_CODE_EXPIRED" in str(e_code).upper():
                         messagebox.showerror("Chyba Autorizace", "Ověřovací kód vypršel. Zkuste to prosím znovu.", parent=self)
                    elif "FLOOD_WAIT" in str(e_code).upper():
                        wait_time = re.search(r"FLOOD_WAIT_(\d+)", str(e_code))
                        wait_msg = f"Příliš mnoho pokusů. Zkuste to prosím znovu za {wait_time.group(1) if wait_time else 'chvíli'}."
                        messagebox.showerror("Chyba Autorizace", wait_msg, parent=self)
                    return
                except Exception as e_code_generic:
                    logging.debug(f"Pokus o přihlášení s kódem '{code}' selhal s obecnou chybou.")
                    self._update_log(f"Obecná chyba při přihlašování pomocí kódu: {e_code_generic}", "ERROR")
                    return
            else:
                authorized_successfully = True
                self._update_log("Uživatel je již autorizován.")

            if authorized_successfully:
                me = await client.get_me()
                if me:
                    self._update_log(f"Úspěšně přihlášen jako: {me.first_name} {me.last_name or ''} (ID: {me.id})")
                    self.main_client = client
                    self.client_running = True
                    self.after(0, lambda: self.refresh_button.configure(state=tk.NORMAL))
                    self.after(0, self._load_dialogs)
                    await client.run_until_disconnected()
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
                self._update_log("Odpojuji klienta v `finally` bloku _run_client...", "INFO")
                await client.disconnect()
                self._update_log("Klient odpojen.", "INFO")

            if not (self.main_client and self.client_running):
                self.main_client = None
                self.client_running = False
                self.after(0, lambda: self.refresh_button.configure(state=tk.DISABLED))

            self._update_log(f"Ukončuji _run_client pro {phone_number}. client_running={self.client_running}", "INFO")

    def _check_client_connection(self):
        if self.client_running and self.main_client and self.main_client.is_connected():
            self.refresh_button.configure(state=tk.NORMAL)
        elif self.client_thread and self.client_thread.is_alive():
            self.after(200, self._check_client_connection)
        else:
            self._update_log("Připojení selhalo nebo bylo ukončeno (kontrola).", "WARNING")
            self.refresh_button.configure(state=tk.DISABLED)

    def _load_dialogs(self):
        if not self.main_client or not self.client_loop or not self.client_running or not self.main_client.is_connected():
            self._update_log("Klient není připraven nebo připojen pro načtení dialogů.", "WARNING")
            self.refresh_button.configure(state=tk.DISABLED)
            return

        self._update_log("Aktualizuji seznam kanálů...")
        self.refresh_button.configure(state=tk.DISABLED)

        async def get_dialogs_task():
            try:
                return await self.main_client.get_dialogs(limit=None)
            except RPCError as e:
                self._update_log(f"Chyba při komunikaci s Telegramem (get_dialogs): {e}", "ERROR")
                return []
            except Exception as e:
                self._update_log(f"Neočekávaná chyba při načítání dialogů: {e}", "ERROR")
                return []

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

        future = asyncio.run_coroutine_threadsafe(get_dialogs_task(), self.client_loop)
        future.add_done_callback(on_dialogs_loaded)

    def _display_dialogs(self, dialogs):
        for widget in self.channels_list_frame.winfo_children(): widget.destroy()

        header_frame = ctk.CTkFrame(self.channels_list_frame, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=5)
        header_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header_frame, text="Název kanálu/skupiny", font=self.FONT_BOLD).grid(row=0, column=0, sticky="w", padx=5)
        ctk.CTkLabel(header_frame, text="Metoda parsování", font=self.FONT_BOLD).grid(row=0, column=1, sticky="w", padx=10)
        ctk.CTkLabel(header_frame, text="Akce", font=self.FONT_BOLD).grid(row=0, column=2, sticky="w", padx=10)

        temp_filtered_dialogs = []
        if dialogs:
            for d in dialogs:
                if d and d.entity:
                    is_broadcast_channel = d.is_channel and hasattr(d.entity, 'broadcast') and d.entity.broadcast and not (hasattr(d.entity, 'megagroup') and d.entity.megagroup)
                    is_group_type = d.is_group

                    if is_broadcast_channel or is_group_type:
                        temp_filtered_dialogs.append(d)
                elif d:
                    if d.is_channel or d.is_group:
                        logging.warning(f"Dialog '{d.name}' (ID: {d.id}) nemá atribut 'entity', používám obecnější filtr.")
                        temp_filtered_dialogs.append(d)
            filtered_dialogs = temp_filtered_dialogs
        else:
            filtered_dialogs = []

        filtered_dialogs.sort(key=lambda d: d.name.lower() if d and d.name else "")

        for i, dialog in enumerate(filtered_dialogs):
            dialog_frame = ctk.CTkFrame(self.channels_list_frame, fg_color="transparent", corner_radius=0)
            dialog_frame.grid(row=i + 1, column=0, sticky="ew", pady=(2,0))
            dialog_frame.grid_columnconfigure(0, weight=1)

            display_name = dialog.name if len(dialog.name) < 50 else dialog.name[:47] + "..."
            name_label = ctk.CTkLabel(dialog_frame, text=display_name, anchor='w', font=self.FONT_NORMAL)
            name_label.grid(row=0, column=0, sticky="ew", padx=10)

            parse_method_var = tk.StringVar(value=self.parsing_methods.get(dialog.id, "SniperPro"))
            method_combobox = ctk.CTkComboBox(dialog_frame,
                                              variable=parse_method_var,
                                              values=["SniperPro", "Standardní"],
                                              state="readonly",
                                              font=self.FONT_NORMAL,
                                              width=150,
                                              corner_radius=8,
                                              fg_color=self.ENTRY_BG_COLOR,
                                              border_width=0,
                                              button_color=self.ACCENT_COLOR,
                                              button_hover_color=self.ACCENT_HOVER_COLOR,
                                              command=lambda choice, d_id=dialog.id: self._update_parsing_method(d_id, choice))
            method_combobox.grid(row=0, column=1, padx=10)

            is_monitoring = self.monitoring_states.get(dialog.id, False)
            btn_text = "Zastavit" if is_monitoring else "Monitorovat"
            btn_fg_color = self.RED_COLOR if is_monitoring else self.ACCENT_COLOR
            btn_hover_color = self.RED_HOVER_COLOR if is_monitoring else self.ACCENT_HOVER_COLOR

            monitor_button = ctk.CTkButton(dialog_frame, text=btn_text,
                                           width=120,
                                           fg_color=btn_fg_color,
                                           hover_color=btn_hover_color,
                                           font=self.FONT_NORMAL,
                                           corner_radius=8)
            monitor_button.grid(row=0, column=2, padx=10)
            monitor_button.configure(command=lambda d=dialog, b=monitor_button: self._toggle_monitoring(d, b))

        self._update_log(f"Nalezeno a zobrazeno {len(filtered_dialogs)} relevantních dialogů.")
        if not filtered_dialogs:
            no_dialogs_label = ctk.CTkLabel(self.channels_list_frame, text="Nebyly nalezeny žádné kanály nebo skupiny.",
                                            font=self.FONT_NORMAL, text_color=self.TEXT_COLOR, anchor='center')
            no_dialogs_label.grid(row=1, column=0, sticky="ew", pady=20)

    def _update_parsing_method(self, dialog_id, choice):
        self._update_log(f"Metoda parsování pro kanál ID {dialog_id} změněna na '{choice}'.")
        self.parsing_methods[dialog_id] = choice
        if self.monitoring_states.get(dialog_id, False) and self.main_client and self.client_loop:
            self._update_log("Změna metody pro aktivně monitorovaný kanál. Pro aplikaci změny restartujte monitorování (Zastavit > Monitorovat).", "INFO")

    def _toggle_monitoring(self, dialog, button):
        if not self.main_client or not self.client_loop or not self.client_running:
            self._update_log("Nelze (de)aktivovat monitorování, klient není připraven.", "WARNING")
            return

        dialog_id = dialog.id
        is_currently_monitoring = self.monitoring_states.get(dialog_id, False)
        new_monitoring_state = not is_currently_monitoring
        self.monitoring_states[dialog_id] = new_monitoring_state
        current_method = self.parsing_methods.get(dialog_id, "SniperPro")

        if new_monitoring_state:
            self.after(0, lambda: button.configure(text="Zastavit",
                                                   fg_color=self.RED_COLOR,
                                                   hover_color=self.RED_HOVER_COLOR))
            coro = self._start_message_processor(dialog, current_method)
        else:
            self.after(0, lambda: button.configure(text="Monitorovat",
                                                   fg_color=self.ACCENT_COLOR,
                                                   hover_color=self.ACCENT_HOVER_COLOR))
            coro = self._stop_monitoring(dialog)
        asyncio.run_coroutine_threadsafe(coro, self.client_loop)

    async def _start_message_processor(self, dialog, parse_method: str):
        dialog_id = dialog.id
        if not self.main_client:
            self._update_log(f"Nelze spustit monitorování pro '{dialog.name}', klient není aktivní.", "ERROR")
            self.monitoring_states[dialog_id] = False
            return

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
            # 1. Ověření, zda událost obsahuje platný 'message' objekt a jeho atributy
            if not hasattr(event, 'message') or \
               not event.message or \
               not hasattr(event.message, 'id') or \
               not hasattr(event.message, 'text') or \
               not event.message.text: # Kontrola, zda text není None nebo prázdný
                return
            message_id = event.message.id
            message_text = event.message.text
            with self.message_id_lock:
                if message_id in self.processed_message_ids:
                    self._update_log(f"HANDLER_DUPLICATE_SKIP: Zpráva ID: {message_id} (dialog: {dialog_id}) již byla zpracována. Přeskakuji.", "WARNING")
                    return
                if len(self.processed_message_ids) > 1000:
                    try:
                        self.processed_message_ids.pop()
                    except KeyError:
                        pass
                self.processed_message_ids.add(message_id)
            self._update_log(f"HANDLER_PROCEED: Zpráva ID: {message_id}, Dialog ID: {dialog_id}, Text: \"{message_text[:50].replace('\n', ' ')}\"", "ERROR")
            active_parse_method = parse_method
            self._update_log(f"Nová zpráva z '{dialog.name}' (ID: {dialog_id}). Metoda: '{active_parse_method}'. Zpráva: \"{message_text[:100].replace('\n', ' ')}\"", "DEBUG")

            parsed_data = None
            if active_parse_method == "SniperPro":
                parsed_data = parse_sniper_pro(message_text)
            elif active_parse_method == "Standardní":
                parsed_data = parse_standard_signal(message_text)
                if parsed_data:
                    self._update_log(f"HANDLER_STD_SAVE: Zpráva ID: {message_id}, Data: {parsed_data}", "ERROR") # Použít message_id
                    self._update_log(f"STANDARD signál: {parsed_data['symbol']} {parsed_data['action']}", "INFO")
                    std_signal_group_id = f"{dialog_id}_{parsed_data['symbol']}_STD_{int(datetime.datetime.now().timestamp())}"
                    channel_context = self.channel_contexts.setdefault(dialog_id, {})
                    channel_context['last_initial_symbol'] = parsed_data['symbol']
                    channel_context['last_initial_action'] = parsed_data['action']
                    channel_context['last_initial_entry_price'] = parsed_data['entry_price_ref']
                    channel_context['last_signal_group_id'] = std_signal_group_id

                    tp_prices = parsed_data.get('tp_prices', [])
                    main_tp_price = tp_prices[0] if tp_prices else None
                    optional_tp2_price = tp_prices[1] if len(tp_prices) > 1 else None

                    self._save_signal_data(
                        symbol=parsed_data['symbol'],
                        action=parsed_data['action'],
                        entry_price=parsed_data['entry_price_ref'],
                        signal_group_id=std_signal_group_id,
                        trade_label="STD_TRADE",
                        signal_type=SIGNAL_TYPE_STANDARD,
                        sl_price=parsed_data.get('sl_price'),
                        tp_price=main_tp_price,
                        tp2_price_optional=optional_tp2_price
                    )
                return
            else:
                self._update_log(f"Neznámá metoda parsování '{active_parse_method}' pro '{dialog.name}'. Zpráva ignorována.", "WARNING")
                return

            if not parsed_data or parsed_data.get('type') == SIGNAL_TYPE_UNKNOWN:
                self._update_log(f"Zpráva z '{dialog.name}' ({active_parse_method}) nebyla rozpoznána nebo neznámého typu. Důvod: {parsed_data.get('reason', 'N/A') if parsed_data else 'Parser nevrátil data'}", "DEBUG")
                return

            if parsed_data['type'] == SIGNAL_TYPE_IGNORE:
                self._update_log(f"Zpráva z '{dialog.name}' ({active_parse_method}) ignorována: {parsed_data.get('reason', '')}", "DEBUG")
                return

            channel_context = self.channel_contexts.setdefault(dialog_id, {})
            current_signal_type_from_parser = parsed_data['type']

            if current_signal_type_from_parser == 'INITIAL':
                symbol = parsed_data['symbol']
                action = parsed_data['action']
                entry_price_ref = parsed_data['entry_price_ref']
                signal_group_id = f"{dialog_id}_{symbol}_{action}_{int(datetime.datetime.now().timestamp())}"

                channel_context['last_initial_symbol'] = symbol
                channel_context['last_initial_action'] = action
                channel_context['last_initial_entry_price'] = entry_price_ref
                channel_context['last_signal_group_id'] = signal_group_id

                self._update_log(f"SNIPERPRO INITIAL: {symbol} {action} @ {entry_price_ref}. GroupID: {signal_group_id}", "INFO")
                self._update_log(f"HANDLER_SNIPER_INITIAL_SAVE_T1: Zpráva ID: {message_id}, GroupID: {signal_group_id}", "DEBUG")
                t1_signal_db_id = self._save_signal_data(
                    symbol=symbol, action=action, entry_price=entry_price_ref,
                    signal_group_id=signal_group_id, trade_label="T1_AUTO",
                    signal_type=SIGNAL_TYPE_INITIAL_T1,
                    sl_pips=DEFAULT_SL_PIPS, tp_pips=INITIAL_TRADE_1_TP_PIPS,
                    is_tp1_for_be_ts=True
                )
                self._update_log(f"HANDLER_SNIPER_INITIAL_SAVE_T2: Zpráva ID: {message_id}, GroupID: {signal_group_id}", "DEBUG")
                t2_signal_db_id = self._save_signal_data(
                    symbol=symbol, action=action, entry_price=entry_price_ref,
                    signal_group_id=signal_group_id, trade_label="T2_AUTO",
                    signal_type=SIGNAL_TYPE_INITIAL_T2_DEFAULT,
                    sl_pips=DEFAULT_SL_PIPS, tp_pips=INITIAL_TRADE_2_DEFAULT_TP_PIPS,
                    be_active=self.function_defaults["SniperPro"]['be_active'].get(),
                    ts_active=self.function_defaults["SniperPro"]['ts_active'].get()
                )

                if t2_signal_db_id:
                    sniper_pro_defaults = self.function_defaults["SniperPro"]
                    if sniper_pro_defaults['be_active'].get():
                        be_params = {"offset_pips": 1.0} # Example BE param, make configurable if needed
                        self._save_trade_function_definition(
                            signal_db_id=t2_signal_db_id, ticket_id=None,
                            function_type="BE", ts_type=None,
                            activation_condition_type="ON_CLOSE_TICKET", activation_target_ticket=None, # TP1 ticket unknown yet
                            params=be_params
                        )
                    if sniper_pro_defaults['ts_active'].get():
                        ts_type = sniper_pro_defaults['ts_type'].get()
                        ts_params = {}
                        tp2_target_price_for_convergent = None
                        if action == "BUY":
                            tp2_target_price_for_convergent = entry_price_ref + (INITIAL_TRADE_2_DEFAULT_TP_PIPS * (PIP_SIZE_XAUUSD if symbol == "XAUUSD" else 0.0001)) # Simplified pip calc
                        elif action == "SELL":
                            tp2_target_price_for_convergent = entry_price_ref - (INITIAL_TRADE_2_DEFAULT_TP_PIPS * (PIP_SIZE_XAUUSD if symbol == "XAUUSD" else 0.0001))


                        if ts_type == "Classic":
                            ts_params = {
                                "trail_start_pips": sniper_pro_defaults['classic_ts_start_pips'].get(),
                                "trail_step_pips": sniper_pro_defaults['classic_ts_step_pips'].get(),
                                "trail_distance_pips": sniper_pro_defaults['classic_ts_distance_pips'].get()
                            }
                        elif ts_type == "Convergent":
                            ts_params = {
                                "activation_start_pips": sniper_pro_defaults['convergent_activation_start_pips'].get(),
                                "converge_factor": sniper_pro_defaults['convergent_converge_factor'].get(),
                                "min_stop_distance_pips": sniper_pro_defaults['convergent_min_stop_distance_pips'].get()
                                # tp_target_price is passed as a separate arg to _save_trade_function_definition
                            }

                        self._save_trade_function_definition(
                            signal_db_id=t2_signal_db_id, ticket_id=None,
                            function_type="TS", ts_type=ts_type,
                            activation_condition_type="ON_CLOSE_TICKET", activation_target_ticket=None, # TP1 ticket unknown yet
                            params=ts_params,
                            tp_target_price=tp2_target_price_for_convergent if ts_type == "Convergent" else None
                        )
                else:
                    self._update_log(f"Chyba: T2 signál pro GroupID {signal_group_id} nebyl úspěšně uložen, BE/TS funkce nebyly vytvořeny.", "ERROR")


            elif current_signal_type_from_parser == 'UPDATE_SLTP':
                active_symbol = channel_context.get('last_initial_symbol')
                active_action = channel_context.get('last_initial_action')
                active_group_id = channel_context.get('last_signal_group_id')

                if not (active_symbol and active_action and active_group_id):
                    self._update_log(f"SNIPERPRO UPDATE_SLTP z '{dialog.name}': chybí kontext z INITIAL. Ignoruji.", "WARNING")
                    return

                tp_prices_from_update = parsed_data.get('tp_prices', [])
                if not tp_prices_from_update:
                    self._update_log(f"SNIPERPRO UPDATE_SLTP pro {active_symbol} (Grp: {active_group_id}): žádné TP hodnoty. Ignoruji.", "WARNING")
                    return

                relevant_tp_for_t2 = None
                if active_action == "BUY":
                    relevant_tp_for_t2 = max(tp_prices_from_update) if tp_prices_from_update else None
                elif active_action == "SELL":
                    relevant_tp_for_t2 = min(tp_prices_from_update) if tp_prices_from_update else None

                if relevant_tp_for_t2 is None:
                    self._update_log(f"SNIPERPRO UPDATE_SLTP pro {active_symbol} {active_action} (Grp: {active_group_id}): nelze určit TP2 z {tp_prices_from_update}. Ignoruji.", "WARNING")
                    return

                self._update_log(f"SNIPERPRO UPDATE_SLTP (pro T2): {active_symbol} {active_action} (Grp: {active_group_id}). Nové TP: {relevant_tp_for_t2}. SL (40pips) zůstává.", "INFO")
                self.after(0, self._schedule_db_update_t2_tp,
                           active_group_id, "T2_AUTO", relevant_tp_for_t2)

            elif current_signal_type_from_parser == 'RE_ENTRY':
                symbol_from_re_entry = parsed_data['symbol']
                action_for_re_entry = channel_context.get('last_initial_action')
                last_ctx_symbol = channel_context.get('last_initial_symbol')

                if not action_for_re_entry:
                    self._update_log(f"SNIPERPRO RE_ENTRY pro {symbol_from_re_entry} z '{dialog.name}': chybí kontext akce z INITIAL. Ignoruji.", "WARNING")
                    return

                if symbol_from_re_entry != last_ctx_symbol:
                    self._update_log(f"SNIPERPRO RE_ENTRY symbol '{symbol_from_re_entry}' se liší od kontextu '{last_ctx_symbol}'. Akce '{action_for_re_entry}' z kontextu kanálu použita.", "WARNING")

                re_signal_group_id = f"{dialog_id}_{symbol_from_re_entry}_RE_{int(datetime.datetime.now().timestamp())}"
                self._update_log(f"SNIPERPRO RE_ENTRY: {symbol_from_re_entry} {action_for_re_entry}. SL: {parsed_data['sl_price']}. GrpID: {re_signal_group_id}", "INFO")
                self._save_signal_data(
                    symbol=symbol_from_re_entry, action=action_for_re_entry,
                    signal_group_id=re_signal_group_id, trade_label="RE_AUTO",
                    signal_type=SIGNAL_TYPE_RE_ENTRY,
                    sl_price=parsed_data['sl_price'],
                    tp_pips=REENTRY_TP_PIPS
                )

        try:
            self.main_client.add_event_handler(handler)
            self.monitoring_handlers[dialog_id] = handler
            self._update_log(f"Monitorování pro '{dialog.name}' úspěšně spuštěno.", "INFO")
        except Exception as e:
            self._update_log(f"Chyba při přidávání event handleru pro '{dialog.name}': {e}", "ERROR")
            self.monitoring_states[dialog_id] = False

    async def _stop_monitoring(self, dialog):
        dialog_id = dialog.id
        if dialog_id in self.monitoring_handlers and self.monitoring_handlers[dialog_id] is not None:
            if self.main_client:
                try:
                    self.main_client.remove_event_handler(self.monitoring_handlers[dialog_id])
                    self._update_log(f"Monitorování pro '{dialog.name}' (ID: {dialog_id}) bylo zastaveno.", "INFO")
                except Exception as e:
                    self._update_log(f"Chyba při odebírání event handleru pro '{dialog.name}': {e}", "WARNING")
            del self.monitoring_handlers[dialog_id]
        else:
            self._update_log(f"Nebylo aktivní žádné monitorování pro '{dialog.name}' (ID: {dialog_id}) k zastavení.", "DEBUG")
        self.monitoring_states[dialog_id] = False

    def _schedule_db_update_t2_tp(self, signal_group_id, trade_label_to_find, new_tp_price):
        success = self._update_db_trade_tp_status(
            signal_group_id=signal_group_id,
            trade_label=trade_label_to_find,
            new_tp_price=new_tp_price,
            new_signal_type=SIGNAL_TYPE_UPDATE_T2,
            new_status="new"
        )
        if success:
            self._update_log(f"DB: Obchod '{trade_label_to_find}' (Group: {signal_group_id}) úspěšně aktualizován s TP: {new_tp_price}, status 'new'.", "INFO")
        else:
            self._update_log(f"DB: Nepodařilo se aktualizovat obchod '{trade_label_to_find}' (Group: {signal_group_id}).", "ERROR")

    def _update_db_trade_tp_status(self, signal_group_id: str, trade_label: str, new_tp_price: float, new_signal_type: str, new_status: str) -> bool:
        if signal_group_id is None or trade_label is None or new_tp_price is None:
            logging.error(f"Chybějící parametry pro _update_db_trade_tp_status: sgid={signal_group_id}, label={trade_label}, tp={new_tp_price}")
            return False

        with db_lock, sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            try:
                c.execute("""SELECT id, status FROM signals
                             WHERE signal_group_id = ? AND trade_label = ?""",
                          (signal_group_id, trade_label))
                trade_row = c.fetchone()

                if not trade_row:
                    logging.warning(f"Pokus o aktualizaci TP pro neexistující obchod: Group ID: {signal_group_id}, Label: {trade_label}")
                    return False

                c.execute('''UPDATE signals
                             SET tp_value = ?, tp_value_type = ?,
                                 signal_type = ?, status = ?, timestamp = ?
                             WHERE signal_group_id = ? AND trade_label = ?''',
                          (new_tp_price, "PRICE", new_signal_type, new_status,
                           datetime.datetime.now(), signal_group_id, trade_label))
                conn.commit()
                if c.rowcount > 0:
                    logging.info(f"Obchod (Group: {signal_group_id}, Label: {trade_label}) aktualizován: TP na {new_tp_price}, Typ na {new_signal_type}, Status na {new_status}.")
                    return True
                else:
                    logging.error(f"Nepodařilo se aktualizovat řádek v DB pro Group ID: {signal_group_id}, Label: {trade_label}, ačkoliv byl nalezen.")
                    return False
            except sqlite3.Error as e:
                logging.error(f"Chyba DB při aktualizaci TP obchodu (Group: {signal_group_id}, Label: {trade_label}): {e}")
                return False

    def _save_signal_data(self, symbol: str, action: str, signal_type: str,
                          signal_group_id: str | None = None,
                          trade_label: str | None = None,
                          entry_price: float = 0,
                          sl_price: float | None = None, tp_price: float | None = None,
                          sl_pips: float | None = None, tp_pips: float | None = None,
                          tp2_price_optional: float | None = None,
                          # BE/TS parameters
                          be_active: bool = False,
                          ts_active: bool = False,
                          be_trigger_condition_type: str | None = None,
                          be_trigger_target_ticket: int | None = None,
                          ts_trigger_condition_type: str | None = None,
                          ts_trigger_target_ticket: int | None = None,
                          ts_start_pips: float | None = None,
                          ts_step_pips: float | None = None,
                          ts_distance_pips: float | None = None,
                          is_tp1_for_be_ts: bool = False
                          ):

        sl_val, sl_val_type, tp_val, tp_val_type, tp2_val = None, None, None, None, None

        if sl_pips is not None: sl_val, sl_val_type = sl_pips, "PIPS"
        elif sl_price is not None: sl_val, sl_val_type = sl_price, "PRICE"

        if tp_pips is not None: tp_val, tp_val_type = tp_pips, "PIPS"
        elif tp_price is not None: tp_val, tp_val_type = tp_price, "PRICE"

        if tp2_price_optional is not None: tp2_val = tp2_price_optional

        # Convert booleans to string "TRUE"/"FALSE" for DB
        be_active_str = "TRUE" if be_active else "FALSE"
        ts_active_str = "TRUE" if ts_active else "FALSE"
        is_tp1_for_be_ts_str = "TRUE" if is_tp1_for_be_ts else "FALSE"

        with db_lock, sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            try:
                sql = '''INSERT INTO signals
                             (symbol, action, entry_price, timestamp, status,
                              signal_group_id, trade_label, signal_type,
                              sl_value, tp_value, sl_value_type, tp_value_type,
                              tp2_value, tp2_value_type,
                              be_active, ts_active, be_trigger_condition_type, be_trigger_target_ticket,
                              ts_trigger_condition_type, ts_trigger_target_ticket,
                              ts_start_pips, ts_step_pips, ts_distance_pips, is_tp1_for_be_ts)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
                params = (symbol, action, entry_price, datetime.datetime.now(), 'new',
                           signal_group_id, trade_label, signal_type,
                           sl_val, tp_val, sl_val_type, tp_val_type,
                           tp2_val, "PRICE" if tp2_val is not None else None,
                           be_active_str, ts_active_str, be_trigger_condition_type, be_trigger_target_ticket,
                           ts_trigger_condition_type, ts_trigger_target_ticket,
                           ts_start_pips, ts_step_pips, ts_distance_pips, is_tp1_for_be_ts_str)
                c.execute(sql, params)
                conn.commit()
                last_id = c.lastrowid
                self._update_log(f"Signál uložen do DB (ID: {last_id}): {signal_type} - {symbol} {action} (Label: {trade_label}, Group: {signal_group_id}, BE:{be_active_str}, TS:{ts_active_str})", "INFO")
                return last_id # Return the ID of the newly inserted signal
            except sqlite3.Error as e:
                log_data = {
                    "symbol": symbol, "action": action, "entry_price": entry_price,
                    "signal_group_id": signal_group_id, "trade_label": trade_label,
                    "signal_type": signal_type, "sl_val": sl_val, "tp_val": tp_val,
                    "sl_val_type": sl_val_type, "tp_val_type": tp_val_type, "tp2_val": tp2_val,
                    "tp2_value_type_for_db": "PRICE" if tp2_val is not None else None,
                    "be_active": be_active_str, "ts_active": ts_active_str,
                    "be_trigger_condition_type": be_trigger_condition_type, "be_trigger_target_ticket": be_trigger_target_ticket,
                    "ts_trigger_condition_type": ts_trigger_condition_type, "ts_trigger_target_ticket": ts_trigger_target_ticket,
                    "ts_start_pips": ts_start_pips, "ts_step_pips": ts_step_pips, "ts_distance_pips": ts_distance_pips,
                    "is_tp1_for_be_ts": is_tp1_for_be_ts_str
                }
                logging.error(f"Chyba při ukládání signálu do DB: {e}. Data: {log_data}")
                self._update_log(f"Chyba DB při ukládání signálu {symbol} {action}", "ERROR")

    def _show_phone_selector(self):
        selector_window = ctk.CTkToplevel(self)
        selector_window.title("Správa telefonních čísel")
        selector_window.geometry("450x420")
        selector_window.transient(self)
        selector_window.grab_set()
        selector_window.resizable(False, False)
        selector_window.configure(fg_color=self.BG_COLOR)

        phone_numbers = self.session_manager.get_saved_phone_numbers()
        selected_phone_var = tk.StringVar(value=self.phone_entry_var.get())

        saved_frame = ctk.CTkFrame(selector_window, fg_color=self.FRAME_COLOR, corner_radius=8)
        saved_frame.pack(fill='x', padx=15, pady=(15,10))

        ctk.CTkLabel(saved_frame, text="Uložená čísla", font=self.FONT_BOLD).pack(anchor="w", padx=10, pady=(8,4))

        listbox_frame = ctk.CTkFrame(saved_frame, fg_color=self.ENTRY_BG_COLOR, corner_radius=6)
        listbox_frame.pack(fill="x", expand=True, padx=10, pady=(0,10))

        phone_listbox = tk.Listbox(listbox_frame, height=5, bg=self.ENTRY_BG_COLOR, fg=self.TEXT_COLOR,
                                   borderwidth=0, highlightthickness=0, selectbackground=self.ACCENT_COLOR,
                                   font=self.FONT_NORMAL, relief='flat', exportselection=False,
                                   selectforeground=self.TEXT_COLOR)
        for phone in phone_numbers: phone_listbox.insert(tk.END, phone)
        phone_listbox.pack(fill="x", expand=True, padx=5, pady=5)

        current_phone_in_list = self.phone_entry_var.get()
        if current_phone_in_list in phone_numbers:
            try:
                idx = phone_numbers.index(current_phone_in_list)
                phone_listbox.selection_set(idx)
                phone_listbox.activate(idx)
                phone_listbox.see(idx)
                selected_phone_var.set(current_phone_in_list)
            except ValueError: pass

        def on_listbox_select(event):
            widget = event.widget
            if widget.curselection(): selected_phone_var.set(widget.get(widget.curselection()[0]))
            else: selected_phone_var.set("")
        phone_listbox.bind('<<ListboxSelect>>', on_listbox_select)

        new_frame = ctk.CTkFrame(selector_window, fg_color=self.FRAME_COLOR, corner_radius=8)
        new_frame.pack(fill='x', padx=15, pady=10)
        new_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(new_frame, text="Nové číslo (formát +420...):", font=self.FONT_NORMAL).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8,2))
        new_phone_entry = ctk.CTkEntry(new_frame, font=self.FONT_NORMAL, fg_color=self.ENTRY_BG_COLOR, border_width=0, corner_radius=6)
        new_phone_entry.grid(row=1, column=0, sticky="ew", padx=(10,5), pady=(0,10))
        new_phone_entry.focus()

        add_button = ctk.CTkButton(new_frame, text="Přidat/Vybrat", command=lambda: add_or_select_phone(),
                                   fg_color=self.ACCENT_COLOR, hover_color=self.ACCENT_HOVER_COLOR,
                                   font=self.FONT_NORMAL, corner_radius=6, width=110)
        add_button.grid(row=1, column=1, sticky="e", padx=(0,10), pady=(0,10))

        def add_or_select_phone():
            new_phone = new_phone_entry.get().strip()
            if not re.match(r'^\+\d{10,}$', new_phone):
                messagebox.showwarning("Neplatný formát", "Zadejte platné telefonní číslo v mezinárodním formátu (např. +420123456789).", parent=selector_window)
                return

            if new_phone not in phone_numbers:
                phone_listbox.insert(tk.END, new_phone)
                phone_numbers.append(new_phone)

            try:
                idx = phone_numbers.index(new_phone)
                phone_listbox.selection_clear(0, tk.END)
                phone_listbox.selection_set(idx)
                phone_listbox.activate(idx)
                phone_listbox.see(idx)
                selected_phone_var.set(new_phone)
            except ValueError: pass
            new_phone_entry.delete(0, tk.END)

        button_frame = ctk.CTkFrame(selector_window, fg_color="transparent")
        button_frame.pack(fill='x', padx=15, pady=(15,15))
        button_frame.grid_columnconfigure((0,1), weight=1)

        def confirm_and_connect():
            phone_to_connect = selected_phone_var.get()
            if not phone_to_connect:
                messagebox.showinfo("Informace", "Nevybrali jste žádné telefonní číslo.", parent=selector_window)
                return

            current_active_phone = self.phone_entry_var.get()
            if current_active_phone != phone_to_connect or not self.client_running:
                self.phone_entry_var.set(phone_to_connect)
                self._update_log(f"Vybráno telefonní číslo: {phone_to_connect}. Pokouším se připojit.")
                self._connect_telegram()
            else:
                self._update_log(f"Telefonní číslo {phone_to_connect} je již aktivní.")
            selector_window.destroy()

        def delete_selected_phone():
            selection_indices = phone_listbox.curselection()
            if not selection_indices:
                messagebox.showinfo("Informace", "Nevybrali jste žádné číslo ke smazání.", parent=selector_window)
                return

            phone_to_delete_idx = selection_indices[0]
            phone_to_delete = phone_numbers[phone_to_delete_idx]

            if messagebox.askyesno("Potvrdit smazání", f"Opravdu chcete odstranit session a záznam pro {phone_to_delete}?", parent=selector_window):
                if self.session_manager.remove_phone_number(phone_to_delete):
                    self._update_log(f"Session pro {phone_to_delete} byla odstraněna.", "INFO")
                    original_index = -1
                    try:
                        original_index = phone_numbers.index(phone_to_delete)
                        del phone_numbers[original_index]
                        phone_listbox.delete(original_index)
                    except ValueError:
                        self._update_log(f"Chyba: {phone_to_delete} nebylo nalezeno v interním seznamu pro smazání z GUI.", "ERROR")

                    if selected_phone_var.get() == phone_to_delete:
                        selected_phone_var.set("")

                    if self.phone_entry_var.get() == phone_to_delete:
                        self.phone_entry_var.set("")
                        self._update_log(f"Aktivní session pro {phone_to_delete} byla smazána.", "INFO")
                        if self.client_running:
                            self._update_log("Odpojuji aktivního klienta.", "INFO")
                            self._shutdown_client()
                        for widget in self.channels_list_frame.winfo_children(): widget.destroy()
                        self.refresh_button.configure(state=tk.DISABLED)
                else:
                    messagebox.showerror("Chyba", f"Nepodařilo se odstranit session soubor pro {phone_to_delete}.", parent=selector_window)

        delete_btn = ctk.CTkButton(button_frame, text="Odstranit vybrané",
                                     font=self.FONT_NORMAL, fg_color=self.RED_COLOR,
                                     hover_color=self.RED_HOVER_COLOR, command=delete_selected_phone, corner_radius=6)
        delete_btn.grid(row=0, column=0, sticky='ew', padx=(0,5), pady=5)

        confirm_btn = ctk.CTkButton(button_frame, text="Potvrdit a Připojit",
                                    font=self.FONT_BOLD, fg_color=self.ACCENT_COLOR,
                                    hover_color=self.ACCENT_HOVER_COLOR, command=confirm_and_connect, corner_radius=6)
        confirm_btn.grid(row=0, column=1, sticky='ew', padx=(5,0), pady=5)

        selector_window.bind("<Return>", lambda event: confirm_and_connect())
        new_phone_entry.bind("<Return>", lambda event: add_or_select_phone())
        selector_window.attributes("-topmost", True)

    def _shutdown_client(self):
        self._update_log("Zahajuji ukončení Telegram klienta...", "INFO")
        self.after(0, lambda: self.refresh_button.configure(state=tk.DISABLED))
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
                    disconnect_future.result(timeout=5)
                    self._update_log("Klient úspěšně odpojen.", "INFO")
                except concurrent.futures.TimeoutError:
                    self._update_log("Timeout při odpojování klienta.", "WARNING")
                except Exception as e:
                    self._update_log(f"Chyba při odpojování klienta: {e}", "ERROR")

        self.main_client = None
        self.client_running = False

        if self.client_loop and not self.client_loop.is_closed():
             self.client_loop.call_soon_threadsafe(self.client_loop.stop)

        if self.client_thread and self.client_thread.is_alive():
             self._update_log("Čekám na ukončení klientského vlákna...", "DEBUG")
             self.client_thread.join(timeout=5)
             if self.client_thread.is_alive():
                 self._update_log("Klientské vlákno se nepodařilo korektně ukončit v časovém limitu.", "WARNING")

        self.client_thread = None
        if self.client_loop and self.client_loop.is_closed():
            self._update_log(f"Asyncio smyčka ({id(self.client_loop)}) byla uzavřena (kontrola v shutdown).", "DEBUG")

        self.client_loop = None
        self._update_log("Telegram klient a jeho vlákno byly ukončeny.", "INFO")
        self.after(0, lambda: [widget.destroy() for widget in self.channels_list_frame.winfo_children()])

    def _save_trade_function_definition(self, signal_db_id: int, ticket_id: int | None,
                                        function_type: str, ts_type: str | None,
                                        activation_condition_type: str, activation_target_ticket: int | None,
                                        params: dict, tp_target_price: float | None = None):
        """Saves a function definition to the trade_functions table."""
        params_json_str = json.dumps(params) if params else None
        is_active_str = "FALSE" # Functions are not active by default when first defined

        with db_lock, sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            try:
                c.execute("""
                    INSERT INTO trade_functions
                        (signal_db_id, ticket_id, function_type, ts_type,
                         activation_condition_type, activation_target_ticket,
                         is_active, params_json, tp_target_price, status_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (signal_db_id, ticket_id, function_type, ts_type,
                      activation_condition_type, activation_target_ticket,
                      is_active_str, params_json_str, tp_target_price,
                      f"Pending: {activation_condition_type} on ticket {activation_target_ticket if activation_target_ticket else 'N/A'}"))
                conn.commit()
                self._update_log(f"DB: Saved {function_type}{f' ({ts_type})' if ts_type else ''} function for signal_db_id {signal_db_id}. Condition: {activation_condition_type}", "INFO")
                return c.lastrowid
            except sqlite3.Error as e:
                self._update_log(f"DB Error: Failed to save function {function_type} for signal_db_id {signal_db_id}: {e}", "ERROR")
                return None


    def _show_functions_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Výchozí Nastavení Funkcí Obchodů")
        dialog.geometry("650x550") # Adjusted size
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(True, True) # Allow resizing
        dialog.configure(fg_color=self.BG_COLOR)
        content_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        content_frame.pack(padx=15, pady=15, fill="both", expand=True)
        content_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(content_frame, text="Typ parsování:", font=self.FONT_BOLD).grid(row=0, column=0, padx=(0,5), pady=10, sticky="w")
        self.current_parser_type_var_dialog = tk.StringVar(value=list(self.function_defaults.keys())[0] if self.function_defaults else "")

        parser_type_options = list(self.function_defaults.keys())
        if not parser_type_options: # Handle case where it might be empty initially
            parser_type_options = ["SniperPro", "Standardní"] # Fallback if somehow empty

        parser_type_dropdown = ctk.CTkComboBox(content_frame, variable=self.current_parser_type_var_dialog,
                                                 values=parser_type_options, state="readonly",
                                                 font=self.FONT_NORMAL, command=self._on_parser_type_selected_in_dialog)
        parser_type_dropdown.grid(row=0, column=1, columnspan=3, padx=0, pady=10, sticky="ew")

        # --- Frame for specific settings of the selected parser type ---
        self.settings_area_frame = ctk.CTkFrame(content_frame, fg_color=self.FRAME_COLOR, corner_radius=6)
        self.settings_area_frame.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(5,10))
        self.settings_area_frame.grid_columnconfigure(1, weight=1) # Allow entries to expand
        content_frame.grid_rowconfigure(1, weight=1) # Allow settings_area_frame to expand vertically
        self._on_parser_type_selected_in_dialog(self.current_parser_type_var_dialog.get())

        dialog.attributes("-topmost", True)


    def _on_parser_type_selected_in_dialog(self, selected_parser_type: str):
        for widget in self.settings_area_frame.winfo_children():
            widget.destroy()

        if not selected_parser_type or selected_parser_type not in self.function_defaults:
            ctk.CTkLabel(self.settings_area_frame, text="Vyberte platný typ parsování.", font=self.FONT_NORMAL).pack(padx=10, pady=10)
            return

        defaults = self.function_defaults[selected_parser_type]

        # BE Settings
        ctk.CTkCheckBox(self.settings_area_frame, text="Aktivovat Breakeven (pro TP2)",
                        variable=defaults['be_active'], font=self.FONT_NORMAL,
                        fg_color=self.ACCENT_COLOR, hover_color=self.ACCENT_HOVER_COLOR
                       ).grid(row=0, column=0, columnspan=2, padx=10, pady=7, sticky="w")

        # TS Activation and Type
        ts_activation_check = ctk.CTkCheckBox(self.settings_area_frame, text="Aktivovat Trailing Stop (pro TP2)",
                                             variable=defaults['ts_active'], font=self.FONT_NORMAL,
                                             fg_color=self.ACCENT_COLOR, hover_color=self.ACCENT_HOVER_COLOR,
                                             command=lambda: self._on_ts_type_changed_in_dialog(selected_parser_type)) # Re-render TS params on change
        ts_activation_check.grid(row=1, column=0, padx=10, pady=7, sticky="w")

        self.ts_type_dropdown_dialog = ctk.CTkComboBox(self.settings_area_frame, variable=defaults['ts_type'],
                                                       values=["Classic", "Convergent"], state="readonly",
                                                       font=self.FONT_NORMAL, width=150,
                                                       command=lambda choice: self._on_ts_type_changed_in_dialog(selected_parser_type))
        self.ts_type_dropdown_dialog.grid(row=1, column=1, padx=10, pady=7, sticky="w")
        self.ts_type_dropdown_dialog.configure(state=tk.NORMAL if defaults['ts_active'].get() else tk.DISABLED)

        # Frame for TS parameters (will be populated by _on_ts_type_changed_in_dialog)
        self.ts_params_frame_dialog = ctk.CTkFrame(self.settings_area_frame, fg_color="transparent")
        self.ts_params_frame_dialog.grid(row=2, column=0, columnspan=4, sticky="nsew", padx=5, pady=5)
        self.ts_params_frame_dialog.grid_columnconfigure(1, weight=1)
        self.ts_params_frame_dialog.grid_columnconfigure(3, weight=1)


        self._on_ts_type_changed_in_dialog(selected_parser_type) # Initial population of TS params

    def _on_ts_type_changed_in_dialog(self, selected_parser_type: str):
        for widget in self.ts_params_frame_dialog.winfo_children():
            widget.destroy()

        defaults = self.function_defaults[selected_parser_type]
        if hasattr(self, 'ts_type_dropdown_dialog'): # Check if it exists
             self.ts_type_dropdown_dialog.configure(state=tk.NORMAL if defaults['ts_active'].get() else tk.DISABLED)

        if not defaults['ts_active'].get():
            return # No TS params to show if TS is not active

        selected_ts_type = defaults['ts_type'].get()

        if selected_ts_type == "Classic":
            ctk.CTkLabel(self.ts_params_frame_dialog, text="Classic TS - Start (pips):", font=self.FONT_NORMAL).grid(row=0, column=0, padx=5, pady=5, sticky="w")
            ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=defaults['classic_ts_start_pips'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=0, column=1, padx=5, pady=5, sticky="w")

            ctk.CTkLabel(self.ts_params_frame_dialog, text="Krok (pips):", font=self.FONT_NORMAL).grid(row=0, column=2, padx=5, pady=5, sticky="w")
            ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=defaults['classic_ts_step_pips'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=0, column=3, padx=5, pady=5, sticky="w")

            ctk.CTkLabel(self.ts_params_frame_dialog, text="Distance (pips):", font=self.FONT_NORMAL).grid(row=1, column=0, padx=5, pady=5, sticky="w")
            ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=defaults['classic_ts_distance_pips'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=1, column=1, padx=5, pady=5, sticky="w")

        elif selected_ts_type == "Convergent":
            ctk.CTkLabel(self.ts_params_frame_dialog, text="Convergent TS - Aktivace (pips):", font=self.FONT_NORMAL).grid(row=0, column=0, padx=5, pady=5, sticky="w")
            ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=defaults['convergent_activation_start_pips'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=0, column=1, padx=5, pady=5, sticky="w")

            ctk.CTkLabel(self.ts_params_frame_dialog, text="Converge Faktor (0-1):", font=self.FONT_NORMAL).grid(row=0, column=2, padx=5, pady=5, sticky="w")
            ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=defaults['convergent_converge_factor'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=0, column=3, padx=5, pady=5, sticky="w")

            ctk.CTkLabel(self.ts_params_frame_dialog, text="Min. odstup SL (pips):", font=self.FONT_NORMAL).grid(row=1, column=0, padx=5, pady=5, sticky="w")
            ctk.CTkEntry(self.ts_params_frame_dialog, textvariable=defaults['convergent_min_stop_distance_pips'], font=self.FONT_NORMAL, width=80, fg_color=self.ENTRY_BG_COLOR).grid(row=1, column=1, padx=5, pady=5, sticky="w")

    def _on_closing(self):
        self._update_log("Aplikace se ukončuje...")
        if self.client_running or (self.client_thread and self.client_thread.is_alive()):
            self._shutdown_client()
        self.destroy()

flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    return "Signal Server is running."

@flask_app.route('/signals')
def get_new_signals():
    """
    Endpoint pro MQL4 EA k získání nových obchodních signálů.
    Metoda: GET
    Odpověď: JSON pole objektů, kde každý objekt reprezentuje signál se statusem 'new'.
             Každý objekt obsahuje klíče odpovídající sloupcům v tabulce 'signals', např.:
             id, symbol, action, entry_price, signal_group_id, trade_label, signal_type,
             sl_value, tp_value, sl_value_type, tp_value_type, tp2_value, tp2_value_type, ticket.
    Příklad odpovědi:
    [
        {
            "id": 1, "symbol": "XAUUSD", "action": "BUY", "entry_price": 1800.50,
            "signal_group_id": "grp1", "trade_label": "T1_AUTO", "signal_type": "INITIAL_T1",
            "sl_value": 40.0, "sl_value_type": "PIPS",
            "tp_value": 40.0, "tp_value_type": "PIPS",
            "tp2_value": null, "tp2_value_type": null, "ticket": null, ...
        },
        {...}
    ]
    """
    logging.info(f"Příchozí požadavek na /signals od {request.remote_addr}")
    signals_to_send = []
    try:
        with db_lock, sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row; c = conn.cursor()
            c.execute("SELECT * FROM signals WHERE status = 'new' ORDER BY timestamp ASC")
            signals_to_send = [dict(row) for row in c.fetchall()]
        return jsonify(signals_to_send)
    except sqlite3.Error as e:
        logging.error(f"Chyba databáze při /signals: {e}")
        return jsonify({"status": "error", "message": "Database error"}), 500
    except Exception as e:
        logging.error(f"Obecná chyba při /signals: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

@flask_app.route('/report_trade', methods=['POST'])
def report_trade():
    """
    Endpoint pro MQL4 EA k nahlášení výsledku zpracování signálu (otevření/modifikace obchodu).
    Metoda: POST
    Očekávané JSON tělo:
    {
        "id": <int>,          // DB ID signálu, který byl zpracován
        "ticket": <int>       // Číslo ticketu otevřeného/modifikovaného obchodu v MT4
    }
    Odpověď:
    - {"status": "ok", "message": "Trade/Update reported successfully"} při úspěchu (HTTP 200)
    - {"status": "error", "message": "..."} při chybě (HTTP 400, 404, 500)

    Logika:
    - Najde signál v DB podle poskytnutého 'id'.
    - Pokud signál neexistuje nebo již není ve stavu 'new', vrátí chybu/informaci.
    - Na základě 'signal_type' v DB:
        - Pro INITIAL_T1, INITIAL_T2_DEFAULT, RE_ENTRY, STANDARD:
            - Uloží 'ticket' k signálu.
            - Změní 'status' signálu na 'open'.
        - Pro UPDATE_T2:
            - Změní 'status' signálu na 'processed_update'.
            - Volitelně uloží 'ticket', pokud byl v DB prázdný (měl by tam být z INITIAL_T2_DEFAULT).
    """
    logging.info(f"Příchozí požadavek na /report_trade od {request.remote_addr}")
    try:
        data = request.get_json(silent=True)
        if data is None:
            logging.error(f"Chybný Content-Type nebo nevalidní JSON v /report_trade. Raw data: {request.data}")
            return jsonify({"status": "error", "message": "Invalid JSON or Content-Type"}), 400

        logging.info(f"Přijatá data pro report: {data}")
        db_signal_id = data.get('id')
        ticket = data.get('ticket')

        if db_signal_id is None:
            logging.error("Chybí 'id' (databázové ID signálu) v přijatých datech pro /report_trade.")
            return jsonify({"status": "error", "message": "Missing 'id' (signal database ID)"}), 400

        try:
            db_signal_id = int(db_signal_id)
            if ticket is not None:
                ticket = int(ticket)
        except ValueError:
            logging.error(f"Neplatný typ pro 'id' nebo 'ticket': id={data.get('id')}, ticket={data.get('ticket')}")
            return jsonify({"status": "error", "message": "Invalid type for 'id' or 'ticket'"}), 400

        with db_lock, sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT id, signal_type, status, ticket FROM signals WHERE id = ?", (db_signal_id,)) # Přidáno ticket pro kontrolu
            signal_row = c.fetchone()

            if not signal_row:
                logging.warning(f"/report_trade: Signál ID {db_signal_id} nenalezen.")
                return jsonify({"status": "error", "message": f"Signal with ID {db_signal_id} not found"}), 404

            current_signal_status = signal_row['status']
            signal_type = signal_row['signal_type']
            signal_group_id = signal_row['signal_group_id']
            signal_trade_label = signal_row['trade_label']
            is_tp1_for_be_ts_val = signal_row['is_tp1_for_be_ts'] == 'TRUE' # From signals table

            log_msg = f"/report_trade: ID:{db_signal_id}, Ticket:{ticket}, Type:{signal_type}, Label:{signal_trade_label}, Group:{signal_group_id}."
            closed_in_profit = data.get('closed_in_profit') # Expects True/False or None

            if closed_in_profit is not None: # This means the EA is reporting a trade closure
                if ticket is None:
                    logging.error(f"{log_msg} Chyba: 'ticket' chybí při hlášení uzavření obchodu.")
                    return jsonify({"status": "error", "message": "Missing 'ticket' when reporting trade closure"}), 400

                c.execute("UPDATE signals SET status = 'closed', ticket = ? WHERE id = ?", (ticket, db_signal_id))
                log_msg += f" Obchod označen jako 'closed' v DB. Ziskový: {closed_in_profit}."
                if closed_in_profit: # Only activate BE/TS if TP1 (trigger) was profitable
                    c.execute("""UPDATE trade_functions
                                 SET is_active = 'TRUE', status_message = 'Activated: Trigger ticket closed profitably.'
                                 WHERE activation_target_ticket = ? AND activation_condition_type = 'ON_CLOSE_TICKET' AND is_active = 'FALSE'""",
                              (ticket,))
                    if c.rowcount > 0:
                        log_msg += f" {c.rowcount} funkcí (BE/TS) aktivováno pro cílový ticket {ticket}."
                else:
                    log_msg += f" Trigger ticket {ticket} uzavřen bez zisku, BE/TS funkce nebyly aktivovány."

                conn.commit()
                logging.info(log_msg)
                return jsonify({"status": "ok", "message": "Trade closure reported and functions updated."})
            if current_signal_status != 'new':
                if signal_type == SIGNAL_TYPE_INITIAL_T1 and ticket is not None and signal_row['ticket'] is None:
                    c.execute("UPDATE signals SET ticket = ? WHERE id = ?", (ticket, db_signal_id))
                    # Update trade_functions for the TP2 of this group
                    if signal_group_id:
                        c.execute("""UPDATE trade_functions
                                     SET activation_target_ticket = ?
                                     WHERE signal_db_id IN (SELECT id FROM signals WHERE signal_group_id = ? AND trade_label = 'T2_AUTO')
                                       AND activation_condition_type = 'ON_CLOSE_TICKET'""",
                                  (ticket, signal_group_id))
                        log_msg += f" Ticket T1 (DB ID {db_signal_id}) aktualizován na {ticket}. {c.rowcount} trade_functions aktualizováno s activation_target_ticket."
                    conn.commit()
                    logging.info(log_msg)
                    return jsonify({"status": "ok", "message": f"Ticket for T1 signal ID {db_signal_id} updated and linked."}), 200

                logging.warning(f"{log_msg} Signál již zpracován (status: {current_signal_status}) a nejedná se o update T1 ticketu. Požadavek ignorován.")
                return jsonify({"status": "ok", "message": f"Signal ID {db_signal_id} already processed (status: {current_signal_status})."}), 200
            if signal_type in [SIGNAL_TYPE_INITIAL_T1, SIGNAL_TYPE_INITIAL_T2_DEFAULT, SIGNAL_TYPE_RE_ENTRY, SIGNAL_TYPE_STANDARD]:
                if ticket is None:
                    logging.error(f"{log_msg} Chyba: 'ticket' chybí pro otevírací signál typu {signal_type}.")
                    return jsonify({"status": "error", "message": f"Missing 'ticket' for opening signal type {signal_type}"}), 400

                c.execute("UPDATE signals SET ticket = ?, status = 'open' WHERE id = ?", (ticket, db_signal_id))
                log_msg += f" Signál označen jako 'open' s ticketem {ticket}."

                if signal_type == SIGNAL_TYPE_INITIAL_T1 and signal_group_id:
                    c.execute("""UPDATE trade_functions
                                 SET activation_target_ticket = ?
                                 WHERE signal_db_id IN (SELECT id FROM signals WHERE signal_group_id = ? AND trade_label = 'T2_AUTO')
                                   AND activation_condition_type = 'ON_CLOSE_TICKET'""",
                              (ticket, signal_group_id))
                    if c.rowcount > 0:
                        log_msg += f" {c.rowcount} trade_functions (pro T2 ze skupiny {signal_group_id}) aktualizováno s T1 ticketem {ticket} jako trigger."

                elif signal_type == SIGNAL_TYPE_INITIAL_T2_DEFAULT:
                    c.execute("UPDATE trade_functions SET ticket_id = ? WHERE signal_db_id = ?", (ticket, db_signal_id))
                    if c.rowcount > 0:
                        log_msg += f" {c.rowcount} trade_functions (pro tento T2 signál) aktualizováno s vlastním ticketem {ticket}."

            elif signal_type == SIGNAL_TYPE_UPDATE_T2: # This is for TP updates, not primary BE/TS logic
                db_ticket = signal_row['ticket']
                if ticket is not None:
                    if db_ticket is not None and db_ticket != ticket:
                        logging.warning(f"Pro UPDATE_T2 (ID: {db_signal_id}), MT4 reportoval ticket {ticket}, ale v DB je {db_ticket}. Používám ticket z DB.")
                    elif db_ticket is None: # Pokud z nějakého důvodu ticket u T2 chyběl
                        c.execute("UPDATE signals SET ticket = ? WHERE id = ?", (ticket, db_signal_id))
                        log_msg += f" Ticket {ticket} doplněn pro UPDATE_T2."

                c.execute("UPDATE signals SET status = 'processed_update' WHERE id = ?", (db_signal_id,))
                log_msg = f"Signál ID: {db_signal_id} (Typ: {signal_type}) modifikace TP potvrzena. Status: 'processed_update'." + log_msg

            else:
                logging.warning(f"Neznámý signal_type '{signal_type}' pro signál ID: {db_signal_id} v /report_trade.")
                return jsonify({"status": "error", "message": f"Unknown signal_type '{signal_type}' for signal ID {db_signal_id}"}), 400

            conn.commit()
            if c.rowcount > 0:
                logging.info(log_msg)
                return jsonify({"status": "ok", "message": "Trade/Update reported successfully"})
            else:
                logging.error(f"Nepodařilo se aktualizovat záznam v DB pro signál ID: {db_signal_id} v /report_trade. Počet ovlivněných řádků: {c.rowcount}.")
                return jsonify({"status": "error", "message": "Failed to update signal in DB, no rows affected."}), 500

    except json.JSONDecodeError:
        logging.error(f"Chyba při dekódování JSON v /report_trade. Raw data: {request.data}")
        return jsonify({"status": "error", "message": "Invalid JSON format"}), 400
    except sqlite3.Error as e:
        logging.error(f"Chyba databáze při /report_trade: {e}")
        return jsonify({"status": "error", "message": "Database error during trade report"}), 500
    except Exception as e:
        logging.error(f"Obecná chyba při zpracování /report_trade: {e}\nRaw data: {request.data}")
        return jsonify({"status": "error", "message": f"Internal server error: {str(e)}"}), 500

def run_flask():
    try:
        from waitress import serve
        serve(flask_app, host='0.0.0.0', port=5000)
    except ImportError:
        logging.warning("Waitress není nainstalován, používám vývojový server Flask. Pro produkci zvažte `pip install waitress`.")
        flask_app.run(host='0.0.0.0', port=5000, use_reloader=False, threaded=True)
    except Exception as e:
        logging.error(f"Nepodařilo se spustit Flask server: {e}")

@flask_app.route('/active_trade_functions', methods=['GET'])
def get_active_trade_functions():
    ticket_id_str = request.args.get('ticket_id')
    logging.info(f"Příchozí požadavek na /active_trade_functions pro ticket_id: {ticket_id_str}")

    if not ticket_id_str:
        return jsonify({"status": "error", "message": "Missing 'ticket_id' query parameter"}), 400
    try:
        ticket_id = int(ticket_id_str)
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid 'ticket_id' format, must be an integer"}), 400

    functions_to_send = []
    try:
        with db_lock, sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT function_type, ts_type, params_json, tp_target_price
                FROM trade_functions
                WHERE ticket_id = ? AND is_active = 'TRUE'
            """, (ticket_id,))

            for row in c.fetchall():
                func_data = dict(row)
                if func_data['params_json']:
                    try:
                        func_data['params'] = json.loads(func_data['params_json'])
                        del func_data['params_json'] # Remove original string if parsed
                    except json.JSONDecodeError:
                        logging.warning(f"Could not parse params_json for ticket {ticket_id}, function {func_data['function_type']}: {func_data['params_json']}")
                else: # Ensure 'params' key exists even if null
                    func_data['params'] = None
                    if 'params_json' in func_data: del func_data['params_json']


                functions_to_send.append(func_data)

        logging.info(f"Nalezeno {len(functions_to_send)} aktivních funkcí pro ticket {ticket_id}.")
        return jsonify(functions_to_send)

    except sqlite3.Error as e:
        logging.error(f"Chyba databáze při /active_trade_functions pro ticket {ticket_id}: {e}")
        return jsonify({"status": "error", "message": "Database error"}), 500
    except Exception as e:
        logging.error(f"Obecná chyba při /active_trade_functions pro ticket {ticket_id}: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


if __name__ == "__main__":
    init_db()
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    app = TelegramBotApp()
    app.mainloop()
