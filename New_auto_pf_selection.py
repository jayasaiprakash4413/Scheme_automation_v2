import streamlit as st
import pandas as pd
import json
import re
from decimal import Decimal, getcontext, ROUND_HALF_UP

getcontext().prec = 50

st.set_page_config(layout="wide")
st.title("Final Scheme Configuration Engine")

# ============================================================
# CONSTANTS (DO NOT TOUCH)
# ============================================================

SECURE_S1_DELIGHT = Decimal("9.95")
SECURE_S2_DELIGHT = Decimal("17.00")

SECURE_S1_ROYAL = Decimal("13.20")
SECURE_S2_ROYAL = Decimal("18.50")

UNSECURE_JSON_6_7 = (Decimal("48.00"), Decimal("48.00"), Decimal("48.00"))
UNSECURE_JSON_12 = (Decimal("37.65"), Decimal("37.65"), Decimal("37.65"))

# Internal calc values
UNSECURE_CALC_6_7 = (Decimal("48.00"), Decimal("46.00"), Decimal("48.00"))
UNSECURE_CALC_12 = (Decimal("37.65"), Decimal("32.00"), Decimal("37.65"))

LTV_CODE_MAP = {
    "e0": Decimal("80"),
    "s5": Decimal("75"),
    "s7": Decimal("77"),
    "s6": Decimal("76"),
    "si5": Decimal("65")
}

GROUP_SCHEME_CODES = {"s2", "s4", "s5", "s7"}
E_SERIES_CODES = {"e0", "e1", "e2", "e4"}

# ============================================================
# EXTRACTIONS
# ============================================================

def extract_ltv_from_code(refname):
    refname_str = str(refname).lower()

    bracket_matches = re.findall(r'\((.*?)\)', refname_str)
    for segment in bracket_matches:
        tokens = [token for token in re.split(r'[^a-z0-9]+', segment) if token]
        for token in tokens:
            if token in LTV_CODE_MAP:
                return LTV_CODE_MAP[token]

    tokens = [token for token in re.split(r'[^a-z0-9]+', refname_str) if token]
    for token in tokens:
        if token in LTV_CODE_MAP:
            return LTV_CODE_MAP[token]

    return None

def extract_tenure(refname):
    match = re.search(r'\b(\d{1,2})\s*M\b', str(refname), re.IGNORECASE)
    return int(match.group(1)) if match else None

def extract_pf(refname):
    refname_str = str(refname)
    pf_match = re.search(
        r'PF\s*[-:]?\s*([0-9]+(?:\.[0-9]+)?)\s*%',
        refname_str,
        re.IGNORECASE
    )
    if pf_match:
        return Decimal(pf_match.group(1))

    # fallback: if PF formatting is odd, pick first % value after PF token
    pf_split = re.split(r'PF', refname_str, flags=re.IGNORECASE)
    if len(pf_split) > 1:
        percents = re.findall(r'([0-9]+(?:\.[0-9]+)?)\s*%', pf_split[1])
        if percents:
            return Decimal(percents[0])

    return None

def extract_pf_range(refname):
    refname_str = str(refname)

    # Flexible parser for formats like:
    # PF-0.70%-1.00%, PF - 0.70 % - 1.00 %, PF: 0.70% – 1.00%
    match = re.search(
        r'PF\s*[-:]?\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*[-–—]\s*([0-9]+(?:\.[0-9]+)?)\s*%',
        refname_str,
        re.IGNORECASE
    )
    if match:
        return Decimal(match.group(1)), Decimal(match.group(2))

    # fallback: take first two percentage values after PF token
    pf_split = re.split(r'PF', refname_str, flags=re.IGNORECASE)
    if len(pf_split) > 1:
        percents = re.findall(r'([0-9]+(?:\.[0-9]+)?)\s*%', pf_split[1])
        if len(percents) >= 2:
            return Decimal(percents[0]), Decimal(percents[1])

    return None, None

def is_flexi_pf_refname(refname):
    pf_min, pf_max = extract_pf_range(refname)
    return pf_min is not None and pf_max is not None and pf_min != pf_max

def extract_opp(refname):
    parts = re.split(r'PF', str(refname), flags=re.IGNORECASE)[0]
    match = re.search(r'([0-9]+(?:\.[0-9]+)?)%', parts)
    return Decimal(match.group(1)) if match else None

def update_refname_tenure(refname, tenure):
    return re.sub(r'\b(\d{1,2})\s*M\b', f'{tenure}M', str(refname), count=1, flags=re.IGNORECASE)

