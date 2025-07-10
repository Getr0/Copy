//+------------------------------------------------------------------+
//|                                  SignalCopierPro.mq4             |
//|                        Copyright 2023, MetaQuotes Software Corp. |
//|                                             https://www.metaquotes.net/ |
//+------------------------------------------------------------------+
#property copyright "Copyright 2023, SignalCopierPro"
#property link      "https://www.yourwebsite.com"
#property version   "1.52"  // Corrected compilation errors
#property strict

#include <stdlib.mqh> // Standard library

// --- Structures for Signal and Function Data ---
struct SignalData { // Holds data parsed from the main /signals endpoint
    string symbol;
    string action;
    double entry;
    double sl;
    double tp1; // Primary TP from signal (tp_value)
    double tp2; // Optional second TP from signal (tp2_value)
    double tp3; // Fallback, rarely used now
    datetime timestamp;
    string status;    // VALID/INVALID (signal data validity)
    string comment;   // Reason for invalid status
    string signalId;  // Database ID of the signal
    string tradeStatus; // NEW, RUNNING, PARTIAL, CLOSED (display only)

    // Fields from signals table for BE/TS defaults (used if no specific function found for ticket)
    bool be_active_default;
    bool ts_active_default;
    string be_trigger_condition_type_default;
    int be_trigger_target_ticket_default;
    string ts_trigger_condition_type_default;
    int ts_trigger_target_ticket_default;
    double ts_start_pips_default;
    double ts_step_pips_default;
    double ts_distance_pips_default;
    bool is_tp1_for_be_ts; // Is this signal a TP1 that triggers BE/TS for a TP2
};

struct TradeFunction { // Holds data for a specific function fetched from /active_trade_functions
    string function_type; // "BE", "TS"
    string ts_type;       // "CLASSIC", "CONVERGENT" (if function_type is "TS")
    double tp_target_price; // For Convergent TS

    // Parameters parsed from params_json
    // BE Params
    double be_offset_pips;
    // Classic TS Params
    double classic_ts_start_pips;
    double classic_ts_step_pips;
    double classic_ts_distance_pips;
    // Convergent TS Params
    double convergent_activation_start_pips;
    double convergent_converge_factor;
    double convergent_min_stop_distance_pips;
};

// --- Function Prototypes ---
string FetchAPIData(string urlPath);
void UpdateConnectionStatus(bool connected, string message = "");
void InitializeSignalDisplay();
void UpdateSignalDisplay();
void AddSignalToHistory(SignalData &signal);
void CheckForNewSignals();
void ProcessSingleSignalFromJson(string jsonSignalObject);
void ExecuteTrade(string symbol, string action, double entryPrice, double stopLoss,
                 double takeProfit, double riskPercent, string tpLabel, string originalSignalId);
double CalculateLotSize(string symbol, double riskPercent, double stopLossDistance);
int GetTPMagicOffset(string tpLabel);
string GetJsonValue(string& json, string key);
bool GetBoolJsonValue(string& json, string key);
double GetDoubleJsonValue(string& json, string key);
int GetIntJsonValue(string& json, string key);
string ErrorDescription(int error);
string StringUpper(string str);
string GetFullSymbol(string baseSymbol);
// BE/TS Logic Prototypes
void ManageTradeFunctions(int ticket);
void ApplyBreakEven(int ticket, TradeFunction &func);
void ApplyTrailingStop_Classic(int ticket, TradeFunction &func);
void ApplyTrailingStop_Convergent(int ticket, TradeFunction &func);
// BE/TS Helper Prototypes
double PipsToPrice(string symbol, double pips);
double PriceToPips(string symbol, double price_difference);
bool IsTradeClosedInProfit(int ticket_to_check);
void SendReportToServer(string path, string jsonData);


// --- WinINet DLL Imports ---
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
#define INTERNET_OPEN_TYPE_DIRECT               1
#define INTERNET_SERVICE_HTTP                   3
#define INTERNET_FLAG_RELOAD                    0x80000000
#define INTERNET_FLAG_NO_CACHE_WRITE            0x04000000
//#define INTERNET_FLAG_KEEP_CONNECTION           0x00400000 // Removed as it can cause issues
#define INTERNET_OPTION_RECEIVE_TIMEOUT         6
#define INTERNET_OPTION_SEND_TIMEOUT            5
#define INTERNET_OPTION_CONNECT_TIMEOUT         2

// --- EA Input Parameters ---
input string InpServerURL = "localhost";
input string InpSignalAPIPath = "/signals";
input string InpFunctionAPIPath = "/active_trade_functions";
input string InpReportTradeAPIPath = "/report_trade";
input int InpServerPort = 5000;
input int InpCheckInterval = 15;
input double InpRiskPercentTP1 = 1.0;
input double InpRiskPercentTP2 = 0.5;
input int InpSlippagePoints = 3;
input bool InpEnableTrading = true;
input bool InpAlertsOnly = false;
input int InpMagicNumber = 123456;
input string InpSymbolPrefix = "";
input string InpSymbolSuffix = "";
input bool InpUseMarketOrders = true;
input bool InpEnableBEGlobally = true;
input bool InpEnableTSGlobally = true;
input double InpDefaultTS_StartPips = 20.0;
input double InpDefaultTS_StepPips = 10.0;
input double InpDefaultTS_DistancePips = 15.0;
input double InpDefaultBE_OffsetPips = 1.0;
input double InpDefaultConvTS_ActivationPips = 30.0;
input double InpDefaultConvTS_ConvergeFactor = 0.5;
input double InpDefaultConvTS_MinStopDistPips = 10.0;
input bool InpDebugMode = true;
input int InpTimeoutMilliseconds = 5000;

// --- Display Settings & Colors ---
#define SIGNAL_HISTORY_SIZE 5
#define SIGNAL_START_Y 50
#define SIGNAL_ROW_HEIGHT 20
color ColorBuySignal = clrLimeGreen;        // Corrected global color name
color ColorSellSignal = clrTomato;          // Corrected global color name
color ColorSignalInvalid = clrGray;       // Corrected global color name
color ColorSignalValid = clrWhite;        // Corrected global color name (though not directly used for row, good for consistency)
color ColorSignalNeutral = clrLightGray;  // Corrected global color name
color ColorSignalClosed = clrDimGray;     // Corrected global color name
color ColorStatusOnline = clrLimeGreen;     // Corrected global color name
color ColorStatusOffline = clrRed;        // Corrected global color name


// --- Global Variables ---
SignalData G_SignalHistory[SIGNAL_HISTORY_SIZE];
string G_StatusLabelName = "CopierStatusLabel";
datetime G_LastSuccessTime = 0;
datetime G_LastCheckTime = 0;
string G_ProcessedSignalIds[];
int G_MaxStoredSignalIds = 200;

#define MAX_ACTIVE_TRADES_MANAGED 50
int G_ManagedTickets[MAX_ACTIVE_TRADES_MANAGED];
bool G_BE_Applied[MAX_ACTIVE_TRADES_MANAGED];
bool G_TS_Active[MAX_ACTIVE_TRADES_MANAGED];
double G_TS_LastSetSL[MAX_ACTIVE_TRADES_MANAGED];
int G_NextManagedTicketSlot = 0;


//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if(!TerminalInfoInteger(TERMINAL_DLLS_ALLOWED)) {
      Alert("OnInit Error: DLL calls not allowed! Enable in Tools > Options > Expert Advisors.");
      return(INIT_FAILED);
   }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && InpEnableTrading && !InpAlertsOnly) {
      Alert("OnInit Error: Automated trading disabled in Terminal/EA settings, but EA is set to trade!");
      return(INIT_FAILED);
   }
   // Corrected TERMINAL_WEBRequest_ALLOWED to TERMINAL_WEBREQUEST_ALLOWED
   if(!TerminalInfoInteger(TERMINAL_WEBREQUEST_ALLOWED)){
        Alert("OnInit Error: WebRequest not allowed! Please enable in Terminal settings (Tools > Options > Expert Advisors -> Allow WebRequest for listed URL) and add http://",InpServerURL);
        return(INIT_FAILED);
    }

   // Corrected AccountInfoString(ACCOUNT_BUILD_NUMBER) to TerminalInfoInteger(TERMINAL_BUILD_NUMBER)
   if(InpDebugMode) Print("SignalCopierPro EA v", TerminalInfoInteger(TERMINAL_BUILD_NUMBER), " Initializing... Server: http://",InpServerURL,":",InpServerPort);

   ArrayResize(G_ProcessedSignalIds, 0);

   for(int i=0; i<MAX_ACTIVE_TRADES_MANAGED; i++) {
       G_ManagedTickets[i] = 0; G_BE_Applied[i] = false; G_TS_Active[i] = false; G_TS_LastSetSL[i] = 0.0;
   }
   G_NextManagedTicketSlot = 0;

   ObjectCreate(0, G_StatusLabelName, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, G_StatusLabelName, OBJPROP_XDISTANCE, 10); ObjectSetInteger(0, G_StatusLabelName, OBJPROP_YDISTANCE, 20);
   ObjectSetInteger(0, G_StatusLabelName, OBJPROP_CORNER, CORNER_LEFT_UPPER); ObjectSetInteger(0, G_StatusLabelName, OBJPROP_FONTSIZE, 10);
   ObjectSetInteger(0, G_StatusLabelName, OBJPROP_BACK, false);

   InitializeSignalDisplay();
   UpdateConnectionStatus(false, "Waiting for first connection...");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(InpDebugMode) Print("EA Deinitialization. Reason: ", reason);
   ArrayResize(G_ProcessedSignalIds, 0);
   ObjectDelete(0, G_StatusLabelName); ObjectDelete(0, "SignalHeader");
   for(int i = 0; i < SIGNAL_HISTORY_SIZE; i++) ObjectDelete(0, "SignalRow"+IntegerToString(i));
   Print("SignalCopierPro EA Deinitialized.");
}

