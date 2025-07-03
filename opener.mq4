//+------------------------------------------------------------------+
//|                                  opener.mq4                      |
//|                        Copyright 2023, SignalCopierPro           |
//|                                             https://www.yourwebsite.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2023, SignalCopierPro"
#property link      "https://www.yourwebsite.com"
#property version   "2.10" // Implemented Lotsize & Trade Execution
#property strict

#include <stdlib.mqh>

// --- Input parameters ---
input string InpServerURL = "localhost";        // Server URL (bez http://)
input int    InpServerPort = 5000;             // Server Port
input string InpServerPathSignals = "/signals"; // Endpoint pro získání signálů
input string InpServerPathReport = "/report_trade"; // Endpoint pro nahlášení obchodu
input int    InpCheckInterval = 10;            // Interval kontroly nových signálů (sekundy)
input int    InpMagicNumberBase = 123456;      // Základní Magic Number
input bool   InpEnableTrading = true;          // Povolit/Zakázat obchodování
input bool   InpDebugMode = true;              // Povolit/Zakázat detailní logování
input int    InpTimeoutMilliseconds = 5000;    // Timeout pro HTTP requesty (ms)
input string InpSymbolPrefix = "";             // Prefix symbolu (pokud broker používá)
input string InpSymbolSuffix = "";             // Suffix symbolu (pokud broker používá)
input int    InpSlippagePoints = 3;            // Maximální skluz v bodech

// --- Lotsize Management Inputs ---
enum ENUM_LOTSIZE_MODE
{
    LS_Fixed,        // Fixní lotsize
    LS_PercentEquity // Procentuální z ekvity
};
input ENUM_LOTSIZE_MODE InpLotSizeMode = LS_PercentEquity; // Výchozí mód lotsize
input double InpFixedLotSize = 0.01;       // Fixní lotsize (použije se, pokud LotSizeMode = LS_Fixed)
input double InpRiskPercent = 1.0;         // Procento ekvity k riskování (použije se, pokud LotSizeMode = LS_PercentEquity)

// --- Function Prototypes ---
string FetchAPIData(string path);
void UpdateConnectionStatus(bool connected, string message = "");
void CheckForNewSignals();
void ProcessReceivedSignals(string json_array_str);
bool ParseSingleSignalJson(string signal_json_str,
                           int &db_id, string &symbol, string &action, double &entry_price,
                           string &signal_group_id, string &trade_label, string &signal_type,
                           double &sl_value, double &tp_value, string &sl_value_type, string &tp_value_type,
                           double &tp2_value, string &tp2_value_type, int &ticket_from_db);
void ExecuteSignalAction(int db_id, string symbol, string action, double entry_price_ref,
                        string signal_group_id, string trade_label, string signal_type,
                        double sl_value, double tp_value, string sl_value_type, string tp_value_type,
                        double tp2_value, string tp2_value_type, int ticket_from_db);
string GetJsonValue(string json, string key);
string ErrorDescription(int error);
string StringUpper(string str);
string GetCorrectSymbol(string baseSymbol);
bool ReportTradeToAPI(int db_id, int ticket);
double CalculateLotSizePro(string symbol, double stopLossDistancePoints, ENUM_LOTSIZE_MODE mode, double fixedLot, double riskPercentage);
double CalculatePriceFromPips(string symbol, double ref_price, string buy_sell_action, double pips_offset, bool is_tp);
double NormalizePrice(string symbol, double price);
int GetSignalMagicNumber(string signal_type, string trade_label);
double GetStopLossDistanceInPoints(string symbol, double price_open, double price_sl);


// --- WinINet Imports ---
#import "wininet.dll"
int InternetOpenW(string lpszAgent, int dwAccessType, string lpszProxyName, string lpszProxyBypass, int dwFlags);
int InternetConnectW(int hInternetSession, string lpszServerName, int nServerPort, string lpszUsername, string lpszPassword, int dwService, int dwFlags, int dwContext);
int HttpOpenRequestW(int hConnect, string lpszVerb, string lpszObjectName, string lpszVersion, string lpszReferrer, string& lpszAcceptTypes[], int dwFlags, int dwContext);
bool HttpSendRequestW(int hRequest, string lpszHeaders, int dwHeadersLength, string lpszPostData, int dwPostDataLength);
bool InternetReadFile(int hFile, uchar &lpBuffer[], int dwNumberOfBytesToRead, uint &lpdwNumberOfBytesRead[]);
bool InternetCloseHandle(int hInet);
bool InternetSetOptionW(int hInternet, int dwOption, int &lpBuffer[], int dwBufferLength);
#import

// --- WinINet Constants ---
#define INTERNET_OPEN_TYPE_DIRECT       1
#define INTERNET_SERVICE_HTTP           3
#define INTERNET_FLAG_RELOAD            0x80000000
#define INTERNET_FLAG_NO_CACHE_WRITE    0x04000000
#define INTERNET_FLAG_PRAGMA_NOCACHE    0x00000100
#define INTERNET_OPTION_RECEIVE_TIMEOUT 6
#define INTERNET_OPTION_SEND_TIMEOUT    5
#define INTERNET_OPTION_CONNECT_TIMEOUT 2

// --- Global Variables ---
string statusLabelName = "SignalCopierStatusLabel";
color onlineColor = clrLimeGreen;
color offlineColor = clrRed;
datetime lastSuccessTime = 0;
datetime LastCheckTime = 0;
string ProcessedSignalDB_IDs[];
int MaxStoredSignalIds = 200;