def get_tenure_days(tenure):
    mapping = {6: 180, 7: 210, 12: 360}
    return mapping.get(int(tenure), int(tenure) * 30)


def extract_scheme_code(refname):
    refname_str = str(refname).lower()
    bracket_matches = re.findall(r'\((.*?)\)', refname_str)
    for segment in bracket_matches:
        tokens = [token for token in re.split(r'[^a-z0-9]+', segment) if token]
        for token in tokens:
            if re.fullmatch(r'[se]\d+', token):
                return token

    tokens = [token for token in re.split(r'[^a-z0-9]+', refname_str) if token]
    for token in tokens:
        if re.fullmatch(r'[se]\d+', token):
            return token
    return None


def extract_variant(refname):
    refname_str = str(refname).lower()
    if "economy" in refname_str:
        return "economy"
    if "silver" in refname_str:
        return "silver"
    return None


def extract_ts_bucket(refname):
    refname_str = str(refname).lower()

    # <6L bucket
    if re.search(r'(<\s*3\s*l)|(0\s*[-–]\s*3\s*l)|(3\s*[-–]\s*6\s*l)', refname_str):
        return "<6L"

    # >6L bucket
    if re.search(r'(6\s*[-–]\s*12\s*l)|(12\s*[-–]\s*25\s*l)|(12\s*l\s*\+)|(>\s*12\s*l)', refname_str):
        return ">6L"

    # legacy tags fallback
    if re.search(r'(<\s*5\s*l)|(<\s*6\s*l)', refname_str):
        return "<6L"
    if re.search(r'(>\s*5\s*l)|(>\s*6\s*l)', refname_str):
        return ">6L"

    return ">6L"


def has_fresh_takeover_keywords(refname):
    refname_str = str(refname).lower()
    pattern = (
        r'\bfl\s*[-/ ]*to\b|'      # FL TO / FL-TO / FL/TO
        r'\bflto\b|'
        r'\bfresh\b|'
        r'\btake\s*[- ]?over\b|'
        r'\btakeover\b|'
        # r'\bto\b\s*[-/:]\s*'     # TO - / TO: / TO/
    )
    return bool(re.search(pattern, refname_str, re.IGNORECASE))


def has_renewal_keywords(refname):
    refname_str = str(refname).lower()
    return bool(re.search(r'\brenewal\b|\bretention\b', refname_str))


def extract_applicable_processes(refname):
    processes = []

    if has_fresh_takeover_keywords(refname):
        processes.extend(["fresh-loan", "takeover-loan"])

    if has_renewal_keywords(refname):
        processes.append("renewal")

    if not processes:
        processes.append("fresh-loan")

    if "release" not in processes:
        processes.append("release")

    # de-duplicate preserving order
    seen = set()
    ordered = []
    for p in processes:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def find_refname_keyword(refname):
    value = str(refname).lower()
    for keyword in ["fl to", "fresh", "takeover", "renewal"]:
        if keyword in value:
            return keyword
    return None


def get_foreclosure_terms(refname, final_tenure):
    scheme_code = extract_scheme_code(refname)
    variant = extract_variant(refname)
    ts_bucket = extract_ts_bucket(refname)

    # default rule if variant missing
    if not variant:
        return Decimal("1.00"), 3

    # Foreclosure table has only 6M and 12M slabs.
    # Per requirement: final tenure 6M or 7M should both use 6M block.
    tenure_int = int(final_tenure)
    if tenure_int == 12:
        tenure_bucket = 12
    elif tenure_int in (6, 7):
        tenure_bucket = 6
    else:
        tenure_bucket = 6

    if scheme_code in GROUP_SCHEME_CODES:
        if variant == "economy":
            if tenure_bucket == 12:
                return (Decimal("1.50"), 4) if ts_bucket == "<6L" else (Decimal("1.00"), 4)
            return (Decimal("1.50"), 3) if ts_bucket == "<6L" else (Decimal("1.00"), 3)

        if variant == "silver":
            if tenure_bucket == 12:
                return (Decimal("1.00"), 4) if ts_bucket == "<6L" else (Decimal("0.50"), 4)
            return (Decimal("1.00"), 3) if ts_bucket == "<6L" else (Decimal("0.50"), 3)

    if scheme_code in E_SERIES_CODES:
        return (Decimal("1.50"), 3) if ts_bucket == "<6L" else (Decimal("1.00"), 3)

    # fallback
    return Decimal("1.00"), 3

# ============================================================
# DECISION ENGINE (12M SAFE)
# ============================================================

