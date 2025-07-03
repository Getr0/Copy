//+------------------------------------------------------------------+
//|                                  SignalCopierPro.mq4             |
//|                        Copyright 2023, MetaQuotes Software Corp. |
//|                                             https://www.metaquotes.net/ |
//+------------------------------------------------------------------+
#property copyright "Copyright 2023, SignalCopierPro"
#property link      "https://www.yourwebsite.com"
#property version   "1.33"  // Updated version with improved symbol handling
#property strict

#include <stdlib.mqh>

struct SignalData {
    string symbol;
    string action;
    double entry;
    double sl;
    double tp1;
    double tp2;
    double tp3;
    datetime timestamp;
    string status;
    string comment;
    string signalId;
    string tradeStatus;
};

// Function prototypes
string FetchAPIData(string url);
void UpdateConnectionStatus(bool connected, string message = "");
void InitializeSignalDisplay();
void UpdateSignalDisplay();
void AddSignalToHistory(SignalData &signal);
void CheckForNewSignals();
void ExecuteTrade(string symbol, string action, double entryPrice, double stopLoss, 
                 double takeProfit, double riskPercent, string tpLabel);
double CalculateLotSize(string symbol, double riskPercent, double stopLossDistance);
int GetTPMagicOffset(string tpLabel);
string GetJsonValue(string json, string key);
string ErrorDescription(int error);
string StringUpper(string str);
string GetFullSymbol(string baseSymbol);
bool ValidateSymbol(string symbol);

// Fixed WinINet imports - properly declared for MQL4
#import "wininet.dll"
int InternetOpenW(string lpszAgent, int dwAccessType, string lpszProxyName, string lpszProxyBypass, int dwFlags);
int InternetConnectW(int hInternetSession, string lpszServerName, int nServerPort, string lpszUsername, string lpszPassword, int dwService, int dwFlags, int dwContext);
int HttpOpenRequestW(int hConnect, string lpszVerb, string lpszObjectName, string lpszVersion, string lpszReferrer, string& lpszAcceptTypes[], int dwFlags, int dwContext); // FIXED: array passed by reference
bool HttpSendRequestW(int hRequest, string lpszHeaders, int dwHeadersLength, string lpszPostData, int dwPostDataLength);
bool InternetReadFile(int hFile, uchar &lpBuffer[], int dwNumberOfBytesToRead, uint &lpdwNumberOfBytesRead[]);
bool InternetCloseHandle(int hInet);
bool InternetSetOptionW(int hInternet, int dwOption, int &lpBuffer[], int dwBufferLength);
#import

#define INTERNET_OPEN_TYPE_DIRECT 1
#define INTERNET_SERVICE_HTTP 3
#define INTERNET_FLAG_RELOAD 0x80000000
#define INTERNET_OPTION_RECEIVE_TIMEOUT 6
#define HTTP_QUERY_CONTENT_TYPE 1
#define HTTP_QUERY_FLAG_NUMBER 0x20000000

// Input parameters
input string ServerURL = "localhost";
input string ServerPath = "/signals";
input int ServerPort = 5000;
input int CheckInterval = 60;
input double RiskPercentTP1 = 1.0;
input double RiskPercentTP2 = 0.5;
input double RiskPercentTP3 = 0.3;
input int SlippagePoints = 3;
input bool EnableTrading = true;
input bool AlertsOnly = false;
input int MagicNumber = 123456;
input string SymbolPrefix = "";
input string SymbolSuffix = "";
input bool UseMarketOrders = true;
input bool DebugMode = true;
input int TimeoutMilliseconds = 5000; // 5 seconds timeout

// Display settings
#define SIGNAL_HISTORY_SIZE 5
#define SIGNAL_START_Y 50
#define SIGNAL_ROW_HEIGHT 20
color buyColor = clrLime;
color sellColor = clrRed;
color invalidColor = clrDarkGray;
color validColor = clrWhite;

