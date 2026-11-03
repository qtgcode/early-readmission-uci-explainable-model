"""Leakage-aware, patient-grouped pipeline for the UCI diabetes readmission dataset."""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeClassifier

TARGET = "readmitted"
PATIENT_ID = "patient_nbr"
ENCOUNTER_ID = "encounter_id"
DEATH_CODES = {"11", "19", "20", "21"}
HOSPICE_CODES = {"13", "14"}
PENDING_DISCHARGE_CODES = {"12"}
EXCLUDED_FEATURES = {
    TARGET, PATIENT_ID, ENCOUNTER_ID, "weight", "payer_code", "gender", "race"
}
DIAG_COLUMNS = ["diag_1", "diag_2", "diag_3"]
NUMERIC_COLUMNS = [
    "time_in_hospital", "num_lab_procedures", "num_procedures", "num_medications",
    "number_outpatient", "number_emergency", "number_inpatient", "number_diagnoses",
]
ID_COLUMNS = ["admission_type_id", "discharge_disposition_id", "admission_source_id"]


def load_id_mappings(path: str | Path) -> dict[str, dict[str, str]]:
    """Parse UCI's sectioned IDS_mapping.csv without relying on row ordering elsewhere."""
    sections: dict[str, dict[str, str]] = {}
    active: str | None = None
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.reader(handle):
            if not row:
                continue
            first = row[0].strip()
            if first in ID_COLUMNS and len(row) > 1 and row[1].strip() == "description":
                active = first
                sections.setdefault(active, {})
                continue
            if active and first.isdigit() and len(row) >= 2:
                sections[active][first] = row[1].strip()
    return sections


def _diag_group(value: Any) -> str:
    if pd.isna(value) or str(value).strip() in {"", "?"}:
        return "Unknown"
    code = str(value).strip().upper()
    if code.startswith("V"):
        return "Supplementary V-codes"
    if code.startswith("E"):
        return "External-cause E-codes"
    try:
        number = int(float(code))
    except (ValueError, TypeError):
        return "Other/invalid"
    if number == 250:
        return "Diabetes (250)"
    ranges = [
        (140, 239, "Neoplasms"), (240, 279, "Endocrine/metabolic"),
        (280, 289, "Blood disorders"), (290, 319, "Mental disorders"),
        (320, 389, "Nervous system/sense organs"), (390, 459, "Circulatory"),
        (460, 519, "Respiratory"), (520, 579, "Digestive"),
        (580, 629, "Genitourinary"), (630, 679, "Pregnancy-related"),
        (680, 709, "Skin/subcutaneous tissue"), (710, 739, "Musculoskeletal"),
        (740, 759, "Congenital anomalies"), (760, 779, "Perinatal conditions"),
        (780, 799, "Symptoms/signs"), (800, 999, "Injury/poisoning"),
    ]
    for low, high, label in ranges:
        if low <= number <= high:
            return label
    return "Other/invalid"


def prepare_cohort(raw: pd.DataFrame, mapping_path: str | Path) -> tuple[pd.DataFrame, pd.Series, pd.Series, dict[str, Any]]:
    """Create encounter-level outcome/features while retaining patient ID only for grouping."""
    required = {TARGET, PATIENT_ID, ENCOUNTER_ID, "discharge_disposition_id", *DIAG_COLUMNS}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df = raw.copy()
    disposition = df["discharge_disposition_id"].astype("string").str.strip()
    death_mask = disposition.isin(DEATH_CODES)
    hospice_mask = disposition.isin(HOSPICE_CODES)
    pending_mask = disposition.isin(PENDING_DISCHARGE_CODES)
    valid_target = df[TARGET].isin(["<30", ">30", "NO"])
    eligible = ~(death_mask | hospice_mask | pending_mask) & valid_target
    cohort = df.loc[eligible].copy()

    ids = cohort[PATIENT_ID].astype("string")
    eligible_patient_counts = ids.value_counts(dropna=True)
    raw_patient_counts = df[PATIENT_ID].value_counts(dropna=True)
    y = (cohort[TARGET] == "<30").astype("int8").rename("uci_label_lt30_early_readmission")

    mappings = load_id_mappings(mapping_path)
    for column in ID_COLUMNS:
        if column in cohort:
            code = cohort[column].astype("string").str.strip()
            cohort[column] = code.map(mappings.get(column, {})).fillna("Unknown/unmapped code")

    for column in DIAG_COLUMNS:
        cohort[column] = cohort[column].map(_diag_group)

    drop_columns = [column for column in EXCLUDED_FEATURES if column in cohort]
    X = cohort.drop(columns=drop_columns).copy()
    X = X.replace({"?": np.nan, "": np.nan})
    for column in NUMERIC_COLUMNS:
        if column in X:
            X[column] = pd.to_numeric(X[column], errors="coerce")
    for column in X.columns:
        if column not in NUMERIC_COLUMNS:
            X[column] = X[column].astype("object")

    info = {
        "raw_rows": int(len(raw)),
        "raw_unique_patients": int(raw[PATIENT_ID].nunique(dropna=True)),
        "raw_patients_with_multiple_encounters": int((raw_patient_counts > 1).sum()),
        "excluded_death_dispositions": int(death_mask.sum()),
        "excluded_hospice_dispositions": int(hospice_mask.sum()),
        "excluded_pending_discharge_disposition_12": int(pending_mask.sum()),
        "excluded_invalid_or_missing_target": int((~valid_target).sum()),
        "eligible_encounters": int(len(cohort)),
        "eligible_unique_patients": int(ids.nunique(dropna=True)),
        "eligible_patients_with_multiple_encounters": int((eligible_patient_counts > 1).sum()),
        "positive_encounters": int(y.sum()),
        "positive_rate": float(y.mean()),
        "feature_count_before_encoding": int(X.shape[1]),
        "excluded_columns": sorted(drop_columns),
    }
    if ids.isna().any():
        raise ValueError("Missing patient IDs prevent leakage-safe group splitting")
    return X.reset_index(drop=True), y.reset_index(drop=True), ids.reset_index(drop=True), info