def decision_engine(overall_ltv, monthly_opp, requested_tenure):

    secure_s1 = Decimal("9.95")

    if requested_tenure == 12:
        secure_ltv = Decimal("60")
        unsecure_s1 = Decimal("37.65")
    else:
        secure_ltv = Decimal("67")
        unsecure_s1 = Decimal("48.00")

    if overall_ltv <= secure_ltv:
        if requested_tenure in (6, 7):
            return ("Royal", 7)
        return ("Royal", requested_tenure)

    secure_weight = secure_ltv / overall_ltv
    unsecure_weight = (overall_ltv - secure_ltv) / overall_ltv

    min_opp = (secure_weight * secure_s1) / Decimal("12")
    max_opp = (
        secure_weight * secure_s1 +
        unsecure_weight * unsecure_s1
    ) / Decimal("12")

    min_opp = min_opp.quantize(Decimal("0.01"), ROUND_HALF_UP)
    max_opp = max_opp.quantize(Decimal("0.01"), ROUND_HALF_UP)

    if min_opp <= monthly_opp <= max_opp:
        return ("Delight", requested_tenure)

    if requested_tenure in (6, 7):
        return ("Royal", 7)

    return ("Royal", requested_tenure)

# ============================================================
# INTEREST ENGINE
# ============================================================

def secure_slab3(tenure):
    r = Decimal("0.229")
    m = Decimal("12")
    t = Decimal(str(tenure))
    compound = (Decimal("1") + r/m) ** t
    result = (compound - Decimal("1")) * m / t
    return (result * 100).quantize(Decimal("0.00"), ROUND_HALF_UP)

def interest_engine(scheme, tenure, overall_ltv, monthly_opp):

    if scheme == "Delight":
        secure_s1 = SECURE_S1_DELIGHT
        secure_s2 = SECURE_S2_DELIGHT
    else:
        secure_s1 = SECURE_S1_ROYAL
        secure_s2 = SECURE_S2_ROYAL

    # secure_ltv = Decimal("67") if tenure != 12 else Decimal("60")
    if tenure == 6:
        secure_ltv = Decimal("67")
    elif tenure == 7:
        secure_ltv = Decimal("66")
    else:
        secure_ltv = Decimal("60")

    secure_s3 = secure_slab3(tenure)

    if tenure == 12:
        calc_unsecure = UNSECURE_CALC_12
        json_unsecure = UNSECURE_JSON_12
    else:
        calc_unsecure = UNSECURE_CALC_6_7
        json_unsecure = UNSECURE_JSON_6_7

    secure_weight = secure_ltv / overall_ltv
    unsecure_weight = (overall_ltv - secure_ltv) / overall_ltv

    s1 = (monthly_opp * Decimal("12")).quantize(Decimal("0.00"), ROUND_HALF_UP)

    s2 = (
        secure_weight * secure_s2 +
        unsecure_weight * calc_unsecure[1]
    ).quantize(Decimal("0.00"), ROUND_HALF_UP)

    s3 = (
        secure_weight * secure_s3 +
        unsecure_weight * calc_unsecure[2]
    ).quantize(Decimal("0.00"), ROUND_HALF_UP)

    return {
        "secure_slabs": (secure_s1, secure_s2, secure_s3),
        "unsecure_slabs": json_unsecure,
        "overall_slabs": (s1, s2, s3),
        "secure_ltv": secure_ltv,
        "calc_unsecure_slabs": calc_unsecure
    }

def update_charge_text(json_str, unsecure_pf, overall_pf):
    try:
        data = json.loads(json_str)
    except Exception:
        return json_str
    data["secureProcessingFee"] = "0%"
    data["unsecureProcessingFee"] = f"{unsecure_pf.quantize(Decimal('0.00'), ROUND_HALF_UP)}%+GST"
    data["processingFee"] = f"{overall_pf.quantize(Decimal('0.00'), ROUND_HALF_UP)}%+GST"
    return json.dumps(data)


def update_json_applicable_processes(json_str, applicable_processes):
    try:
        data = json.loads(json_str)
    except Exception:
        return json_str

    if isinstance(data, dict):
        data["applicableProcesses"] = applicable_processes
    return json.dumps(data)

