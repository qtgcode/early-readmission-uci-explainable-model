#!/usr/bin/env python3
"""Run the complete analysis and export aggregate-only artifacts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import precision_recall_curve, roc_curve

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from readmission.pipeline import (  # noqa: E402
    evaluate, grouped_splits, make_models, model_pipeline, prepare_cohort,
    select_f1_threshold, tree_rules, logistic_explanations,
)

DATA = ROOT / "data/raw/diabetic_data.csv"
MAPPING = ROOT / "data/raw/IDS_mapping.csv"
OUT = ROOT / "outputs"


def main() -> None:
    if not DATA.exists() or not MAPPING.exists():
        raise SystemExit("Missing UCI files. Run: python scripts/download_data.py")
    OUT.mkdir(exist_ok=True)
    (OUT / "tables").mkdir(parents=True, exist_ok=True)
    (OUT / "figures").mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(DATA, low_memory=False, dtype={"encounter_id": "string", "patient_nbr": "string"})
    X, y, groups, cohort_info = prepare_cohort(raw, MAPPING)
    train_idx, val_idx, test_idx = grouped_splits(X, y, groups)
    splits = {"train": train_idx, "validation": val_idx, "test": test_idx}
    split_info = {}
    for name, idx in splits.items():
        split_info[name] = {
            "encounters": int(len(idx)),
            "positive_encounters": int(y.iloc[idx].sum()),
            "positive_rate": float(y.iloc[idx].mean()),
            "unique_patients": int(groups.iloc[idx].nunique()),
        }

    # Prevalence-only reference, estimated using training data only.
    train_prevalence = float(y.iloc[train_idx].mean())
    baseline_test_prob = np.full(len(test_idx), train_prevalence, dtype=float)
    baseline = evaluate(y.iloc[test_idx].to_numpy(), baseline_test_prob, 0.5)
    baseline["validation_threshold_selection"] = "fixed 0.5; prevalence-only baseline"
    results = [{"model": "prevalence_baseline", **baseline}]
    fitted = {}
    val_probs = {}
    test_probs = {"prevalence_baseline": baseline_test_prob}

    for name, estimator in make_models().items():
        pipe = model_pipeline(X, estimator)
        pipe.fit(X.iloc[train_idx], y.iloc[train_idx])
        p_val = pipe.predict_proba(X.iloc[val_idx])[:, 1]
        threshold, val_f1 = select_f1_threshold(y.iloc[val_idx].to_numpy(), p_val)
        p_test = pipe.predict_proba(X.iloc[test_idx])[:, 1]
        row = evaluate(y.iloc[test_idx].to_numpy(), p_test, threshold)
        row["model"] = name
        row["validation_f1_at_selected_threshold"] = float(val_f1)
        row["validation_threshold_selection"] = "threshold maximizing validation F1"
        results.append(row)
        fitted[name] = pipe
        val_probs[name] = p_val
        test_probs[name] = p_test

    # No patient-level records or predictions are written.
    pd.DataFrame(results).to_csv(OUT / "tables/model_metrics.csv", index=False)
    summary = {
        "source": "UCI Machine Learning Repository, dataset 296; DOI 10.24432/C5230J",
        "target_definition": "1 for UCI label <30; 0 for >30 or NO",
        "analysis_unit": "eligible encounter; patient_nbr used only for grouped splitting",
        "cohort": cohort_info,
        "splits": split_info,
        "train_prevalence_for_baseline": train_prevalence,
        "primary_metric": "average precision (PR-AUC)",
        "threshold_policy": "maximize F1 on validation separately for each fitted model; evaluate once on held-out test",
        "privacy": "No raw records, patient identifiers, encounter identifiers, or row-level predictions exported.",
        "reproducibility_seed": 42,
        "metrics": results,
    }
    (OUT / "tables/analysis_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # Aggregate-only logistic coefficients and shallow-tree rules for interpretation.
    logistic_explanations(
        fitted["logistic_regression"], X.iloc[train_idx], minimum_support=200
    ).to_csv(OUT / "tables/logistic_top_terms.csv", index=False)
    tree_threshold = next(r["threshold"] for r in results if r["model"] == "shallow_decision_tree")
    (OUT / "tables/shallow_tree_rules.txt").write_text(
        tree_rules(fitted["shallow_decision_tree"], threshold=tree_threshold), encoding="utf-8"
    )

    # Group-level audits only: gender/race are excluded from X and are not model predictors.
    # Suppress small groups to reduce re-identification risk.
    fairness_rows = []
    test_y = y.iloc[test_idx].to_numpy()
    # Build aligned subgroup attributes using the exact eligibility filter.
    disp = raw["discharge_disposition_id"].astype("string").str.strip()
    keep = ~disp.isin({"11", "19", "20", "21", "13", "14", "12"}) & raw["readmitted"].isin(["<30", ">30", "NO"])
    eligible_raw = raw.loc[keep].reset_index(drop=True)
    test_attrs = eligible_raw.iloc[test_idx].reset_index(drop=True)
    for attribute in ["gender", "race", "age"]:
        if attribute not in test_attrs:
            continue
        for group_value, local_idx in test_attrs.groupby(attribute, dropna=False).groups.items():
            local_idx = np.asarray(list(local_idx), dtype=int)
            if len(local_idx) < 200:
                continue
            group_prob = test_probs["logistic_regression"][local_idx]
            group_truth = test_y[local_idx]
            group_pred = group_prob >= next(r["threshold"] for r in results if r["model"] == "logistic_regression")
            positives = int(group_truth.sum())
            tp = int(((group_pred == 1) & (group_truth == 1)).sum())
            fp = int(((group_pred == 1) & (group_truth == 0)).sum())
            negatives = len(group_truth) - positives
            tn = int(((group_pred == 0) & (group_truth == 0)).sum())
            fairness_rows.append({
                "attribute": attribute,
                "group": "Unknown" if pd.isna(group_value) or str(group_value) == "?" else str(group_value),
                "n": int(len(local_idx)),
                "positive_rate": float(group_truth.mean()),
                "precision": float(tp / (tp + fp)) if tp + fp else 0.0,
                "recall_sensitivity": float(tp / positives) if positives else 0.0,
                "specificity": float(tn / negatives) if negatives else 0.0,
            })
    pd.DataFrame(fairness_rows).to_csv(OUT / "tables/subgroup_metrics.csv", index=False)

    # Figures from held-out test aggregates/predictions only.
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for name in ["logistic_regression", "shallow_decision_tree"]:
        precision, recall, _ = precision_recall_curve(test_y, test_probs[name])
        ap = next(r["pr_auc_average_precision"] for r in results if r["model"] == name)
        axes[0].plot(recall, precision, label=f"{name.replace('_', ' ')} (AP={ap:.3f})")
        fpr, tpr, _ = roc_curve(test_y, test_probs[name])
        auc = next(r["roc_auc"] for r in results if r["model"] == name)
        axes[1].plot(fpr, tpr, label=f"{name.replace('_', ' ')} (AUC={auc:.3f})")
    prevalence = float(test_y.mean())
    axes[0].axhline(prevalence, linestyle="--", color="gray", label=f"Test prevalence={prevalence:.3f}")
    axes[0].set(xlabel="Recall", ylabel="Precision", title="Precision–Recall (test)", xlim=(0, 1), ylim=(0, 1))
    axes[1].plot([0, 1], [0, 1], linestyle="--", color="gray")
    axes[1].set(xlabel="False positive rate", ylabel="True positive rate", title="ROC (test)", xlim=(0, 1), ylim=(0, 1))
    for ax in axes:
        ax.legend(loc="best", fontsize=8)
        ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(OUT / "figures/test_pr_roc_curves.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 6))
    ax.plot([0, 1], [0, 1], "--", color="gray", label="Perfect calibration")
    for name in ["logistic_regression", "shallow_decision_tree"]:
        frac_pos, mean_pred = calibration_curve(test_y, test_probs[name], n_bins=10, strategy="quantile")
        brier = next(r["brier_score"] for r in results if r["model"] == name)
        ax.plot(mean_pred, frac_pos, marker="o", label=f"{name.replace('_', ' ')} (Brier={brier:.3f})")
    ax.set(xlabel="Mean predicted probability", ylabel="Observed fraction of positives", title="Calibration (held-out test)", xlim=(0, 1), ylim=(0, 1))
    ax.grid(alpha=0.2)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "figures/test_calibration.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    print("Completed analysis on official UCI data.")
    print(f"Cohort: {cohort_info['eligible_encounters']:,} encounters; positive rate={cohort_info['positive_rate']:.3%}")
    print(pd.DataFrame(results)[["model", "pr_auc_average_precision", "roc_auc", "precision", "recall_sensitivity", "specificity", "f1", "brier_score", "threshold"]].to_string(index=False))
    print("Aggregate outputs written under outputs/. No row-level output generated.")

if __name__ == "__main__":
    main()