def grouped_splits(X: pd.DataFrame, y: pd.Series, groups: pd.Series, seed: int = 42):
    """Return approximately 60/20/20 train/validation/test indices with no patient overlap."""
    outer = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=seed)
    train_val_idx, test_idx = next(outer.split(X, y, groups))
    inner = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=seed + 1)
    train_rel, val_rel = next(inner.split(X.iloc[train_val_idx], y.iloc[train_val_idx], groups.iloc[train_val_idx]))
    train_idx = train_val_idx[train_rel]
    val_idx = train_val_idx[val_rel]
    train_groups, val_groups, test_groups = (set(groups.iloc[i].astype(str)) for i in (train_idx, val_idx, test_idx))
    if train_groups & val_groups or train_groups & test_groups or val_groups & test_groups:
        raise AssertionError("Patient group leakage detected across splits")
    return train_idx, val_idx, test_idx


def build_preprocessor(X: pd.DataFrame) -> ColumnTransformer:
    numeric = [c for c in X.columns if c in NUMERIC_COLUMNS]
    categorical = [c for c in X.columns if c not in numeric]
    numeric_pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    categorical_pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="constant", fill_value="Unknown")),
        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=20, sparse_output=True)),
    ])
    return ColumnTransformer(
        [("numeric", numeric_pipe, numeric), ("categorical", categorical_pipe, categorical)],
        remainder="drop",
        sparse_threshold=0.3,
    )


def select_f1_threshold(y_true: pd.Series, probability: np.ndarray) -> tuple[float, float]:
    precision, recall, thresholds = precision_recall_curve(y_true, probability)
    if len(thresholds) == 0:
        return 0.5, 0.0
    f1 = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-12)
    best = int(np.nanargmax(f1))
    return float(thresholds[best]), float(f1[best])


def evaluate(y_true: pd.Series, probability: np.ndarray, threshold: float) -> dict[str, float | int]:
    pred = (probability >= threshold).astype("int8")
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "n": int(len(y_true)),
        "positive_rate": float(np.mean(y_true)),
        "pr_auc_average_precision": float(average_precision_score(y_true, probability)),
        "roc_auc": float(roc_auc_score(y_true, probability)),
        "threshold": float(threshold),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall_sensitivity": float(recall_score(y_true, pred, zero_division=0)),
        "specificity": float(tn / (tn + fp)) if (tn + fp) else 0.0,
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "brier_score": float(brier_score_loss(y_true, probability)),
        "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp),
    }


def make_models(seed: int = 42) -> dict[str, Any]:
    return {
        "logistic_regression": LogisticRegression(
            max_iter=1000, solver="liblinear", C=1.0, random_state=seed
        ),
        "shallow_decision_tree": DecisionTreeClassifier(
            max_depth=3, min_samples_leaf=0.02, class_weight=None, random_state=seed
        ),
    }


def model_pipeline(X: pd.DataFrame, estimator: Any) -> Pipeline:
    return Pipeline([("preprocess", build_preprocessor(X)), ("model", estimator)])


def encoded_feature_names(fitted_pipeline: Pipeline) -> list[str]:
    pre = fitted_pipeline.named_steps["preprocess"]
    try:
        return list(pre.get_feature_names_out())
    except Exception:
        return [f"feature_{i}" for i in range(len(fitted_pipeline.named_steps["model"].coef_[0]))]