//+------------------------------------------------------------------+
//| Get or create index for tracking BE/TS states for a ticket     |
//+------------------------------------------------------------------+
int GetManagedTicketIndex(int ticket, bool createIfNotFound = true) {
    if (ticket <= 0) return -1;
    for (int i = 0; i < G_NextManagedTicketSlot; i++) {
        if (G_ManagedTickets[i] == ticket) return i;
    }
    if (!createIfNotFound) return -1;

    if (G_NextManagedTicketSlot < MAX_ACTIVE_TRADES_MANAGED) {
        G_ManagedTickets[G_NextManagedTicketSlot] = ticket;
        G_BE_Applied[G_NextManagedTicketSlot] = false;
        G_TS_Active[G_NextManagedTicketSlot] = false;
        G_TS_LastSetSL[G_NextManagedTicketSlot] = 0.0;
        int newIndex = G_NextManagedTicketSlot;
        G_NextManagedTicketSlot++;
        if(InpDebugMode) Print("Now managing BE/TS for ticket #", ticket, " at index ", newIndex);
        return newIndex;
    }
    Print("Error: MAX_ACTIVE_TRADES_MANAGED (",MAX_ACTIVE_TRADES_MANAGED,") reached. Cannot manage new ticket: ", ticket);
    return -1;
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   static datetime lastTickProcessingTime_Signals;
   static datetime lastTickProcessingTime_Functions;

   if(IsStopped()) return;

   if(TimeCurrent() - lastTickProcessingTime_Signals >= 1) {
        UpdateSignalDisplay();
        lastTickProcessingTime_Signals = TimeCurrent();
   }

   if(TimeCurrent() - G_LastCheckTime >= InpCheckInterval) {
      CheckForNewSignals();
      G_LastCheckTime = TimeCurrent();
   }

   if(!InpEnableTrading && InpAlertsOnly) return;
   if(!InpEnableTrading && !InpAlertsOnly) return;

   if(TimeCurrent() - lastTickProcessingTime_Functions >= 2)
   {
       for(int i = OrdersTotal() - 1; i >= 0; i--) {
          if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
          if(OrderSymbol() != Symbol()) continue;
          if(OrderMagicNumber() >= InpMagicNumber && OrderMagicNumber() <= InpMagicNumber + 10) {
             ManageTradeFunctions(OrderTicket());
          }
       }
       lastTickProcessingTime_Functions = TimeCurrent();
   }
}

//+------------------------------------------------------------------+
//| Manage BE and TS functions for a specific ticket                 |
//+------------------------------------------------------------------+
void ManageTradeFunctions(int ticket)
{
    if(!OrderSelect(ticket, SELECT_BY_TICKET)) {
        return;
    }

    int trackedIdx = GetManagedTicketIndex(ticket, true);
    if(trackedIdx < 0) return;

    string functionsJson = FetchAPIData(InpFunctionAPIPath + "?ticket_id=" + IntegerToString(ticket));
    if(StringLen(functionsJson) == 0 || StringGetCharacter(functionsJson,0) != '[') {
        return;
    }

    string remainingJson = functionsJson;
    if(InpDebugMode && StringLen(functionsJson)>2) Print("Ticket #",ticket," Active Functions JSON: ", functionsJson);

    int objectDepth = 0;
    int objScanStart = 0;
    int currentPos = 1;

    while(currentPos < StringLen(remainingJson)) {
        ushort charCurrent = StringGetCharacter(remainingJson, currentPos);
        if(charCurrent == '{') {
            if(objectDepth == 0) objScanStart = currentPos;
            objectDepth++;
        } else if (charCurrent == '}') {
            objectDepth--;
            if(objectDepth == 0 && objScanStart > 0) {
                string funcJson = StringSubstr(remainingJson, objScanStart, currentPos - objScanStart + 1);
                if(StringLen(funcJson) > 0) {
                    TradeFunction currentFunc; ZeroMemory(currentFunc);
                    currentFunc.function_type = GetJsonValue(funcJson, "function_type");

                    string paramsJsonStr = GetJsonValue(funcJson, "params");

                    if(currentFunc.function_type == "BE") {
                        if(!G_BE_Applied[trackedIdx]) {
                            currentFunc.be_offset_pips = GetDoubleJsonValue(paramsJsonStr, "offset_pips");
                            if(currentFunc.be_offset_pips == 0 && InpDefaultBE_OffsetPips != 0) currentFunc.be_offset_pips = InpDefaultBE_OffsetPips;
                            ApplyBreakEven(ticket, currentFunc);
                        }
                    } else if (currentFunc.function_type == "TS") {
                        currentFunc.ts_type = GetJsonValue(funcJson, "ts_type");
                        currentFunc.tp_target_price = GetDoubleJsonValue(funcJson, "tp_target_price");

                        if(currentFunc.ts_type == "CLASSIC") {
                            currentFunc.classic_ts_start_pips = GetDoubleJsonValue(paramsJsonStr, "trail_start_pips");
                            currentFunc.classic_ts_step_pips = GetDoubleJsonValue(paramsJsonStr, "trail_step_pips");
                            currentFunc.classic_ts_distance_pips = GetDoubleJsonValue(paramsJsonStr, "trail_distance_pips");
                            ApplyTrailingStop_Classic(ticket, currentFunc);
                        } else if (currentFunc.ts_type == "CONVERGENT") {
                            currentFunc.convergent_activation_start_pips = GetDoubleJsonValue(paramsJsonStr, "activation_start_pips");
                            currentFunc.convergent_converge_factor = GetDoubleJsonValue(paramsJsonStr, "converge_factor");
                            currentFunc.convergent_min_stop_distance_pips = GetDoubleJsonValue(paramsJsonStr, "min_stop_distance_pips");
                            ApplyTrailingStop_Convergent(ticket, currentFunc);
                        }
                    }
                }
                objScanStart = 0;
            } else if (objectDepth <0) break;
        }
        currentPos++;
    }
}

//+------------------------------------------------------------------+
//| Apply Breakeven                                                  |
//+------------------------------------------------------------------+
void ApplyBreakEven(int ticket, TradeFunction &func)
{
    if(!OrderSelect(ticket, SELECT_BY_TICKET)) return;
    int trackedIdx = GetManagedTicketIndex(ticket, false);
    if(trackedIdx < 0 || G_BE_Applied[trackedIdx]) return;

    double point = SymbolInfoDouble(OrderSymbol(), SYMBOL_POINT);
    int digits = (int)SymbolInfoInteger(OrderSymbol(), SYMBOL_DIGITS);
    double offsetPrice = PipsToPrice(OrderSymbol(), func.be_offset_pips);
    double newSL = OrderOpenPrice();

    if(OrderType() == OP_BUY) newSL = OrderOpenPrice() + offsetPrice;
    else if(OrderType() == OP_SELL) newSL = OrderOpenPrice() - offsetPrice;

    newSL = NormalizeDouble(newSL, digits);

    if(MathAbs(OrderStopLoss() - newSL) > point) {
        RefreshRates();
        if(OrderModify(ticket, OrderOpenPrice(), newSL, OrderTakeProfit(), 0, clrNONE)) {
            G_BE_Applied[trackedIdx] = true;
            Print("BE Applied: Ticket #", ticket, " SL set to ", DoubleToString(newSL, digits));
        } else {
            Print("BE Modify Failed: Ticket #", ticket, " Error: ", ErrorDescription(GetLastError()));
        }
    } else {
         G_BE_Applied[trackedIdx] = true;
         if(InpDebugMode) Print("BE: Ticket #",ticket," SL already at or near BE target ", DoubleToString(newSL, digits));
    }
}