// Global variables
SignalData signalHistory[SIGNAL_HISTORY_SIZE];
string statusLabelName = "ConnectionStatusLabel";
color onlineColor = clrLimeGreen;
color offlineColor = clrRed;
datetime lastSuccessTime = 0;
datetime LastCheckTime = 0;
int LastSignalID = 0;
string ProcessedSignalIds[];
int MaxStoredSignalIds = 50;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if(!TerminalInfoInteger(TERMINAL_DLLS_ALLOWED))
   {
      Alert("Error: DLL calls not allowed! Please enable in Terminal settings.");
      return(INIT_FAILED);
   }

   if(DebugMode) Print("Initializing EA with server: http://",ServerURL,":",ServerPort,ServerPath);

   // Initialize signal ID tracking
   ArrayResize(ProcessedSignalIds, 0);

   ObjectCreate(0, statusLabelName, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, statusLabelName, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, statusLabelName, OBJPROP_YDISTANCE, 20);
   ObjectSetInteger(0, statusLabelName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, statusLabelName, OBJPROP_COLOR, offlineColor);
   ObjectSetString(0, statusLabelName, OBJPROP_TEXT, "Status: Initializing...");
   ObjectSetInteger(0, statusLabelName, OBJPROP_FONTSIZE, 10);
   ObjectSetInteger(0, statusLabelName, OBJPROP_BACK, false);
   
   InitializeSignalDisplay();
   
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      Alert("Warning: Trading is disabled in terminal settings!");
      UpdateConnectionStatus(false, "Trading disabled");
      return INIT_FAILED;
   }
   
   // Pre-check that XAU/USD or XAUUSD is available
   string goldSymbols[] = {"XAUUSD", "XAU/USD", "GOLD"};
   bool goldFound = false;
   for(int i=0; i<ArraySize(goldSymbols); i++)
   {
      if(SymbolSelect(goldSymbols[i], true))
      {
         if(MarketInfo(goldSymbols[i], MODE_TICKVALUE) > 0)
         {
            Print("Gold symbol available as: ", goldSymbols[i]);
            goldFound = true;
            break;
         }
      }
   }
   
   if(!goldFound && DebugMode)
   {
      Print("Warning: Gold symbol not found in any common format. Check broker symbol list.");
   }
   
   UpdateConnectionStatus(false, "Waiting for first connection");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(DebugMode) Print("EA deinitialization started. Reason: ", reason);
   
   // Clear signal ID tracking
   ArrayResize(ProcessedSignalIds, 0);
   
   ObjectDelete(0, statusLabelName);
   ObjectDelete(0, "SignalHeader");  // Delete header
   for(int i = 0; i < SIGNAL_HISTORY_SIZE; i++)
      ObjectDelete(0, "SignalRow"+IntegerToString(i));
      
   Print("EA deinitialized. Reason code: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   static datetime lastTick;
   if(TimeCurrent() - lastTick < 1) return; // Limit to 1 second
   lastTick = TimeCurrent();
   
   // Update trade statuses and display
   UpdateSignalDisplay();
   
   if(TimeCurrent() - LastCheckTime >= CheckInterval)
   {
      CheckForNewSignals();
      LastCheckTime = TimeCurrent();
   }
}

