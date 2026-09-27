"""Generate DejaVu's synthetic payment-exception dataset.

Everything here is fictional: banks, BICs, companies, people and account numbers.
The data is *hand-designed* rather than random so it contains the kind of
patterns a real payment-operations desk sees:

    P1  Pearl Delta Commercial Bank moved to 15-digit accounts on 25 Aug (prefix 601)
    P2  Golconda Commercial Bank credits next day if we arrive after 14:30 SGT
    P3  Tungabhadra Co-operative Bank rejects abbreviated company names (Pvt Ltd)
    P4  "Desert Star General Trading LLC" is a recurring sanctions false positive
    P5  Victoria Harbour Bank wants an invoice number on trade payments > USD 10k
    P6  Nordkyst Bank changed its USD correspondent on 1 Sep (ACBKUS33 -> HFBKUS33)
    P7  Arcadia Foods' ERP re-sends its payment file every Monday morning
    P8  Our IDR nostro runs short on the last business days of the month

...mixed with one-off "noise" cases that share the same error codes but have a
different root cause (so the agent is punished for over-generalising), and a
couple of cases that only make sense with time awareness (a rule that started
on a date, a cut-off that depends on submission time).

Nothing in a case's visible fields gives away the answer. The ground truth only
reaches memory through the analyst's resolution note, after the case is solved.

Run:  python scripts/generate_dataset.py
Writes: data/history.json, data/live.json, data/entities.json
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SGT = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
rng = random.Random(20260927)

CLIENTS = {
    "sunrise": {"name": "Sunrise Textiles Pte Ltd", "account": "0721-004512-3", "industry": "Textiles"},
    "arcadia": {"name": "Arcadia Foods Pte Ltd", "account": "0721-009877-1", "industry": "Food distribution"},
    "blueharbour": {"name": "Blue Harbour Logistics Pte Ltd", "account": "0721-013340-8", "industry": "Freight & logistics"},
    "kestrel": {"name": "Kestrel Components Pte Ltd", "account": "0721-006621-5", "industry": "Electronics"},
    "orchid": {"name": "Orchid Pharma Asia Pte Ltd", "account": "0721-011208-2", "industry": "Pharmaceuticals"},
    "lotus": {"name": "Lotus Hospitality Group Pte Ltd", "account": "0721-015532-9", "industry": "Hospitality"},
    "tanjong": {"name": "Tanjong Marine Services Pte Ltd", "account": "0721-003390-4", "industry": "Marine services"},
    "vantage": {"name": "Vantage Analytics Pte Ltd", "account": "0721-017745-6", "industry": "IT services"},
}

BANKS = {
    "PDCB": {"name": "Pearl Delta Commercial Bank", "bic": "PDCBCNGZXXX", "city": "Guangzhou", "country": "CN"},
    "GOLC": {"name": "Golconda Commercial Bank", "bic": "GOLCINHHXXX", "city": "Hyderabad", "country": "IN"},
    "TUNG": {"name": "Tungabhadra Co-operative Bank", "bic": "TUNGINBBXXX", "city": "Bengaluru", "country": "IN"},
    "DRBK": {"name": "Desert Rose Bank", "bic": "DRBKAEADXXX", "city": "Dubai", "country": "AE"},
    "VHBK": {"name": "Victoria Harbour Bank", "bic": "VHBKHKHHXXX", "city": "Hong Kong", "country": "HK"},
    "NKYS": {"name": "Nordkyst Bank", "bic": "NKYSNOBBXXX", "city": "Bergen", "country": "NO"},
    "NSBK": {"name": "Nusantara Sentral Bank", "bic": "NSBKIDJAXXX", "city": "Jakarta", "country": "ID"},
    "SGRB": {"name": "Saigon Riverside Bank", "bic": "SGRBVNVXXXX", "city": "Ho Chi Minh City", "country": "VN"},
    "MBSB": {"name": "Manila Bay Savings Bank", "bic": "MBSBPHMMXXX", "city": "Manila", "country": "PH"},
    "RHHB": {"name": "Rheinland Handelsbank", "bic": "RHHBDEFFXXX", "city": "Frankfurt", "country": "DE"},
    "THUB": {"name": "Thames Union Bank", "bic": "THUBGB2LXXX", "city": "London", "country": "GB"},
    "STHB": {"name": "Straits Heritage Bank", "bic": "STHBMYKPXXX", "city": "Penang", "country": "MY"},
}

INTERMEDIARIES = {
    "ACBK": {"name": "Atlantic Clearing Bank, New York", "bic": "ACBKUS33XXX"},
    "HFBK": {"name": "Hudson Federal Bank, New York", "bic": "HFBKUS33XXX"},
    "CRMC": {"name": "Coromandel Clearing Bank, Mumbai", "bic": "CRMCINBBXXX"},
    "ALBN": {"name": "Albion Clearing Bank, London", "bic": "ALBNGB2LXXX"},
}

REASON_TEXT = {
    "AC01": "Incorrect account number",
    "AC04": "Closed account number",
    "AM04": "Insufficient funds",
    "AM05": "Duplication",
    "BE01": "Inconsistent with end customer",
    "RC01": "Bank identifier incorrect",
    "RR04": "Regulatory reason",
}


def digits(n: int, first: str = "") -> str:
    body = "".join(str(rng.randint(0, 9)) for _ in range(n - len(first)))
    return first + body


def default_account(bank: str) -> str:
    return {
        "PDCB": digits(12, "620"),
        "GOLC": digits(15, "5020"),
        "TUNG": digits(14, "118"),
        "DRBK": "AE" + digits(21, "07033"),
        "VHBK": f"{digits(3)}-{digits(6)}-{digits(3)}",
        "NKYS": "NO" + digits(13, "93"),
        "NSBK": digits(13, "1270"),
        "SGRB": digits(12, "7001"),
        "MBSB": digits(12, "0045"),
        "RHHB": "DE" + digits(20, "89370400"),
        "THUB": "GB" + digits(2) + "THUB" + digits(14, "4011"),
        "STHB": digits(12, "5140"),
    }[bank]


def amount_for(ccy: str) -> float:
    lo, hi = {
        "USD": (8_000, 60_000), "INR": (450_000, 2_600_000), "IDR": (180_000_000, 1_250_000_000),
        "EUR": (5_000, 40_000), "GBP": (3_000, 25_000), "PHP": (250_000, 2_000_000),
        "MYR": (20_000, 150_000), "AED": (30_000, 220_000),
    }[ccy]
    value = rng.uniform(lo, hi)
    step = 1_000_000 if ccy == "IDR" else (1_000 if ccy in ("INR", "PHP") else 10)
    return round(value / step) * step


# ---------------------------------------------------------------------------
# Hand-authored case specs. Order = chronological.
# p = pattern id (None = one-off noise case)
# ---------------------------------------------------------------------------
HISTORY = [
    # ---- week of 17 Aug ----
    dict(at="2026-08-17 09:40", p="P7", rc="CLIENT_ERP_DUPLICATE", et="HELD", code="AM05",
         client="arcadia", bank="SGRB", ben="Mekong Delta Seafood JSC", ccy="USD", amount=27_450,
         msg="Duplicate check: identical instruction received at 06:12 and 06:47 SGT (same beneficiary, amount and value date). Second instruction held.",
         remit="INV MDS-4410 frozen seafood", minutes=38,
         note="Arcadia's treasury confirmed their ERP weekend batch re-ran on Monday morning and re-sent the whole payment file. Cancelled the second instruction and informed Arcadia's IT team, who opened a ticket with their ERP vendor."),
    dict(at="2026-08-18 11:05", p=None, rc="CLIENT_TYPO_IN_DETAILS", et="REJECTED", code="AC01",
         client="kestrel", bank="PDCB", ben="Dongguan Precision Parts Co., Ltd.", ccy="USD",
         account="62014488091", remit="INV DPP-2231 machined housings", minutes=25,
         msg="Beneficiary account 62014488091 not found. Please verify and resend.",
         note="Account number had only 11 digits - Kestrel dropped a digit while keying it. Kestrel confirmed the correct 12-digit number; repaired and resent. Pearl Delta accepted it the same day."),
    dict(at="2026-08-19 14:20", p="P4", rc="SANCTIONS_POTENTIAL_MATCH", et="SCREENING_HOLD", code=None,
         client="blueharbour", bank="DRBK", ben="Desert Star General Trading LLC", ccy="USD",
         remit="INV DSGT-0977 port handling charges", minutes=95,
         msg="Sanctions screening hit: beneficiary 'DESERT STAR GENERAL TRADING LLC' vs list entry 'DESERT STAR SHIPPING FZE' (match score 0.86). Payment held.",
         note="First time we saw this hit. Escalated to Compliance. Compliance reviewed the trade licence, ownership and address - Desert Star General Trading LLC is a different company from the listed Desert Star Shipping FZE. Cleared as a false positive, clearance reference CLR-2026-0819-07. Compliance released the payment two days later."),
    dict(at="2026-08-20 16:10", p="P2", rc="MISSED_CUTOFF_NEXT_DAY_CREDIT", et="NON_RECEIPT_CLAIM", code=None,
         client="vantage", bank="GOLC", ben="Srinivasa Infotech Private Limited", ccy="INR",
         submitted="2026-08-19 15:05", inter="CRMC", remit="INV SI-8841 data engineering services Jul", minutes=50,
         tracker="Tracker: delivered to Golconda Commercial Bank 19 Aug 15:09 SGT - status: credit pending",
         msg="Client reports the beneficiary did not receive funds on the value date (19 Aug). Please investigate.",
         note="Checked the tracker - nothing stuck at the intermediary. Golconda Commercial Bank confirmed our payment arrived after their same-day cut-off of 12:00 IST (14:30 SGT), so it was credited the next morning (20 Aug, 09:10 IST). Told Vantage no trace is needed."),
    dict(at="2026-08-21 10:30", p="P3", rc="NAME_LEGAL_SUFFIX_MISMATCH", et="REJECTED", code="BE01",
         client="orchid", bank="TUNG", ben="Deccan Bio Labs Pvt Ltd", ccy="INR",
         inter="CRMC", remit="INV DBL-3307 lab consumables", minutes=45,
         msg="Beneficiary name does not match the account holder name. Payment rejected.",
         note="Tungabhadra Co-operative Bank does exact name matching. The account is registered as 'DECCAN BIO LABS PRIVATE LIMITED' but we sent 'Deccan Bio Labs Pvt Ltd'. Amended the name to the full legal suffix and resent - accepted."),

    # ---- week of 24 Aug ----
    dict(at="2026-08-24 09:15", p="P7", rc="CLIENT_ERP_DUPLICATE", et="HELD", code="AM05",
         client="arcadia", bank="STHB", ben="Penang Spice Traders Sdn Bhd", ccy="MYR",
         remit="INV PST-1182 spices", minutes=20,
         msg="Duplicate check: identical instruction received at 06:15 and 06:40 SGT (same beneficiary, amount and value date). Second instruction held.",
         note="Same as last Monday: Arcadia's ERP re-sent its weekend batch at 06:40. Cancelled the duplicate. Arcadia IT is investigating their job scheduler."),
    dict(at="2026-08-24 15:30", p="P5", rc="MISSING_INVOICE_REFERENCE", et="REJECTED", code="RR04",
         client="kestrel", bank="VHBK", ben="Kowloon Circuit Supplies Ltd", ccy="USD", amount=24_800,
         inter="ACBK", remit="Payment for goods", minutes=55,
         msg="Payment rejected. Reason: RR04 Regulatory reason.",
         note="Called Victoria Harbour Bank: their compliance rules require an invoice number in the remittance information for trade payments above USD 10,000. Got invoice KCS-55812 from Kestrel and resubmitted with it - accepted."),
    dict(at="2026-08-25 11:45", p=None, rc="CLIENT_TYPO_IN_DETAILS", et="REJECTED", code="RC01",
         client="tanjong", bank="NKYS", ben="Bergen Subsea Services AS", ccy="USD",
         inter="ACBK", bic_sent="NKYSNOBX", remit="INV BSS-620 ROV hire", minutes=20,
         msg="Bank identifier NKYSNOBX incorrect. Payment rejected.",
         note="The client keyed the BIC as NKYSNOBX instead of NKYSNOBB. Corrected the BIC and resent via Atlantic Clearing Bank New York as usual."),
    dict(at="2026-08-26 10:50", p="P1", rc="ACCOUNT_FORMAT_CHANGED", et="REJECTED", code="AC01",
         client="sunrise", bank="PDCB", ben="Guangzhou Hengtai Trading Co., Ltd.", ccy="USD",
         account="620144880913", remit="INV HT-20931 cotton yarn", minutes=60,
         msg="Beneficiary account 620144880913 not found. Please verify and resend.",
         note="Called Pearl Delta Commercial Bank's Guangzhou operations desk: they migrated to 15-digit account numbers on 25 Aug 2026. Old 12-digit numbers must be prefixed with branch code 601. Repaired the account to 601620144880913 and resent - credited the same day."),
    dict(at="2026-08-26 16:20", p=None, rc="BENEFICIARY_ACCOUNT_CLOSED", et="RETURNED", code="AC04",
         client="lotus", bank="MBSB", ben="Cebu Linen Supply Inc", ccy="PHP",
         remit="INV CLS-9021 linen", minutes=35,
         msg="Payment returned. Reason: AC04 Closed account number.",
         note="The beneficiary closed this account in July. Funds returned; Lotus got the new account details from the supplier and re-initiated."),
    dict(at="2026-08-27 11:30", p=None, rc="POSSIBLE_DUPLICATE_VERIFY", et="HELD", code="AM05",
         client="lotus", bank="NSBK", ben="PT Bali Villas Management", ccy="IDR",
         remit="Management fee Aug", minutes=25,
         msg="Duplicate check: two instructions with the same beneficiary and amount received at 10:02 and 10:48 SGT. Second instruction held.",
         note="Lotus confirmed both payments were intended - two separate monthly invoices that happen to have the same amount. Released the second payment after the client's confirmation."),
    dict(at="2026-08-27 14:10", p="P2", rc="MISSED_CUTOFF_NEXT_DAY_CREDIT", et="NON_RECEIPT_CLAIM", code=None,
         client="orchid", bank="GOLC", ben="Hyderabad Organics Private Limited", ccy="INR",
         submitted="2026-08-26 16:05", inter="CRMC", remit="INV HO-5520 active ingredients", minutes=30,
         tracker="Tracker: delivered to Golconda Commercial Bank 26 Aug 16:08 SGT - status: credit pending",
         msg="Client reports the beneficiary did not receive funds on the value date (26 Aug). Please investigate.",
         note="Golconda Commercial Bank again: the payment landed after their 12:00 IST (14:30 SGT) same-day cut-off and was credited the next business day morning. Advised Orchid to submit INR payments before 14:00 SGT for same-day value."),
    dict(at="2026-08-28 10:05", p="P8", rc="NOSTRO_FUNDING_SHORTFALL", et="HELD", code="AM04",
         client="lotus", bank="NSBK", ben="PT Bali Villas Management", ccy="IDR", balance="passed",
         remit="Villa lease Sep", minutes=70,
         msg="Payment held: AM04 Insufficient funds on the IDR settlement account. Client debit account balance check: passed.",
         note="Our IDR nostro at Nusantara Sentral Bank was short - month-end outflows drained it. Treasury topped it up by IDR 5bn at 13:00 and we released the payment after funding."),
    dict(at="2026-08-28 15:40", p="P4", rc="SANCTIONS_KNOWN_FALSE_POSITIVE", et="SCREENING_HOLD", code=None,
         client="blueharbour", bank="DRBK", ben="Desert Star General Trading LLC", ccy="USD",
         remit="INV DSGT-1012 port handling charges", minutes=30,
         msg="Sanctions screening hit: beneficiary 'DESERT STAR GENERAL TRADING LLC' vs list entry 'DESERT STAR SHIPPING FZE' (match score 0.86). Payment held.",
         note="Same false-positive hit as 19 Aug. Routed to Compliance with the prior clearance CLR-2026-0819-07. Compliance re-confirmed and released it the same day."),

    # ---- week of 31 Aug ----
    dict(at="2026-08-31 09:20", p="P7", rc="CLIENT_ERP_DUPLICATE", et="HELD", code="AM05",
         client="arcadia", bank="SGRB", ben="Mekong Delta Seafood JSC", ccy="USD",
         remit="INV MDS-4466 frozen seafood", minutes=12,
         msg="Duplicate check: identical instruction received at 06:10 and 06:44 SGT (same beneficiary, amount and value date). Second instruction held.",
         note="Third Monday in a row - Arcadia's ERP re-sent the file. Cancelled the duplicate within 10 minutes."),
    dict(at="2026-08-31 11:10", p="P8", rc="NOSTRO_FUNDING_SHORTFALL", et="HELD", code="AM04",
         client="blueharbour", bank="NSBK", ben="PT Surabaya Port Logistics", ccy="IDR", balance="passed",
         remit="INV SPL-3390 terminal handling", minutes=45,
         msg="Payment held: AM04 Insufficient funds on the IDR settlement account. Client debit account balance check: passed.",
         note="IDR nostro short again on the last business day of the month. Requested a Treasury top-up at 11:30; payment released at 14:05. Treasury suggests pre-funding the IDR nostro before month-end."),
    dict(at="2026-08-31 14:45", p="P1", rc="ACCOUNT_FORMAT_CHANGED", et="REJECTED", code="AC01",
         client="kestrel", bank="PDCB", ben="Foshan Metalworks Co., Ltd.", ccy="USD",
         account="620177312045", remit="INV FMW-8802 steel brackets", minutes=15,
         msg="Beneficiary account 620177312045 not found. Please verify and resend.",
         note="Pearl Delta 15-digit migration again. Prefixed branch code 601 (601620177312045), resent, credited."),
    dict(at="2026-09-01 10:25", p="P3", rc="NAME_LEGAL_SUFFIX_MISMATCH", et="REJECTED", code="BE01",
         client="vantage", bank="TUNG", ben="Mysuru Software Services Pvt. Ltd.", ccy="INR",
         inter="CRMC", remit="INV MSS-0442 QA services", minutes=20,
         msg="Beneficiary name does not match the account holder name. Payment rejected.",
         note="Tungabhadra strict name match again - 'Pvt. Ltd.' was rejected, it needs 'PRIVATE LIMITED'. Amended and resent."),
    dict(at="2026-09-02 11:50", p="P6", rc="CORRESPONDENT_ROUTING_CHANGED", et="REJECTED", code="RC01",
         client="tanjong", bank="NKYS", ben="Bergen Subsea Services AS", ccy="USD", amount=41_000,
         inter="ACBK", remit="INV BSS-655 ROV hire", minutes=65,
         msg="Bank identifier incorrect. Payment rejected by intermediary.",
         note="Nordkyst Bank moved its USD correspondent from Atlantic Clearing Bank New York (ACBKUS33) to Hudson Federal Bank New York (HFBKUS33) effective 1 Sep 2026. Updated the settlement instructions and resent via Hudson Federal."),
    dict(at="2026-09-02 15:05", p=None, rc="CLIENT_TYPO_IN_DETAILS", et="REJECTED", code="BE01",
         client="lotus", bank="MBSB", ben="Illoilo Food Distributors Inc", ccy="PHP",
         remit="INV IFD-2210 F&B supplies", minutes=20,
         msg="Beneficiary name does not match the account holder name. Payment rejected.",
         note="The client misspelled the beneficiary ('Illoilo' instead of 'Iloilo'). Corrected the spelling and resent."),
    dict(at="2026-09-03 16:30", p="P2", rc="MISSED_CUTOFF_NEXT_DAY_CREDIT", et="NON_RECEIPT_CLAIM", code=None,
         client="vantage", bank="GOLC", ben="Srinivasa Infotech Private Limited", ccy="INR",
         submitted="2026-09-02 14:52", inter="CRMC", remit="INV SI-8903 data engineering services Aug", minutes=15,
         tracker="Tracker: delivered to Golconda Commercial Bank 2 Sep 14:55 SGT - status: credit pending",
         msg="Client reports the beneficiary did not receive funds on the value date (2 Sep). Please investigate.",
         note="Submitted at 14:52 SGT, after Golconda's 14:30 SGT cut-off, so it credited the next day. Client informed; no trace required."),
    dict(at="2026-09-04 10:40", p="P5", rc="MISSING_INVOICE_REFERENCE", et="REJECTED", code="RR04",
         client="sunrise", bank="VHBK", ben="Tsuen Wan Garment Accessories Ltd", ccy="USD", amount=12_300,
         inter="ACBK", remit="Trade payment", minutes=25,
         msg="Payment rejected. Reason: RR04 Regulatory reason.",
         note="Victoria Harbour Bank again needs an invoice number for trade payments above USD 10k. Added invoice TWG-7718 and resubmitted."),

    # ---- week of 7 Sep ----
    dict(at="2026-09-07 09:30", p="P7", rc="CLIENT_ERP_DUPLICATE", et="HELD", code="AM05",
         client="arcadia", bank="STHB", ben="Penang Spice Traders Sdn Bhd", ccy="MYR",
         remit="INV PST-1219 spices", minutes=10,
         msg="Duplicate check: identical instruction received at 06:08 and 06:41 SGT (same beneficiary, amount and value date). Second instruction held.",
         note="Arcadia ERP Monday resend again; cancelled the duplicate."),
    dict(at="2026-09-07 13:15", p="P4", rc="SANCTIONS_KNOWN_FALSE_POSITIVE", et="SCREENING_HOLD", code=None,
         client="blueharbour", bank="DRBK", ben="Desert Star General Trading LLC", ccy="USD",
         remit="INV DSGT-1049 port handling charges", minutes=20,
         msg="Sanctions screening hit: beneficiary 'DESERT STAR GENERAL TRADING LLC' vs list entry 'DESERT STAR SHIPPING FZE' (match score 0.86). Payment held.",
         note="Recurring false positive; routed to Compliance with CLR-2026-0819-07 and it was released in two hours. Compliance asked us to always attach the prior clearance reference."),
    dict(at="2026-09-08 11:20", p=None, rc="CLIENT_TYPO_IN_DETAILS", et="REJECTED", code="AC01",
         client="orchid", bank="RHHB", ben="Rhein Chemie Vertrieb GmbH", ccy="EUR",
         remit="INV RCV-44120 reagents", minutes=20,
         msg="Beneficiary IBAN invalid (check digits failed). Payment rejected.",
         note="The IBAN had two transposed digits so the check digits failed. The client supplied the correct IBAN; resent."),
    dict(at="2026-09-08 15:10", p="P6", rc="CORRESPONDENT_ROUTING_CHANGED", et="REJECTED", code="RC01",
         client="tanjong", bank="NKYS", ben="Stavanger Offshore Supply AS", ccy="USD",
         inter="ACBK", remit="INV SOS-218 deck equipment", minutes=30,
         msg="Bank identifier incorrect. Payment rejected by intermediary.",
         note="Old correspondent used again - our settlement-instruction table was not updated for Nordkyst. Rerouted via Hudson Federal Bank New York (HFBKUS33) and raised a request to update static data."),
    dict(at="2026-09-09 10:05", p="P1", rc="ACCOUNT_FORMAT_CHANGED", et="REJECTED", code="AC01",
         client="sunrise", bank="PDCB", ben="Zhongshan Weaving Co., Ltd.", ccy="USD",
         account="620190047781", remit="INV ZSW-3310 woven fabric", minutes=8,
         msg="Beneficiary account 620190047781 not found. Please verify and resend.",
         note="Pearl Delta account format change (12 to 15 digits, prefix 601). Fixed in five minutes."),
    dict(at="2026-09-09 14:40", p=None, rc="INTERMEDIARY_DELAY", et="NON_RECEIPT_CLAIM", code=None,
         client="orchid", bank="THUB", ben="Albion Lab Equipment Ltd", ccy="GBP",
         submitted="2026-09-08 10:15", inter="ALBN", remit="INV ALE-7781 lab equipment", minutes=60,
         tracker="Tracker: last update from Albion Clearing Bank, London 8 Sep 17:40 SGT - status: pending (compliance check)",
         msg="Client reports the beneficiary did not receive funds on the value date (8 Sep). Please investigate.",
         note="Funds were held at the intermediary (Albion Clearing Bank) for a compliance check. Sent an MT199 trace; released the next day."),
    dict(at="2026-09-10 11:35", p=None, rc="CLIENT_INSUFFICIENT_FUNDS", et="HELD", code="AM04",
         client="lotus", bank="MBSB", ben="Manila Harbour Laundry Services Inc", ccy="PHP", balance="failed",
         remit="INV MHL-3301 laundry services", minutes=30,
         msg="Payment held: AM04 Insufficient funds. Client debit account balance check: failed.",
         note="Lotus's own account lacked funds because their payroll went out the same morning. Held the payment; the client funded the account at 15:00 and we released it."),
    dict(at="2026-09-10 12:40", p=None, rc="PURPOSE_CODE_MISSING", et="REJECTED", code="RR04",
         client="vantage", bank="GOLC", ben="Srinivasa Infotech Private Limited", ccy="INR",
         inter="CRMC", remit="Services", minutes=30,
         msg="Payment rejected. Reason: RR04 Regulatory reason.",
         note="Inward INR remittances need an RBI purpose code and this one had none. Vantage confirmed P0802 (software services); resubmitted with the purpose code."),
    dict(at="2026-09-11 10:15", p="P3", rc="NAME_LEGAL_SUFFIX_MISMATCH", et="REJECTED", code="BE01",
         client="orchid", bank="TUNG", ben="Deccan Bio Labs Pvt Ltd", ccy="INR",
         inter="CRMC", remit="INV DBL-3361 lab consumables", minutes=12,
         msg="Beneficiary name does not match the account holder name. Payment rejected.",
         note="The client used the abbreviated name for Deccan Bio Labs again. Amended to 'DECCAN BIO LABS PRIVATE LIMITED' and asked Orchid to update their beneficiary master data."),
    dict(at="2026-09-11 16:00", p="P2", rc="MISSED_CUTOFF_NEXT_DAY_CREDIT", et="NON_RECEIPT_CLAIM", code=None,
         client="orchid", bank="GOLC", ben="Hyderabad Organics Private Limited", ccy="INR",
         submitted="2026-09-10 15:40", inter="CRMC", remit="INV HO-5588 active ingredients", minutes=10,
         tracker="Tracker: delivered to Golconda Commercial Bank 10 Sep 15:44 SGT - status: credit pending",
         msg="Client reports the beneficiary did not receive funds on the value date (10 Sep). Please investigate.",
         note="After Golconda's cut-off again (submitted 15:40 SGT). Credited on 11 Sep morning."),

    # ---- week of 14 Sep ----
    dict(at="2026-09-14 09:25", p="P7", rc="CLIENT_ERP_DUPLICATE", et="HELD", code="AM05",
         client="arcadia", bank="SGRB", ben="Mekong Delta Seafood JSC", ccy="USD",
         remit="INV MDS-4521 frozen seafood", minutes=8,
         msg="Duplicate check: identical instruction received at 06:11 and 06:45 SGT (same beneficiary, amount and value date). Second instruction held.",
         note="Arcadia Monday resend; cancelled. Arcadia IT says their scheduler fix goes live in October."),
    dict(at="2026-09-14 12:30", p=None, rc="CLIENT_TYPO_IN_DETAILS", et="REJECTED", code="RC01",
         client="blueharbour", bank="THUB", ben="Felixstowe Freight Ltd", ccy="GBP",
         inter="ALBN", bic_sent="THUBGB2X", remit="INV FFL-9902 haulage", minutes=15,
         msg="Bank identifier THUBGB2X incorrect. Payment rejected.",
         note="Wrong location code in the BIC; corrected to THUBGB2L and resent."),
    dict(at="2026-09-15 11:00", p="P5", rc="MISSING_INVOICE_REFERENCE", et="REJECTED", code="RR04",
         client="kestrel", bank="VHBK", ben="Kowloon Circuit Supplies Ltd", ccy="USD", amount=16_750,
         inter="ACBK", remit="Components", minutes=12,
         msg="Payment rejected. Reason: RR04 Regulatory reason.",
         note="Same Victoria Harbour invoice-number rule for trade payments over USD 10k. Added invoice KCS-56120 and resubmitted."),
    dict(at="2026-09-15 15:45", p=None, rc="SANCTIONS_POTENTIAL_MATCH", et="SCREENING_HOLD", code=None,
         client="kestrel", bank="SGRB", ben="Orient Star Maritime Co", ccy="USD",
         remit="INV OSM-117 freight", minutes=40,
         msg="Sanctions screening hit: beneficiary 'ORIENT STAR MARITIME CO' vs list entry 'ORIENT STAR MARINE LTD' (match score 0.91). Payment held.",
         note="New screening hit on a different entity (not Desert Star). Escalated to Compliance for full review; Compliance requested ownership documents and the payment stays held pending review."),
    dict(at="2026-09-16 10:20", p=None, rc="INTERMEDIARY_DELAY", et="NON_RECEIPT_CLAIM", code=None,
         client="vantage", bank="GOLC", ben="Srinivasa Infotech Private Limited", ccy="INR",
         submitted="2026-09-15 10:20", inter="CRMC", remit="INV SI-8960 data engineering services", minutes=55,
         tracker="Tracker: last update from Coromandel Clearing Bank, Mumbai 15 Sep 10:31 SGT - status: pending",
         msg="Client reports the beneficiary did not receive funds on the value date (15 Sep). Please investigate.",
         note="Submitted at 10:20 SGT, well before Golconda's cut-off, so this was not a cut-off issue. Funds were stuck at the INR correspondent (Coromandel Clearing Bank) over a purpose-code query. Sent a trace; credited on 16 Sep afternoon."),
    dict(at="2026-09-16 14:30", p="P4", rc="SANCTIONS_KNOWN_FALSE_POSITIVE", et="SCREENING_HOLD", code=None,
         client="blueharbour", bank="DRBK", ben="Desert Star General Trading LLC", ccy="USD",
         remit="INV DSGT-1088 port handling charges", minutes=10,
         msg="Sanctions screening hit: beneficiary 'DESERT STAR GENERAL TRADING LLC' vs list entry 'DESERT STAR SHIPPING FZE' (match score 0.86). Payment held.",
         note="Recurring Desert Star false positive. Routed to Compliance with CLR-2026-0819-07; released within the hour."),
    dict(at="2026-09-17 11:10", p=None, rc="POSSIBLE_DUPLICATE_VERIFY", et="HELD", code="AM05",
         client="lotus", bank="NSBK", ben="PT Bali Villas Management", ccy="IDR",
         remit="Management fee instalment", minutes=15,
         msg="Duplicate check: two instructions with the same beneficiary and amount received at 10:05 and 10:52 SGT. Second instruction held.",
         note="Lotus confirmed the second payment was a genuine second instalment; released after confirmation."),
    dict(at="2026-09-17 15:20", p="P1", rc="ACCOUNT_FORMAT_CHANGED", et="REJECTED", code="AC01",
         client="blueharbour", bank="PDCB", ben="Guangzhou Harbour Freight Agency Co., Ltd.", ccy="USD",
         account="620133905527", remit="INV GHF-7740 freight forwarding", minutes=6,
         msg="Beneficiary account 620133905527 not found. Please verify and resend.",
         note="Pearl Delta 12-to-15-digit issue (prefix 601). Fixed and resent."),
    dict(at="2026-09-18 10:40", p="P6", rc="CORRESPONDENT_ROUTING_CHANGED", et="REJECTED", code="RC01",
         client="tanjong", bank="NKYS", ben="Bergen Subsea Services AS", ccy="USD",
         inter="ACBK", remit="INV BSS-701 ROV hire", minutes=15,
         msg="Bank identifier incorrect. Payment rejected by intermediary.",
         note="Static data still pointed to Atlantic Clearing Bank for Nordkyst. Rerouted via Hudson Federal Bank (HFBKUS33) and escalated the settlement-instruction update to the reference-data team again."),

    # ---- week of 21 Sep ----
    dict(at="2026-09-21 09:35", p="P7", rc="CLIENT_ERP_DUPLICATE", et="HELD", code="AM05",
         client="arcadia", bank="STHB", ben="Penang Spice Traders Sdn Bhd", ccy="MYR",
         remit="INV PST-1260 spices", minutes=5,
         msg="Duplicate check: identical instruction received at 06:14 and 06:43 SGT (same beneficiary, amount and value date). Second instruction held.",
         note="Monday ERP resend from Arcadia; cancelled."),
    dict(at="2026-09-21 16:05", p=None, rc="BENEFICIARY_ACCOUNT_CLOSED", et="RETURNED", code="AC04",
         client="kestrel", bank="SGRB", ben="Hanoi Electronics Assembly JSC", ccy="USD",
         remit="INV HEA-5501 PCB assembly", minutes=25,
         msg="Payment returned. Reason: AC04 Closed account number.",
         note="The beneficiary switched banks and closed this account. Funds returned; the client re-initiated to the new account."),
    dict(at="2026-09-22 11:15", p="P2", rc="MISSED_CUTOFF_NEXT_DAY_CREDIT", et="NON_RECEIPT_CLAIM", code=None,
         client="vantage", bank="GOLC", ben="Srinivasa Infotech Private Limited", ccy="INR",
         submitted="2026-09-21 15:15", inter="CRMC", remit="INV SI-9012 data engineering services", minutes=8,
         tracker="Tracker: delivered to Golconda Commercial Bank 21 Sep 15:18 SGT - status: credit pending",
         msg="Client reports the beneficiary did not receive funds on the value date (21 Sep). Please investigate.",
         note="After the Golconda cut-off again; next-day credit confirmed with the bank."),
    dict(at="2026-09-22 14:50", p=None, rc="CLIENT_TYPO_IN_DETAILS", et="REJECTED", code="AC01",
         client="vantage", bank="GOLC", ben="Srinivasa Infotech Private Limited", ccy="INR",
         account="5020112233445566", inter="CRMC", remit="INV SI-9030 cloud migration", minutes=15,
         msg="Beneficiary account 5020112233445566 not found. Please verify and resend.",
         note="The client mistyped the account number (an extra digit - 16 instead of 15). Corrected and resent."),
    dict(at="2026-09-23 10:30", p="P3", rc="NAME_LEGAL_SUFFIX_MISMATCH", et="REJECTED", code="BE01",
         client="vantage", bank="TUNG", ben="Mysuru Software Services Pvt Ltd", ccy="INR",
         inter="CRMC", remit="INV MSS-0470 QA services", minutes=6,
         msg="Beneficiary name does not match the account holder name. Payment rejected.",
         note="Tungabhadra strict name match; amended to 'MYSURU SOFTWARE SERVICES PRIVATE LIMITED'."),
    dict(at="2026-09-23 15:30", p="P5", rc="MISSING_INVOICE_REFERENCE", et="REJECTED", code="RR04",
         client="sunrise", bank="VHBK", ben="Tsuen Wan Garment Accessories Ltd", ccy="USD", amount=11_050,
         inter="ACBK", remit="Trade settlement", minutes=6,
         msg="Payment rejected. Reason: RR04 Regulatory reason.",
         note="Added invoice reference TWG-7802 for Victoria Harbour; accepted."),
    dict(at="2026-09-24 11:40", p="P1", rc="ACCOUNT_FORMAT_CHANGED", et="REJECTED", code="AC01",
         client="kestrel", bank="PDCB", ben="Dongguan Precision Parts Co., Ltd.", ccy="USD",
         account="620144880917", remit="INV DPP-2309 machined housings", minutes=5,
         msg="Beneficiary account 620144880917 not found. Please verify and resend.",
         note="Prefixed 601 for the Pearl Delta format change; resent."),
    dict(at="2026-09-25 10:10", p="P4", rc="SANCTIONS_KNOWN_FALSE_POSITIVE", et="SCREENING_HOLD", code=None,
         client="blueharbour", bank="DRBK", ben="Desert Star General Trading LLC", ccy="USD",
         remit="INV DSGT-1120 port handling charges", minutes=8,
         msg="Sanctions screening hit: beneficiary 'DESERT STAR GENERAL TRADING LLC' vs list entry 'DESERT STAR SHIPPING FZE' (match score 0.86). Payment held.",
         note="Recurring false positive; Compliance released it with CLR-2026-0819-07."),
]

# Open cases for the live demo ("today" is Mon 28 / Tue 29 Sep 2026).
LIVE = [
    dict(at="2026-09-28 09:20", p="P7", rc="CLIENT_ERP_DUPLICATE", et="HELD", code="AM05",
         client="arcadia", bank="SGRB", ben="Mekong Delta Seafood JSC", ccy="USD",
         remit="INV MDS-4603 frozen seafood",
         msg="Duplicate check: identical instruction received at 06:13 and 06:46 SGT (same beneficiary, amount and value date). Second instruction held.",
         note="Arcadia's ERP re-sent the Monday file again. Cancelled the duplicate and pinged Arcadia IT."),
    dict(at="2026-09-28 10:05", p="P1", rc="ACCOUNT_FORMAT_CHANGED", et="REJECTED", code="AC01",
         client="orchid", bank="PDCB", ben="Guangzhou Hengtai Chemical Co., Ltd.", ccy="USD", amount=38_600,
         account="620152286604", remit="INV GHC-1187 excipients",
         msg="Beneficiary account 620152286604 not found. Please verify and resend.",
         note="Pearl Delta 15-digit account format - prefixed branch code 601 and resent."),
    dict(at="2026-09-28 11:30", p="P4", rc="SANCTIONS_KNOWN_FALSE_POSITIVE", et="SCREENING_HOLD", code=None,
         client="blueharbour", bank="DRBK", ben="Desert Star General Trading LLC", ccy="USD",
         remit="INV DSGT-1149 port handling charges",
         msg="Sanctions screening hit: beneficiary 'DESERT STAR GENERAL TRADING LLC' vs list entry 'DESERT STAR SHIPPING FZE' (match score 0.86). Payment held.",
         note="Known false positive. Routed to Compliance with clearance CLR-2026-0819-07; Compliance released it."),
    dict(at="2026-09-28 14:10", p="P6", rc="CORRESPONDENT_ROUTING_CHANGED", et="REJECTED", code="RC01",
         client="tanjong", bank="NKYS", ben="Stavanger Offshore Supply AS", ccy="USD",
         inter="ACBK", remit="INV SOS-240 deck equipment",
         msg="Bank identifier incorrect. Payment rejected by intermediary.",
         note="Nordkyst's USD correspondent is Hudson Federal Bank (HFBKUS33) since 1 Sep. Rerouted and resent."),
    dict(at="2026-09-28 10:40", p="P2", rc="MISSED_CUTOFF_NEXT_DAY_CREDIT", et="NON_RECEIPT_CLAIM", code=None,
         client="orchid", bank="GOLC", ben="Hyderabad Organics Private Limited", ccy="INR",
         submitted="2026-09-25 15:20", inter="CRMC", remit="INV HO-5671 active ingredients",
         tracker="Tracker: delivered to Golconda Commercial Bank 25 Sep 15:24 SGT - status: credit pending",
         msg="Client reports the beneficiary did not receive funds on the value date (Fri 25 Sep). Please investigate.",
         note="Arrived after Golconda's 14:30 SGT cut-off on Friday, so it credits on Monday morning. Informed Orchid; no trace needed."),
    dict(at="2026-09-28 16:20", p=None, rc="CLIENT_TYPO_IN_DETAILS", et="REJECTED", code="AC01",
         client="kestrel", bank="RHHB", ben="Rhein Elektronik Handels GmbH", ccy="EUR",
         remit="INV REH-2210 connectors",
         msg="Beneficiary IBAN invalid (check digits failed). Payment rejected.",
         note="Typo in the IBAN from the client. Asked Kestrel for the correct IBAN."),
    dict(at="2026-09-29 09:50", p="P8", rc="NOSTRO_FUNDING_SHORTFALL", et="HELD", code="AM04",
         client="lotus", bank="NSBK", ben="PT Bali Villas Management", ccy="IDR", balance="passed",
         remit="Villa lease Oct",
         msg="Payment held: AM04 Insufficient funds on the IDR settlement account. Client debit account balance check: passed.",
         note="Month-end again - IDR nostro short. Asked Treasury for a top-up, will release after funding."),
    dict(at="2026-09-29 11:15", p="P3", rc="NAME_LEGAL_SUFFIX_MISMATCH", et="REJECTED", code="BE01",
         client="arcadia", bank="TUNG", ben="Coorg Coffee Exports Pvt Ltd", ccy="INR",
         inter="CRMC", remit="INV CCE-0319 green coffee beans",
         msg="Beneficiary name does not match the account holder name. Payment rejected.",
         note="Tungabhadra exact name matching - amended to 'COORG COFFEE EXPORTS PRIVATE LIMITED'."),
    dict(at="2026-09-29 13:40", p=None, rc="SANCTIONS_POTENTIAL_MATCH", et="SCREENING_HOLD", code=None,
         client="blueharbour", bank="DRBK", ben="Red Sea Star Logistics LLC", ccy="USD",
         remit="INV RSSL-044 container haulage",
         msg="Sanctions screening hit: beneficiary 'RED SEA STAR LOGISTICS LLC' vs list entry 'RED SEA STAR SHIPPING CO' (match score 0.89). Payment held.",
         note="New beneficiary, new hit - not the Desert Star false positive. Escalated to Compliance for a full review."),
    dict(at="2026-09-29 15:05", p="P5", rc="MISSING_INVOICE_REFERENCE", et="REJECTED", code="RR04",
         client="kestrel", bank="VHBK", ben="Kowloon Circuit Supplies Ltd", ccy="USD", amount=13_400,
         inter="ACBK", remit="Components Sep",
         msg="Payment rejected. Reason: RR04 Regulatory reason.",
         note="Victoria Harbour invoice-number rule; asked Kestrel for the invoice number and resubmitted."),
]


def parse_sgt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=SGT)


def build_case(spec: dict, seq_by_day: dict, status: str) -> dict:
    created = parse_sgt(spec["at"])
    day_key = created.strftime("%y%m%d")
    seq_by_day[day_key] = seq_by_day.get(day_key, 0) + 1
    case_id = f"EXC-{day_key}-{seq_by_day[day_key]:02d}"

    bank = BANKS[spec["bank"]]
    client = CLIENTS[spec["client"]]
    submitted = parse_sgt(spec["submitted"]) if spec.get("submitted") else created - timedelta(minutes=rng.randint(35, 170))
    inter = INTERMEDIARIES.get(spec.get("inter")) if spec.get("inter") else None
    bic_sent = spec.get("bic_sent") or bank["bic"]

    payment = {
        "payment_ref": f"PAY-{rng.randint(7_000_000, 7_999_999)}",
        "channel": "SWIFT pacs.008 (ISO 20022)",
        "submitted_at": submitted.isoformat(),
        "value_date": submitted.date().isoformat(),
        "amount": float(spec.get("amount") or amount_for(spec["ccy"])),
        "currency": spec["ccy"],
        "debtor": {"name": client["name"], "account": client["account"], "client_id": spec["client"]},
        "creditor": {"name": spec["ben"], "account": spec.get("account") or default_account(spec["bank"]),
                     "country": bank["country"]},
        "creditor_bank": {"name": bank["name"], "bic": bic_sent, "city": bank["city"], "country": bank["country"],
                          "code": spec["bank"]},
        "intermediary_bank": inter,
        "remittance_info": spec.get("remit", ""),
    }
    case = {
        "case_id": case_id,
        "created_at": created.isoformat(),
        "status": status,
        "exception_type": spec["et"],
        "reason_code": spec["code"],
        "reason_text": REASON_TEXT.get(spec["code"]) if spec["code"] else None,
        "counterparty_message": spec["msg"],
        "tracker": spec.get("tracker"),
        "debtor_balance_check": spec.get("balance"),
        "payment": payment,
        "ground_truth": {
            "root_cause": spec["rc"],
            "pattern_id": spec["p"],
            "resolution_note": spec["note"],
            "minutes_spent": spec.get("minutes"),
        },
    }
    return case


def main() -> None:
    DATA.mkdir(exist_ok=True)
    seq: dict = {}
    history = [build_case(s, seq, "resolved") for s in sorted(HISTORY, key=lambda s: s["at"])]
    live = [build_case(s, seq, "open") for s in sorted(LIVE, key=lambda s: s["at"])]

    # mark "repeat" cases: a pattern we have already seen at least once before
    seen: set[str] = set()
    for case in history + live:
        pid = case["ground_truth"]["pattern_id"]
        case["ground_truth"]["is_repeat"] = bool(pid and pid in seen)
        if pid:
            seen.add(pid)

    (DATA / "history.json").write_text(json.dumps(history, indent=2))
    (DATA / "live.json").write_text(json.dumps(live, indent=2))
    (DATA / "entities.json").write_text(json.dumps({"clients": CLIENTS, "banks": BANKS,
                                                     "intermediaries": INTERMEDIARIES}, indent=2))
    print(f"history: {len(history)} cases, live: {len(live)} cases -> {DATA}")


if __name__ == "__main__":
    main()