//+------------------------------------------------------------------+
//| Apply Classic Trailing Stop                                      |
//+------------------------------------------------------------------+
void ApplyTrailingStop_Classic(int ticket, TradeFunction &func)
{
    if(!OrderSelect(ticket, SELECT_BY_TICKET)) return;
    int trackedIdx = GetManagedTicketIndex(ticket, false);
    if(trackedIdx < 0) return;

    double point = SymbolInfoDouble(OrderSymbol(), SYMBOL_POINT);
    int digits = (int)SymbolInfoInteger(OrderSymbol(), SYMBOL_DIGITS);
    RefreshRates();
    double currentBid = SymbolInfoDouble(OrderSymbol(), SYMBOL_BID);
    double currentAsk = SymbolInfoDouble(OrderSymbol(), SYMBOL_ASK);

    double startPips = (func.classic_ts_start_pips > 0) ? func.classic_ts_start_pips : InpDefaultTS_StartPips;
    double stepPips = (func.classic_ts_step_pips > 0) ? func.classic_ts_step_pips : InpDefaultTS_StepPips;
    double distPips = (func.classic_ts_distance_pips > 0) ? func.classic_ts_distance_pips : InpDefaultTS_DistancePips;

    double startPrice = PipsToPrice(OrderSymbol(), startPips);
    double stepPrice = PipsToPrice(OrderSymbol(), stepPips);
    double distPrice = PipsToPrice(OrderSymbol(), distPips);

    double currentSL = OrderStopLoss();
    double newSL = currentSL;

    if(!G_TS_Active[trackedIdx]) {
        if(OrderType() == OP_BUY && (currentBid - OrderOpenPrice()) >= startPrice) G_TS_Active[trackedIdx] = true;
        else if(OrderType() == OP_SELL && (OrderOpenPrice() - currentAsk) >= startPrice) G_TS_Active[trackedIdx] = true;

        if(G_TS_Active[trackedIdx]) {
             if(InpDebugMode) Print("Classic TS Activated for #",ticket,". StartPips:",startPips);
             G_TS_LastSetSL[trackedIdx] = currentSL;
        }
    }

    if(G_TS_Active[trackedIdx]) {
        if(OrderType() == OP_BUY) {
            double potentialSL = NormalizeDouble(currentBid - distPrice, digits);
            if(potentialSL > currentSL) {
                if(G_TS_LastSetSL[trackedIdx] == 0 || (potentialSL - G_TS_LastSetSL[trackedIdx]) >= stepPrice) {
                    newSL = potentialSL;
                }
            }
        } else if (OrderType() == OP_SELL) {
            double potentialSL = NormalizeDouble(currentAsk + distPrice, digits);
            if(potentialSL < currentSL || currentSL == 0) {
                 if(G_TS_LastSetSL[trackedIdx] == 0 || (G_TS_LastSetSL[trackedIdx] - potentialSL) >= stepPrice) {
                    newSL = potentialSL;
                }
            }
        }

        if(newSL != currentSL && newSL != 0) {
            double minStopDist = SymbolInfoInteger(OrderSymbol(), SYMBOL_TRADE_STOPS_LEVEL) * point;
            bool isValidSL = false;
            if(OrderType()==OP_BUY && newSL < currentBid - minStopDist && newSL >= OrderOpenPrice()) isValidSL = true;
            if(OrderType()==OP_SELL && newSL > currentAsk + minStopDist && newSL <= OrderOpenPrice()) isValidSL = true;

            if(isValidSL) {
                if(OrderModify(ticket, OrderOpenPrice(), newSL, OrderTakeProfit(), 0, clrNONE)) {
                    G_TS_LastSetSL[trackedIdx] = newSL;
                    if(InpDebugMode) Print("Classic TS: #",ticket," SL moved to ",DoubleToString(newSL,digits));
                } else {
                    if(InpDebugMode) Print("Classic TS Modify Failed: #",ticket," to SL ",DoubleToString(newSL,digits),". Error: ",ErrorDescription(GetLastError()));
                }
            } else {
                 if(InpDebugMode && newSL != currentSL) Print("Classic TS: Proposed SL ",DoubleToString(newSL,digits)," for #",ticket," invalid (StopLevel/BE).");
            }
        }
    }
}

