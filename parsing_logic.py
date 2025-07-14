import re
import logging

# Signal type constants, which might be shared or passed around
SIGNAL_TYPE_IGNORE = "IGNORE"
SIGNAL_TYPE_UNKNOWN = "UNKNOWN"
SIGNAL_TYPE_RE_ENTRY = "RE_ENTRY"
SIGNAL_TYPE_INITIAL = "INITIAL"
SIGNAL_TYPE_UPDATE_SLTP = "UPDATE_SLTP"
SIGNAL_TYPE_STANDARD = "STANDARD"

def parse_sniper_pro(message_text: str) -> dict | None:
    """
    Parses a message to identify a trading signal from the "SniperPro" format.
    """
    message_text_cleaned = message_text.strip()

    # Ignore patterns
    re_ignore_pips = re.compile(r"^\d+\s+pips\s+ruining\s*✅", re.IGNORECASE)
    re_ignore_book_profit = re.compile(r"^Book\s+some\s+profit", re.IGNORECASE)
    re_ignore_reentry_closed = re.compile(r"^(Not\s+active\s+re\s*entry\s+closed|Closed\s+re\s*entry)", re.IGNORECASE)

    if (re_ignore_pips.search(message_text_cleaned) or
        re_ignore_book_profit.search(message_text_cleaned) or
        re_ignore_reentry_closed.search(message_text_cleaned)):
        return {'type': SIGNAL_TYPE_IGNORE, 'reason': 'Matched ignore pattern'}

    # Re-entry signal pattern
    re_reentry = re.compile(
        r"FOR\s+(GOLD|XAUUSD)\s+REE\s+ENTRY(?:[\s\S]*?)WITH\s+SL\s*[:\s]?\s*([\d\.]+)",
        re.IGNORECASE | re.DOTALL
    )
    match_reentry = re_reentry.search(message_text_cleaned)
    if match_reentry:
        symbol_raw = match_reentry.group(1).upper()
        symbol = "XAUUSD" if symbol_raw == "GOLD" else symbol_raw
        try:
            sl_price = float(match_reentry.group(2))
            return {'type': SIGNAL_TYPE_RE_ENTRY, 'symbol': symbol, 'sl_price': sl_price}
        except ValueError:
            logging.warning(f"Could not convert SL price in re-entry signal: {match_reentry.group(2)}")
            return {'type': SIGNAL_TYPE_UNKNOWN, 'reason': 'Re-entry SL price conversion error'}

    # Initial signal pattern (e.g., "GOLD BUY 1234.5")
    re_initial = re.compile(
        r"^(GOLD|XAUUSD)\s+(BUY|SELL|SEEL)\s+([\d\.]+)(?:\s+[\d\.]+)?(?:\s+small\s+lot)?$",
        re.IGNORECASE
    )
    match_initial = re_initial.search(message_text_cleaned)
    if match_initial:
        # Avoid matching messages that are actually SL/TP updates
        has_sl = re.search(r"Sl\s*[:\s]?\s*[\d\.]+", message_text_cleaned, re.IGNORECASE)
        has_tp = re.search(r"Tp\s*[:\s]?\s*[\d\.]+", message_text_cleaned, re.IGNORECASE)
        if has_sl and has_tp:
            logging.debug(f"Text matches INITIAL pattern but contains SL/TP. Trying as UPDATE_SLTP: {message_text_cleaned[:50]}")
        else:
            symbol_raw = match_initial.group(1).upper()
            symbol = "XAUUSD" if symbol_raw == "GOLD" else symbol_raw
            action_raw = match_initial.group(2).upper()
            action = "SELL" if action_raw == "SEEL" else action_raw
            try:
                entry_price_ref = float(match_initial.group(3))
                # For SniperPro, an initial signal always implies two trades.
                # The logic to create these two trades will be in the main bot file,
                # this parser just provides the core data.
                return {'type': SIGNAL_TYPE_INITIAL, 'symbol': symbol, 'action': action, 'entry_price_ref': entry_price_ref}
            except ValueError:
                logging.warning(f"Could not convert entry price in initial signal: {match_initial.group(3)}")
                return {'type': SIGNAL_TYPE_UNKNOWN, 'reason': 'Initial signal entry price conversion error'}

    # SL/TP Update pattern
    sl_pattern_general = r"Sl\s*[:\s]?\s*([\d\.]+)"
    match_sl_general = re.search(sl_pattern_general, message_text_cleaned, re.IGNORECASE)
    if match_sl_general:
        sl_price_str = match_sl_general.group(1)
        tp_matches_all = re.findall(r"Tp\s*[:\s]?\s*([\d\.]+)", message_text_cleaned, re.IGNORECASE)
        if tp_matches_all:
            try:
                sl_price = float(sl_price_str)
                tp_prices = [float(tp_str) for tp_str in tp_matches_all]
                return {'type': SIGNAL_TYPE_UPDATE_SLTP, 'sl_price': sl_price, 'tp_prices': tp_prices}
            except ValueError:
                logging.warning(f"Could not convert prices in UPDATE_SLTP (general): SL='{sl_price_str}', TPs='{tp_matches_all}'")
                return {'type': SIGNAL_TYPE_UNKNOWN, 'reason': 'SL/TP update price conversion error (general)'}
        else:
            logging.debug(f"UPDATE_SLTP: Found SL='{sl_price_str}' but no TPs in the message. Not a valid UPDATE_SLTP.")

    logging.debug(f"Message was not recognized by any SniperPro parser: '{message_text_cleaned}'")
    return {'type': SIGNAL_TYPE_UNKNOWN, 'reason': 'No SniperPro pattern matched'}


def parse_standard_signal(message_text: str) -> dict | None:
    """
    Parses a message to identify a trading signal from a standard, more generic format.
    """
    message_text_lower = message_text.lower()

    # Clean the message from ignore phrases
    lines = message_text_lower.split('\n')
    cleaned_lines = [line for line in lines if not ("pips ruining" in line or "book some profit" in line or "closed re entry" in line or "not active" in line)]
    message_text_cleaned_for_standard = "\n".join(cleaned_lines).strip()

    if not message_text_cleaned_for_standard:
        return None

    # Pattern: "BUY XAUUSD 1234.5"
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
            return {'type': SIGNAL_TYPE_STANDARD, 'symbol': symbol, 'action': action, 'entry_price_ref': entry_price, 'sl_price': sl, 'tp_prices': tp_values}
        except ValueError:
            logging.warning(f"Error converting numbers in standard parser (format 1) for: {message_text}")
            return None

    # Pattern: "XAUUSD SELL 1234.5" or "XAUUSD SELL LIMIT 1234.5"
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
            return {'type': SIGNAL_TYPE_STANDARD, 'symbol': symbol, 'action': action, 'entry_price_ref': entry_price, 'sl_price': sl, 'tp_prices': tp_values}
        except ValueError:
            logging.warning(f"Error converting numbers in standard parser (format 2) for: {message_text}")
            return None

    return None