//+------------------------------------------------------------------+
//| Initialize signal display                                        |
//+------------------------------------------------------------------+
void InitializeSignalDisplay()
{
    // Create header row
    string headerName = "SignalHeader";
    ObjectCreate(0, headerName, OBJ_LABEL, 0, 0, 0);
    ObjectSetInteger(0, headerName, OBJPROP_XDISTANCE, 10);
    ObjectSetInteger(0, headerName, OBJPROP_YDISTANCE, SIGNAL_START_Y + 20); // Moved down by 20 pixels
    ObjectSetInteger(0, headerName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
    ObjectSetInteger(0, headerName, OBJPROP_COLOR, clrWhite);
    ObjectSetInteger(0, headerName, OBJPROP_FONTSIZE, 8);
    ObjectSetString(0, headerName, OBJPROP_TEXT, "TIME  | SYMBOL  | ACTION | STATUS | TRADE  | ENTRY    | SL       | TP1      | TP2      | TP3");
    
    for(int i = 0; i < SIGNAL_HISTORY_SIZE; i++)
    {
        string objName = "SignalRow"+IntegerToString(i);
        ObjectCreate(0, objName, OBJ_LABEL, 0, 0, 0);
        ObjectSetInteger(0, objName, OBJPROP_XDISTANCE, 10);
        ObjectSetInteger(0, objName, OBJPROP_YDISTANCE, SIGNAL_START_Y + 20 + ((i+1)*SIGNAL_ROW_HEIGHT)); // Adjusted for new header position
        ObjectSetInteger(0, objName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
        ObjectSetInteger(0, objName, OBJPROP_COLOR, validColor);
        ObjectSetInteger(0, objName, OBJPROP_FONTSIZE, 8);
        ObjectSetString(0, objName, OBJPROP_TEXT, "- Empty -");
    }
    if(DebugMode) Print("Signal display initialized");
}

//+------------------------------------------------------------------+
//| Update signal display                                            |
//+------------------------------------------------------------------+
void UpdateSignalDisplay()
{
    // First update trade statuses
    UpdateTradeStatuses();
    
    // Create header row if it doesn't exist
    string headerName = "SignalHeader";
    if(!ObjectCreate(0, headerName, OBJ_LABEL, 0, 0, 0))
        ObjectSetInteger(0, headerName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
    
    // Move the header down to avoid overlap with status
    ObjectSetInteger(0, headerName, OBJPROP_XDISTANCE, 10);
    ObjectSetInteger(0, headerName, OBJPROP_YDISTANCE, SIGNAL_START_Y + 20); // Moved down by 20 pixels
    ObjectSetInteger(0, headerName, OBJPROP_COLOR, clrWhite);
    ObjectSetInteger(0, headerName, OBJPROP_FONTSIZE, 8);
    ObjectSetString(0, headerName, OBJPROP_TEXT, "TIME  | SYMBOL  | ACTION | STATUS | TRADE  | ENTRY    | SL       | TP1      | TP2      | TP3");
    
    for(int i = 0; i < SIGNAL_HISTORY_SIZE; i++)
    {
        string objName = "SignalRow"+IntegerToString(i);
        string text = "";
        color rowColor = validColor;
        
        // Adjust Y distance to match the new header position
        ObjectSetInteger(0, objName, OBJPROP_YDISTANCE, SIGNAL_START_Y + 20 + ((i+1)*SIGNAL_ROW_HEIGHT));
        
        if(i < ArraySize(signalHistory))
        {
            // Format each value with fixed width
            string timeStr = StringFormat("%-5s", TimeToString(signalHistory[i].timestamp, TIME_MINUTES));
            string symbolStr = StringFormat("%-7s", StringSubstr(signalHistory[i].symbol, 0, 7));
            string actionStr = StringFormat("%-5s", signalHistory[i].action);
            string statusStr = StringFormat("%-6s", signalHistory[i].status);
            string tradeStatusStr = StringFormat("%-6s", signalHistory[i].tradeStatus);
            
            string entryStr = (signalHistory[i].entry > 0) ? 
                StringFormat("%-8s", DoubleToString(signalHistory[i].entry, 2)) : "N/A     ";
            string slStr = (signalHistory[i].sl > 0) ? 
                StringFormat("%-8s", DoubleToString(signalHistory[i].sl, 2)) : "N/A     ";
            string tp1Str = (signalHistory[i].tp1 > 0) ? 
                StringFormat("%-8s", DoubleToString(signalHistory[i].tp1, 2)) : "N/A     ";
            string tp2Str = (signalHistory[i].tp2 > 0) ? 
                StringFormat("%-8s", DoubleToString(signalHistory[i].tp2, 2)) : "N/A     ";
            string tp3Str = (signalHistory[i].tp3 > 0) ? 
                StringFormat("%-8s", DoubleToString(signalHistory[i].tp3, 2)) : "N/A     ";
            
            // Format the display with fixed widths and separators
            text = StringFormat("%s | %s | %s | %s | %s | %s | %s | %s | %s | %s",
                timeStr,
                symbolStr,
                actionStr,
                statusStr,
                tradeStatusStr,
                entryStr,
                slStr,
                tp1Str,
                tp2Str,
                tp3Str);
            
            if(signalHistory[i].status == "VALID") {
                // Set color based on trade status
                if(signalHistory[i].tradeStatus == "RUNNING" || signalHistory[i].tradeStatus == "PARTIAL") {
                    rowColor = (signalHistory[i].action == "BUY") ? buyColor : sellColor;
                } 
                else if(signalHistory[i].tradeStatus == "CLOSED") {
                    rowColor = clrGray;  // Use gray for closed trades
                }
                else {
                    rowColor = (signalHistory[i].action == "BUY") ? buyColor : sellColor;
                }
            } else {
                rowColor = invalidColor;
            }
        }
        
        ObjectSetString(0, objName, OBJPROP_TEXT, text);
        ObjectSetInteger(0, objName, OBJPROP_COLOR, rowColor);
    }
}

//+------------------------------------------------------------------+
//| Add new signal to history                                        |
//+------------------------------------------------------------------+
void AddSignalToHistory(SignalData &signal)
{
    for(int i = SIGNAL_HISTORY_SIZE-1; i > 0; i--)
        signalHistory[i] = signalHistory[i-1];
    signalHistory[0] = signal;
}

//+------------------------------------------------------------------+
//| Fetch data from API                                              |
//+------------------------------------------------------------------+
string FetchAPIData(string url)
{
   if(DebugMode) Print("Fetching data from: http://",ServerURL,":",ServerPort,url);
   
   string response = "";
   int hInternet = InternetOpenW("MT4 EA", INTERNET_OPEN_TYPE_DIRECT, "", "", 0);
   if(hInternet == 0)
   {
      Print("InternetOpenW failed. Error: ", GetLastError());
      UpdateConnectionStatus(false, "Internet init failed");
      return "";
   }
   
   int hConnect = InternetConnectW(hInternet, ServerURL, ServerPort, "", "", INTERNET_SERVICE_HTTP, 0, 0);
   if(hConnect == 0)
   {
      Print("InternetConnectW failed. Error: ", GetLastError());
      InternetCloseHandle(hInternet);
      UpdateConnectionStatus(false, "Server connect failed");
      return "";
   }
   
   // Create empty array for acceptTypes parameter - FIXED: now correctly using reference
   string acceptTypes[];
   ArrayResize(acceptTypes, 0);
   
   int hRequest = HttpOpenRequestW(hConnect, "GET", url, "HTTP/1.1", "", acceptTypes, INTERNET_FLAG_RELOAD, 0);
   if(hRequest == 0)
   {
      Print("HttpOpenRequestW failed. Error: ", GetLastError());
      InternetCloseHandle(hConnect);
      InternetCloseHandle(hInternet);
      UpdateConnectionStatus(false, "HTTP request failed");
      return "";
   }
   
   // Set timeout for the request - fixed implementation using int array
   int timeoutBuffer[1];
   timeoutBuffer[0] = TimeoutMilliseconds;
   if(!InternetSetOptionW(hRequest, INTERNET_OPTION_RECEIVE_TIMEOUT, timeoutBuffer, 4))
   {
      Print("InternetSetOptionW failed to set timeout. Error: ", GetLastError());
   }
   
   if(!HttpSendRequestW(hRequest, "", 0, "", 0))
   {
      Print("HttpSendRequestW failed. Error: ", GetLastError());
      InternetCloseHandle(hRequest);
      InternetCloseHandle(hConnect);
      InternetCloseHandle(hInternet);
      UpdateConnectionStatus(false, "Request send failed");
      return "";
   }
   
   // Fixed: Correct InternetReadFile implementation
   uchar buffer[1024];
   uint bytesRead[1];  // Use uint array for bytesRead
   ArrayInitialize(bytesRead, 0);
   
   while(true)
   {
      if(!InternetReadFile(hRequest, buffer, sizeof(buffer), bytesRead))
      {
         Print("InternetReadFile failed. Error: ", GetLastError());
         break;
      }
      
      if(bytesRead[0] <= 0) break;
      
      response += CharArrayToString(buffer, 0, bytesRead[0]);
      ArrayInitialize(buffer, 0);
   }
   
   InternetCloseHandle(hRequest);
   InternetCloseHandle(hConnect);
   InternetCloseHandle(hInternet);
   
   if(StringLen(response) > 0)
   {
      lastSuccessTime = TimeCurrent();
      UpdateConnectionStatus(true, "Data received");
      if(DebugMode) Print("API response: ", response);
      return response;
   }
   
   UpdateConnectionStatus(false, "Empty response");
   return "";
}

//+------------------------------------------------------------------+
//| Check for new trading signals                                    |
//+------------------------------------------------------------------+
void CheckForNewSignals()
{
   string response = FetchAPIData(ServerPath);
   if(StringLen(response) == 0)
   {
      Print("Empty API response");
      return;
   }

   // Process response regardless of format
   ProcessSignalFromJson(response);
}

//+------------------------------------------------------------------+
//| Process JSON signal data                                         |
//+------------------------------------------------------------------+
void ProcessSignalFromJson(string jsonStr)
{
   SignalData signal;
   
   // Extract data directly from JSON
   signal.action = StringUpper(GetJsonValue(jsonStr, "action"));
   
   // Extract signal ID
   signal.signalId = GetJsonValue(jsonStr, "id");
   
   // If no ID found, try alternate keys
   if(StringLen(signal.signalId) == 0)
      signal.signalId = GetJsonValue(jsonStr, "signal_id");
   
   // If still no ID, generate one from the data itself
   if(StringLen(signal.signalId) == 0)
   {
      string rawSymbol = GetJsonValue(jsonStr, "symbol");
      string entryStr = GetJsonValue(jsonStr, "entry_price");
      if(StringLen(entryStr) == 0) entryStr = GetJsonValue(jsonStr, "entry");
      string slStr = GetJsonValue(jsonStr, "sl");
      
      signal.signalId = StringConcatenate(
         rawSymbol, "_",
         signal.action, "_",
         entryStr, "_",
         slStr
      );
   }
   
   // Check if this signal ID has already been processed
   if(IsSignalProcessed(signal.signalId))
   {
      if(DebugMode) Print("Signal ID already processed: ", signal.signalId);
      return; // Skip further processing
   }
   
   // Handle entry price with both possible keys
   string entryStr = GetJsonValue(jsonStr, "entry_price");
   if(StringLen(entryStr) == 0)
      entryStr = GetJsonValue(jsonStr, "entry");
   
   // Ensure proper decimal handling
   StringReplace(entryStr, ",", "."); // Replace comma with dot if needed
   signal.entry = StringToDouble(entryStr);
   
   // Extract stop loss with proper decimal handling
   string slStr = GetJsonValue(jsonStr, "sl");
   StringReplace(slStr, ",", ".");
   signal.sl = StringToDouble(slStr);
   
   signal.timestamp = TimeCurrent();
   
   // Handle symbol with improved extraction
   string rawSymbol = GetJsonValue(jsonStr, "symbol");
   
   // Special handling for XAU/USD format
   if(StringLen(rawSymbol) > 0)
   {
      // Remove quotes if present
      if(StringGetCharacter(rawSymbol, 0) == '"')
         rawSymbol = StringSubstr(rawSymbol, 1, StringLen(rawSymbol) - 2);
   }
   
   if(DebugMode) Print("Raw symbol extracted: ", rawSymbol);
   
   // Get proper symbol
   signal.symbol = GetFullSymbol(rawSymbol);
   
   // TP values - handle null values with proper decimal handling
   string tp1Value = GetJsonValue(jsonStr, "tp1");
   if(tp1Value == "null" || StringLen(tp1Value) == 0)
      signal.tp1 = 0;
   else {
      StringReplace(tp1Value, ",", ".");
      signal.tp1 = StringToDouble(tp1Value);
   }
   
   // Extract TP2 and TP3 values and store them in the signal struct
   string tp2Value = GetJsonValue(jsonStr, "tp2");
   if(tp2Value == "null" || StringLen(tp2Value) == 0)
      signal.tp2 = 0;
   else {
      StringReplace(tp2Value, ",", ".");
      signal.tp2 = StringToDouble(tp2Value);
   }
   
   string tp3Value = GetJsonValue(jsonStr, "tp3");
   if(tp3Value == "null" || StringLen(tp3Value) == 0) 
      signal.tp3 = 0;
   else {
      StringReplace(tp3Value, ",", ".");
      signal.tp3 = StringToDouble(tp3Value);
   }

   // Initialize trade status
   signal.tradeStatus = "NEW";
   
   // Debug information
   if(DebugMode) 
   {
      Print("Signal extracted: Symbol=", signal.symbol);
      Print("Action=", signal.action);
      Print("Entry=", signal.entry);
      Print("SL=", signal.sl);
      Print("TP1=", signal.tp1);
      Print("TP2=", signal.tp2);
      Print("TP3=", signal.tp3);
      Print("SignalID=", signal.signalId);
   }
                       
   // Validate the signal
   if(StringLen(signal.symbol) == 0)
   {
      signal.status = "INVALID";
      signal.comment = "Empty symbol";
   }
   else if(signal.entry <= 0)
   {
      signal.status = "INVALID";
      signal.comment = "Invalid entry price";
   }
   else if(signal.sl <= 0)
   {
      signal.status = "INVALID";
      signal.comment = "Invalid stop loss";
   }
   else
   {
      // Force selection for gold if needed
      if(StringFind(StringUpper(signal.symbol), "XAU") >= 0 || StringFind(StringUpper(signal.symbol), "GOLD") >= 0)
      {
         if(SymbolSelect("XAUUSD", true) && MarketInfo("XAUUSD", MODE_TICKVALUE) > 0)
            signal.symbol = "XAUUSD";
         else if(SymbolSelect("XAU/USD", true) && MarketInfo("XAU/USD", MODE_TICKVALUE) > 0) 
            signal.symbol = "XAU/USD";
         else if(SymbolSelect("GOLD", true) && MarketInfo("GOLD", MODE_TICKVALUE) > 0)
            signal.symbol = "GOLD";
      }
      
      // Final validation
      if(MarketInfo(signal.symbol, MODE_TICKVALUE) <= 0)
      {
         signal.status = "INVALID";
         signal.comment = "Symbol not available: " + signal.symbol;
      }
      else
      {
         signal.status = "VALID";
         signal.comment = "";
      }
   }
   
   // Process the signal
   AddSignalToHistory(signal);
   UpdateSignalDisplay();
   
   Print("Signal processed: ", signal.symbol," ",signal.action," Status: ",signal.status,
         (signal.status == "INVALID" ? " Reason: " + signal.comment : ""));
   
   if(signal.status == "VALID" && EnableTrading && !AlertsOnly)
   {
      // Mark signal as processed before executing trades
      AddProcessedSignalId(signal.signalId);
      
      // Execute trades for each TP level with non-zero risk
      if(signal.tp1 > 0 && RiskPercentTP1 > 0) {
         ExecuteTrade(signal.symbol, signal.action, signal.entry, signal.sl, signal.tp1, RiskPercentTP1, "TP1");
      }
      
      if(signal.tp2 > 0 && RiskPercentTP2 > 0) {
         ExecuteTrade(signal.symbol, signal.action, signal.entry, signal.sl, signal.tp2, RiskPercentTP2, "TP2");
      }
      
      if(signal.tp3 > 0 && RiskPercentTP3 > 0) {
         ExecuteTrade(signal.symbol, signal.action, signal.entry, signal.sl, signal.tp3, RiskPercentTP3, "TP3");
      }
   }
}

//+------------------------------------------------------------------+
//| Validate symbol format                                           |
//+------------------------------------------------------------------+
bool ValidateSymbol(string symbol)
{
   if(StringLen(symbol) < 2) return false;
   
   // Try to select the symbol - this will verify if it's available
   if(!SymbolSelect(symbol, true))
   {
      // Special handling for XAU/USD
      if(StringFind(symbol, "XAU") >= 0 || StringFind(symbol, "GOLD") >= 0)
      {
         string goldSymbols[] = {"XAUUSD", "XAU/USD", "GOLD"};
         for(int i=0; i<ArraySize(goldSymbols); i++)
         {
            if(SymbolSelect(goldSymbols[i], true))
            {
               return true;
            }
         }
      }
      return false;
   }
   
   // Extra check - make sure we can get market info
   if(MarketInfo(symbol, MODE_TICKVALUE) <= 0)
      return false;
      
   return true;
}

//+------------------------------------------------------------------+
//| Execute trade order                                              |
//+------------------------------------------------------------------+
void ExecuteTrade(string symbol, string action, double entryPrice, double stopLoss, 
                 double takeProfit, double riskPercent, string tpLabel)
{
   if(!EnableTrading || AlertsOnly)
   {
      Print("Trading disabled - would execute: ",symbol," ",action," at ",entryPrice);
      return;
   }

   if(DebugMode) Print("Executing trade: ", symbol, " ", action, " EntryPrice=", entryPrice, 
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
   
   // Hlavní změna - UseMarketOrders má prioritu před vším ostatním
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

   // Silná validace pro limitky (pouze pokud UseMarketOrders není aktivní)
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
         if(DebugMode) Print("Entry price too close, switching to market order");
         orderType = actionIsBuy ? OP_BUY : OP_SELL;
         isLimitOrder = false;
      }
   }

   // Určení finální ceny
   double orderPrice = isLimitOrder ? entryPrice : currentPrice;

   // Zbytek kódu zůstává stejný
   double stopLossDistance = MathAbs(orderPrice - stopLoss);
   double lotSize = CalculateLotSize(symbol, riskPercent, stopLossDistance);
   
   if(lotSize <= 0) return;

   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   double normOrderPrice = NormalizeDouble(orderPrice, digits);
   double normStopLoss = NormalizeDouble(stopLoss, digits);
   double normTakeProfit = NormalizeDouble(takeProfit, digits);

   if(DebugMode) Print("Order details: Price=", normOrderPrice, " SL=", normStopLoss, 
                       " TP=", normTakeProfit, " Lots=", lotSize, " OrderType=", orderType);

   int maxRetries = 3;
   int retryCount = 0;
   int ticket = -1;
   
   while(retryCount < maxRetries && ticket < 0)
   {
      ticket = OrderSend(
         symbol,
         orderType,
         lotSize,
         normOrderPrice,
         SlippagePoints,
         normStopLoss,
         normTakeProfit,
         "SignalCopierPro_"+tpLabel,
         MagicNumber + GetTPMagicOffset(tpLabel),
         0,
         orderType == OP_BUY || orderType == OP_BUYLIMIT ? clrGreen : clrRed
      );
      
      if(ticket < 0) HandleOrderError(retryCount, symbol, orderType, digits);
      retryCount++;
   }

   if(ticket > 0) Print("Order #",ticket," opened successfully");
   else Print("Final order failed: ", ErrorDescription(GetLastError()));
}

void HandleOrderError(int &retryCount, string symbol, int orderType, int digits)
{
   int errorCode = GetLastError();
   Print("Attempt ", retryCount+1, " failed: ", ErrorDescription(errorCode));

   switch(errorCode)
   {
      case 4: case 137: case 146: // Server busy
         Sleep(1000);
         break;
         
      case 129: case 135: // Price changed
         RefreshRates();
         if(orderType == OP_BUYLIMIT || orderType == OP_SELLLIMIT)
         {
            Print("Retrying as market order");
            orderType = (orderType == OP_BUYLIMIT) ? OP_BUY : OP_SELL;
         }
         break;
         
      case 138: // Requote
         RefreshRates();
         break;
   }
}

//+------------------------------------------------------------------+
//| Calculate proper lot size                                        |
//+------------------------------------------------------------------+
double CalculateLotSize(string symbol, double riskPercent, double stopLossDistance)
{
   if(riskPercent <= 0) return 0;
   
   double accountEquity = AccountEquity();  // Získání aktuálního majetku (balance + profit z otevřených pozic)
   if(accountEquity <= 0) return 0;
   
   double riskAmount = accountEquity * (riskPercent / 100.0); // Riziková částka z majetku
   double tickValue = MarketInfo(symbol, MODE_TICKVALUE);
   double tickSize = MarketInfo(symbol, MODE_TICKSIZE);
   
   if(tickValue <= 0 || tickSize <= 0 || stopLossDistance <= 0)
   {
      Print("Chyba: Neplatná data pro výpočet lotu (tickValue/tickSize/stopLoss)");
      return 0;
   }
   
   double stopLossPips = stopLossDistance / tickSize;
   double lotSize = riskAmount / (stopLossPips * tickValue);
   
   // Normalizace podle brokerových pravidel
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

//+------------------------------------------------------------------+
//| Extract value from JSON string                                   |
//+------------------------------------------------------------------+
string GetJsonValue(string json, string key)
{
   string keyPattern = "\"" + key + "\"";
   int keyPos = StringFind(json, keyPattern);
   if(keyPos < 0) return "";
   
   // Find the colon after the key
   int colonPos = StringFind(json, ":", keyPos);
   if(colonPos < 0) return "";
   
   int valueStart = colonPos + 1;
   int valueEnd = -1;
   
   // Skip whitespace
   while(valueStart < StringLen(json))
   {
      ushort c = StringGetCharacter(json, valueStart);
      if(c != ' ' && c != '\t' && c != '\r' && c != '\n')
         break;
      valueStart++;
   }
   
   if(valueStart >= StringLen(json)) return "";
   
   ushort startChar = StringGetCharacter(json, valueStart);
   
   // String value
   if(startChar == '"')
   {
      valueStart++;
      valueEnd = StringFind(json, "\"", valueStart);
      if(valueEnd < 0) return "";
      
      return StringSubstr(json, valueStart, valueEnd - valueStart);
   }
   // Numeric/boolean/null value
   else
   {
      valueEnd = valueStart;
      while(valueEnd < StringLen(json))
      {
         ushort c = StringGetCharacter(json, valueEnd);
         // Allow decimal points (.) in numerical values
         if(c == ',' || c == '}' || c == ']' || c == ' ')
            break;
         valueEnd++;
      }
      
      return StringSubstr(json, valueStart, valueEnd - valueStart);
   }
}

//+------------------------------------------------------------------+
//| Helper function for error description                            |
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
//| Update connection status display                                 |
//+------------------------------------------------------------------+
void UpdateConnectionStatus(bool connected, string message = "")
{
   color statusColor = connected ? onlineColor : offlineColor;
   string statusText = "Status: " + (connected ? "Online" : "Offline");
   string timeText = lastSuccessTime > 0 ? "\nLast success: " + TimeToString(lastSuccessTime) : "";
   
   ObjectSetInteger(0, statusLabelName, OBJPROP_COLOR, statusColor);
   ObjectSetString(0, statusLabelName, OBJPROP_TEXT, statusText + timeText + (message != "" ? "\n" + message : ""));
}

//+------------------------------------------------------------------+
//| Convert string to uppercase                                      |
//+------------------------------------------------------------------+
string StringUpper(string str)
{
   string result = str;
   StringToUpper(result);
   return result;
}

//+------------------------------------------------------------------+
//| Get full symbol name with prefix/suffix                          |
//+------------------------------------------------------------------+
string GetFullSymbol(string baseSymbol)
{
   if(StringLen(baseSymbol) == 0) return "";
   
   string result = baseSymbol;
   
   // Direct check for gold symbols
   if(StringFind(StringUpper(result), "XAU") >= 0 || StringUpper(result) == "GOLD")
   {
      // Try each format directly without modifying
      string goldSymbols[] = {"XAUUSD", "XAU/USD", "GOLD"};
      for(int i=0; i<ArraySize(goldSymbols); i++)
      {
         if(SymbolSelect(goldSymbols[i], true))
         {
            if(MarketInfo(goldSymbols[i], MODE_TICKVALUE) > 0)
            {
               Print("Found valid Gold symbol: ", goldSymbols[i]);
               return goldSymbols[i];
            }
         }
      }
      
      // Force return the symbol - better to attempt with the cleaner format
      return "XAUUSD";
   }
   
   // Handle other symbols with prefix/suffix
   return SymbolPrefix + result + SymbolSuffix;
}

//+------------------------------------------------------------------+
//| Check if a signal has already been processed                     |
//+------------------------------------------------------------------+
bool IsSignalProcessed(string signalId)
{
   if(StringLen(signalId) == 0) return false;
   
   for(int i = 0; i < ArraySize(ProcessedSignalIds); i++)
   {
      if(ProcessedSignalIds[i] == signalId)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Add signal ID to the list of processed signals                   |
//+------------------------------------------------------------------+
void AddProcessedSignalId(string signalId)
{
   if(StringLen(signalId) == 0) return;
   
   // Make room if needed
   if(ArraySize(ProcessedSignalIds) >= MaxStoredSignalIds)
   {
      // Shift array to remove oldest ID
      for(int i = 0; i < ArraySize(ProcessedSignalIds) - 1; i++)
         ProcessedSignalIds[i] = ProcessedSignalIds[i+1];
      
      ArrayResize(ProcessedSignalIds, ArraySize(ProcessedSignalIds) - 1);
   }
   
   // Add new ID at the end
   int size = ArraySize(ProcessedSignalIds);
   ArrayResize(ProcessedSignalIds, size + 1);
   ProcessedSignalIds[size] = signalId;
   
   if(DebugMode) Print("Added signal ID to processed list: ", signalId);
}

//+------------------------------------------------------------------+
//| Update trade statuses                                            |
//+------------------------------------------------------------------+
void UpdateTradeStatuses()
{
    for(int i = 0; i < SIGNAL_HISTORY_SIZE; i++)
    {
        if(StringLen(signalHistory[i].signalId) == 0 || signalHistory[i].status != "VALID")
            continue;
            
        // Initialize counters
        int openTP1 = 0, openTP2 = 0, openTP3 = 0;
        int closedTP1 = 0, closedTP2 = 0, closedTP3 = 0;
        bool hasOrders = false;
        
        // Check all orders for this signal
        for(int j = 0; j < OrdersTotal(); j++)
        {
            if(OrderSelect(j, SELECT_BY_POS, MODE_TRADES))
            {
                // Check if order belongs to this signal using MagicNumber and symbol
                if(OrderSymbol() == signalHistory[i].symbol)
                {
                    int baseMagic = MagicNumber;
                    
                    if(OrderMagicNumber() == baseMagic)
                    {
                        openTP1++;
                        hasOrders = true;
                    }
                    else if(OrderMagicNumber() == baseMagic + 1)
                    {
                        openTP2++;
                        hasOrders = true;
                    }
                    else if(OrderMagicNumber() == baseMagic + 2)
                    {
                        openTP3++;
                        hasOrders = true;
                    }
                }
            }
        }
        
        // Check history for closed orders
        int totalHistory = OrdersHistoryTotal();
        for(int j = 0; j < totalHistory; j++)
        {
            if(OrderSelect(j, SELECT_BY_POS, MODE_HISTORY))
            {
                // Check if order belongs to this signal using MagicNumber and symbol
                if(OrderSymbol() == signalHistory[i].symbol)
                {
                    int baseMagic = MagicNumber;
                    
                    if(OrderMagicNumber() == baseMagic)
                    {
                        closedTP1++;
                        hasOrders = true;
                    }
                    else if(OrderMagicNumber() == baseMagic + 1)
                    {
                        closedTP2++;
                        hasOrders = true;
                    }
                    else if(OrderMagicNumber() == baseMagic + 2)
                    {
                        closedTP3++;
                        hasOrders = true;
                    }
                }
            }
        }
        
        // Determine trade status based on open/closed orders
        string status = "NEW";
        
        if(hasOrders)
        {
            if(openTP1 > 0 || openTP2 > 0 || openTP3 > 0)
            {
                if(closedTP1 > 0 || closedTP2 > 0 || closedTP3 > 0)
                    status = "PARTIAL";
                else
                    status = "RUNNING";
            }
            else
            {
                status = "CLOSED";
            }
        }
        
        signalHistory[i].tradeStatus = status;
    }
}
//+------------------------------------------------------------------+