//+------------------------------------------------------------------+
//| Apply Convergent Trailing Stop                                   |
//+------------------------------------------------------------------+
void ApplyTrailingStop_Convergent(int ticket, TradeFunction &func)
{
    if(!OrderSelect(ticket, SELECT_BY_TICKET)) return;
    int trackedIdx = GetManagedTicketIndex(ticket, false);
    if(trackedIdx < 0) return;

    double tpTarget = func.tp_target_price;
    if(tpTarget == 0) {
        if(InpDebugMode) Print("Convergent TS Error: TP Target price is 0 for ticket #", ticket);
        return;
    }

    double point = SymbolInfoDouble(OrderSymbol(), SYMBOL_POINT);
    int digits = (int)SymbolInfoInteger(OrderSymbol(), SYMBOL_DIGITS);
    RefreshRates();
    double currentBid = SymbolInfoDouble(OrderSymbol(), SYMBOL_BID);
    double currentAsk = SymbolInfoDouble(OrderSymbol(), SYMBOL_ASK);
    double currentPrice = (OrderType() == OP_BUY) ? currentBid : currentAsk;

    double activationPips = (func.convergent_activation_start_pips > 0) ? func.convergent_activation_start_pips : InpDefaultConvTS_ActivationPips;
    double convergeFactor = (func.convergent_converge_factor > 0 && func.convergent_converge_factor <=1) ? func.convergent_converge_factor : InpDefaultConvTS_ConvergeFactor;
    double minStopDistPips = (func.convergent_min_stop_distance_pips > 0) ? func.convergent_min_stop_distance_pips : InpDefaultConvTS_MinStopDistPips;

    double activationPrice = PipsToPrice(OrderSymbol(), activationPips);
    double minStopDistPrice = PipsToPrice(OrderSymbol(), minStopDistPips);

    double currentSL = OrderStopLoss();
    double newSL = currentSL;

    if(!G_TS_Active[trackedIdx]) {
        if(OrderType() == OP_BUY && (currentPrice - OrderOpenPrice()) >= activationPrice) G_TS_Active[trackedIdx] = true;
        else if(OrderType() == OP_SELL && (OrderOpenPrice() - currentPrice) >= activationPrice) G_TS_Active[trackedIdx] = true;

        if(G_TS_Active[trackedIdx]) {
            if(InpDebugMode) Print("Convergent TS Activated for #",ticket,". ActivationPips:", activationPips);
        }
    }

    if(G_TS_Active[trackedIdx]) {
        double distanceToTP = MathAbs(tpTarget - currentPrice);
        double trailingDistance = distanceToTP * convergeFactor;
        if(trailingDistance < minStopDistPrice) trailingDistance = minStopDistPrice;

        if(OrderType() == OP_BUY) {
            newSL = NormalizeDouble(currentPrice - trailingDistance, digits);
            if(newSL <= currentSL) newSL = currentSL;
        } else if (OrderType() == OP_SELL) {
            newSL = NormalizeDouble(currentPrice + trailingDistance, digits);
            if(newSL >= currentSL && currentSL != 0) newSL = currentSL;
        }

        if(OrderType() == OP_BUY && newSL >= tpTarget) newSL = NormalizeDouble(tpTarget - minStopDistPrice, digits);
        if(OrderType() == OP_SELL && newSL <= tpTarget) newSL = NormalizeDouble(tpTarget + minStopDistPrice, digits);


        if(newSL != currentSL && newSL != 0) {
            double minStopLevelDist = SymbolInfoInteger(OrderSymbol(), SYMBOL_TRADE_STOPS_LEVEL) * point;
            bool isValidSL = false;
            if(OrderType()==OP_BUY && newSL < currentBid - minStopLevelDist && newSL >= OrderOpenPrice()) isValidSL = true;
            if(OrderType()==OP_SELL && newSL > currentAsk + minStopLevelDist && newSL <= OrderOpenPrice()) isValidSL = true;

            if(isValidSL) {
                if(OrderModify(ticket, OrderOpenPrice(), newSL, OrderTakeProfit(), 0, clrNONE)) {
                     if(InpDebugMode) Print("Convergent TS: #",ticket," SL moved to ",DoubleToString(newSL,digits), ". DistToTP:",DoubleToString(distanceToTP,digits), " TrailDist:",DoubleToString(trailingDistance,digits));
                } else {
                     if(InpDebugMode) Print("Convergent TS Modify Failed: #",ticket," to SL ",DoubleToString(newSL,digits),". Error: ",ErrorDescription(GetLastError()));
                }
            } else {
                 if(InpDebugMode && newSL != currentSL) Print("Convergent TS: Proposed SL ",DoubleToString(newSL,digits)," for #",ticket," invalid (StopLevel/BE/TP).");
            }
        }
    }
}
//+------------------------------------------------------------------+
//| Initialize signal display objects on chart                       |
//+------------------------------------------------------------------+
void InitializeSignalDisplay()
{
    string headerName = "SignalHeader";
    ObjectCreate(0, headerName, OBJ_LABEL, 0, 0, 0);
    ObjectSetInteger(0, headerName, OBJPROP_XDISTANCE, 10);
    ObjectSetInteger(0, headerName, OBJPROP_YDISTANCE, SIGNAL_START_Y + 20);
    ObjectSetInteger(0, headerName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
    ObjectSetInteger(0, headerName, OBJPROP_COLOR, clrWhite);
    ObjectSetInteger(0, headerName, OBJPROP_FONTSIZE, 8);
    ObjectSetString(0, headerName, OBJPROP_TEXT, "TIME  | SYMBOL  | ACTION | STATUS | TRADE  | ENTRY    | SL       | TP1      | TP2      | TP3");

    for(int i = 0; i < SIGNAL_HISTORY_SIZE; i++) {
        string objName = "SignalRow"+IntegerToString(i);
        ObjectCreate(0, objName, OBJ_LABEL, 0, 0, 0);
        ObjectSetInteger(0, objName, OBJPROP_XDISTANCE, 10);
        ObjectSetInteger(0, objName, OBJPROP_YDISTANCE, SIGNAL_START_Y + 20 + ((i+1)*SIGNAL_ROW_HEIGHT));
        ObjectSetInteger(0, objName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
        ObjectSetInteger(0, objName, OBJPROP_COLOR, ColorSignalNeutral);
        ObjectSetInteger(0, objName, OBJPROP_FONTSIZE, 8);
        ObjectSetString(0, objName, OBJPROP_TEXT, "- Empty -");
    }
    if(InpDebugMode) Print("Signal display initialized.");
}

//+------------------------------------------------------------------+
//| Update signal display on chart                                   |
//+------------------------------------------------------------------+
void UpdateSignalDisplay()
{
    UpdateTradeStatuses();

    string headerName = "SignalHeader";
    if(ObjectFind(0,headerName) < 0) InitializeSignalDisplay();

    ObjectSetString(0, headerName, OBJPROP_TEXT, "TIME  | SYMBOL  | ACTION | STATUS | TRADE  | ENTRY    | SL       | TP1      | TP2      | TP3");

    for(int i = 0; i < SIGNAL_HISTORY_SIZE; i++) {
        string objName = "SignalRow"+IntegerToString(i);
        if(ObjectFind(0,objName) < 0) { InitializeSignalDisplay(); break; }

        string text = "- Empty -";
        color rowColor = clrDarkGray;

        if(StringLen(G_SignalHistory[i].signalId) > 0) {
            int d = (int)SymbolInfoInteger(G_SignalHistory[i].symbol, SYMBOL_DIGITS);
            if (d==0 && StringLen(G_SignalHistory[i].symbol)>0) d=(int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);
            else if (d==0) d=Digits;


            string timeStr = StringFormat("%-5s", TimeToString(G_SignalHistory[i].timestamp, TIME_MINUTES));
            string symbolStr = StringFormat("%-7s", StringSubstr(G_SignalHistory[i].symbol, 0, 7));
            string actionStr = StringFormat("%-5s", G_SignalHistory[i].action);
            string statusStr = StringFormat("%-6s", G_SignalHistory[i].status);
            string tradeStatusStr = StringFormat("%-6s", G_SignalHistory[i].tradeStatus);

            string entryStr = (G_SignalHistory[i].entry > 0) ? StringFormat("%-8s", DoubleToString(G_SignalHistory[i].entry, d)) : "N/A     ";
            string slStr = (G_SignalHistory[i].sl > 0) ? StringFormat("%-8s", DoubleToString(G_SignalHistory[i].sl, d)) : "N/A     ";
            string tp1Str = (G_SignalHistory[i].tp1 > 0) ? StringFormat("%-8s", DoubleToString(G_SignalHistory[i].tp1, d)) : "N/A     ";
            string tp2Str = (G_SignalHistory[i].tp2 > 0) ? StringFormat("%-8s", DoubleToString(G_SignalHistory[i].tp2, d)) : "N/A     ";
            string tp3Str = (G_SignalHistory[i].tp3 > 0) ? StringFormat("%-8s", DoubleToString(G_SignalHistory[i].tp3, d)) : "N/A     ";

            text = StringFormat("%s | %s | %s | %s | %s | %s | %s | %s | %s | %s",
                timeStr, symbolStr, actionStr, statusStr, tradeStatusStr,
                entryStr, slStr, tp1Str, tp2Str, tp3Str);

            if(G_SignalHistory[i].status == "VALID") {
                if(G_SignalHistory[i].tradeStatus == "RUNNING" || G_SignalHistory[i].tradeStatus == "PARTIAL") {
                    rowColor = (G_SignalHistory[i].action == "BUY") ? ColorBuySignal : ColorSellSignal;
                } else if(G_SignalHistory[i].tradeStatus == "CLOSED") {
                    rowColor = ColorSignalClosed;
                } else {
                    rowColor = ColorSignalNeutral;
                }
            } else {
                rowColor = ColorSignalInvalid;
            }
        }
        ObjectSetString(0, objName, OBJPROP_TEXT, text);
        ObjectSetInteger(0, objName, OBJPROP_COLOR, rowColor);
    }
}

//+------------------------------------------------------------------+
//| Add new signal to history (circular buffer)                      |
//+------------------------------------------------------------------+
void AddSignalToHistory(SignalData &signal)
{
    for(int i = SIGNAL_HISTORY_SIZE-1; i > 0; i--) {
        G_SignalHistory[i] = G_SignalHistory[i-1];
    }
    G_SignalHistory[0] = signal;
    if(InpDebugMode) Print("Added SigID ", signal.signalId, " to display history. Status: ", signal.status, ", Action: ", signal.action);
}

//+------------------------------------------------------------------+
//| Fetch data from API                                              |
//+------------------------------------------------------------------+
string FetchAPIData(string path)
{
   if(IsStopped()) return "";
   if(InpDebugMode) Print("Fetching data from: http://",InpServerURL,":",InpServerPort,path);

   string response = "";
   int hInternet = InternetOpenW("MQL4_EA_SignalCopierPro/1.52", INTERNET_OPEN_TYPE_DIRECT, NULL, NULL, 0);
   if(hInternet == 0) {
      Print("InternetOpenW failed. Error: ", GetLastError());
      UpdateConnectionStatus(false, "InternetOpenW failed");
      return "";
   }

   int timeout[1]; timeout[0] = InpTimeoutMilliseconds;
   InternetSetOptionW(hInternet, INTERNET_OPTION_CONNECT_TIMEOUT, timeout, 4);

   int hConnect = InternetConnectW(hInternet, InpServerURL, InpServerPort, NULL, NULL, INTERNET_SERVICE_HTTP, 0, 0);
   if(hConnect == 0) {
      Print("InternetConnectW failed to ", InpServerURL, ":", InpServerPort, ". Error: ", GetLastError());
      InternetCloseHandle(hInternet);
      UpdateConnectionStatus(false, "Server connect failed");
      return "";
   }

   string acceptTypes[1]; acceptTypes[0] = "application/json";

   int hRequest = HttpOpenRequestW(hConnect, "GET", path, "HTTP/1.1", NULL, acceptTypes, INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE_WRITE , 0);
   if(hRequest == 0) {
      Print("HttpOpenRequestW failed for path '",path,"'. Error: ", GetLastError());
      InternetCloseHandle(hConnect); InternetCloseHandle(hInternet);
      UpdateConnectionStatus(false, "HTTP request failed");
      return "";
   }

   InternetSetOptionW(hRequest, INTERNET_OPTION_RECEIVE_TIMEOUT, timeout, 4);
   InternetSetOptionW(hRequest, INTERNET_OPTION_SEND_TIMEOUT, timeout, 4);

   if(!HttpSendRequestW(hRequest, NULL, 0, NULL, 0)) {
      Print("HttpSendRequestW failed for path '",path,"'. Error: ", GetLastError());
      InternetCloseHandle(hRequest); InternetCloseHandle(hConnect); InternetCloseHandle(hInternet);
      UpdateConnectionStatus(false, "Request send failed");
      return "";
   }

   uchar buffer[8192];
   uint bytesRead[1];
   string tempResponsePart = "";

   while(true) {
      ArrayInitialize(buffer,0); ArrayInitialize(bytesRead,0);
      if(!InternetReadFile(hRequest, buffer, sizeof(buffer)-1, bytesRead)) {
         int err = GetLastError();
         if(err != 0 && err != 12017) Print("InternetReadFile failed for path '",path,"'. Error: ", err);
         break;
      }
      if(bytesRead[0] == 0) break;

      tempResponsePart = CharArrayToString(buffer, 0, bytesRead[0]);
      response += tempResponsePart;
   }

   InternetCloseHandle(hRequest); InternetCloseHandle(hConnect); InternetCloseHandle(hInternet);

   if(StringLen(response) > 0) {
      G_LastSuccessTime = TimeCurrent();
      UpdateConnectionStatus(true, "Data received");
      if(InpDebugMode && path == InpSignalAPIPath) Print("API Signals Raw Response (len ",StringLen(response),"): ", StringSubstr(response,0, (StringLen(response)>500?500:StringLen(response)) ));
      return response;
   }

   UpdateConnectionStatus(false, "Empty response on "+path);
   return "";
}

//+------------------------------------------------------------------+
//| Check for new trading signals from server                        |
//+------------------------------------------------------------------+
void CheckForNewSignals()
{
   string response = FetchAPIData(InpSignalAPIPath);
   if(StringLen(response) == 0) return;

   if(StringGetCharacter(response,0) != '[' || StringGetCharacter(response, StringLen(response)-1) != ']') {
      Print("Error: Signals API response is not a valid JSON array. Response: ", StringSubstr(response,0,200));
      return;
   }
   if (StringLen(response) <= 2) {
        if(InpDebugMode) Print("CheckForNewSignals: Received empty signal array [].");
        return;
   }

   int currentPos = 1;
   int objectDepth = 0;
   int objScanStart = 0;

   while(currentPos < StringLen(response)) {
       ushort charCurrent = StringGetCharacter(response, currentPos);

       if(charCurrent == '{') {
           if(objectDepth == 0) objScanStart = currentPos;
           objectDepth++;
       } else if (charCurrent == '}') {
           objectDepth--;
           if(objectDepth == 0 && objScanStart > 0) {
               string singleJsonSignal = StringSubstr(response, objScanStart, currentPos - objScanStart + 1);
               if(StringLen(singleJsonSignal) > 0) {
                   ProcessSingleSignalFromJson(singleJsonSignal);
               }
               objScanStart = 0;
           } else if (objectDepth < 0) {
                Print("Error: Malformed JSON array in signals response - unexpected '}'. Response: ", StringSubstr(response,0,200));
                return;
           }
       }
       currentPos++;
   }
    if(objectDepth != 0 && InpDebugMode) Print("Warning: Signals JSON array parsing finished with non-zero object depth: ", objectDepth);
}

//+------------------------------------------------------------------+
//| Process Single JSON signal data object                           |
//+------------------------------------------------------------------+
void ProcessSingleSignalFromJson(string jsonStr)
{
   SignalData signal; ZeroMemory(signal);

   signal.signalId = GetJsonValue(jsonStr, "id");

   if(IsSignalProcessed(signal.signalId)) {
      return;
   }

   signal.action = StringUpper(GetJsonValue(jsonStr, "action"));
   signal.entry = GetDoubleJsonValue(jsonStr, "entry_price");

   string rawSymbol = GetJsonValue(jsonStr, "symbol");
   signal.symbol = GetFullSymbol(rawSymbol);
   int currentDigits = (int)SymbolInfoInteger(signal.symbol, SYMBOL_DIGITS);
   if(currentDigits == 0) currentDigits = Digits;

   double sl_value = GetDoubleJsonValue(jsonStr, "sl_value");
   string slTypeStr = StringUpper(GetJsonValue(jsonStr, "sl_value_type"));
   if(slTypeStr == "PIPS") {
       signal.sl = (signal.action == "BUY") ? signal.entry - PipsToPrice(signal.symbol, sl_value) : signal.entry + PipsToPrice(signal.symbol, sl_value);
   } else if (slTypeStr == "PRICE") {
       signal.sl = sl_value;
   } else { signal.sl = 0; }
   signal.sl = NormalizeDouble(signal.sl, currentDigits);

   signal.timestamp = TimeCurrent();

   double tp_value = GetDoubleJsonValue(jsonStr, "tp_value");
   string tpTypeStr = StringUpper(GetJsonValue(jsonStr, "tp_value_type"));
   if(tpTypeStr == "PIPS") {
       signal.tp1 = (signal.action == "BUY") ? signal.entry + PipsToPrice(signal.symbol, tp_value) : signal.entry - PipsToPrice(signal.symbol, tp_value);
   } else if (tpTypeStr == "PRICE") {
       signal.tp1 = tp_value;
   } else { signal.tp1 = 0; }
   signal.tp1 = NormalizeDouble(signal.tp1, currentDigits);

    signal.tp2 = GetDoubleJsonValue(jsonStr, "tp2_value");
    if(signal.tp2 != 0) signal.tp2 = NormalizeDouble(signal.tp2, currentDigits);
    signal.tp3 = 0;

   signal.be_active_default = GetBoolJsonValue(jsonStr, "be_active");
   signal.ts_active_default = GetBoolJsonValue(jsonStr, "ts_active");
   signal.be_trigger_target_ticket_default = GetIntJsonValue(jsonStr, "be_trigger_target_ticket");
   signal.ts_trigger_target_ticket_default = GetIntJsonValue(jsonStr, "ts_trigger_target_ticket");
   signal.is_tp1_for_be_ts = GetBoolJsonValue(jsonStr, "is_tp1_for_be_ts");

   signal.tradeStatus = "NEW";

   if(StringLen(signal.symbol) == 0) { signal.status = "INVALID"; signal.comment = "Empty symbol"; }
   else if (!SymbolSelect(signal.symbol, true) || MarketInfo(signal.symbol, MODE_TICKVALUE) <= 0) {
        signal.status = "INVALID"; signal.comment = "Symbol invalid/NA: " + signal.symbol;
   } else if(signal.entry <= 0 && (signal.action=="BUY" || signal.action=="SELL") && StringFind(GetJsonValue(jsonStr,"signal_type"),"RE_ENTRY")<0) {
      signal.status = "INVALID"; signal.comment = "Invalid entry price for market";
   } else if(signal.sl <= 0 ) {
      string signalTypeJson = GetJsonValue(jsonStr, "signal_type");
      if (StringFind(signalTypeJson, "RE_ENTRY") < 0 && StringFind(signalTypeJson,"UPDATE_T2") < 0 ) {
          signal.status = "INVALID"; signal.comment = "Stop loss is zero";
      } else {
          signal.status = "VALID"; signal.comment = "";
      }
   } else {
      signal.status = "VALID"; signal.comment = "";
   }

   if(InpDebugMode) {
      Print("Signal (ID:", signal.signalId, ") Parsed: Sym=", signal.symbol, ", Act=", signal.action,
            ", Entry=", DoubleToString(signal.entry,currentDigits), ", SL=", DoubleToString(signal.sl,currentDigits),
            ", TP1=", DoubleToString(signal.tp1,currentDigits), ", Valid:", signal.status,
            ", DefBE:", signal.be_active_default, "(TrigTkt:",signal.be_trigger_target_ticket_default,")",
            ", DefTS:", signal.ts_active_default, "(TrigTkt:",signal.ts_trigger_target_ticket_default,")",
            ", isTP1:", signal.is_tp1_for_be_ts);
   }

   AddSignalToHistory(signal);

   if(signal.status == "VALID" && InpEnableTrading && !InpAlertsOnly) {
      AddProcessedSignalId(signal.signalId);

      string tradeLabel = GetJsonValue(jsonStr, "trade_label");
      double riskToUse = 0;

      if (StringFind(tradeLabel, "T1") >= 0 || StringFind(tradeLabel, "STD") >=0 || StringFind(tradeLabel, "RE_ENTRY") >=0 ) riskToUse = InpRiskPercentTP1;
      else if (StringFind(tradeLabel, "T2") >= 0) riskToUse = InpRiskPercentTP2;
      else riskToUse = InpRiskPercentTP1;

      if ( (signal.tp1 > 0 || StringFind(tradeLabel,"RE_ENTRY")>=0) && riskToUse > 0) {
          ExecuteTrade(signal.symbol, signal.action, signal.entry, signal.sl, signal.tp1, riskToUse, tradeLabel, signal.signalId);
      } else {
          if(InpDebugMode) Print("Signal ID ", signal.signalId, " (",tradeLabel,") not traded: TP1=",signal.tp1," or Risk=",riskToUse);
      }
   } else if (signal.status == "VALID" && InpAlertsOnly) {
        Alert("Signal Alert (ID:",signal.signalId,"): ", signal.symbol, " ", signal.action, " @ ", DoubleToString(signal.entry, currentDigits),
              " SL: ", DoubleToString(signal.sl, currentDigits), " TP: ", DoubleToString(signal.tp1, currentDigits) );
        AddProcessedSignalId(signal.signalId);
   }
}

//+------------------------------------------------------------------+
//| Execute trade order (refined)                                    |
//+------------------------------------------------------------------+
void ExecuteTrade(string symbol, string action, double entryPrice, double stopLoss,
                 double takeProfit, double riskPercent, string tradeLabel, string originalSignalId)
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)){ Print("ExecuteTrade: Trading is disabled in terminal properties. SigID:",originalSignalId); return; }
   if(!InpEnableTrading || InpAlertsOnly) { Print("ExecuteTrade: Trading disabled by EA settings. SigID:",originalSignalId); return; }

   if(InpDebugMode) Print("Attempting trade for SigID: ", originalSignalId, ", Label: ", tradeLabel, ", Sym: ", symbol, ", Act: ", action, ", Entry: ", DoubleToString(entryPrice,Digits));

   if(!SymbolSelect(symbol, true)) { Print("Error selecting symbol ", symbol, " for SigID ", originalSignalId); return; }
   int currentDigits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   if(MarketInfo(symbol, MODE_TICKVALUE) <= 0) { Print("Error: Symbol ", symbol, " has invalid tick value. SigID ", originalSignalId); return; }

   int orderType;
   double priceForMarketOrder;
   bool isBuyAction = (StringFind(action, "BUY") >= 0);

   if(isBuyAction) {
      priceForMarketOrder = SymbolInfoDouble(symbol, SYMBOL_ASK);
      orderType = InpUseMarketOrders ? OP_BUY : OP_BUYLIMIT;
   } else {
      priceForMarketOrder = SymbolInfoDouble(symbol, SYMBOL_BID);
      orderType = InpUseMarketOrders ? OP_SELL : OP_SELLLIMIT;
   }

   double priceToUse = priceForMarketOrder;
   if(orderType == OP_BUYLIMIT || orderType == OP_SELLLIMIT) {
       if(entryPrice <= 0 && StringFind(tradeLabel,"RE_ENTRY")<0) {Print("Error: Limit order for ",originalSignalId," needs valid entry price."); return;}
       priceToUse = entryPrice;
       double minStopLevelDist = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL) * SymbolInfoDouble(symbol, SYMBOL_POINT);
       if(isBuyAction && entryPrice > priceForMarketOrder - minStopLevelDist) {
          if(InpDebugMode) Print("Buy Limit for SigID ", originalSignalId, " too close. Entry: ", DoubleToString(entryPrice,currentDigits), ", Ask: ", DoubleToString(priceForMarketOrder,currentDigits), ". Switching to Market.");
          orderType = OP_BUY; priceToUse = priceForMarketOrder;
      } else if (!isBuyAction && entryPrice < priceForMarketOrder + minStopLevelDist) {
          if(InpDebugMode) Print("Sell Limit for SigID ", originalSignalId, " too close. Entry: ", DoubleToString(entryPrice,currentDigits), ", Bid: ", DoubleToString(priceForMarketOrder,currentDigits), ". Switching to Market.");
          orderType = OP_SELL; priceToUse = priceForMarketOrder;
      }
   }
    if(priceToUse <=0 && StringFind(tradeLabel,"RE_ENTRY")<0) {Print("Error: Price to use is zero for non-reentry. SigID:",originalSignalId); return;}
    if(priceToUse <=0 && StringFind(tradeLabel,"RE_ENTRY")>=0) {
        priceToUse = isBuyAction ? SymbolInfoDouble(symbol, SYMBOL_ASK) : SymbolInfoDouble(symbol, SYMBOL_BID);
        if(InpDebugMode) Print("Re-entry SigID ",originalSignalId," using current market price: ", DoubleToString(priceToUse, currentDigits));
    }


   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double stopLevelPnts = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);

   if (stopLoss == 0 && StringFind(tradeLabel,"RE_ENTRY")<0 ) {
        Print("Error: Stop loss is 0 for new order (SigID:", originalSignalId,"). Trade not placed."); return;
   }
   if (stopLoss != 0) {
        if (isBuyAction && priceToUse - stopLoss < stopLevelPnts * point) {
            Print("Error: SL for BUY (SigID:",originalSignalId,") too close. Price:", priceToUse, " SL:", stopLoss); return;
        }
        if (!isBuyAction && stopLoss - priceToUse < stopLevelPnts * point) {
            Print("Error: SL for SELL (SigID:",originalSignalId,") too close. Price:", priceToUse, " SL:", stopLoss); return;
        }
   }

   if (takeProfit != 0) {
        if (isBuyAction && takeProfit - priceToUse < stopLevelPnts * point) {
            if(InpDebugMode) Print("TP for BUY (SigID:",originalSignalId,") too close (Price:",priceToUse," TP:",takeProfit,"). Disabling TP."); takeProfit = 0;
        }
        if (!isBuyAction && priceToUse - takeProfit < stopLevelPnts * point) {
            if(InpDebugMode) Print("TP for SELL (SigID:",originalSignalId,") too close (Price:",priceToUse," TP:",takeProfit,"). Disabling TP."); takeProfit = 0;
        }
   }

   double slDistance = (stopLoss == 0 && StringFind(tradeLabel,"RE_ENTRY")>=0) ? PipsToPrice(symbol, 50) : MathAbs(priceToUse - stopLoss);
   if (slDistance < stopLevelPnts * point && StringFind(tradeLabel,"RE_ENTRY")<0) {
       Print("Error: SL distance ", DoubleToString(slDistance, currentDigits), " too small for SigID ", originalSignalId, ". Min: ", DoubleToString(stopLevelPnts * point, currentDigits));
       return;
   }
    if (slDistance == 0 && StringFind(tradeLabel,"RE_ENTRY")<0) {Print("Error: SL distance is zero. SigID:", originalSignalId); return;}


   double lots = CalculateLotSize(symbol, riskPercent, slDistance);
   if(lots <= 0) { Print("Error: Lot size is 0 or less for SigID ", originalSignalId, ". SLDist: ",slDistance, " Risk%: ", riskPercent); return; }

   double normPrice = NormalizeDouble(priceToUse, currentDigits);
   double normSL = (stopLoss == 0 && StringFind(tradeLabel,"RE_ENTRY")>=0) ? 0 : NormalizeDouble(stopLoss, currentDigits);
   double normTP = (takeProfit == 0) ? 0 : NormalizeDouble(takeProfit, currentDigits);

   if(InpDebugMode) Print("Order Details for SigID ", originalSignalId, ": Type=", orderType, ", Price=", normPrice, ", SL=", normSL,
                       " TP=", normTP, ", Lots=", lots, ", Label=", tradeLabel);

   int ticket = -1;
   string comment = "SCP_"+tradeLabel+"_id"+originalSignalId;
   if(StringLen(comment) > 31) comment = StringSubstr(comment, 0, 31);

   int finalMagic = InpMagicNumber + GetTPMagicOffset(tradeLabel);

   RefreshRates();
   ticket = OrderSend(symbol, orderType, lots, normPrice, InpSlippagePoints, normSL, normTP, comment, finalMagic, 0,
                      (isBuyAction ? clrDodgerBlue : clrOrangeRed));

   if(ticket > 0) {
       Print("Order #",ticket," (Label:",tradeLabel,", SigID:",originalSignalId,") placed successfully.");
       // EA now needs to be able to report profit status on close for ON_CLOSE_TICKET condition
       string reportJson = StringFormat("{\"id\": \"%s\", \"ticket\": %d}", originalSignalId, ticket); // Ensure signalId is string in JSON
       SendReportToServer(InpReportTradeAPIPath, reportJson); // Report opening
   } else {
       Print("OrderSend failed for SigID:",originalSignalId," Label:",tradeLabel,". Error: ", ErrorDescription(GetLastError()));
   }
}