def update_bs2_charge_2(json_str, charge_value, backcalc_min, backcalc_max, is_flexi):
    try:
        data = json.loads(json_str) if str(json_str).strip() else {}
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    data["chargeValue"] = float(charge_value.quantize(Decimal("0.00"), ROUND_HALF_UP))

    if is_flexi:
        if "chargesMetaData" not in data or not isinstance(data["chargesMetaData"], dict):
            data["chargesMetaData"] = {}
        data["chargesMetaData"]["minPercentUnsecure"] = float(backcalc_min.quantize(Decimal("0.00"), ROUND_HALF_UP))
        data["chargesMetaData"]["maxPercentUnsecure"] = float(backcalc_max.quantize(Decimal("0.00"), ROUND_HALF_UP))
    else:
        data["chargeCalculationType"] = "fixed-percentage"
        data["chargeType"] = "processing-fee"
        data["percentageOn"] = "loanamount"
        if "chargesMetaData" in data:
            data.pop("chargesMetaData")

    return json.dumps(data)


def update_foreclosure_charge(json_str, foreclosure_unsecure_value, duration_months, applicable_processes):
    template = {
        "name": "Foreclosure",
        "chargeType": "foreclosure",
        "chargeCalculationType": "fixed-percentage",
        "applicableProcesses": ["fresh-loan", "renewal", "release"],
        "chargeValue": 0,
        "maxValue": 100000,
        "cityId": None,
        "percentageOn": "loanamount",
        "chargesMetaData": {"duration": 2},
        "minValue": 999
    }

    try:
        data = json.loads(json_str) if str(json_str).strip() else template
        if not isinstance(data, dict):
            data = template
    except Exception:
        data = template

    data["name"] = "Foreclosure"
    data["chargeType"] = "foreclosure"
    data["chargeCalculationType"] = "fixed-percentage"
    data["percentageOn"] = "loanamount"
    data["maxValue"] = 100000
    data["minValue"] = 999
    data["cityId"] = None
    data["applicableProcesses"] = applicable_processes
    data["chargeValue"] = float(foreclosure_unsecure_value.quantize(Decimal("0.00"), ROUND_HALF_UP))

    if "chargesMetaData" not in data or not isinstance(data["chargesMetaData"], dict):
        data["chargesMetaData"] = {}
    data["chargesMetaData"]["duration"] = int(duration_months)

    return json.dumps(data)


def update_bs2_legal_name_pf_fc(text, pf_value, fc_duration_months, has_pf_in_refname):
    updated = str(text).strip()

    # Normalize any existing FC token + optional day suffix
    updated = re.sub(r'\bFC(?:\s*[-:]?\s*\d+D)?\b', 'FC', updated, flags=re.IGNORECASE)

    # Remove PF if not present in original refname
    if not has_pf_in_refname:
        updated = re.sub(r'\bPF\s*[-:]?\s*[0-9]+(?:\.[0-9]+)?\s*%\s*', '', updated, flags=re.IGNORECASE)
        updated = re.sub(r'\s+', ' ', updated).strip()
    elif pf_value is not None:
        # Update/add PF only if pf_value is provided
        pf_str = f"{pf_value.quantize(Decimal('0.00'), ROUND_HALF_UP)}%"
        if re.search(r'PF\s*[-:]?\s*[0-9]+(?:\.[0-9]+)?\s*%', updated, re.IGNORECASE):
            updated = re.sub(
                r'PF\s*[-:]?\s*[0-9]+(?:\.[0-9]+)?\s*%',
                f'PF {pf_str}',
                updated,
                count=1,
                flags=re.IGNORECASE
            )
        elif re.search(r'\bFC\b', updated, re.IGNORECASE):
            updated = re.sub(r'\bFC\b', f'PF {pf_str} FC', updated, count=1, flags=re.IGNORECASE)
        else:
            updated = f"{updated} PF {pf_str}".strip()

    # Ensure FC exists
    if not re.search(r'\bFC\b', updated, re.IGNORECASE):
        updated = f"{updated} FC".strip()

    # Apply FC duration suffix
    if int(fc_duration_months) == 3:
        updated = re.sub(r'\bFC\b', 'FC 90D', updated, count=1, flags=re.IGNORECASE)
    elif int(fc_duration_months) == 4:
        updated = re.sub(r'\bFC\b', 'FC 120D', updated, count=1, flags=re.IGNORECASE)

    updated = re.sub(r'\s+', ' ', updated).strip()
    return updated

def update_bs2_legal_name(text, tenure, encoding):
    updated = re.sub(r'\b(6M|7M|12M)\b', f'{tenure}M', str(text), count=1)

    if re.search(r'(th7\.si5|f8)', updated):
        updated = re.sub(r'(th7\.si5|f8)', encoding, updated, count=1)
    else:
        updated = re.sub(r'(48(?:\.00)?%|37\.65%)', encoding, updated, count=1)

    return updated