def logistic_explanations(
    fitted_pipeline: Pipeline,
    X_train: pd.DataFrame,
    top_n: int = 20,
    minimum_support: int = 200,
) -> pd.DataFrame:
    model = fitted_pipeline.named_steps["model"]
    pre = fitted_pipeline.named_steps["preprocess"]
    names = encoded_feature_names(fitted_pipeline)
    coef = model.coef_[0]
    encoded = pre.transform(X_train)
    categorical_support = np.asarray(encoded.sum(axis=0)).ravel()
    support = np.array([
        len(X_train) if name.startswith("numeric__") else int(round(categorical_support[i]))
        for i, name in enumerate(names)
    ])
    table = pd.DataFrame({
        "feature": names,
        "coefficient_log_odds": coef,
        "training_support": support,
    })
    table["odds_ratio"] = np.exp(np.clip(table["coefficient_log_odds"], -20, 20))
    table["absolute_coefficient"] = table["coefficient_log_odds"].abs()
    table = table.loc[table["training_support"] >= minimum_support]
    table = table.sort_values("absolute_coefficient", ascending=False).head(top_n)
    return table.drop(columns="absolute_coefficient").reset_index(drop=True)


def tree_rules(fitted_pipeline: Pipeline, threshold: float = 0.5) -> str:
    pre = fitted_pipeline.named_steps["preprocess"]
    model = fitted_pipeline.named_steps["model"]
    names = list(pre.get_feature_names_out())
    tree = model.tree_
    numeric_columns = list(pre.transformers_[0][2])
    scaler = pre.named_transformers_["numeric"].named_steps["scale"]
    numeric_stats = {
        f"numeric__{column}": (float(scaler.mean_[i]), float(scaler.scale_[i]))
        for i, column in enumerate(numeric_columns)
    }
    labels = {
        "number_inpatient": "số lần nhập viện nội trú năm trước",
        "number_emergency": "số lần cấp cứu năm trước",
        "number_outpatient": "số lần khám ngoại trú năm trước",
        "time_in_hospital": "số ngày nằm viện",
        "num_lab_procedures": "số xét nghiệm",
        "num_procedures": "số thủ thuật",
        "num_medications": "số thuốc trong đợt điều trị",
        "number_diagnoses": "số chẩn đoán",
    }
    lines = [
        f"Tỷ lệ ở mỗi lá ước lượng từ train; gắn cờ nếu tỷ lệ nhãn <30 >= threshold chọn trên validation ({threshold:.3f})."
    ]

    def walk(node: int, conditions: list[str]) -> None:
        left = int(tree.children_left[node])
        right = int(tree.children_right[node])
        if left == right:
            values = np.asarray(tree.value[node]).ravel()
            rate = float(values[1] / values.sum()) if values.sum() else 0.0
            action = "GẮN CỜ" if rate >= threshold else "không gắn cờ"
            path = " VÀ ".join(conditions) if conditions else "mọi encounter"
            lines.append(
                f"NẾU {path} THÌ tỷ lệ nhãn <30 trong train={rate:.3f}; "
                f"số encounter train ở lá={int(tree.n_node_samples[node]):,}; {action}."
            )
            return

        feature_index = int(tree.feature[node])
        feature_name = names[feature_index]
        split = float(tree.threshold[node])
        if feature_name in numeric_stats:
            mean, scale = numeric_stats[feature_name]
            raw_split = split * scale + mean
            raw_name = feature_name.removeprefix("numeric__")
            display = labels.get(raw_name, raw_name)
            integer_boundary = math.floor(raw_split + 1e-9)
            left_text = f"{display} <= {integer_boundary}"
            right_text = f"{display} >= {integer_boundary + 1}"
        else:
            category = feature_name.removeprefix("categorical__")
            if category.startswith("discharge_disposition_id_"):
                value = category.removeprefix("discharge_disposition_id_")
                value = {
                    "Discharged to home": "về nhà",
                    "Discharged/transferred to home with home health service": "chuyển về nhà có dịch vụ chăm sóc tại nhà",
                }.get(value, value)
                category = "nơi ra viện là “" + value + "”"
            elif category.startswith("admission_type_id_"):
                category = "loại nhập viện là “" + category.removeprefix("admission_type_id_") + "”"
            elif category.startswith("admission_source_id_"):
                category = "nguồn nhập viện là “" + category.removeprefix("admission_source_id_") + "”"
            left_text = f"{category}: không"
            right_text = f"{category}: có"
        walk(left, conditions + [left_text])
        walk(right, conditions + [right_text])

    walk(0, [])
    return "\n".join(lines)