//+------------------------------------------------------------------+
//| Send generic report to Python server                             |
//+------------------------------------------------------------------+
void SendReportToServer(string path, string jsonData)
{
    if(IsStopped()) return;
    if(InpDebugMode) Print("Sending report to server path '", path, "': ", jsonData);

    string serverResponse = "";
    int hInternet = InternetOpenW("MQL4_EA_SignalCopierPro_Report/1.52", INTERNET_OPEN_TYPE_DIRECT, NULL, NULL, 0);
    if(hInternet == 0) { Print("Report: InternetOpenW failed: ", GetLastError()); return; }

    int timeout[1]; timeout[0] = InpTimeoutMilliseconds;
    InternetSetOptionW(hInternet, INTERNET_OPTION_CONNECT_TIMEOUT, timeout, 4);

    int hConnect = InternetConnectW(hInternet, InpServerURL, InpServerPort, NULL, NULL, INTERNET_SERVICE_HTTP, 0, 0);
    if(hConnect == 0) {
        Print("Report: InternetConnectW failed to ", InpServerURL, ":", InpServerPort, ". Error: ", GetLastError());
        InternetCloseHandle(hInternet); return;
    }

    string headers = "Content-Type: application/json\r\n";
    string acceptTypes[1]; acceptTypes[0] = "application/json";

    int hRequest = HttpOpenRequestW(hConnect, "POST", path, "HTTP/1.1", NULL, acceptTypes, INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE_WRITE , 0);
    if(hRequest == 0) {
        Print("Report: HttpOpenRequestW failed for path '",path,"': ", GetLastError());
        InternetCloseHandle(hConnect); InternetCloseHandle(hInternet); return;
    }

    InternetSetOptionW(hRequest, INTERNET_OPTION_RECEIVE_TIMEOUT, timeout, 4);
    InternetSetOptionW(hRequest, INTERNET_OPTION_SEND_TIMEOUT, timeout, 4);

    if(!HttpSendRequestW(hRequest, headers, StringLen(headers), jsonData, StringLen(jsonData))) {
        Print("Report: HttpSendRequestW failed for path '",path,"': ", GetLastError());
    } else {
        uchar buffer[1024]; uint bytesRead[1]; string tempStr = "";
        ArrayInitialize(buffer,0); ArrayInitialize(bytesRead,0);
        if(InternetReadFile(hRequest, buffer, sizeof(buffer)-1, bytesRead) && bytesRead[0] > 0) {
            tempStr = CharArrayToString(buffer, 0, bytesRead[0]);
            if(InpDebugMode) Print("Report server response from '",path,"': ", tempStr);
        } else if (bytesRead[0]==0 && GetLastError()==0) {
             if(InpDebugMode) Print("Report server response from '",path,"': Success (No Content).");
        } else if (GetLastError()!=0){
            if(InpDebugMode) Print("Report: InternetReadFile failed after POST to '",path,"': ", GetLastError());
        }
    }
    InternetCloseHandle(hRequest); InternetCloseHandle(hConnect); InternetCloseHandle(hInternet);
}