# ============================================================
# JSON UPDATE
# ============================================================

def _find_slab_list(node):
    if isinstance(node, dict):
        if "interestSlabs" in node and isinstance(node["interestSlabs"], list):
            return node["interestSlabs"]
        for value in node.values():
            found = _find_slab_list(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        if len(node) >= 3 and all(isinstance(item, dict) for item in node):
            if any("interestRate" in item for item in node):
                return node
        for item in node:
            found = _find_slab_list(item)
            if found is not None:
                return found
    return None


def update_interest_json(json_str, slabs, tenure_days):
    try:
        data = json.loads(json_str)
    except Exception:
        return json_str

    slab_list = _find_slab_list(data)
    if slab_list:
        max_count = min(3, len(slab_list), len(slabs))
        for i in range(max_count):
            value = Decimal(slabs[i]).quantize(Decimal("0.00"), ROUND_HALF_UP)
            slab_list[i]["interestRate"] = float(value)

        if slab_list and isinstance(slab_list[-1], dict):
            slab_list[-1]["toDay"] = tenure_days

        return json.dumps(data)

    if isinstance(data, dict) and "interestRate" in data and len(slabs) > 0:
        data["interestRate"] = float(Decimal(slabs[0]).quantize(Decimal("0.00"), ROUND_HALF_UP))
        if "toDay" in data:
            data["toDay"] = tenure_days
        return json.dumps(data)

    return json.dumps(data)

# ============================================================
# STREAMLIT FLOW
# ============================================================

uploaded_file = st.file_uploader("Upload Scheme CSV", type=["csv"])

if uploaded_file:

    current_upload_key = f"{uploaded_file.name}:{uploaded_file.size}"
    if st.session_state.get("uploaded_file_key") != current_upload_key:
        st.session_state.df = pd.read_csv(uploaded_file)
        st.session_state.uploaded_file_key = current_upload_key

    edited_df = st.data_editor(
        st.session_state.df,
        use_container_width=True,
        num_rows="dynamic"
    )

    if st.button("Compute"):

        df = edited_df.copy()

        for idx in df.index:

            refname = df.at[idx, "refName"]

            overall_ltv = extract_ltv_from_code(refname)
            requested_tenure = extract_tenure(refname)
            monthly_opp = extract_opp(refname)
            pf_min, pf_max = extract_pf_range(refname)
            overall_pf = pf_max if pf_max is not None else extract_pf(refname)
            has_pf_in_name = bool(re.search(r'\bPF\b', str(refname), re.IGNORECASE))

            if not all([overall_ltv, requested_tenure, monthly_opp]):
                continue

            df.at[idx, "customerLtv"] = float(overall_ltv)

            scheme, final_tenure = decision_engine(
                overall_ltv,
                monthly_opp,
                requested_tenure
            )

            df.at[idx, "tenure"] = final_tenure
            df.at[idx, "refName"] = update_refname_tenure(refname, final_tenure)

            if "refno" in df.columns:
                df.at[idx, "refno"] = df.at[idx, "refName"]

            if "bs1-legalName" in df.columns:
                df.at[idx, "bs1-legalName"] = f"Rupeek {scheme}"

            result = interest_engine(
                scheme,
                final_tenure,
                overall_ltv,
                monthly_opp
            )

            if "bs1-ltv" in df.columns:
                df.at[idx, "bs1-ltv"] = float(result["secure_ltv"])

            tenure_days = get_tenure_days(final_tenure)

            df.at[idx, "OverallInterestCalculation"] = update_interest_json(
                df.at[idx, "OverallInterestCalculation"],
                result["overall_slabs"],
                tenure_days
            )

            df.at[idx, "bs1-addon-1"] = update_interest_json(
                df.at[idx, "bs1-addon-1"],
                result["secure_slabs"],
                tenure_days
            )

            df.at[idx, "bs2-addon-1"] = update_interest_json(
                df.at[idx, "bs2-addon-1"],
                result["unsecure_slabs"],
                tenure_days
            )

            if "bs2-calculation" in df.columns:
                df.at[idx, "bs2-calculation"] = update_interest_json(
                    df.at[idx, "bs2-calculation"],
                    result["unsecure_slabs"],
                    tenure_days
                )

            secure_ltv = result["secure_ltv"]
            denominator = Decimal("1") - (secure_ltv / overall_ltv)
            if denominator == 0:
                continue

            refname_for_process = str(df.at[idx, "refName"])
            matched_keyword = find_refname_keyword(refname_for_process)

            if matched_keyword == "renewal":
                applicable_processes = ["renewal"]
                description_value = "RWL"
            elif matched_keyword in {"fl to", "fresh", "takeover", "to"}:
                applicable_processes = ["fresh-loan", "takeover-loan"]
                description_value = "FL TO"
            else:
                applicable_processes = extract_applicable_processes(refname_for_process)
                description_value = None

            if description_value is None:
                if "renewal" in applicable_processes and len(applicable_processes) == 1:
                    description_value = "RWL"
                elif "fresh-loan" in applicable_processes or "takeover-loan" in applicable_processes:
                    description_value = "FL TO"

            if "description" in df.columns and description_value is not None:
                df.at[idx, "description"] = description_value

            if "applicableProcesses" in df.columns:
                df.at[idx, "applicableProcesses"] = ",".join(applicable_processes)

            charge_pf_value = None
            if has_pf_in_name and overall_pf is not None:
                min_pf_input = pf_min if pf_min is not None else overall_pf
                max_pf_input = pf_max if pf_max is not None else overall_pf

                min_unsecure_pf = (min_pf_input / denominator).quantize(Decimal("0.00"), ROUND_HALF_UP)
                max_unsecure_pf = (max_pf_input / denominator).quantize(Decimal("0.00"), ROUND_HALF_UP)

                refname_lower = str(df.at[idx, "refName"]).lower()
                is_flexi = is_flexi_pf_refname(df.at[idx, "refName"]) or any(token in refname_lower for token in ["flexipf", "flexi pf", "flexi-pf"])
                charge_pf_value = max_unsecure_pf if is_flexi else min_unsecure_pf

                if "chargeText" in df.columns:
                    charge_text_overall_pf = max_pf_input if is_flexi else min_pf_input
                    charge_text_unsecure_pf = max_unsecure_pf if is_flexi else min_unsecure_pf
                    df.at[idx, "chargeText"] = update_charge_text(
                        df.at[idx, "chargeText"],
                        charge_text_unsecure_pf,
                        charge_text_overall_pf
                    )

                if "bs2-charge-2" in df.columns:
                    updated_pf_json = update_bs2_charge_2(
                        df.at[idx, "bs2-charge-2"],
                        charge_pf_value,
                        min_unsecure_pf,
                        max_unsecure_pf,
                        is_flexi
                    )
                    df.at[idx, "bs2-charge-2"] = update_json_applicable_processes(updated_pf_json, applicable_processes)
            else:
                if "chargeText" in df.columns:
                    df.at[idx, "chargeText"] = "{}"

            overall_fc, duration_months = get_foreclosure_terms(df.at[idx, "refName"], final_tenure)
            fc_unsecure_value = (overall_fc / denominator).quantize(Decimal("0.00"), ROUND_HALF_UP)

            foreclosure_target_column = "bs2-charge-3" if has_pf_in_name else "bs2-charge-2"
            if foreclosure_target_column not in df.columns:
                df[foreclosure_target_column] = ""

            df.at[idx, foreclosure_target_column] = update_foreclosure_charge(
                df.at[idx, foreclosure_target_column],
                fc_unsecure_value,
                duration_months,
                applicable_processes
            )

            # If PF is absent, move foreclosure to bs2-charge-2 and clear bs2-charge-3
            if not has_pf_in_name:
                if "bs2-charge-3" not in df.columns:
                    df["bs2-charge-3"] = ""
                df.at[idx, "bs2-charge-3"] = "{}"

            if "bs2-NoOfCharges" in df.columns:
                df.at[idx, "bs2-NoOfCharges"] = 3 if has_pf_in_name else 2

            if "bs2-legalName" in df.columns:
                encoding = "th7.si5" if final_tenure == 12 else "f8"
                updated_legal_name = update_bs2_legal_name(
                    df.at[idx, "bs2-legalName"],
                    final_tenure,
                    encoding
                )
                df.at[idx, "bs2-legalName"] = update_bs2_legal_name_pf_fc(
                    updated_legal_name,
                    fc_unsecure_value,
                    duration_months,
                    has_pf_in_name
                )

        st.session_state.df = df

        st.success("Computation Complete")
        st.subheader("Updated Schemes")
        st.dataframe(df, use_container_width=True)

    st.download_button(
        "Download Updated CSV",
        st.session_state.df.to_csv(index=False),
        "updated_scheme.csv"
    )