//+------------------------------------------------------------------+
int OnInit()
{
   if(!TerminalInfoInteger(TERMINAL_DLLS_ALLOWED))
   {
      Alert("Error: DLLs not allowed! Please enable 'Allow DLL imports' in Terminal settings (Tools > Options > Expert Advisors).");
      return(INIT_FAILED);
   }
   if(InpDebugMode) Print("Initializing EA Opener. Version ", __FILE__, " Server: http://",InpServerURL,":",InpServerPort);
   ArrayResize(ProcessedSignalDB_IDs, 0);

   ObjectCreate(0, statusLabelName, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, statusLabelName, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, statusLabelName, OBJPROP_YDISTANCE, 10);
   ObjectSetInteger(0, statusLabelName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, statusLabelName, OBJPROP_COLOR, offlineColor);
   ObjectSetString(0, statusLabelName, OBJPROP_TEXT, "Status: Initializing...");
   ObjectSetInteger(0, statusLabelName, OBJPROP_FONTSIZE, 10);
   ObjectSetInteger(0, statusLabelName, OBJPROP_BACK, false);

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && InpEnableTrading)
   {
      Alert("Warning: Trading is disabled in Terminal settings but EA trading is enabled. EA will not trade.");
      UpdateConnectionStatus(false, "Trading disabled in Terminal");
   }

   string testSymbol = GetCorrectSymbol("XAUUSD"); // Test s normalizovaným symbolem
   if(SymbolInfoDouble(testSymbol, SYMBOL_ASK) == 0 && InpDebugMode)
   {
      Print("Warning: XAUUSD (or its variant '"+testSymbol+"') not found or not available in Market Watch. Please add it.");
   }
   UpdateConnectionStatus(false, "Waiting for first API call");
   return(INIT_SUCCEEDED);
}
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(InpDebugMode) Print("EA deinitialization. Reason: ", reason);
   ArrayResize(ProcessedSignalDB_IDs, 0);
   ObjectDelete(0, statusLabelName);
}
//+------------------------------------------------------------------+
void OnTick()
{
   static datetime lastTickTimeInternal;
   if(TimeCurrent() - lastTickTimeInternal < 1 && MQLInfoInteger(MQL_TESTER) == false) return;
   lastTickTimeInternal = TimeCurrent();

   if(TimeCurrent() - LastCheckTime >= InpCheckInterval)
   {
      if(InpDebugMode) Print("OnTick: Time to check for new signals.");
      CheckForNewSignals();
      LastCheckTime = TimeCurrent();
   }
}
//+------------------------------------------------------------------+
string FetchAPIData(string path)
{
   if(InpDebugMode) Print("FetchAPIData: Fetching from http://",InpServerURL,":",InpServerPort,path);
   string response = "";
   int hInternet = InternetOpenW("MQL4_EA_Opener", INTERNET_OPEN_TYPE_DIRECT, "", "", 0);
   if(hInternet == 0){ Print("FetchAPIData: InternetOpenW failed. Error: ", GetLastError()); UpdateConnectionStatus(false, "Internet init failed"); return "";}

   int hConnect = InternetConnectW(hInternet, InpServerURL, InpServerPort, "", "", INTERNET_SERVICE_HTTP, 0, 0);
   if(hConnect == 0){ Print("FetchAPIData: InternetConnectW failed for ",InpServerURL,":",InpServerPort,". Error: ", GetLastError()); InternetCloseHandle(hInternet); UpdateConnectionStatus(false, "Server connect failed"); return "";}

   string acceptTypes[] = {"application/json", NULL};
   int hRequest = HttpOpenRequestW(hConnect, "GET", path, "HTTP/1.1", "", acceptTypes, INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE_WRITE | INTERNET_FLAG_PRAGMA_NOCACHE, 0);
   if(hRequest == 0){ Print("FetchAPIData: HttpOpenRequestW failed for path ",path,". Error: ", GetLastError()); InternetCloseHandle(hConnect); InternetCloseHandle(hInternet); UpdateConnectionStatus(false, "HTTP request failed"); return "";}

   int timeoutBuffer[1]; timeoutBuffer[0] = InpTimeoutMilliseconds;
   InternetSetOptionW(hRequest, INTERNET_OPTION_RECEIVE_TIMEOUT, timeoutBuffer, 4);
   InternetSetOptionW(hRequest, INTERNET_OPTION_SEND_TIMEOUT, timeoutBuffer, 4);
   InternetSetOptionW(hRequest, INTERNET_OPTION_CONNECT_TIMEOUT, timeoutBuffer, 4);

   if(!HttpSendRequestW(hRequest, "", 0, "", 0)){ Print("FetchAPIData: HttpSendRequestW failed. Error: ", GetLastError()); InternetCloseHandle(hRequest); InternetCloseHandle(hConnect); InternetCloseHandle(hInternet); UpdateConnectionStatus(false, "Request send failed"); return "";}

   uchar buffer[4096]; // Zvětšený buffer
   uint bytesRead[1]; ArrayInitialize(bytesRead, 0);
   while(true){
      if(!InternetReadFile(hRequest, buffer, sizeof(buffer)-1, bytesRead)){Print("FetchAPIData: InternetReadFile failed. Error: ", GetLastError()); break;}
      if(bytesRead[0] == 0) break;
      response += CharArrayToString(buffer, 0, bytesRead[0]);
      ArrayInitialize(buffer, 0);
   }
   InternetCloseHandle(hRequest); InternetCloseHandle(hConnect); InternetCloseHandle(hInternet);
   if(StringLen(response) > 0){ lastSuccessTime = TimeCurrent(); UpdateConnectionStatus(true, "Data received"); if(InpDebugMode) Print("FetchAPIData: API response: ", response); return response;}
   UpdateConnectionStatus(false, "Empty API response"); return "";
}
//+------------------------------------------------------------------+
void CheckForNewSignals()
{
   string response = FetchAPIData(InpServerPathSignals);
   if(StringLen(response) == 0){ if(InpDebugMode) Print("CheckForNewSignals: Empty API response or fetch failed."); return;}
   ProcessReceivedSignals(response);
}
//+------------------------------------------------------------------+
void ProcessReceivedSignals(string json_array_str)
{
   if(InpDebugMode) Print("ProcessReceivedSignals: Processing: ", StringSubstr(json_array_str,0, 200) + "...");
   string content = json_array_str;
   if(StringGetCharacter(content, 0) != '[' || StringGetCharacter(content, StringLen(content) - 1) != ']'){
      if(StringGetCharacter(content, 0) == '{' && StringGetCharacter(content, StringLen(content) - 1) == '}'){
         if(InpDebugMode) Print("ProcessReceivedSignals: Input is not an array, trying to parse as single object.");
         int db_id; string symbol; string action; double entry_price; string signal_group_id; string trade_label; string signal_type;
         double sl_value; double tp_value; string sl_value_type; string tp_value_type; double tp2_value; string tp2_value_type; int ticket_from_db;
         if(ParseSingleSignalJson(content, db_id, symbol, action, entry_price, signal_group_id, trade_label, signal_type, sl_value, tp_value, sl_value_type, tp_value_type, tp2_value, tp2_value_type, ticket_from_db)){
            if(!IsSignalProcessed(IntegerToString(db_id))){
               if(InpDebugMode) Print("ProcessReceivedSignals: Processing new single signal DB ID: ", db_id);
               ExecuteSignalAction(db_id, symbol, action, entry_price, signal_group_id, trade_label, signal_type, sl_value, tp_value, sl_value_type, tp_value_type, tp2_value, tp2_value_type, ticket_from_db);
               AddProcessedSignalId(IntegerToString(db_id));
            } else { if(InpDebugMode) Print("ProcessReceivedSignals: Single signal DB ID: ", db_id, " already processed.");}
         } else { Print("ProcessReceivedSignals: Failed to parse single signal JSON object: ", content); }
         return;
      } else if (json_array_str == "[]"){ if(InpDebugMode) Print("ProcessReceivedSignals: Received empty signal array []."); return; }
      Print("ProcessReceivedSignals: Invalid JSON format. Not an array or single object. Received: ", json_array_str); return;
   }
   content = StringSubstr(content, 1, StringLen(content) - 2);
   if(StringLen(content) == 0){ if(InpDebugMode) Print("ProcessReceivedSignals: No signals content after stripping brackets (empty array)."); return;}
   int current_pos = 0;
   while(current_pos < StringLen(content)){
      int object_start = StringFind(content, "{", current_pos);
      if(object_start < 0) break;
      int brace_level = 0; int object_end = -1;
      for(int i = object_start; i < StringLen(content); i++){
         if(StringGetCharacter(content, i) == '{') brace_level++;
         else if(StringGetCharacter(content, i) == '}') brace_level--;
         if(brace_level == 0 && StringGetCharacter(content, i) == '}'){ object_end = i; break;}
      }
      if(object_end > object_start){
         string signal_json_str = StringSubstr(content, object_start, object_end - object_start + 1);
         if(InpDebugMode) Print("ProcessReceivedSignals: Extracted single signal JSON: ", signal_json_str);
         int db_id_val; string symbol_val; string action_val; double entry_price_val; string signal_group_id_val; string trade_label_val; string signal_type_val;
         double sl_value_val; double tp_value_val; string sl_value_type_val; string tp_value_type_val; double tp2_value_val; string tp2_value_type_val; int ticket_from_db_val;
         if(ParseSingleSignalJson(signal_json_str, db_id_val, symbol_val, action_val, entry_price_val, signal_group_id_val, trade_label_val, signal_type_val, sl_value_val, tp_value_val, sl_value_type_val, tp_value_type_val, tp2_value_val, tp2_value_type_val, ticket_from_db_val)){
            if(!IsSignalProcessed(IntegerToString(db_id_val))){
               if(InpDebugMode) Print("ProcessReceivedSignals: Processing new signal DB ID: ", db_id_val);
               ExecuteSignalAction(db_id_val, symbol_val, action_val, entry_price_val, signal_group_id_val, trade_label_val, signal_type_val, sl_value_val, tp_value_val, sl_value_type_val, tp_value_type_val, tp2_value_val, tp2_value_type_val, ticket_from_db_val);
               AddProcessedSignalId(IntegerToString(db_id_val));
            } else { if(InpDebugMode) Print("ProcessReceivedSignals: Signal DB ID: ", db_id_val, " already processed. Skipping.");}
         } else { Print("ProcessReceivedSignals: Failed to parse single signal JSON: ", signal_json_str); }
         current_pos = object_end + 1;
         while(current_pos < StringLen(content) && (StringGetCharacter(content, current_pos) == ',' || StringGetCharacter(content, current_pos) == ' ')){ current_pos++; }
      } else { Print("ProcessReceivedSignals: Error finding matching braces for a signal object. Remaining content: ", StringSubstr(content, current_pos)); break; }
   }
}
//+------------------------------------------------------------------+
bool ParseSingleSignalJson(string signal_json_str,
                           int &db_id, string &symbol, string &action, double &entry_price_ref,
                           string &signal_group_id, string &trade_label, string &signal_type,
                           double &sl_value, double &tp_value, string &sl_value_type, string &tp_value_type,
                           double &tp2_value, string &tp2_value_type, int &ticket_from_db)
{
   if(InpDebugMode) Print("ParseSingleSignalJson: Parsing: ", signal_json_str);
   string temp_str, sl_value_str, tp_value_str, tp2_value_str, ticket_str;

   temp_str = GetJsonValue(signal_json_str, "id");
   if(StringLen(temp_str) == 0 || temp_str == "null") { Print("ParseSingleSignalJson: Missing or null 'id'"); return false; }
   db_id = (int)StringToInteger(temp_str);

   symbol = GetJsonValue(signal_json_str, "symbol");
   if(StringLen(symbol) == 0 || symbol == "null") { Print("ParseSingleSignalJson: Missing or null 'symbol' for id: ", db_id); return false; }
   if(StringGetCharacter(symbol,0)=='"' && StringGetCharacter(symbol,StringLen(symbol)-1)=='"') symbol = StringSubstr(symbol,1,StringLen(symbol)-2);
   symbol = GetCorrectSymbol(symbol);

   action = StringUpper(GetJsonValue(signal_json_str, "action"));
   if(StringLen(action) == 0 || action == "null") { Print("ParseSingleSignalJson: Missing or null 'action' for id: ", db_id); return false; }
   if(StringGetCharacter(action,0)=='"' && StringGetCharacter(action,StringLen(action)-1)=='"') action = StringSubstr(action,1,StringLen(action)-2);

   signal_type = GetJsonValue(signal_json_str, "signal_type");
   if(StringLen(signal_type) == 0 || signal_type == "null") { Print("ParseSingleSignalJson: Missing or null 'signal_type' for id: ", db_id); return false; }
   if(StringGetCharacter(signal_type,0)=='"' && StringGetCharacter(signal_type,StringLen(signal_type)-1)=='"') signal_type = StringSubstr(signal_type,1,StringLen(signal_type)-2);

   temp_str = GetJsonValue(signal_json_str, "entry_price");
   entry_price_ref = (temp_str == "null" || StringLen(temp_str) == 0) ? 0.0 : StringToDouble(temp_str);

   signal_group_id = GetJsonValue(signal_json_str, "signal_group_id");
   if(StringLen(signal_group_id)>0 && StringGetCharacter(signal_group_id,0)=='"' && StringGetCharacter(signal_group_id,StringLen(signal_group_id)-1)=='"') signal_group_id = StringSubstr(signal_group_id,1,StringLen(signal_group_id)-2);

   trade_label = GetJsonValue(signal_json_str, "trade_label");
   if(StringLen(trade_label)>0 && StringGetCharacter(trade_label,0)=='"' && StringGetCharacter(trade_label,StringLen(trade_label)-1)=='"') trade_label = StringSubstr(trade_label,1,StringLen(trade_label)-2);

   sl_value_str = GetJsonValue(signal_json_str, "sl_value");
   sl_value = (sl_value_str == "null" || StringLen(sl_value_str) == 0) ? 0.0 : StringToDouble(sl_value_str);
   sl_value_type = GetJsonValue(signal_json_str, "sl_value_type");
   if(StringLen(sl_value_type)>0 &&StringGetCharacter(sl_value_type,0)=='"' && StringGetCharacter(sl_value_type,StringLen(sl_value_type)-1)=='"') sl_value_type = StringSubstr(sl_value_type,1,StringLen(sl_value_type)-2);

   tp_value_str = GetJsonValue(signal_json_str, "tp_value");
   tp_value = (tp_value_str == "null" || StringLen(tp_value_str) == 0) ? 0.0 : StringToDouble(tp_value_str);
   tp_value_type = GetJsonValue(signal_json_str, "tp_value_type");
   if(StringLen(tp_value_type)>0 && StringGetCharacter(tp_value_type,0)=='"' && StringGetCharacter(tp_value_type,StringLen(tp_value_type)-1)=='"') tp_value_type = StringSubstr(tp_value_type,1,StringLen(tp_value_type)-2);

   tp2_value_str = GetJsonValue(signal_json_str, "tp2_value");
   tp2_value = (tp2_value_str == "null" || StringLen(tp2_value_str) == 0) ? 0.0 : StringToDouble(tp2_value_str);
   tp2_value_type = GetJsonValue(signal_json_str, "tp2_value_type");
   if(StringLen(tp2_value_type) > 0 && StringGetCharacter(tp2_value_type,0)=='"' && StringGetCharacter(tp2_value_type,StringLen(tp2_value_type)-1)=='"') tp2_value_type = StringSubstr(tp2_value_type,1,StringLen(tp2_value_type)-2);

   ticket_str = GetJsonValue(signal_json_str, "ticket"); // Načtení ticketu z DB
   ticket_from_db = (ticket_str == "null" || StringLen(ticket_str) == 0) ? 0 : (int)StringToInteger(ticket_str);

   if(InpDebugMode){
      int sym_digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS); if(sym_digits <=0) sym_digits = 5;
      Print("ParseSingleSignalJson: Parsed DB ID ", db_id, ": Sym=", symbol, ", Act=", action, ", Typ=", signal_type,
            ", Entry=", DoubleToString(entry_price_ref, sym_digits), ", SLv=", DoubleToString(sl_value, sym_digits), ", SLt=", sl_value_type,
            ", TPv=", DoubleToString(tp_value, sym_digits), ", TPt=", tp_value_type, ", TP2v=", DoubleToString(tp2_value, sym_digits),
            ", TP2t=", tp2_value_type, ", GrpID=", signal_group_id, ", Lbl=", trade_label, ", TktDB=", ticket_from_db);
   }
   return true;
}
//+------------------------------------------------------------------+
void ExecuteSignalAction(int db_id, string symbol, string action, double entry_price_ref,
                        string signal_group_id, string trade_label, string signal_type,
                        double sl_value, double tp_value, string sl_value_type, string tp_value_type,
                        double tp2_value, string tp2_value_type, int ticket_from_db)
{
   if(InpDebugMode) Print("ExecuteSignalAction: DB ID:", db_id, " Sym:", symbol, " Act:", action, " Type:", signal_type, " SLval:", sl_value, " SLtype:", sl_value_type, " TPval:", tp_value, " TPtype:", tp_value_type, " EntryRef:", entry_price_ref, " TktDB:", ticket_from_db);

   if(!InpEnableTrading){ Print("ExecuteSignalAction: Trading disabled (Input). Signal DB ID:", db_id); return; }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)){ Print("ExecuteSignalAction: Trading disabled (Terminal). Signal DB ID:", db_id); return; }

   double current_ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double current_bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   if(current_ask == 0 || current_bid == 0){ Print("ExecuteSignalAction: Invalid market prices for ", symbol); return; }

   double final_sl_price = 0; double final_tp_price = 0;
   double sl_dist_points = 0;
   int    cmd = -1;
   double open_price_for_calc = (action == "BUY") ? current_ask : current_bid; // Pro Market Order

   // --- SL Calculation ---
   if(sl_value_type == "PIPS" && sl_value > 0) {
      final_sl_price = CalculatePriceFromPips(symbol, entry_price_ref, action, sl_value, false);
      sl_dist_points = GetStopLossDistanceInPoints(symbol, open_price_for_calc, final_sl_price);
   } else if(sl_value_type == "PRICE" && sl_value > 0) {
      final_sl_price = NormalizePrice(symbol, sl_value);
      sl_dist_points = GetStopLossDistanceInPoints(symbol, open_price_for_calc, final_sl_price);
   } else { Print("ExecuteSignalAction: Invalid SL config for DB ID:", db_id); return; }
   if(sl_dist_points <= SymbolInfoInteger(symbol, SYMBOL_SPREAD)) { Print("ExecuteSignalAction: SL too close to market for DB ID:", db_id, " SL Dist Points:", sl_dist_points, " Spread:", SymbolInfoInteger(symbol, SYMBOL_SPREAD)); return; }


   // --- TP Calculation (primární TP) ---
   if(tp_value_type == "PIPS" && tp_value > 0) {
      final_tp_price = CalculatePriceFromPips(symbol, entry_price_ref, action, tp_value, true);
   } else if(tp_value_type == "PRICE" && tp_value > 0) {
      final_tp_price = NormalizePrice(symbol, tp_value);
   } // Pokud 0 nebo null, TP bude 0 (bez TP)

   // --- Lotsize ---
   double lots = CalculateLotSizePro(symbol, sl_dist_points, InpLotSizeMode, InpFixedLotSize, InpRiskPercent);
   if(lots <= 0){ Print("ExecuteSignalAction: Lot size calc failed or zero for DB ID:", db_id); return; }

   // --- Magic Number & Comment ---
   int magic = GetSignalMagicNumber(signal_type, trade_label);
   string comment = signal_group_id + "_" + trade_label + "_id" + IntegerToString(db_id);
   if(StringLen(comment) > 31) comment = StringSubstr(comment, 0, 31);

   // --- Order Execution ---
   int ticket = 0;
   if(action == "BUY") cmd = OP_BUY; else if(action == "SELL") cmd = OP_SELL;
   else { Print("ExecuteSignalAction: Invalid action '",action,"'"); return; }

   if(signal_type == "INITIAL_T1" || signal_type == "INITIAL_T2_DEFAULT" || signal_type == "RE_ENTRY" || signal_type == SIGNAL_TYPE_STANDARD)
   {
      if(InpDebugMode) Print("OrderSend: Sym:",symbol,",Cmd:",cmd,",Lots:",lots,",Price:",open_price_for_calc,",Slip:",InpSlippagePoints,",SL:",final_sl_price,",TP:",final_tp_price,",Comm:",comment,",Magic:",magic);
      ticket = OrderSend(symbol, cmd, lots, open_price_for_calc, InpSlippagePoints, final_sl_price, final_tp_price, comment, magic, 0, (cmd==OP_BUY ? clrBlue : clrRed));
      if(ticket > 0){ Print("OrderSend SUCCESS: DB ID:", db_id, " Ticket:", ticket); ReportTradeToAPI(db_id, ticket); }
      else { Print("OrderSend FAILED: DB ID:", db_id, " Error:", ErrorDescription(GetLastError())); }
   }
   else if(signal_type == "UPDATE_T2")
   {
      int ticket_to_modify = ticket_from_db; // Použijeme ticket z DB, který byl uložen při otevření INITIAL_T2_DEFAULT
      if(ticket_to_modify <= 0) // Fallback: hledání podle magic a komentu, pokud ticket z DB není
      {
         for(int i = OrdersTotal() - 1; i >= 0; i--) {
            if(OrderSelect(i, SELECT_BY_POS) && OrderSymbol() == symbol && OrderMagicNumber() == GetSignalMagicNumber("INITIAL_T2_DEFAULT", "T2_AUTO")) { // Hledáme původní T2
               if(StringFind(OrderComment(), signal_group_id) >=0 && StringFind(OrderComment(), "T2_AUTO") >=0) {
                  ticket_to_modify = OrderTicket();
                  break;
               }
            }
         }
      }

      if(ticket_to_modify > 0 && OrderSelect(ticket_to_modify, SELECT_BY_TICKET))
      {
         double new_tp_price = tp_value; // Pro UPDATE_T2 je tp_value již cena
         if(new_tp_price <= 0) { Print("ExecuteSignalAction UPDATE_T2: Invalid new TP price (",new_tp_price,") for Ticket:", ticket_to_modify); return; }

         bool modified = OrderModify(ticket_to_modify, OrderOpenPrice(), OrderStopLoss(), NormalizePrice(symbol, new_tp_price), 0, clrNONE);
         if(modified){ Print("OrderModify SUCCESS: Ticket:", ticket_to_modify, " New TP:", new_tp_price); ReportTradeToAPI(db_id, ticket_to_modify); }
         else{ Print("OrderModify FAILED: Ticket:", ticket_to_modify, " Error:", ErrorDescription(GetLastError())); }
      } else { Print("ExecuteSignalAction UPDATE_T2: Could not find T2_AUTO trade to modify. GroupID:", signal_group_id, " Ticket from DB:", ticket_from_db); }
   }
   else { Print("ExecuteSignalAction: Unknown signal_type '",signal_type,"' for DB ID:", db_id); }
}
//+------------------------------------------------------------------+
bool ReportTradeToAPI(int db_id, int ticket)
{
   if(db_id <= 0) return false;
   string json_payload = StringFormat("{\"id\": %d, \"ticket\": %d}", db_id, ticket);
   if(InpDebugMode) Print("ReportTradeToAPI: Reporting DB ID ", db_id, ", Ticket ", ticket, " with payload: ", json_payload);
   uchar post_data[]; StringToCharArray(json_payload, post_data); int data_len = ArraySize(post_data) -1; // MQL4 přidává null terminator
   if(data_len <=0){ Print("ReportTradeToAPI: Failed to convert payload."); return false; }

   string headers = "Content-Type: application/json\r\n";
   int hInternet = InternetOpenW("MQL4_EA_Report", INTERNET_OPEN_TYPE_DIRECT, NULL, NULL, 0);
   if(hInternet == 0){ Print("ReportTradeToAPI: InternetOpenW failed: ", GetLastError()); return false; }
   int hConnect = InternetConnectW(hInternet, InpServerURL, InpServerPort, NULL, NULL, INTERNET_SERVICE_HTTP, 0, 0);
   if(hConnect == 0){ Print("ReportTradeToAPI: InternetConnectW failed: ", GetLastError()); InternetCloseHandle(hInternet); return false; }
   string acceptTypes[] = {"application/json", NULL};
   int hRequest = HttpOpenRequestW(hConnect, "POST", InpServerPathReport, NULL, NULL, acceptTypes, INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE_WRITE | INTERNET_FLAG_PRAGMA_NOCACHE, 0);
   if(hRequest == 0){ Print("ReportTradeToAPI: HttpOpenRequestW failed: ", GetLastError()); InternetCloseHandle(hConnect); InternetCloseHandle(hInternet); return false; }
   int timeout_ms[1]; timeout_ms[0] = InpTimeoutMilliseconds;
   InternetSetOptionW(hRequest, INTERNET_OPTION_RECEIVE_TIMEOUT, timeout_ms, 4);
   InternetSetOptionW(hRequest, INTERNET_OPTION_SEND_TIMEOUT, timeout_ms, 4);
   InternetSetOptionW(hRequest, INTERNET_OPTION_CONNECT_TIMEOUT, timeout_ms, 4);
   if(!HttpSendRequestW(hRequest, headers, StringLen(headers), post_data, data_len)){ Print("ReportTradeToAPI: HttpSendRequestW failed: ", GetLastError()); /* Close handles */ return false; } // Zkráceno pro přehlednost
   string http_response_text = ""; uchar buffer[1024]; uint bytes_read[1];
   while(InternetReadFile(hRequest, buffer, ArraySize(buffer)-1, bytes_read) && bytes_read[0] > 0){ http_response_text += CharArrayToString(buffer,0,bytes_read[0]); ArrayInitialize(buffer,0); }
   if(InpDebugMode) Print("ReportTradeToAPI: Response: ", http_response_text);
   InternetCloseHandle(hRequest); InternetCloseHandle(hConnect); InternetCloseHandle(hInternet);
   if(StringFind(http_response_text, "\"status\": \"ok\"") >=0){ if(InpDebugMode) Print("ReportTradeToAPI: Success for DB ID ", db_id); return true; }
   else{ Print("ReportTradeToAPI: Server NO OK for DB ID ", db_id, ". Resp: ", http_response_text); return false; }
}
//+------------------------------------------------------------------+
string GetCorrectSymbol(string baseSymbol)
{
   if(StringLen(baseSymbol) == 0) return "";
   string upperBaseSymbol = StringUpper(baseSymbol);
   string finalSymbol = baseSymbol;

   if(upperBaseSymbol == "XAUUSD" || upperBaseSymbol == "XAU/USD" || upperBaseSymbol == "GOLD")
   {
      string goldVariants[] = {"XAUUSD", "GOLD", "XAUUSD.","XAUUSDm", "XAUUSDmicro", "GOLD.micro", "GOLDmicro", "XAUUSDpro", "GOLDpro"};
      for(int i=0; i<ArraySize(goldVariants); i++)
      {
         if(SymbolExist(goldVariants[i], false) && MarketInfo(goldVariants[i], MODE_ASK) > 0)
         {
            if(InpDebugMode) Print("GetCorrectSymbol: Found valid Gold symbol: ", goldVariants[i]);
            return goldVariants[i];
         }
      }
      if(StringLen(InpSymbolPrefix)>0 || StringLen(InpSymbolSuffix)>0)
      {
         string prefixedSymbol = InpSymbolPrefix + "XAUUSD" + InpSymbolSuffix;
         if(SymbolExist(prefixedSymbol, false) && MarketInfo(prefixedSymbol, MODE_ASK) > 0){
            if(InpDebugMode) Print("GetCorrectSymbol: Found Gold symbol with prefix/suffix: ", prefixedSymbol);
            return prefixedSymbol;
         }
      }
      if(InpDebugMode) Print("GetCorrectSymbol: Gold symbol not found in common variants or with prefix/suffix. Returning original: ", baseSymbol);
      return baseSymbol;
   }

   finalSymbol = InpSymbolPrefix + baseSymbol + InpSymbolSuffix;
   if(SymbolExist(finalSymbol, false) && MarketInfo(finalSymbol, MODE_ASK) > 0)
   {
      if(InpDebugMode) Print("GetCorrectSymbol: Constructed symbol with prefix/suffix: ", finalSymbol);
      return finalSymbol;
   }
   else if (SymbolExist(baseSymbol,false) && MarketInfo(baseSymbol, MODE_ASK) > 0)
   {
      if(InpDebugMode) Print("GetCorrectSymbol: Symbol with prefix/suffix '"+finalSymbol+"' not found, using base symbol: ", baseSymbol);
      return baseSymbol;
   }

   if(InpDebugMode) Print("GetCorrectSymbol: Symbol '"+baseSymbol+"' (nor with affixes) not found. Returning original.");
   return baseSymbol;
}
//+------------------------------------------------------------------+
bool IsSignalProcessed(string db_id_str)
{
   if(StringLen(db_id_str) == 0) return true;
   for(int i = 0; i < ArraySize(ProcessedSignalDB_IDs); i++)
   {
      if(ProcessedSignalDB_IDs[i] == db_id_str)
         return true;
   }
   return false;
}
//+------------------------------------------------------------------+
void AddProcessedSignalId(string db_id_str)
{
   if(StringLen(db_id_str) == 0) return;
   if(ArraySize(ProcessedSignalDB_IDs) >= MaxStoredSignalIds)
   {
      string tempArray[];
      ArrayCopy(tempArray, ProcessedSignalDB_IDs, 0, 1, MaxStoredSignalIds - 1);
      ArrayCopy(ProcessedSignalDB_IDs, tempArray, 0, 0, MaxStoredSignalIds -1);
      ArrayResize(ProcessedSignalDB_IDs, MaxStoredSignalIds -1);
   }
   int size = ArraySize(ProcessedSignalDB_IDs);
   ArrayResize(ProcessedSignalDB_IDs, size + 1);
   ProcessedSignalDB_IDs[size] = db_id_str;
   if(InpDebugMode) Print("AddProcessedSignalId: Added DB ID ", db_id_str, ". Total processed: ", ArraySize(ProcessedSignalDB_IDs));
}
//+------------------------------------------------------------------+
double CalculateLotSizePro(string symbol, double stopLossDistancePoints, ENUM_LOTSIZE_MODE mode, double fixedLot, double riskPercentage)
{
   if(InpDebugMode) Print("CalculateLotSizePro: Symbol=", symbol, ", SL_Points=", stopLossDistancePoints, ", Mode=", EnumToString(mode), ", FixedLot=", fixedLot, ", Risk%=", riskPercentage);
   if(mode == LS_Fixed){
      if(fixedLot <= 0){ Print("CalculateLotSizePro: Fixed lot invalid (<=0): ", fixedLot); return 0.0; }
      double minLotFx = MarketInfo(symbol, MODE_MINLOT), maxLotFx = MarketInfo(symbol, MODE_MAXLOT), lotStepFx = MarketInfo(symbol, MODE_LOTSTEP);
      if(lotStepFx <= 0) lotStepFx = 0.01;
      double normalizedFixedLot = MathRound(fixedLot / lotStepFx) * lotStepFx;
      normalizedFixedLot = MathMax(minLotFx, MathMin(maxLotFx, normalizedFixedLot));
      if(InpDebugMode) Print("CalculateLotSizePro: Fixed. Req: ", fixedLot, ", Norm: ", normalizedFixedLot, " (Min:", minLotFx, ", Max:", maxLotFx,", Step:", lotStepFx,")");
      return NormalizeDouble(normalizedFixedLot,2);
   } else if(mode == LS_PercentEquity){
      if(riskPercentage <= 0 || riskPercentage > 50){ Print("CalculateLotSizePro: Invalid RiskPercent (", riskPercentage, "). Must be >0 & <=50."); return 0.0;}
      if(stopLossDistancePoints <= 0){ Print("CalculateLotSizePro: SLDistPoints must be >0. Val: ", stopLossDistancePoints); return 0.0;}
      double accountEquity = AccountEquity(); if(accountEquity <= 0){ Print("CalculateLotSizePro: Account Equity <=0."); return 0.0;}
      double riskAmount = accountEquity * (riskPercentage / 100.0);
      double tickValue = MarketInfo(symbol, MODE_TICKVALUE);
      double pointSize = MarketInfo(symbol, MODE_POINT);
      if(tickValue <= 0 || pointSize <=0){ Print("CalculateLotSizePro: Invalid MarketInfo for ", symbol, " (TV:", tickValue, ", PS:", pointSize, ")"); return 0.0;}
      double lossPerLot = stopLossDistancePoints * tickValue;
      if(MarketInfo(symbol, MODE_TICKSIZE) != pointSize && MarketInfo(symbol, MODE_TICKSIZE) > 0) {
         lossPerLot = stopLossDistancePoints * (tickValue / MarketInfo(symbol, MODE_TICKSIZE)) * pointSize;
      }
      if(lossPerLot <= 0){ Print("CalculateLotSizePro: lossPerLot <=0 for ", symbol, ". SLP:", stopLossDistancePoints, " TV:", tickValue); return 0.0;}
      double lotSize = riskAmount / lossPerLot;
      if(InpDebugMode) Print("CalcLot: Eq=", accountEquity, ", RiskAmt=", riskAmount, ", SLP=", stopLossDistancePoints, ", TV=", tickValue, ", PS=", pointSize, ", LPL=", lossPerLot, ", RawLot=", lotSize);
      double minLot = MarketInfo(symbol, MODE_MINLOT), maxLot = MarketInfo(symbol, MODE_MAXLOT), lotStep = MarketInfo(symbol, MODE_LOTSTEP);
      if(lotStep <= 0) lotStep = 0.01;
      lotSize = MathRound(lotSize / lotStep) * lotStep;
      lotSize = MathMax(minLot, MathMin(maxLot, lotSize));
      if(InpDebugMode) Print("CalcLot: PercentEquity. Lot: ", lotSize, " (Min:", minLot, ", Max:", maxLot,", Step:", lotStep,")");
      return NormalizeDouble(lotSize, 2);
   } else { Print("CalculateLotSizePro: Unknown LotSizeMode: ", EnumToString(mode)); return 0.0; }
}
//+------------------------------------------------------------------+
double CalculatePriceFromPips(string symbol, double ref_price, string buy_sell_action, double pips_offset, bool is_tp)
{
   if(pips_offset <= 0 && is_tp) return 0.0; // Pro TP musí být offset kladný
   if(pips_offset <= 0 && !is_tp) return 0.0; // Pro SL musí být offset kladný

   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(point <= 0) {Print("CalculatePriceFromPips: Invalid point size for ",symbol); return 0.0;}

   double price_offset = pips_offset * 10 * point;

   if(StringFind(StringUpper(buy_sell_action), "BUY") >=0)
      return NormalizePrice(symbol, is_tp ? (ref_price + price_offset) : (ref_price - price_offset));
   else
      return NormalizePrice(symbol, is_tp ? (ref_price - price_offset) : (ref_price + price_offset));
}
//+------------------------------------------------------------------+
double GetStopLossDistanceInPoints(string symbol, double price_open, double price_sl)
{
    double point_val = SymbolInfoDouble(symbol, SYMBOL_POINT);
    if(point_val <= 0) { Print("GetStopLossDistanceInPoints: Invalid point for ", symbol); return 0; }
    if(price_open == 0 || price_sl == 0) { Print("GetStopLossDistanceInPoints: Invalid prices ", price_open, " / ", price_sl); return 0;}
    return MathAbs(price_open - price_sl) / point_val;
}
//+------------------------------------------------------------------+
int GetSignalMagicNumber(string signal_type, string trade_label)
{
    int base_magic = InpMagicNumberBase;
    if(signal_type == "INITIAL_T1" && trade_label == "T1_AUTO") return base_magic + 1;
    if(signal_type == "INITIAL_T2_DEFAULT" && trade_label == "T2_AUTO") return base_magic + 2;
    if(signal_type == "UPDATE_T2" && trade_label == "T2_AUTO") return base_magic + 2;
    if(signal_type == "RE_ENTRY" && trade_label == "RE_AUTO") return base_magic + 3;
    if(signal_type == "STANDARD" && trade_label == "STD_TRADE") return base_magic + 10;
    Print("GetSignalMagicNumber: Unknown combination: Type=", signal_type, ", Label=", trade_label, ". Using default offset +99.");
    return base_magic + 99;
}
//+------------------------------------------------------------------+
// Další funkce (GetJsonValue, ErrorDescription, StringUpper, atd. zůstávají)
// Původní ExecuteTrade, CalculateLotSize, GetTPMagicOffset mohou být odstraněny nebo ponechány pro referenci
// ale neměly by být volány novou logikou.
// ValidateSymbol byla nahrazena GetCorrectSymbol a SymbolExist
//+------------------------------------------------------------------+
//| Extract value from JSON string (basic implementation)            |
//+------------------------------------------------------------------+
string GetJsonValue(string json, string key)
{
   string keyPattern = "\"" + key + "\"";
   int keyPos = StringFind(json, keyPattern);
   if(keyPos < 0) return "";

   int colonPos = StringFind(json, ":", keyPos);
   if(colonPos < 0) return "";

   int valueStart = colonPos + 1;

   while(valueStart < StringLen(json))
   {
      ushort c = StringGetCharacter(json, valueStart);
      if(c != ' ' && c != '\t' && c != '\r' && c != '\n' && c != '"')
         break;
      if(c == '"') { valueStart++; break; } // Pokud hodnota začíná uvozovkou, posunout za ni
      valueStart++;
   }

   if(valueStart >= StringLen(json)) return "";

   int valueEnd = valueStart;
   bool inQuotes = (StringGetCharacter(json, valueStart-1) == '"');

   while(valueEnd < StringLen(json))
   {
      ushort c = StringGetCharacter(json, valueEnd);
      if (inQuotes) {
          if (c == '"' && StringGetCharacter(json, valueEnd-1) != '\\') break; // Konec stringu, ošetření escapovaných uvozovek
      } else {
          if(c == ',' || c == '}' || c == ']' || c == ' ' || c == '\r' || c == '\n')
             break;
      }
      valueEnd++;
   }

   string result = StringSubstr(json, valueStart, valueEnd - valueStart);
   return result;
}
//+------------------------------------------------------------------+
string ErrorDescription(int error)
{
   string errorDesc = "";
   switch(error)
   {
      case 0: errorDesc = "No error"; break;
      case 1: errorDesc = "No error, trade conditions not changed"; break;
      case 2: errorDesc = "Common error"; break;
      case 3: errorDesc = "Invalid trade parameters"; break;
      case 4: errorDesc = "Trade server busy"; break;
      case 5: errorDesc = "Old version of the client terminal"; break;
      case 6: errorDesc = "No connection with trade server"; break;
      case 7: errorDesc = "Not enough rights"; break;
      case 8: errorDesc = "Too frequent requests"; break;
      case 9: errorDesc = "Malfunctional trade operation"; break;
      case 64: errorDesc = "Account disabled"; break;
      case 65: errorDesc = "Invalid account"; break;
      case 128: errorDesc = "Trade timeout"; break;
      case 129: errorDesc = "Invalid price"; break;
      case 130: errorDesc = "Invalid stops"; break;
      case 131: errorDesc = "Invalid trade volume"; break;
      case 132: errorDesc = "Market closed"; break;
      case 133: errorDesc = "Trade disabled"; break;
      case 134: errorDesc = "Not enough money"; break;
      case 135: errorDesc = "Price changed"; break;
      case 136: errorDesc = "Off quotes"; break;
      case 137: errorDesc = "Broker busy"; break;
      case 138: errorDesc = "Requote"; break;
      case 139: errorDesc = "Order locked"; break;
      case 140: errorDesc = "Long positions only allowed"; break;
      case 141: errorDesc = "Too many requests"; break;
      case 145: errorDesc = "Modification denied because order too close to market"; break;
      case 146: errorDesc = "Trade context busy"; break;
      case 147: errorDesc = "Expirations are denied by broker"; break;
      case 148: errorDesc = "Amount of open and pending orders has reached the limit"; break;
      default: errorDesc = "Unknown error " + IntegerToString(error); break;
   }
   return errorDesc;
}
//+------------------------------------------------------------------+
void UpdateConnectionStatus(bool connected, string message = "")
{
   color statusColor = connected ? onlineColor : offlineColor;
   string statusText = "Status: " + (connected ? "Online" : "Offline");
   if(StringLen(message) > 0) statusText += " - " + message;

   string timeText = "";
   if(lastSuccessTime > 0)
      timeText = "\nLast success: " + TimeToString(lastSuccessTime, TIME_SECONDS);

   ObjectSetInteger(0, statusLabelName, OBJPROP_COLOR, statusColor);
   ObjectSetString(0, statusLabelName, OBJPROP_TEXT, statusText + timeText);
}
//+------------------------------------------------------------------+
string StringUpper(string str)
{
   string result = str;
   StringToUpper(result);
   return result;
}
//+------------------------------------------------------------------+
string GetCorrectSymbol(string baseSymbol)
{
   if(StringLen(baseSymbol) == 0) return "";
   string upperBaseSymbol = StringUpper(baseSymbol);
   string finalSymbol = baseSymbol;

   if(upperBaseSymbol == "XAUUSD" || upperBaseSymbol == "XAU/USD" || upperBaseSymbol == "GOLD")
   {
      string goldVariants[] = {"XAUUSD", "GOLD", "XAUUSD.","XAUUSDm", "XAUUSDmicro", "GOLD.micro", "GOLDmicro", "XAUUSDpro", "GOLDpro"};
      for(int i=0; i<ArraySize(goldVariants); i++)
      {
         if(SymbolExist(goldVariants[i], false) && MarketInfo(goldVariants[i], MODE_ASK) > 0)
         {
            if(InpDebugMode) Print("GetCorrectSymbol: Found valid Gold symbol: ", goldVariants[i]);
            return goldVariants[i];
         }
      }
      if(StringLen(InpSymbolPrefix)>0 || StringLen(InpSymbolSuffix)>0)
      {
         string prefixedSymbol = InpSymbolPrefix + "XAUUSD" + InpSymbolSuffix;
         if(SymbolExist(prefixedSymbol, false) && MarketInfo(prefixedSymbol, MODE_ASK) > 0){
            if(InpDebugMode) Print("GetCorrectSymbol: Found Gold symbol with prefix/suffix: ", prefixedSymbol);
            return prefixedSymbol;
         }
      }
      if(InpDebugMode) Print("GetCorrectSymbol: Gold symbol not found in common variants or with prefix/suffix. Returning original: ", baseSymbol);
      return baseSymbol;
   }

   finalSymbol = InpSymbolPrefix + baseSymbol + InpSymbolSuffix;
   if(SymbolExist(finalSymbol, false) && MarketInfo(finalSymbol, MODE_ASK) > 0)
   {
      if(InpDebugMode) Print("GetCorrectSymbol: Constructed symbol with prefix/suffix: ", finalSymbol);
      return finalSymbol;
   }
   else if (SymbolExist(baseSymbol,false) && MarketInfo(baseSymbol, MODE_ASK) > 0)
   {
      if(InpDebugMode) Print("GetCorrectSymbol: Symbol with prefix/suffix '"+finalSymbol+"' not found, using base symbol: ", baseSymbol);
      return baseSymbol;
   }

   if(InpDebugMode) Print("GetCorrectSymbol: Symbol '"+baseSymbol+"' (nor with affixes) not found. Returning original.");
   return baseSymbol;
}
//+------------------------------------------------------------------+
bool IsSignalProcessed(string db_id_str)
{
   if(StringLen(db_id_str) == 0) return true;
   for(int i = 0; i < ArraySize(ProcessedSignalDB_IDs); i++)
   {
      if(ProcessedSignalDB_IDs[i] == db_id_str)
         return true;
   }
   return false;
}
//+------------------------------------------------------------------+
void AddProcessedSignalId(string db_id_str)
{
   if(StringLen(db_id_str) == 0) return;
   if(ArraySize(ProcessedSignalDB_IDs) >= MaxStoredSignalIds)
   {
      string tempArray[];
      ArrayCopy(tempArray, ProcessedSignalDB_IDs, 0, 1, MaxStoredSignalIds - 1);
      ArrayCopy(ProcessedSignalDB_IDs, tempArray, 0, 0, MaxStoredSignalIds -1);
      ArrayResize(ProcessedSignalDB_IDs, MaxStoredSignalIds -1);
   }
   int size = ArraySize(ProcessedSignalDB_IDs);
   ArrayResize(ProcessedSignalDB_IDs, size + 1);
   ProcessedSignalDB_IDs[size] = db_id_str;
   if(InpDebugMode) Print("AddProcessedSignalId: Added DB ID ", db_id_str, ". Total processed: ", ArraySize(ProcessedSignalDB_IDs));
}
//+------------------------------------------------------------------+
// Původní ExecuteTrade, CalculateLotSize, GetTPMagicOffset a ValidateSymbol mohou být odstraněny,
// protože jejich funkcionalita je nyní v ExecuteSignalAction, CalculateLotSizePro, GetSignalMagicNumber a GetCorrectSymbol.
// Prozatím je nechávám zakomentované pro referenci.
/*
//+------------------------------------------------------------------+
//| Execute trade order                                              |
//+------------------------------------------------------------------+
void ExecuteTrade(string symbol, string action, double entryPrice, double stopLoss,
                 double takeProfit, double riskPercent, string tpLabel)
{
   if(!InpEnableTrading || AlertsOnly)
   {
      Print("Trading disabled - would execute: ",symbol," ",action," at ",entryPrice);
      return;
   }

   if(InpDebugMode) Print("Executing trade: ", symbol, " ", action, " EntryPrice=", entryPrice,
                        " SL=", stopLoss, " TP=", takeProfit, " Risk=", riskPercent);

   if(!SymbolSelect(symbol, true))
   {
      Print("Error: Cannot select symbol ", symbol);
      return;
   }

   if(MarketInfo(symbol, MODE_TICKVALUE) <= 0)
   {
      Print("Error: Symbol ", symbol, " has no valid tick value");
      return;
   }

   int orderType;
   double currentPrice;
   bool actionIsBuy = (StringFind(action, "BUY") >= 0);
   bool actionIsSell = (StringFind(action, "SELL") >= 0);

   bool isLimitOrder = !UseMarketOrders && (StringFind(action, "LIMIT") >= 0);

   if(actionIsBuy)
   {
      currentPrice = MarketInfo(symbol, MODE_ASK);
      orderType = isLimitOrder ? OP_BUYLIMIT : OP_BUY;
   }
   else if(actionIsSell)
   {
      currentPrice = MarketInfo(symbol, MODE_BID);
      orderType = isLimitOrder ? OP_SELLLIMIT : OP_SELL;
   }
   else
   {
      Print("Invalid trade action: ", action);
      return;
   }

   if(isLimitOrder)
   {
      if(entryPrice <= 0)
      {
         Print("Error: Limit order requires valid entry price for ", symbol);
         return;
      }
      double minDistance = MarketInfo(symbol, MODE_STOPLEVEL) * MarketInfo(symbol, MODE_POINT);
      double priceDistance = MathAbs(currentPrice - entryPrice);
      if(priceDistance < minDistance)
      {
         if(InpDebugMode) Print("Entry price too close, switching to market order");
         orderType = actionIsBuy ? OP_BUY : OP_SELL;
         isLimitOrder = false;
      }
   }
   double orderPrice = isLimitOrder ? entryPrice : currentPrice;
   double stopLossDistance = MathAbs(orderPrice - stopLoss);
   double lotSize = CalculateLotSize(symbol, riskPercent, stopLossDistance);

   if(lotSize <= 0) return;

   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   double normOrderPrice = NormalizeDouble(orderPrice, digits);
   double normStopLoss = NormalizeDouble(stopLoss, digits);
   double normTakeProfit = NormalizeDouble(takeProfit, digits);

   if(InpDebugMode) Print("Order details: Price=", normOrderPrice, " SL=", normStopLoss,
                       " TP=", normTakeProfit, " Lots=", lotSize, " OrderType=", orderType);
   int maxRetries = 3;
   int retryCount = 0;
   int ticket = -1;
   while(retryCount < maxRetries && ticket < 0)
   {
      ticket = OrderSend( symbol, orderType, lotSize, normOrderPrice, SlippagePoints,
         normStopLoss, normTakeProfit, "SignalCopierPro_"+tpLabel,
         MagicNumber + GetTPMagicOffset(tpLabel), 0, (orderType == OP_BUY || orderType == OP_BUYLIMIT ? clrGreen : clrRed)
      );
      if(ticket < 0) HandleOrderError(retryCount, symbol, orderType, digits); // HandleOrderError by měla být definována
      retryCount++;
   }
   if(ticket > 0) Print("Order #",ticket," opened successfully");
   else Print("Final order failed: ", ErrorDescription(GetLastError()));
}

//+------------------------------------------------------------------+
//| Calculate proper lot size                                        |
//+------------------------------------------------------------------+
double CalculateLotSize(string symbol, double riskPercent, double stopLossDistance)
{
   if(riskPercent <= 0) return 0;
   double accountEquity = AccountEquity();
   if(accountEquity <= 0) return 0;
   double riskAmount = accountEquity * (riskPercent / 100.0);
   double tickValue = MarketInfo(symbol, MODE_TICKVALUE);
   double tickSize = MarketInfo(symbol, MODE_TICKSIZE);
   if(tickValue <= 0 || tickSize <= 0 || stopLossDistance <= 0){ Print("Chyba: Neplatná data pro výpočet lotu (tickValue/tickSize/stopLoss)"); return 0;}
   double stopLossPips = stopLossDistance / tickSize;
   double lotSize = riskAmount / (stopLossPips * tickValue);
   double minLot = MarketInfo(symbol, MODE_MINLOT);
   double maxLot = MarketInfo(symbol, MODE_MAXLOT);
   double lotStep = MarketInfo(symbol, MODE_LOTSTEP);
   lotSize = MathRound(lotSize / lotStep) * lotStep;
   lotSize = MathMax(minLot, MathMin(maxLot, lotSize));
   return NormalizeDouble(lotSize, 2);
}

//+------------------------------------------------------------------+
//| Get magic number offset for TP levels                            |
//+------------------------------------------------------------------+
int GetTPMagicOffset(string tpLabel)
{
   if(tpLabel == "TP1") return 0;
   if(tpLabel == "TP2") return 1;
   if(tpLabel == "TP3") return 2;
   return 0;
}
bool ValidateSymbol(string symbol)
{
   if(StringLen(symbol) < 2) return false;
   if(!SymbolSelect(symbol, true))
   {
      if(StringFind(symbol, "XAU") >= 0 || StringFind(symbol, "GOLD") >= 0)
      {
         string goldSymbols[] = {"XAUUSD", "XAU/USD", "GOLD"};
         for(int i=0; i<ArraySize(goldSymbols); i++)
         {
            if(SymbolSelect(goldSymbols[i], true)) return true;
         }
      }
      return false;
   }
   if(MarketInfo(symbol, MODE_TICKVALUE) <= 0) return false;
   return true;
}
void HandleOrderError(int &retryCount, string symbol, int orderType, int digits) // Placeholder, nutno implementovat
{
 Print("HandleOrderError called for attempt ", retryCount);
}
*/
//+------------------------------------------------------------------+