//+------------------------------------------------------------------+
//| Handle OrderSend errors                                          |
//+------------------------------------------------------------------+
void HandleOrderError(int &retryCount, string symbol, int &orderTypeRef, int currentDigits)
{
   int static_retryCount = retryCount;
   static_retryCount++;
   int errorCode = GetLastError();
   Print("OrderSend Attempt ", IntegerToString(static_retryCount), " failed: ", ErrorDescription(errorCode));
   Sleep(500);
}

//+------------------------------------------------------------------+
//| CalculateLotSize (refined)                                       |
//+------------------------------------------------------------------+
double CalculateLotSize(string symbol, double riskPercent, double stopLossDistance)
{
   if(riskPercent <= 0) { return 0.0; }
   double accEquity = AccountEquity();
   if(accEquity <= 0) { Print("Account Equity is 0 or less."); return 0.0; }

   double riskAmount = accEquity * (riskPercent / 100.0);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double minStopPnts = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);

   if(tickValue <= 0 || tickSize <= 0 || point <=0) {
      Print("CalcLotSize Error: Invalid market info for ",symbol," (TickValue:",tickValue,", TickSize:",tickSize,", Point:",point,")");
      return 0.0;
   }
   if(stopLossDistance < minStopPnts * point) {
      if(InpDebugMode) Print("CalcLotSize Warning: StopLoss distance ", DoubleToString(stopLossDistance, (int)SymbolInfoInteger(symbol,SYMBOL_DIGITS)),
            " is smaller than min STOPS_LEVEL distance: ", DoubleToString(minStopPnts*point, (int)SymbolInfoInteger(symbol,SYMBOL_DIGITS)), " for ", symbol,". Using min STOPS_LEVEL.");
      stopLossDistance = minStopPnts * point * 1.1;
      if(stopLossDistance == 0) {Print("CalcLotSize Error: Min stop distance results in zero SL distance."); return 0.0;}
   }

   double ticksInSl = stopLossDistance / tickSize;
   if (ticksInSl == 0) { Print("CalcLotSize Error: Ticks in SL is zero. SLDist:", stopLossDistance, " TickSize:",tickSize); return 0.0; }

   double slValuePerLot = ticksInSl * tickValue;
   if (slValuePerLot == 0) { Print("CalcLotSize Error: SL Value Per Lot is zero. TicksInSL:",ticksInSl," TickValue:",tickValue); return 0.0;}

   double lots = riskAmount / slValuePerLot;

   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(minLot, MathMin(maxLot, lots));

   if(InpDebugMode) Print("CalcLotSize for ",symbol,": SLDistPrice=",stopLossDistance,", RiskAmt=",riskAmount,", SLValuePerLot=",slValuePerLot,", Lots=",NormalizeDouble(lots,2));
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| GetTPMagicOffset                                                 |
//+------------------------------------------------------------------+
int GetTPMagicOffset(string tradeLabel) {
   if(StringFind(tradeLabel, "T1_AUTO") >= 0 || StringFind(tradeLabel,"STD_TRADE") >=0 ) return 0;
   if(StringFind(tradeLabel, "T2_AUTO") >= 0) return 1;
   if(StringFind(tradeLabel, "RE_AUTO") >=0 || StringFind(tradeLabel, "RE_ENTRY") >=0) return 3;
   if(tradeLabel=="TP1") return 0; if(tradeLabel=="TP2") return 1; if(tradeLabel=="TP3") return 2;
   return 4;
}

