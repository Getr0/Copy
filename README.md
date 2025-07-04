# Inteligentní Kopírovací Bot a MetaTrader 4 EA

## Přehled

Tento projekt se skládá ze dvou hlavních komponent:

1.  **`bot.py`**: Python aplikace, která monitoruje Telegramové kanály pro obchodní signály, parsuje je, ukládá do databáze a poskytuje API pro MetaTrader 4 Expert Advisor (EA). Obsahuje také grafické uživatelské rozhraní (GUI) pro správu.
2.  **`opener.mq4`**: MetaTrader 4 Expert Advisor (EA), který komunikuje s `bot.py` API, aby získal obchodní signály a automaticky podle nich otevíral obchody na MT4 platformě.

Cílem systému je automatizovat proces kopírování obchodních signálů z Telegramu do MT4.

## Obsah

- [Přehled](#přehled)
- [Funkce `bot.py`](#funkce-botpy)
    - [Hlavní Komponenty](#hlavní-komponenty-botpy)
    - [Telegram Klient](#telegram-klient)
    - [Parsery Signálů](#parsery-signálů)
    - [Databázové Operace](#databázové-operace)
    - [Grafické Uživatelské Rozhraní (GUI)](#grafické-uživatelské-rozhraní-gui)
    - [Flask API](#flask-api)
    - [Použité Knihovny (Python)](#použité-knihovny-python)
- [Funkce `opener.mq4`](#funkce-openermq4)
    - [Hlavní Komponenty](#hlavní-komponenty-openermq4)
    - [Komunikace s `bot.py` API](#komunikace-s-botpy-api)
    - [Zpracování a Validace Signálů](#zpracování-a-validace-signálů)
    - [Otevírání Obchodů](#otevírání-obchodů)
    - [Správa MagicNumber](#správa-magicnumber)
    - [Zobrazování Informací na Grafu](#zobrazování-informací-na-grafu)
    - [Vstupní Parametry EA](#vstupní-parametry-ea)
    - [Použití WinINet.dll](#použití-wininetdll)
- [Instalace a Nastavení](#instalace-a-nastavení)
    - [Předpoklady](#předpoklady)
    - [Nastavení `bot.py`](#nastavení-botpy)
    - [Nastavení `opener.mq4`](#nastavení-openermq4)
- [Použití](#použití)
    - [Spuštění `bot.py`](#spuštění-botpy)
    - [Nasazení a Konfigurace `opener.mq4`](#nasazení-a-konfigurace-openermq4)
- [API Endpoints (`bot.py`)](#api-endpoints-botpy)
    - [`GET /signals`](#get-signals)
    - [`POST /report_trade`](#post-report_trade)
- [Struktura Databáze (`signals.db`)](#struktura-databáze-signalsdb)
    - [Tabulka `signals`](#tabulka-signals)
- [Přispívání / Vývoj](#přispívání--vývoj)
- [Řešení Problémů](#řešení-problémů)

## Funkce `bot.py`

`bot.py` je srdcem systému pro sběr a distribuci signálů. Je napsán v Pythonu a využívá několik knihoven pro své různé funkce.

### Hlavní Komponenty (`bot.py`)

*   **Telegram Klient**: Připojuje se k Telegramu, monitoruje vybrané kanály a skupiny pro nové zprávy.
*   **Parsery Signálů**: Analyzují text zpráv a extrahují relevantní obchodní informace (symbol, akce, vstupní cena, SL, TP).
*   **Databáze**: Ukládá zpracované signály do SQLite databáze.
*   **GUI**: Poskytuje uživatelské rozhraní postavené na Tkinter/CustomTkinter pro správu telefonních čísel, výběr kanálů k monitorování a zobrazení logů.
*   **Flask API**: Vystavuje HTTP endpointy, přes které může `opener.mq4` (nebo jiný klient) získávat nové signály a reportovat stav otevřených obchodů.

### Telegram Klient

Komponenta Telegram klienta je zodpovědná za:

*   **Připojení a Autorizace**:
    *   Umožňuje uživateli zadat telefonní číslo.
    *   Zpracovává proces přihlášení k Telegramu, včetně zadání ověřovacího kódu a hesla pro dvoufázové ověření (2FA), pokud je aktivní.
    *   Spravuje session soubory pro uložená telefonní čísla, což umožňuje rychlejší opětovné připojení.
*   **Výběr Kanálů/Skupin**:
    *   Po úspěšném připojení načte seznam všech dialogů (kanálů a skupin), ke kterým má uživatel přístup.
    *   GUI umožňuje uživateli vybrat, které kanály/skupiny chce monitorovat.
*   **Monitorování Zpráv**:
    *   Pro každý vybraný kanál/skupinu naslouchá novým příchozím zprávám.
    *   Používá `telethon.events.NewMessage`.
*   **Správa Duplicit**: Sleduje ID již zpracovaných zpráv, aby se zabránilo vícenásobnému zpracování stejného signálu.

### Parsery Signálů

Systém obsahuje dva hlavní typy parserů pro extrakci informací z textu zpráv:

*   **`parse_sniper_pro(message_text)`**:
    *   Specializovaný parser pro formát signálů z kanálu "SniperPro" (nebo podobných).
    *   Rozpoznává různé typy zpráv:
        *   **Ignorované zprávy**: (např. "pips ruining", "Book some profit").
        *   **Re-entry signály**: Hledá klíčová slova jako "REE ENTRY" a extrahuje symbol a cenu SL.
        *   **Iniciální signály**: Typicky ve formátu "GOLD BUY 1234.5". Určuje symbol, akci (BUY/SELL) a referenční vstupní cenu. Na základě iniciálního signálu generuje dva záznamy do DB:
            *   `INITIAL_T1`: Pro první obchod s definovaným SL (např. 40 pipů) a TP (např. 40 pipů).
            *   `INITIAL_T2_DEFAULT`: Pro druhý obchod se stejným SL a výchozím širším TP (např. 200 pipů), který může být později aktualizován.
        *   **Aktualizace SL/TP**: Hledá "Sl <cena>" a "Tp <cena>" pro aktualizaci existujícího obchodu (typicky `INITIAL_T2_DEFAULT`).
    *   Využívá regulární výrazy pro identifikaci a extrakci dat.
    *   Udržuje kontext posledního iniciálního signálu v rámci kanálu pro správné přiřazení aktualizací a re-entry.

*   **`parse_standard_signal(message_text)`**:
    *   Obecnější parser navržený pro různé běžné formáty signálů.
    *   Hledá vzory jako:
        *   `BUY XAUUSD 1234.5 SL 1230 TP 1240 TP2 1250`
        *   `EURUSD SELL 1.1200 SL 1.1250 TP 1.1150`
    *   Extrahuje symbol, akci, vstupní cenu, SL a více úrovní TP.
    *   Pokud jsou SL/TP přítomny, uloží je jako typ `STANDARD`.

Výběr parseru pro daný kanál se provádí v GUI.

### Databázové Operace

Všechny zpracované signály a jejich stavy jsou ukládány do SQLite databáze (`signals.db`).

*   **Inicializace (`init_db`)**:
    *   Při spuštění kontroluje existenci tabulky `signals`.
    *   Pokud tabulka neexistuje, vytvoří ji s definovanou strukturou.
    *   Pokud existuje starší verze tabulky, přejmenuje ji na `signals_old` a vytvoří novou.
    *   Zajišťuje přidání nových sloupců, pokud chybí, pro zpětnou kompatibilitu.
    *   Maže nedokončené signály (status 'new' nebo NULL) při startu, aby se předešlo zpracování starých, neaktuálních dat.
*   **Ukládání Signálů (`_save_signal_data`)**:
    *   Vkládá nový záznam do tabulky `signals`.
    *   Ukládá všechny relevantní informace: symbol, akce, vstupní cena, typ signálu, SL/TP (jako hodnota a typ – PIPS/PRICE), ID skupiny signálu, label obchodu atd.
    *   Každý nový signál dostává status `'new'`.
*   **Aktualizace Signálů (`_update_db_trade_tp_status`)**:
    *   Používá se specificky pro aktualizaci Take Profitu u `UPDATE_T2` signálů od SniperPro.
    *   Najde odpovídající obchod T2_AUTO podle `signal_group_id` a `trade_label`.
    *   Aktualizuje jeho `tp_value`, `tp_value_type`, `signal_type` na `UPDATE_T2` a `status` na `'new'`, aby byl znovu vyzvednut EA.
*   **Zamykání (`db_lock`)**: Používá `threading.Lock` k zajištění bezpečného přístupu k databázi z více vláken (např. Telegram handler a Flask API).

### Grafické Uživatelské Rozhraní (GUI)

GUI je postaveno pomocí `tkinter` a `customtkinter` pro modernější vzhled.

*   **Správa Telefonních Čísel**:
    *   Umožňuje zadat nové telefonní číslo.
    *   Zobrazuje seznam již uložených (autorizovaných) čísel.
    *   Umožňuje vybrat číslo pro připojení nebo odstranit uloženou session.
*   **Připojení k Telegramu**:
    *   Tlačítko "Připojit" spustí proces připojení k Telegramu s vybraným/zadaným číslem.
    *   Řídí zobrazení dialogů pro zadání kódu a 2FA hesla.
*   **Seznam Kanálů/Skupin**:
    *   Po připojení zobrazí seznam dostupných kanálů a skupin.
    *   U každého kanálu umožňuje:
        *   Vybrat metodu parsování ("SniperPro" nebo "Standardní").
        *   Spustit/Zastavit monitorování zpráv z daného kanálu.
*   **Log Událostí**:
    *   Zobrazuje textové pole s logy z aplikace (připojení, nové zprávy, zpracované signály, chyby).

### Flask API

`bot.py` spouští jednoduchý Flask webový server, který poskytuje dva hlavní endpointy pro komunikaci s `opener.mq4`.

*   **`GET /signals`**:
    *   Vrací JSON pole všech signálů z databáze, které mají status `'new'`.
    *   Tyto signály jsou určeny k vyzvednutí a zpracování EA.
*   **`POST /report_trade`**:
    *   Přijímá JSON data od EA, která informují o výsledku zpracování signálu.
    *   Očekává `id` (databázové ID signálu) a `ticket` (číslo obchodu v MT4).
    *   Na základě `signal_type`:
        *   Pro otevírací signály (`INITIAL_T1`, `INITIAL_T2_DEFAULT`, `RE_ENTRY`, `STANDARD`): změní status signálu na `'open'` a uloží `ticket`.
        *   Pro aktualizační signály (`UPDATE_T2`): změní status na `'processed_update'`.
    *   Toto potvrzení je důležité, aby `bot.py` věděl, které signály byly úspěšně přijaty a zpracovány EA.

### Použité Knihovny (Python)

*   **`tkinter`, `customtkinter`**: Pro tvorbu grafického uživatelského rozhraní.
*   **`re`**: Pro práci s regulárními výrazy při parsování zpráv.
*   **`os`, `glob`**: Pro práci se souborovým systémem (správa session souborů).
*   **`sqlite3`**: Pro interakci s SQLite databází.
*   **`datetime`**: Pro práci s časovými značkami.
*   **`threading`**: Pro běh Telegram klienta a Flask serveru v oddělených vláknech, aby GUI zůstalo responzivní.
*   **`asyncio`**: Využíváno knihovnou Telethon pro asynchronní operace.
*   **`json`**: Pro práci s JSON daty (není explicitně v importech, ale Flask ho používá interně).
*   **`logging`**: Pro logování událostí aplikace.
*   **`requests`**: (Přidán v kódu, ale zdá se, že není aktivně využíván v `bot.py` pro odchozí požadavky. Flask ho používá interně pro příchozí.)
*   **`flask`**: Pro vytvoření webového API.
*   **`telethon`**: Hlavní knihovna pro interakci s Telegram API.
*   **`concurrent.futures`**: Používá se při ukončování klienta.
*   **`waitress`**: Doporučený produkční WSGI server pro Flask (pokud je nainstalován).

## Funkce `opener.mq4`

`opener.mq4` je Expert Advisor pro platformu MetaTrader 4. Jeho hlavním úkolem je automatizovat obchodování na základě signálů poskytovaných `bot.py`.

### Hlavní Komponenty (`opener.mq4`)

*   **HTTP Komunikace**: Používá WinINet.dll pro zasílání požadavků na API `bot.py`.
*   **Zpracování Signálů**: Načítá signály, validuje je a připravuje pro obchodování.
*   **Správa Obchodů**: Otevírá nové obchody (tržní nebo limitní), nastavuje Stop Loss a Take Profit.
*   **Výpočet Velikosti Lotu**: Dynamicky počítá velikost lotu na základě definovaného rizika a vzdálenosti SL.
*   **Zobrazení na Grafu**: Ukazuje stav připojení k API a historii posledních signálů přímo na grafu v MT4.

### Komunikace s `bot.py` API

*   **`FetchAPIData(url)`**:
    *   Hlavní funkce pro získávání dat z API `bot.py`.
    *   Sestavuje HTTP GET požadavek na zadanou URL (typicky `http://localhost:5000/signals`).
    *   Využívá funkce z `wininet.dll` pro provedení HTTP požadavku (viz níže).
    *   Zpracovává odpověď a vrací ji jako textový řetězec (očekává se JSON).
    *   Aktualizuje stav připojení zobrazený na grafu.

### Zpracování a Validace Signálů

*   **`CheckForNewSignals()`**:
    *   Volána periodicky (dle `CheckInterval`) ve funkci `OnTick()`.
    *   Zavolá `FetchAPIData` pro získání nových signálů.
    *   Pokud je odpověď prázdná, nic nedělá.
    *   Jinak volá `ProcessSignalFromJson` pro zpracování každého signálu v odpovědi.
*   **`ProcessSignalFromJson(jsonStr)`**:
    *   Parzuje JSON data obdržená z API.
    *   **Extrakce dat**: Používá pomocnou funkci `GetJsonValue` k extrakci jednotlivých polí (symbol, akce, ceny, ID signálu atd.).
    *   **ID Signálu**: Získá ID signálu (z `id` nebo `signal_id`). Pokud ID chybí, vygeneruje ho z kombinace symbolu, akce a cen.
    *   **Prevence Duplicit**: Kontroluje, zda signál s daným ID již nebyl zpracován pomocí `IsSignalProcessed` a pole `ProcessedSignalIds`. Pokud ano, signál ignoruje.
    *   **Normalizace Symbolu**: Používá `GetFullSymbol` k přizpůsobení názvu symbolu (např. přidání prefixu/suffixu, standardizace "GOLD" na "XAUUSD").
    *   **Validace**:
        *   Kontroluje, zda symbol není prázdný.
        *   Kontroluje, zda vstupní cena a SL jsou kladné.
        *   Ověřuje dostupnost symbolu na trhu pomocí `MarketInfo(symbol, MODE_TICKVALUE)`.
        *   Pokud je signál nevalidní, označí jeho status jako "INVALID" a uloží důvod.
    *   **Uložení do Historie**: Přidá zpracovaný (validní i nevalidní) signál do interní historie (`signalHistory`) pro zobrazení na grafu.
    *   **Spuštění Obchodu**: Pokud je signál validní, `EnableTrading` je `true` a `AlertsOnly` je `false`, zavolá `ExecuteTrade` pro každý definovaný TP s nenulovým rizikem (`RiskPercentTP1`, `RiskPercentTP2`, `RiskPercentTP3`).
    *   Označí signál jako zpracovaný pomocí `AddProcessedSignalId`.

### Otevírání Obchodů

*   **`ExecuteTrade(symbol, action, entryPrice, stopLoss, takeProfit, riskPercent, tpLabel)`**:
    *   Zkontroluje, zda je obchodování povoleno.
    *   Ověří dostupnost symbolu.
    *   **Typ Objednávky**:
        *   Určí typ objednávky (BUY, SELL, BUYLIMIT, SELLLIMIT) na základě `action` a vstupního parametru `UseMarketOrders`.
        *   Pokud `UseMarketOrders` je `true`, vždy použije tržní objednávku (OP_BUY, OP_SELL).
        *   Pokud `UseMarketOrders` je `false` a `action` obsahuje "LIMIT", použije limitní objednávku.
        *   Pro limitní objednávky ověřuje, zda je vstupní cena dostatečně daleko od aktuální tržní ceny (dle `MODE_STOPLEVEL`). Pokud ne, přepne na tržní objednávku.
    *   **Cena Objednávky**: Pro tržní objednávky použije aktuální Ask/Bid, pro limitní objednávky použije `entryPrice`.
    *   **Výpočet Velikosti Lotu**: Zavolá `CalculateLotSize`.
    *   **Normalizace Cen**: Normalizuje ceny (objednávky, SL, TP) na správný počet desetinných míst pro daný symbol.
    *   **Odeslání Objednávky**:
        *   Použije `OrderSend()` k odeslání obchodního příkazu.
        *   Nastaví komentář objednávky (např. "SignalCopierPro_TP1").
        *   Nastaví MagicNumber (kombinace `MagicNumber` a offsetu z `GetTPMagicOffset` podle `tpLabel`).
        *   Implementuje logiku pro opakované pokusy (max 3x) v případě některých chyb (server busy, requote, cena se změnila).
*   **`CalculateLotSize(symbol, riskPercent, stopLossDistance)`**:
    *   Vypočítá velikost lotu na základě:
        *   `riskPercent` (procento z `AccountEquity`).
        *   `stopLossDistance` (vzdálenost SL od vstupní ceny v bodech).
        *   `MODE_TICKVALUE` a `MODE_TICKSIZE` pro daný symbol.
    *   Normalizuje vypočítanou velikost lotu podle `MODE_MINLOT`, `MODE_MAXLOT` a `MODE_LOTSTEP` brokera.
*   **`HandleOrderError(...)`**: Pomocná funkce pro zpracování chyb při odesílání objednávky a rozhodnutí o dalším postupu (např. čekání, obnovení cen).

### Správa MagicNumber

*   **`MagicNumber` (vstupní parametr)**: Základní MagicNumber pro všechny obchody otevřené tímto EA.
*   **`GetTPMagicOffset(tpLabel)`**: Vrací offset (0 pro TP1, 1 pro TP2, 2 pro TP3), který se přičítá k základnímu `MagicNumber`. To umožňuje rozlišit obchody otevřené pro různé úrovně Take Profitu v rámci jednoho signálu.

### Zobrazování Informací na Grafu

*   **Stav Připojení (`statusLabelName`)**:
    *   Zobrazuje "Status: Online" nebo "Status: Offline".
    *   Zobrazuje čas posledního úspěšného spojení.
    *   Aktualizováno funkcí `UpdateConnectionStatus`.
*   **Historie Signálů**:
    *   Zobrazuje tabulku posledních `SIGNAL_HISTORY_SIZE` (výchozí 5) signálů.
    *   Sloupce zahrnují: Čas, Symbol, Akce, Status (VALID/INVALID), Stav Obchodu (NEW, RUNNING, PARTIAL, CLOSED), Vstup, SL, TP1, TP2, TP3.
    *   Barva řádku se mění podle akce (BUY/SELL) a stavu.
    *   Aktualizováno funkcemi `InitializeSignalDisplay` a `UpdateSignalDisplay`.
    *   `UpdateTradeStatuses()`: Periodicky kontroluje stav otevřených a historických obchodů patřících k zobrazeným signálům (podle MagicNumber a symbolu) a aktualizuje sloupec "Stav Obchodu".

### Vstupní Parametry EA

EA má řadu vstupních parametrů, které umožňují uživateli konfigurovat jeho chování:

*   `ServerURL`, `ServerPath`, `ServerPort`: Nastavení pro připojení k API `bot.py`.
*   `CheckInterval`: Jak často (v sekundách) EA kontroluje nové signály.
*   `RiskPercentTP1`, `RiskPercentTP2`, `RiskPercentTP3`: Procento kapitálu riskované na obchody pro jednotlivé TP úrovně.
*   `SlippagePoints`: Povolený skluz v bodech.
*   `EnableTrading`: Povolí/zakáže reálné obchodování.
*   `AlertsOnly`: Pokud `true`, EA nebude obchodovat, pouze zobrazí signály.
*   `MagicNumber`: Základní magické číslo pro obchody.
*   `SymbolPrefix`, `SymbolSuffix`: Pro přizpůsobení názvů symbolů (např. "EURUSD.m").
*   `UseMarketOrders`: Pokud `true`, všechny signály (i limitní) se otevírají jako tržní objednávky.
*   `DebugMode`: Povolí/zakáže detailnější logování do záložky "Experti" v MT4.
*   `TimeoutMilliseconds`: Časový limit pro HTTP požadavky.

### Použití WinINet.dll

Pro HTTP komunikaci s `bot.py` API využívá `opener.mq4` funkce z Windows knihovny `wininet.dll`. To je standardní způsob, jak provádět webové požadavky v MQL4, protože MQL4 nemá nativní podporu pro HTTP na takové úrovni jako MQL5.

*   **Importované funkce**: `InternetOpenW`, `InternetConnectW`, `HttpOpenRequestW`, `HttpSendRequestW`, `InternetReadFile`, `InternetCloseHandle`, `InternetSetOptionW`.
*   **Proces**:
    1.  `InternetOpenW`: Inicializuje použití WinINet funkcí.
    2.  `InternetConnectW`: Naváže spojení se serverem.
    3.  `HttpOpenRequestW`: Vytvoří HTTP GET požadavek.
    4.  `InternetSetOptionW`: Nastaví timeout pro příjem odpovědi.
    5.  `HttpSendRequestW`: Odešle požadavek.
    6.  `InternetReadFile`: Čte odpověď od serveru po částech.
    7.  `InternetCloseHandle`: Uzavře všechny otevřené handlery.
*   **DLL volání**: Je nutné povolit DLL importy v nastavení MetaTrader 4 terminálu, aby EA mohlo tyto funkce používat.

## Instalace a Nastavení

### Předpoklady

*   **Python**: Verze 3.8 nebo vyšší.
*   **MetaTrader 4 Terminál**: Nainstalovaný a funkční.
*   **Přístup k Telegram API**: Platné `API_ID` a `API_HASH` (ty jsou již v kódu `bot.py`, ale pro vlastní aplikaci byste potřebovali své).
*   **Internetové připojení**: Pro `bot.py` k připojení k Telegramu a pro `opener.mq4` k připojení k `bot.py` API.

### Nastavení `bot.py`

1.  **Klonování/Stažení**: Získejte soubor `bot.py`.
2.  **Instalace Závislostí**:
    Otevřete terminál nebo příkazový řádek v adresáři, kde je `bot.py` a spusťte:
    ```bash
    pip install tkinter customtkinter telethon flask waitress requests
    ```
    (Poznámka: `requests` je sice v `bot.py` importován, ale jeho primární využití je interní ve Flasku nebo pro případné budoucí rozšíření. `waitress` je doporučen pro produkční nasazení Flasku.)
3.  **Konfigurace (volitelné)**:
    *   `API_ID` a `API_HASH` jsou již v kódu. Pokud byste chtěli použít vlastní Telegram aplikaci, tyto hodnoty byste změnili.
    *   `DB_NAME`: Název souboru SQLite databáze (výchozí: `signals.db`).
    *   `SESSIONS_DIR`: Adresář pro ukládání Telegram session souborů (výchozí: `sessions`).
4.  **Spuštění**:
    ```bash
    python bot.py
    ```
    Po spuštění se objeví GUI.

### Nastavení `opener.mq4`

1.  **Kopírování Souboru**:
    *   Otevřete MetaTrader 4.
    *   Jděte do `Soubor -> Otevřít složku dat` (File -> Open Data Folder).
    *   Přejděte do složky `MQL4 -> Experts`.
    *   Nakopírujte soubor `opener.mq4` do této složky.
2.  **Kompilace**:
    *   V MetaTrader 4 otevřete `MetaEditor` (ikona knihy nebo F4).
    *   V navigátoru MetaEditoru (vlevo) najděte `opener` pod `Experts`.
    *   Dvakrát klikněte na `opener.mq4` pro jeho otevření.
    *   Klikněte na tlačítko `Kompilovat` (Compile) nebo stiskněte F7. Zkontrolujte, zda nejsou žádné chyby v záložce "Chyby" (Errors).
3.  **Povolení DLL a WebRequest**:
    *   V MetaTrader 4 jděte do `Nástroje -> Možnosti` (Tools -> Options).
    *   Přejděte na kartu `Poradci` (Expert Advisors).
    *   Zaškrtněte:
        *   `Povolit automatické obchodování` (Allow automated trading).
        *   `Povolit import DLL` (Allow DLL imports). **Důležité pro `wininet.dll`**.
        *   `Povolit WebRequest pro uvedené URL` (Allow WebRequest for listed URL).
    *   Do seznamu URL přidejte adresu, na které běží `bot.py` API. Pokud `bot.py` běží na stejném počítači, přidejte `http://localhost:5000` (nebo `http://127.0.0.1:5000`).
    *   Klikněte na OK.

## Použití

### Spuštění `bot.py`

1.  Spusťte `bot.py` z příkazového řádku: `python bot.py`.
2.  **Přihlášení k Telegramu**:
    *   V GUI klikněte na "Vybrat / Spravovat".
    *   Zadejte své telefonní číslo v mezinárodním formátu (např. `+420123456789`) do pole "Nové číslo" a klikněte na "Přidat/Vybrat".
    *   Vyberte číslo ze seznamu a klikněte na "Potvrdit a Připojit".
    *   Pokud se připojujete poprvé, `bot.py` vás požádá o zadání ověřovacího kódu zaslaného na váš Telegram, a případně o 2FA heslo, prostřednictvím vyskakovacích dialogových oken.
3.  **Výběr Kanálů**:
    *   Po úspěšném připojení klikněte na tlačítko "🔄 Obnovit" vedle "Kanály a skupiny".
    *   Zobrazí se seznam vašich kanálů a skupin.
    *   Pro každý kanál, který chcete monitorovat:
        *   Vyberte metodu parsování ("SniperPro" nebo "Standardní").
        *   Klikněte na tlačítko "Monitorovat". Tlačítko změní text na "Zastavit".
4.  Aplikace nyní monitoruje vybrané kanály a ukládá signály do databáze. Flask API server běží na pozadí.

### Nasazení a Konfigurace `opener.mq4`

1.  **Připojení na Graf**:
    *   V MetaTrader 4 v okně "Navigátor" (Navigator) najděte `opener` pod "Poradci" (Expert Advisors).
    *   Přetáhněte `opener` na graf měnového páru, na kterém chcete obchodovat (např. XAUUSD).
2.  **Nastavení Vstupních Parametrů**:
    *   Při přetažení EA na graf se objeví okno s nastaveními. Přejděte na kartu `Vstupy` (Inputs).
    *   **`ServerURL`**: Nastavte na `localhost` (nebo IP adresu, kde běží `bot.py`, bez `http://`).
    *   **`ServerPort`**: Nastavte na `5000` (nebo port, na kterém běží Flask API v `bot.py`).
    *   **`ServerPath`**: Nastavte na `/signals`.
    *   **`EnableTrading`**: Nastavte na `true`, pokud chcete, aby EA reálně obchodovalo. Pro testování nebo jen sledování nastavte na `false`.
    *   **`MagicNumber`**: Zvolte unikátní číslo, pokud používáte jiné EA.
    *   **`RiskPercentTP1/TP2/TP3`**: Nastavte požadované riziko pro jednotlivé TP. Pokud nechcete obchodovat určitý TP, nastavte jeho risk na 0.
    *   **`SymbolPrefix`/`SymbolSuffix`**: Pokud váš broker používá prefixy/suffixy (např. ".m" za "EURUSD"), nastavte je zde. Pro standardní symboly nechte prázdné.
    *   Ostatní parametry nastavte dle svých preferencí.
    *   Klikněte na OK.
3.  **Kontrola Funkčnosti**:
    *   **Smajlík EA**: V pravém horním rohu grafu by se měl objevit název EA (`opener`) a veselý smajlík (`🙂`). Pokud je smajlík smutný (`🙁`), automatické obchodování není pro toto EA povoleno. Zkontrolujte nastavení:
        *   Hlavní tlačítko "Automatické obchodování" na liště nástrojů MT4 musí být zelené.
        *   V nastavení EA na grafu (pravý klik na graf -> Poradci -> Vlastnosti -> záložka "Obecné") musí být zaškrtnuto "Povolit reálné obchodování".
    *   **Stav Připojení**: V levém horním rohu grafu by se měl zobrazit stav připojení k API `bot.py` (např. "Status: Online") a tabulka pro historii signálů.
        *   Pokud je status "Offline", ověřte:
            *   Zda `bot.py` skutečně běží.
            *   Zda jsou `ServerURL`, `ServerPort`, `ServerPath` v EA správně nastaveny.
            *   Zda je v MT4 povoleno `WebRequest` pro danou URL (`Nástroje -> Možnosti -> Poradci`).
            *   Zda firewall neblokuje komunikaci.
    *   **Logy MT4**: Zkontrolujte záložky "Experti" (Experts) a "Deník" (Journal) ve spodní části MT4 (okno "Terminál", přístupné přes Ctrl+T) pro jakékoliv chybové hlášky nebo informační zprávy od EA. Tyto logy jsou klíčové pro diagnostiku problémů.

EA nyní periodicky (dle `CheckInterval`) kontroluje nové signály z `bot.py` a obchoduje podle nich, pokud jsou splněny všechny podmínky.

## API Endpoints (`bot.py`)

### `GET /signals`

*   **Účel**: Poskytuje `opener.mq4` (nebo jinému klientovi) seznam nových obchodních signálů, které jsou připraveny k zpracování.
*   **Metoda**: `GET`
*   **Endpoint**: `/signals`
*   **Odpověď**: JSON pole objektů. Každý objekt reprezentuje signál se statusem `'new'`.
    *   **Formát objektu signálu**:
        ```json
        {
            "id": 123, // Unikátní ID signálu v databázi
            "symbol": "XAUUSD",
            "action": "BUY", // "BUY" nebo "SELL"
            "entry_price": 2300.50, // Referenční vstupní cena (pro SniperPro INITIAL) nebo konkrétní vstup (pro STANDARD)
            "signal_group_id": "telegram_dialog_id_XAUUSD_BUY_timestamp", // ID skupiny signálů (pro SniperPro)
            "trade_label": "T1_AUTO", // Label obchodu (např. T1_AUTO, T2_AUTO, RE_AUTO, STD_TRADE)
            "signal_type": "INITIAL_T1", // Typ signálu (INITIAL_T1, INITIAL_T2_DEFAULT, UPDATE_T2, RE_ENTRY, STANDARD)
            "sl_value": 40.0, // Hodnota Stop Loss
            "sl_value_type": "PIPS", // Typ hodnoty SL ("PIPS" nebo "PRICE")
            "tp_value": 40.0, // Hodnota Take Profit
            "tp_value_type": "PIPS", // Typ hodnoty TP ("PIPS" nebo "PRICE")
            "tp2_value": null, // Volitelná druhá hodnota TP (používá se pro STANDARD signály s více TP)
            "tp2_value_type": null, // Typ druhé hodnoty TP (vždy "PRICE", pokud je tp2_value nastaveno)
            "timestamp": "YYYY-MM-DD HH:MM:SS.ffffff", // Časová značka vytvoření signálu
            "status": "new",
            "ticket": null // Číslo MT4 ticketu (vyplní se po reportování obchodu)
        }
        ```
*   **Příklad odpovědi**:
    ```json
    [
        {
            "id": 1, "symbol": "XAUUSD", "action": "BUY", "entry_price": 1800.50,
            "signal_group_id": "grp1", "trade_label": "T1_AUTO", "signal_type": "INITIAL_T1",
            "sl_value": 40.0, "sl_value_type": "PIPS",
            "tp_value": 40.0, "tp_value_type": "PIPS",
            "tp2_value": null, "tp2_value_type": null, "ticket": null,
            "status": "new", "timestamp": "2023-10-27 10:00:00.000000"
        }
    ]
    ```

### `POST /report_trade`

*   **Účel**: Umožňuje `opener.mq4` nahlásit zpět `bot.py` výsledek zpracování signálu, typicky číslo otevřeného obchodu (ticket).
*   **Metoda**: `POST`
*   **Endpoint**: `/report_trade`
*   **Tělo požadavku (JSON)**:
    ```json
    {
        "id": 123,       // DB ID signálu, který byl zpracován
        "ticket": 789012 // Číslo ticketu otevřeného/modifikovaného obchodu v MT4 (může být null pro UPDATE_T2, pokud se nemění)
    }
    ```
*   **Odpověď**:
    *   **Úspěch (HTTP 200)**:
        ```json
        {"status": "ok", "message": "Trade/Update reported successfully"}
        ```
    *   **Chyba (HTTP 400, 404, 500)**:
        ```json
        {"status": "error", "message": "Důvod chyby"}
        ```
*   **Logika na straně serveru (`bot.py`)**:
    *   Najde signál v DB podle poskytnutého `id`.
    *   Pokud signál neexistuje nebo již není ve stavu `'new'`, vrátí chybu/informaci.
    *   Na základě `signal_type` v DB:
        *   Pro `INITIAL_T1`, `INITIAL_T2_DEFAULT`, `RE_ENTRY`, `STANDARD`:
            *   Uloží `ticket` k signálu.
            *   Změní `status` signálu na `'open'`.
        *   Pro `UPDATE_T2`:
            *   Změní `status` signálu na `'processed_update'`.
            *   Volitelně uloží `ticket`, pokud byl v DB prázdný.

## Struktura Databáze (`signals.db`)

Databáze `signals.db` je SQLite databáze, která obsahuje jednu hlavní tabulku `signals`.

### Tabulka `signals`

Uchovává informace o všech detekovaných a zpracovaných obchodních signálech.

| Sloupec            | Typ                               | Popis                                                                                                | Příklad                                       |
| ------------------ | --------------------------------- | ---------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `id`               | INTEGER PRIMARY KEY AUTOINCREMENT | Unikátní identifikátor záznamu.                                                                    | `1`                                           |
| `symbol`           | TEXT                              | Symbol měnového páru nebo instrumentu.                                                               | `XAUUSD`, `EURUSD`                            |
| `action`           | TEXT                              | Typ obchodní akce ("BUY" nebo "SELL").                                                              | `BUY`                                         |
| `entry_price`      | REAL                              | Referenční vstupní cena (pro SniperPro INITIAL) nebo konkrétní vstupní cena (pro STANDARD).          | `1800.50`                                     |
| `timestamp`        | DATETIME                          | Časová značka, kdy byl signál zaznamenán nebo vytvořen v `bot.py`.                                  | `2023-10-27 10:00:00.123456`                  |
| `status`           | TEXT                              | Aktuální stav signálu (`new`, `open`, `processed_update`, `closed` - closed zatím není implementováno). | `new`                                         |
| `ticket`           | INTEGER                           | Číslo obchodního ticketu z MetaTrader 4, pokud byl obchod otevřen.                                   | `12345678`                                    |
| `signal_group_id`  | TEXT                              | Identifikátor skupiny signálů (používá se pro SniperPro k propojení T1, T2 a aktualizací).             | `dialogid_XAUUSD_BUY_1678886400`              |
| `trade_label`      | TEXT                              | Popisný štítek obchodu (např. `T1_AUTO`, `T2_AUTO`, `RE_AUTO`, `STD_TRADE`).                          | `T1_AUTO`                                     |
| `signal_type`      | TEXT                              | Typ signálu (např. `INITIAL_T1`, `INITIAL_T2_DEFAULT`, `UPDATE_T2`, `RE_ENTRY`, `STANDARD`).       | `INITIAL_T1`                                  |
| `sl_value`         | REAL                              | Hodnota Stop Loss.                                                                                   | `40.0` (pro PIPS) nebo `1795.50` (pro PRICE)  |
| `tp_value`         | REAL                              | Hodnota Take Profit.                                                                                 | `40.0` (pro PIPS) nebo `1805.50` (pro PRICE)  |
| `sl_value_type`    | TEXT                              | Typ hodnoty SL (`PIPS` nebo `PRICE`).                                                                | `PIPS`                                        |
| `tp_value_type`    | TEXT                              | Typ hodnoty TP (`PIPS` nebo `PRICE`).                                                                | `PIPS`                                        |
| `tp2_value`        | REAL                              | Volitelná druhá hodnota Take Profit (primárně pro `STANDARD` signály s více TP úrovněmi).             | `1810.75`                                     |
| `tp2_value_type`   | TEXT                              | Typ druhé hodnoty TP (vždy `PRICE`, pokud je `tp2_value` nastaveno, jinak NULL).                      | `PRICE`                                       |

## Přispívání / Vývoj

Příspěvky do projektu jsou vítány. Pokud máte nápady na vylepšení nebo opravy chyb:

1.  Vytvořte si "fork" repozitáře.
2.  Vytvořte novou větev pro vaši funkci nebo opravu (`git checkout -b nazev-funkce`).
3.  Proveďte změny a commitněte je (`git commit -am 'Přidána nová funkce X'`).
4.  Pushněte změny do své větve (`git push origin nazev-funkce`).
5.  Vytvořte "Pull Request".

Při vývoji se snažte dodržovat stávající styl kódu a přidávat komentáře tam, kde je to vhodné.

## Řešení Problémů

*   **`bot.py` se nepřipojí k Telegramu**:
    *   Zkontrolujte správnost `API_ID` a `API_HASH` (pokud jste je měnili).
    *   Ověřte své internetové připojení.
    *   Ujistěte se, že zadáváte telefonní číslo ve správném mezinárodním formátu.
    *   Smažte soubor `.session` pro dané číslo ze složky `sessions` a zkuste se přihlásit znovu.
*   **`opener.mq4` nezobrazuje status "Online"**:
    *   Ujistěte se, že `bot.py` běží a Flask server naslouchá na správné adrese a portu (výchozí `localhost:5000`).
    *   Zkontrolujte nastavení `ServerURL`, `ServerPort`, `ServerPath` v parametrech EA.
    *   Ověřte, že v MT4 (`Nástroje -> Možnosti -> Poradci`) je povoleno `Povolit WebRequest pro uvedené URL` a že URL `http://localhost:5000` (nebo odpovídající) je v seznamu.
    *   Zkontrolujte firewall, zda neblokuje komunikaci mezi MT4 a `bot.py`.
*   **EA neotevírá obchody**:
    *   Zkontrolujte, zda je `EnableTrading` v EA nastaveno na `true`.
    *   Ověřte, zda je v MT4 povoleno "Automatické obchodování" (tlačítko na hlavní liště a v nastavení EA na grafu).
    *   Podívejte se do záložky "Experti" a "Deník" v MT4 pro chybové zprávy. Mohou indikovat problémy s velikostí lotu, nesprávnými cenami SL/TP, nedostatkem prostředků atd.
    *   Ujistěte se, že `RiskPercentTP1/2/3` jsou nastaveny na hodnoty větší než 0 pro TP úrovně, které chcete obchodovat.
    *   Zkontrolujte, zda přicházejí validní signály z `bot.py` (měly by se objevit v tabulce na grafu).
*   **Chyba "DLL calls not allowed" v `opener.mq4`**:
    *   V MT4 jděte do `Nástroje -> Možnosti -> Poradci` a zaškrtněte `Povolit import DLL`.
*   **Signály se v `bot.py` parsují nesprávně**:
    *   Zkontrolujte, zda je pro daný Telegram kanál vybrána správná "Metoda parsování" v GUI `bot.py`.
    *   Ověřte regulární výrazy v `parse_sniper_pro` nebo `parse_standard_signal`, zda odpovídají formátu zpráv ve vašem kanálu.
*   **Problémy se symboly (např. XAUUSD vs GOLD)**:
    *   `opener.mq4` se snaží normalizovat symboly, ale ujistěte se, že váš broker podporuje symboly tak, jak jsou posílány z `bot.py`, nebo použijte `SymbolPrefix` a `SymbolSuffix` v nastavení EA.