//+------------------------------------------------------------------+
//| GetJsonValue (robust string, number, bool, null)                 |
//+------------------------------------------------------------------+
string GetJsonValue(string& json, string key) {
    string search_pattern = "\"" + key + "\":";
    int key_pos = StringFind(json, search_pattern);
    if (key_pos < 0) return "";

    int value_start = key_pos + StringLen(search_pattern);
    while (value_start < StringLen(json) && StringGetCharacter(json, value_start) == ' ') value_start++;

    if (value_start >= StringLen(json)) return "";

    ushort first_char = StringGetCharacter(json, value_start);
    int value_end = value_start;

    if (first_char == '"') {
        value_start++;
        value_end = value_start;
        while (value_end < StringLen(json)) {
            if (StringGetCharacter(json, value_end) == '"' && StringGetCharacter(json, value_end - 1) != '\\') break;
            value_end++;
        }
        if (value_end >= StringLen(json)) return "";
        return StringSubstr(json, value_start, value_end - value_start);
    } else {
        value_end = value_start;
        while (value_end < StringLen(json)) {
            ushort c = StringGetCharacter(json, value_end);
            if (c == ',' || c == '}' || c == ']') break; // Simplified delimiter check
            value_end++;
        }
        string val = StringTrimRight(StringTrimLeft(StringSubstr(json, value_start, value_end - value_start)));
        if (val == "null") return "";
        return val;
    }
}
//+------------------------------------------------------------------+
//| Get Boolean JSON Value                                           |
//+------------------------------------------------------------------+
bool GetBoolJsonValue(string& json, string key){
    string val = StringUpper(GetJsonValue(json, key));
    return (val == "TRUE");
}
//+------------------------------------------------------------------+
//| Get Double JSON Value                                            |
//+------------------------------------------------------------------+
double GetDoubleJsonValue(string& json, string key){
    string val = GetJsonValue(json, key);
    if(StringLen(val) == 0) return 0.0;
    return StringToDouble(val);
}
//+------------------------------------------------------------------+
//| Get Integer JSON Value                                           |
//+------------------------------------------------------------------+
int GetIntJsonValue(string& json, string key){
    string val = GetJsonValue(json, key);
    if(StringLen(val) == 0) return 0;
    return (int)StringToInteger(val);
}

//+------------------------------------------------------------------+
//| ErrorDescription                                                 |
//+------------------------------------------------------------------+
string ErrorDescription(int error) {
   string errorDesc = "";
   switch(error) {
      case 0: errorDesc = "No error"; break; case 1: errorDesc = "No error, trade conditions not changed"; break;
      case 2: errorDesc = "Common error"; break; case 3: errorDesc = "Invalid trade parameters"; break;
      case 4: errorDesc = "Trade server busy"; break; case 5: errorDesc = "Old version of client"; break;
      case 6: errorDesc = "No connection with trade server"; break; case 7: errorDesc = "Not enough rights"; break;
      case 8: errorDesc = "Too frequent requests"; break; case 9: errorDesc = "Malfunctional trade operation"; break;
      case 64: errorDesc = "Account disabled"; break; case 65: errorDesc = "Invalid account"; break;
      case 128: errorDesc = "Trade timeout"; break; case 129: errorDesc = "Invalid price"; break;
      case 130: errorDesc = "Invalid stops"; break; case 131: errorDesc = "Invalid trade volume"; break;
      case 132: errorDesc = "Market closed"; break; case 133: errorDesc = "Trade disabled"; break;
      case 134: errorDesc = "Not enough money"; break; case 135: errorDesc = "Price changed"; break;
      case 136: errorDesc = "Off quotes"; break; case 137: errorDesc = "Broker busy"; break;
      case 138: errorDesc = "Requote"; break; case 139: errorDesc = "Order locked"; break;
      case 140: errorDesc = "Long positions only allowed"; break; case 141: errorDesc = "Too many requests"; break;
      case 145: errorDesc = "Modification denied: order too close to market"; break;
      case 146: errorDesc = "Trade context busy"; break; case 147: errorDesc = "Expirations denied by broker"; break;
      case 148: errorDesc = "Amount of open/pending orders reached limit"; break;
      default: errorDesc = "Unknown error " + IntegerToString(error); break;
   }
   return errorDesc;
}

//+------------------------------------------------------------------+
//| UpdateConnectionStatus                                           |
//+------------------------------------------------------------------+
void UpdateConnectionStatus(bool connected, string message = "") {
   color statusColor = connected ? ColorStatusOnline : ColorStatusOffline;
   string statusText = "Status: " + (connected ? "Online" : "Offline");
   string timeText = G_LastSuccessTime > 0 ? "\nLast success: " + TimeToString(G_LastSuccessTime, TIME_SECONDS) : "";
   if(ObjectFind(0, G_StatusLabelName) < 0) {
        ObjectCreate(0, G_StatusLabelName, OBJ_LABEL, 0, 0, 0);
        ObjectSetInteger(0, G_StatusLabelName, OBJPROP_XDISTANCE, 10); ObjectSetInteger(0, G_StatusLabelName, OBJPROP_YDISTANCE, 20);
        ObjectSetInteger(0, G_StatusLabelName, OBJPROP_CORNER, CORNER_LEFT_UPPER); ObjectSetInteger(0, G_StatusLabelName, OBJPROP_FONTSIZE, 10);
        ObjectSetInteger(0, G_StatusLabelName, OBJPROP_BACK, false);
   }
   ObjectSetInteger(0, G_StatusLabelName, OBJPROP_COLOR, statusColor);
   ObjectSetString(0, G_StatusLabelName, OBJPROP_TEXT, statusText + timeText + (StringLen(message) > 0 ? "\n" + message : ""));
}

//+------------------------------------------------------------------+
//| StringUpper                                                      |
//+------------------------------------------------------------------+
string StringUpper(string str) { string r=str; StringToUpper(r); return r; }

//+------------------------------------------------------------------+
//| GetFullSymbol                                                    |
//+------------------------------------------------------------------+
string GetFullSymbol(string baseSymbol) {
   if(StringLen(baseSymbol) == 0) return "";
   string result = StringTrimLeft(StringTrimRight(baseSymbol));
   if(StringFind(StringUpper(result), "XAU") >= 0 || StringUpper(result) == "GOLD") {
      string goldSymbols[] = {"XAUUSD", "XAU/USD", "GOLD"};
      for(int i=0; i<ArraySize(goldSymbols); i++) {
         if(SymbolSelect(goldSymbols[i], true) && MarketInfo(goldSymbols[i], MODE_TICKVALUE) > 0) {
            if(InpDebugMode && result != goldSymbols[i]) Print("Normalized Gold symbol '", baseSymbol, "' to '", goldSymbols[i], "'");
            return goldSymbols[i];
         }
      }
      if(InpDebugMode) Print("Warning: Could not validate any common Gold symbol for '", baseSymbol, "'. Defaulting to XAUUSD if available, else original.");
      if(SymbolSelect("XAUUSD", true) && MarketInfo("XAUUSD", MODE_TICKVALUE) > 0) return "XAUUSD";
      return result;
   }
   string prefixedSuffixed = InpSymbolPrefix + result + InpSymbolSuffix;
   if (StringLen(InpSymbolPrefix)>0 || StringLen(InpSymbolSuffix)>0) {
        if(SymbolSelect(prefixedSuffixed, true) && MarketInfo(prefixedSuffixed, MODE_TICKVALUE) > 0) {
           return prefixedSuffixed;
        }
   }
   if (result != prefixedSuffixed || (StringLen(InpSymbolPrefix)==0 && StringLen(InpSymbolSuffix)==0) ) {
       if(SymbolSelect(result, true) && MarketInfo(result, MODE_TICKVALUE) > 0) {
           return result;
       }
   }
   if(InpDebugMode) Print("Warning: Symbol '", baseSymbol, "' (prefixed/suffixed: '", prefixedSuffixed, "') not found or invalid. Returning original '", result, "' for further checks.");
   return result;
}

//+------------------------------------------------------------------+
//| IsSignalProcessed                                                |
//+------------------------------------------------------------------+
bool IsSignalProcessed(string signalId) {
   if(StringLen(signalId) == 0) return true;
   for(int i = 0; i < ArraySize(G_ProcessedSignalIds); i++) {
      if(G_ProcessedSignalIds[i] == signalId) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| AddSignalProcessedId                                             |
//+------------------------------------------------------------------+
void AddProcessedSignalId(string signalId) {
   if(StringLen(signalId) == 0 || IsSignalProcessed(signalId)) return;
   if(ArraySize(G_ProcessedSignalIds) >= G_MaxStoredSignalIds) {
      for(int i = 0; i < G_MaxStoredSignalIds - 1; i++) G_ProcessedSignalIds[i] = G_ProcessedSignalIds[i+1];
      ArrayResize(G_ProcessedSignalIds, G_MaxStoredSignalIds - 1);
   }
   int currentSize = ArraySize(G_ProcessedSignalIds);
   ArrayResize(G_ProcessedSignalIds, currentSize + 1);
   G_ProcessedSignalIds[currentSize] = signalId;
   if(InpDebugMode) Print("Signal ID ", signalId, " added to processed list. Total: ", ArraySize(G_ProcessedSignalIds));
}

//+------------------------------------------------------------------+
//| UpdateTradeStatuses                                              |
//+------------------------------------------------------------------+
void UpdateTradeStatuses() {
    for(int i = 0; i < SIGNAL_HISTORY_SIZE; i++) {
        if(StringLen(G_SignalHistory[i].signalId) == 0 ) {
             if (StringLen(G_SignalHistory[i].tradeStatus) > 0) G_SignalHistory[i].tradeStatus = "";
             continue;
        }
        if (G_SignalHistory[i].status != "VALID") {
            if (G_SignalHistory[i].tradeStatus != "N/A") G_SignalHistory[i].tradeStatus = "N/A";
            continue;
        }
        int openTradesForSignal = 0; int closedTradesForSignal = 0;
        string sigIdToMatch = "_id" + G_SignalHistory[i].signalId;
        for(int j = OrdersTotal() - 1; j >= 0; j--) {
            if(OrderSelect(j, SELECT_BY_POS, MODE_TRADES)) {
                if(OrderSymbol() == G_SignalHistory[i].symbol &&
                   (OrderMagicNumber() >= InpMagicNumber && OrderMagicNumber() <= InpMagicNumber + 10) &&
                   StringFind(OrderComment(), sigIdToMatch) >= 0 ) {
                    openTradesForSignal++;
                }
            }
        }
        for(int j = OrdersHistoryTotal() - 1; j >= 0; j--) {
            if(OrderSelect(j, SELECT_BY_POS, MODE_HISTORY)) {
                 if(OrderSymbol() == G_SignalHistory[i].symbol &&
                    (OrderMagicNumber() >= InpMagicNumber && OrderMagicNumber() <= InpMagicNumber + 10) &&
                    StringFind(OrderComment(), sigIdToMatch) >= 0 ) {
                    closedTradesForSignal++;
                }
            }
        }
        if(openTradesForSignal > 0) {
            G_SignalHistory[i].tradeStatus = (closedTradesForSignal > 0) ? "PARTIAL" : "RUNNING";
        } else if (closedTradesForSignal > 0) {
            G_SignalHistory[i].tradeStatus = "CLOSED";
        } else {
             G_SignalHistory[i].tradeStatus = "NEW";
        }
    }
}

//+------------------------------------------------------------------+
//| PipsToPrice                                                      |
//+------------------------------------------------------------------+
double PipsToPrice(string symbol, double pips) {
    double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
    int digitsFactor = 1;
    if (SymbolInfoInteger(symbol, SYMBOL_DIGITS) == 3 || SymbolInfoInteger(symbol, SYMBOL_DIGITS) == 5) {
        if(StringFind(symbol, "JPY") < 0) digitsFactor = 10;
    }
    if(StringFind(symbol, "JPY") >= 0 && SymbolInfoInteger(symbol, SYMBOL_DIGITS) == 3) digitsFactor = 10;
    else if(StringFind(symbol, "JPY") < 0 && SymbolInfoInteger(symbol, SYMBOL_DIGITS) == 5) digitsFactor = 10;
    else digitsFactor = 1;

    return NormalizeDouble(pips * digitsFactor * point, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS));
}

//+------------------------------------------------------------------+
//| PriceToPips                                                      |
//+------------------------------------------------------------------+
double PriceToPips(string symbol, double price_difference) {
    double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
    if (point == 0) return 0;
    price_difference = MathAbs(price_difference);
    int digitsFactor = 1;
    if (SymbolInfoInteger(symbol, SYMBOL_DIGITS) == 3 || SymbolInfoInteger(symbol, SYMBOL_DIGITS) == 5) {
         if(StringFind(symbol, "JPY") < 0) digitsFactor = 10;
    }
    if(StringFind(symbol, "JPY") >= 0 && SymbolInfoInteger(symbol, SYMBOL_DIGITS) == 3) digitsFactor = 10;
    else if(StringFind(symbol, "JPY") < 0 && SymbolInfoInteger(symbol, SYMBOL_DIGITS) == 5) digitsFactor = 10;
    else digitsFactor = 1;

    return NormalizeDouble((price_difference / point) / digitsFactor, 1);
}

//+------------------------------------------------------------------+
//| IsTradeClosedInProfit                                            |
//+------------------------------------------------------------------+
bool IsTradeClosedInProfit(int ticket_to_check) {
    if(ticket_to_check <= 0) return false;
    for(int i = OrdersHistoryTotal() - 1; i >= 0; i--) {
        if(OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) {
            if(OrderTicket() == ticket_to_check) {
                // A trade is profitable if its net profit (Profit + Swap + Commission) is > 0
                // Some brokers might report small negative profit as break-even if commission > profit.
                // For simplicity, > 0 is considered profitable.
                if(OrderProfit() + OrderSwap() + OrderCommission() > 0.00001) { // Use small epsilon if needed
                    if(InpDebugMode) Print("Trade #", ticket_to_check, " confirmed closed IN PROFIT. Net: ", OrderProfit() + OrderSwap() + OrderCommission());
                    return true;
                } else {
                    if(InpDebugMode) Print("Trade #", ticket_to_check, " confirmed closed, but NOT in profit. Net: ", OrderProfit() + OrderSwap() + OrderCommission());
                    return false;
                }
            }
        }
    }
    if(InpDebugMode) Print("Trade #", ticket_to_check, " not found in history or not considered closed for profit check.");
    return false;
}
//+------------------------------------------------------------------+
